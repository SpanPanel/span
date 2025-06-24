"""Solar-specific synthetic sensors for SPAN Panel."""

import logging
from pathlib import Path
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import yaml

from .const import DOMAIN, USE_DEVICE_PREFIX
from .helpers import (
    construct_entity_id,
    construct_synthetic_entity_id,
    get_user_friendly_suffix,
    sanitize_name_for_entity_id,
)
from .synthetic_bridge import SyntheticSensorsBridge
from .util import panel_to_device_info

_LOGGER = logging.getLogger(__name__)


class SolarSyntheticSensors:
    """Solar-specific synthetic sensor configuration generator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        config_dir: str | None = None,
    ):
        """Initialize the solar synthetic sensors generator.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for the integration
            config_dir: Optional directory to store config files. If None, uses integration directory.

        """
        self._hass = hass
        self._config_entry = config_entry
        # Use the generic bridge for file operations with solar-specific filename
        self._bridge = SyntheticSensorsBridge(
            hass, config_entry, config_dir, config_filename="solar_synthetic_sensors.yaml"
        )

    @property
    def config_file_path(self) -> Path:
        """Get the path to the solar synthetic sensors config file."""
        return self._bridge.config_file_path

    async def generate_config(self, leg1: int, leg2: int) -> None:
        """Generate YAML configuration for solar inverter sensors.

        Args:
            leg1: First solar inverter leg circuit number
            leg2: Second solar inverter leg circuit number

        """
        _LOGGER.debug("Generating solar synthetic sensors configuration")

        # Migrate entity ID patterns if device prefix setting changed
        await self._migrate_entity_id_patterns_if_needed()

        # Get the coordinator and span panel data
        coordinator, span_panel = self._get_coordinator_data(DOMAIN)
        if not coordinator or not span_panel:
            return

        # Get entity IDs for each sensor type
        power_entities = self._get_entity_ids_for_field(
            leg1, leg2, coordinator, span_panel, "instantPowerW"
        )
        produced_entities = self._get_entity_ids_for_field(
            leg1, leg2, coordinator, span_panel, "producedEnergyWh"
        )
        consumed_entities = self._get_entity_ids_for_field(
            leg1, leg2, coordinator, span_panel, "consumedEnergyWh"
        )

        # Validate we have at least power entities
        if not power_entities["leg1"] and not power_entities["leg2"]:
            _LOGGER.error("No valid solar legs configured")
            return

        # Load existing config (if any) to merge with
        existing_config = await self._load_existing_config()

        # Build the solar sensor configurations
        solar_sensors = self._build_solar_sensors(
            leg1,
            leg2,
            coordinator,
            span_panel,
            power_entities,
            produced_entities,
            consumed_entities,
        )

        # Merge solar sensors into existing config
        final_config = self._merge_solar_into_config(existing_config, solar_sensors)

        # Write the merged configuration
        await self._write_solar_config(final_config)

    async def remove_config(self) -> None:
        """Remove the solar configuration file and clean up entities."""
        # First, identify the solar entities we need to clean up
        solar_entity_ids = await self._get_solar_entity_ids_for_cleanup()

        # Remove the YAML configuration file
        await self._bridge.remove_config()

        # Force reload of ha-synthetic-sensors to remove the entities
        reload_success = await self._reload_synthetic_sensors_for_removal()

        # If reload failed or didn't clean up properly, manually remove from entity registry
        if not reload_success and solar_entity_ids:
            await self._cleanup_orphaned_solar_entities(solar_entity_ids)

    async def validate_config(self) -> bool:
        """Validate the generated solar YAML configuration."""
        return await self._bridge.validate_config()

    def _get_coordinator_data(self, domain: str) -> tuple[Any, Any]:
        """Get coordinator and span panel data."""
        coordinator_data = self._hass.data.get(domain, {}).get(self._config_entry.entry_id, {})
        coordinator = coordinator_data.get("coordinator")
        if not coordinator:
            _LOGGER.error("Could not find coordinator for entity ID construction")
            return None, None

        span_panel = coordinator.data
        if not span_panel:
            _LOGGER.error("Could not find span panel data for entity ID construction")
            return None, None

        return coordinator, span_panel

    def _get_entity_ids_for_field(
        self,
        leg1: int,
        leg2: int,
        coordinator: Any,
        span_panel: Any,
        field: str,
    ) -> dict[str, str | None]:
        """Get entity IDs for a specific field (power, produced, consumed)."""
        entities: dict[str, str | None] = {"leg1": None, "leg2": None}

        # Process each leg using a helper method
        entities["leg1"] = self._get_entity_id_for_leg(leg1, coordinator, span_panel, field)
        entities["leg2"] = self._get_entity_id_for_leg(leg2, coordinator, span_panel, field)

        return entities

    def _get_entity_id_for_leg(
        self,
        leg_number: int,
        coordinator: Any,
        span_panel: Any,
        field: str,
    ) -> str | None:
        """Get entity ID for a single leg."""
        if leg_number <= 0:
            return None

        circuit_id = f"unmapped_tab_{leg_number}"
        if circuit_id not in span_panel.circuits:
            return None

        circuit = span_panel.circuits[circuit_id]
        suffix = get_user_friendly_suffix(field)

        # Handle unmapped circuits with modern naming (always device prefix as they are invisible)
        if circuit_id.startswith("unmapped_tab_"):
            return self._construct_unmapped_entity_id_simple(span_panel, leg_number, suffix)
        else:
            # Regular circuit - use standard naming logic
            return construct_entity_id(
                coordinator, span_panel, "sensor", circuit.name, leg_number, suffix
            )

    def _construct_unmapped_entity_id_simple(
        self, span_panel: Any, leg_number: int, suffix: str
    ) -> str:
        """Construct entity ID for unmapped tab."""
        # Always use device prefix and circuit numbers for unmapped entities
        device_info = panel_to_device_info(span_panel)
        device_name_raw = device_info.get("name")
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            return f"sensor.{device_name}_unmapped_tab_{leg_number}_{suffix}"
        else:
            return f"sensor.unmapped_tab_{leg_number}_{suffix}"

    def _create_formula_and_variables(
        self, entities: dict[str, str | None], var_prefix: str
    ) -> tuple[str, dict[str, str]]:
        """Create formula and variables for a sensor type."""
        leg1_entity = entities["leg1"]
        leg2_entity = entities["leg2"]

        if leg1_entity and leg2_entity:
            formula = f"leg1_{var_prefix} + leg2_{var_prefix}"
            variables = {
                f"leg1_{var_prefix}": leg1_entity,
                f"leg2_{var_prefix}": leg2_entity,
            }
        elif leg1_entity:
            formula = f"leg1_{var_prefix}"
            variables = {f"leg1_{var_prefix}": leg1_entity}
        elif leg2_entity:
            formula = f"leg2_{var_prefix}"
            variables = {f"leg2_{var_prefix}": leg2_entity}
        else:
            formula = "0"
            variables = {}

        return formula, variables

    def _construct_solar_inverter_entity_id(
        self,
        coordinator: Any,
        span_panel: Any,
        platform: str,
        inverter_leg1: int,
        inverter_leg2: int,
        suffix: str,
        friendly_name: str | None = None,
    ) -> str | None:
        """Construct solar inverter entity ID based on integration configuration flags.

        This is a solar-specific convenience wrapper around construct_synthetic_entity_id.

        Args:
            coordinator: The coordinator instance
            span_panel: The span panel data
            platform: Platform name ("sensor")
            inverter_leg1: First circuit/leg number
            inverter_leg2: Second circuit/leg number
            suffix: Entity-specific suffix ("instant_power", "energy_produced", etc.)
            friendly_name: Optional friendly name for legacy installations (e.g., "Solar Inverter")

        Returns:
            Constructed entity ID string or None if device info unavailable

        """
        # Convert solar inverter legs to circuit numbers list
        circuit_numbers = [inverter_leg1]
        if inverter_leg2 > 0:
            circuit_numbers.append(inverter_leg2)

        return construct_synthetic_entity_id(
            coordinator=coordinator,
            span_panel=span_panel,
            platform=platform,
            circuit_numbers=circuit_numbers,
            suffix=suffix,
            friendly_name=friendly_name,
        )

    async def _write_solar_config(self, config: dict[str, Any]) -> None:
        """Write the solar configuration to file using the bridge."""
        try:
            yaml_content = yaml.dump(config, default_flow_style=False, sort_keys=False)
            config_file = self._bridge.config_file_path
            await self._hass.async_add_executor_job(
                self._write_config_file, config_file, yaml_content
            )
            _LOGGER.info("Generated solar synthetic sensors config: %s", config_file)
        except Exception as e:
            _LOGGER.error("Failed to write synthetic sensors config: %s", e)

    def _write_config_file(self, config_file: Path, yaml_content: str) -> None:
        """Write YAML content to file."""
        # Ensure the directory exists
        config_file.parent.mkdir(parents=True, exist_ok=True)

        # Write the YAML content
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(yaml_content)

    async def _reload_synthetic_sensors_for_removal(self) -> bool:
        """Reload ha-synthetic-sensors integration after removing solar config.

        Returns:
            True if reload was successful, False otherwise

        """
        try:
            # Find and reload the ha-synthetic-sensors integration
            for entry in self._hass.config_entries.async_entries("ha_synthetic_sensors"):
                _LOGGER.info("Reloading ha-synthetic-sensors to clean up removed solar sensors")
                await self._hass.config_entries.async_reload(entry.entry_id)
                return True

            # No ha-synthetic-sensors integration found
            _LOGGER.debug("No ha-synthetic-sensors integration found for reload")
            return False

        except Exception as e:
            _LOGGER.warning(
                "Failed to reload ha-synthetic-sensors after solar config removal: %s", e
            )
            return False

    async def _get_solar_entity_ids_for_cleanup(self) -> list[str]:
        """Get the entity IDs of solar synthetic sensors that need cleanup.

        Returns:
            List of entity IDs for solar synthetic sensors

        """
        entity_registry = er.async_get(self._hass)
        solar_entity_ids: list[str] = []

        # Look for entities that match our solar synthetic sensor patterns
        entities = getattr(entity_registry, "entities", {})
        for entity_entry in entities.values():
            entity_id = entity_entry.entity_id

            # Check if this looks like one of our solar synthetic sensors
            if (
                entity_entry.config_entry_id == self._config_entry.entry_id
                and entity_entry.platform
                == "ha_synthetic_sensors"  # The synthetic sensors platform
                and (
                    "solar_inverter" in entity_id
                    or ("instant_power" in entity_id and "span_panel" in entity_id)
                    or ("energy_produced" in entity_id and "span_panel" in entity_id)
                    or ("energy_consumed" in entity_id and "span_panel" in entity_id)
                )
            ):
                solar_entity_ids.append(entity_id)
                _LOGGER.debug("Found solar synthetic sensor for cleanup: %s", entity_id)

        _LOGGER.info("Found %d solar synthetic sensors for cleanup", len(solar_entity_ids))
        return solar_entity_ids

    async def _cleanup_orphaned_solar_entities(self, entity_ids: list[str]) -> None:
        """Remove orphaned solar entities from the entity registry.

        Args:
            entity_ids: List of entity IDs to remove from registry

        """
        if not entity_ids:
            return

        entity_registry = er.async_get(self._hass)
        removed_count = 0

        for entity_id in entity_ids:
            try:
                entity_registry.async_remove(entity_id)
                removed_count += 1
                _LOGGER.info("Removed orphaned solar sensor entity: %s", entity_id)
            except Exception as e:
                _LOGGER.warning(
                    "Failed to remove orphaned solar sensor entity %s: %s", entity_id, e
                )

        if removed_count > 0:
            _LOGGER.info("Cleaned up %d orphaned solar synthetic sensor entities", removed_count)

    async def _update_yaml_variables_from_coordinator(self) -> None:
        """Update YAML variables to reflect current coordinator circuit data.

        This method reads the existing YAML file, updates any SPAN sensor references
        in the variables to match current circuit names, and writes the updated YAML back.
        Non-SPAN sensors and unmapped circuits are left unchanged.
        """
        if not self.config_file_path.exists():
            _LOGGER.debug("No YAML config file exists to update")
            return

        # Get coordinator data
        coordinator, span_panel = self._get_coordinator_data(DOMAIN)
        if not coordinator or not span_panel:
            _LOGGER.debug("No coordinator data available for YAML update")
            return

        # Read existing YAML
        try:

            def _read_yaml() -> dict[str, Any]:
                with open(self.config_file_path, encoding="utf-8") as f:
                    result = yaml.safe_load(f)
                    if isinstance(result, dict):
                        return cast(dict[str, Any], result)
                    return {}

            config = await self._hass.async_add_executor_job(_read_yaml)
        except (yaml.YAMLError, OSError) as e:
            _LOGGER.error("Failed to read YAML config for update: %s", e)
            return

        if not config or "sensors" not in config:
            _LOGGER.debug("No sensors section found in YAML config")
            return

        updated = False

        # Update variables in each sensor
        for sensor_key, sensor_config in config["sensors"].items():
            if "variables" not in sensor_config:
                continue

            for var_name, entity_id in sensor_config["variables"].items():
                # Update SPAN sensor entity IDs (both modern and legacy patterns)
                if not (
                    entity_id.startswith("sensor.span_panel_")
                    or entity_id.startswith("sensor.solar_inverter_")
                ):
                    continue

                # Skip unmapped circuits (they have stable entity IDs)
                if "unmapped_tab_" in entity_id:
                    continue

                # Try to find the circuit this entity ID refers to and update it
                updated_entity_id = self._update_span_entity_id(entity_id, coordinator, span_panel)
                if updated_entity_id != entity_id:
                    sensor_config["variables"][var_name] = updated_entity_id
                    updated = True
                    _LOGGER.debug(
                        "Updated variable %s in sensor %s: %s -> %s",
                        var_name,
                        sensor_key,
                        entity_id,
                        updated_entity_id,
                    )

        # Write updated YAML if changes were made
        if updated:
            try:

                def _write_yaml() -> None:
                    with open(self.config_file_path, "w", encoding="utf-8") as f:
                        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

                await self._hass.async_add_executor_job(_write_yaml)
                _LOGGER.info("Updated YAML variables to reflect circuit name changes")
            except OSError as e:
                _LOGGER.error("Failed to write updated YAML config: %s", e)
        else:
            _LOGGER.debug("No YAML variable updates needed")

    def _update_span_entity_id(self, entity_id: str, coordinator: Any, span_panel: Any) -> str:
        """Update a SPAN entity ID to reflect current circuit name.

        Args:
            entity_id: The current entity ID (e.g., sensor.span_panel_old_name_power)
            coordinator: The coordinator with current circuit data
            span_panel: The span panel data

        Returns:
            Updated entity ID if the circuit was found, otherwise the original entity ID

        """
        # Extract the circuit identifier from the entity ID
        # Pattern: sensor.span_panel_{circuit_name}_{suffix}
        if not entity_id.startswith("sensor.span_panel_"):
            return entity_id

        # Remove sensor.span_panel_ prefix
        remainder = entity_id[len("sensor.span_panel_") :]

        # Find the suffix (power, energy_produced, etc.)
        known_suffixes = [
            "_power",
            "_energy_produced",
            "_energy_consumed",
            "_instant_power",
            "_produced_energy",
            "_consumed_energy",
        ]

        circuit_name_part = remainder
        suffix = ""

        for known_suffix in known_suffixes:
            if remainder.endswith(known_suffix):
                circuit_name_part = remainder[: -len(known_suffix)]
                suffix = known_suffix
                break

        if not suffix:
            # Couldn't parse the entity ID
            return entity_id

        # Try to find the circuit by circuit_id first (most reliable)
        if hasattr(coordinator, "data") and hasattr(coordinator.data, "circuits"):  # type: ignore[misc]
            if circuit_name_part in coordinator.data.circuits:  # type: ignore[misc]
                circuit = coordinator.data.circuits[circuit_name_part]  # type: ignore[misc]
                new_entity_id = construct_entity_id(
                    coordinator,
                    span_panel,
                    "sensor",
                    circuit.name,
                    circuit.id,  # type: ignore[misc]
                    get_user_friendly_suffix(suffix.lstrip("_")),
                )
                if new_entity_id:
                    return new_entity_id

            # Try to find the circuit by looking for a match in coordinator data
            # This handles cases where the entity ID doesn't exactly match the circuit_id
            for circuit in coordinator.data.circuits.values():  # type: ignore[misc]
                # Check if this could be the circuit by comparing the sanitized name
                expected_name_part = sanitize_name_for_entity_id(circuit.name).lower()  # type: ignore[misc]
                if (
                    circuit_name_part.replace("_", "").lower()
                    == expected_name_part.replace("_", "").lower()
                ):
                    # Found a match, construct the new entity ID
                    new_entity_id = construct_entity_id(
                        coordinator,
                        span_panel,
                        "sensor",
                        circuit.name,
                        circuit.id,  # type: ignore[misc]
                        get_user_friendly_suffix(suffix.lstrip("_")),
                    )
                    if new_entity_id:
                        return new_entity_id

        # No match found, return original
        return entity_id

    async def _load_existing_config(self) -> dict[str, Any]:
        """Load existing YAML configuration if it exists."""
        config_file = self._bridge.config_file_path
        if not config_file.exists():
            return {"version": "1.0", "sensors": {}}

        try:

            def _load_yaml() -> dict[str, Any]:
                with open(config_file, encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    return content if content else {"version": "1.0", "sensors": {}}

            return await self._hass.async_add_executor_job(_load_yaml)
        except Exception as e:
            _LOGGER.warning("Failed to load existing config, starting fresh: %s", e)
            return {"version": "1.0", "sensors": {}}

    def _build_solar_sensors(
        self,
        leg1: int,
        leg2: int,
        coordinator: Any,
        span_panel: Any,
        power_entities: dict[str, str | None],
        produced_entities: dict[str, str | None],
        consumed_entities: dict[str, str | None],
    ) -> dict[str, Any]:
        """Build just the solar sensor configurations."""
        solar_sensors: dict[str, Any] = {}

        # Construct synthetic entity IDs using stable, friendly naming pattern
        # These should always be stable regardless of underlying circuit naming
        power_entity_id = self._construct_solar_inverter_entity_id(
            coordinator,
            span_panel,
            "sensor",
            leg1,
            leg2,
            "instant_power",
            "Solar Inverter",
        )
        produced_entity_id = self._construct_solar_inverter_entity_id(
            coordinator,
            span_panel,
            "sensor",
            leg1,
            leg2,
            "energy_produced",
            "Solar Inverter",
        )
        consumed_entity_id = self._construct_solar_inverter_entity_id(
            coordinator,
            span_panel,
            "sensor",
            leg1,
            leg2,
            "energy_consumed",
            "Solar Inverter",
        )

        # Create power sensor - use circuit-based key to match v1.0.10 unique_id format
        power_formula, power_variables = self._create_formula_and_variables(power_entities, "power")
        if power_entity_id:
            key = self._generate_circuit_based_key(leg1, leg2, "instant_power")
            solar_sensors[key] = {
                "name": "Solar Inverter Instant Power",
                "entity_id": power_entity_id,
                "formula": power_formula,
                "variables": power_variables,
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
            }

        # Create energy produced sensor
        produced_formula, produced_variables = self._create_formula_and_variables(
            produced_entities, "produced"
        )
        if produced_entity_id:
            key = self._generate_circuit_based_key(leg1, leg2, "energy_produced")
            solar_sensors[key] = {
                "name": "Solar Inverter Energy Produced",
                "entity_id": produced_entity_id,
                "formula": produced_formula,
                "variables": produced_variables,
                "unit_of_measurement": "Wh",
                "device_class": "energy",
                "state_class": "total_increasing",
            }

        # Create energy consumed sensor
        consumed_formula, consumed_variables = self._create_formula_and_variables(
            consumed_entities, "consumed"
        )
        if consumed_entity_id:
            key = self._generate_circuit_based_key(leg1, leg2, "energy_consumed")
            solar_sensors[key] = {
                "name": "Solar Inverter Energy Consumed",
                "entity_id": consumed_entity_id,
                "formula": consumed_formula,
                "variables": consumed_variables,
                "unit_of_measurement": "Wh",
                "device_class": "energy",
                "state_class": "total_increasing",
            }

        return solar_sensors

    def _generate_circuit_based_key(self, leg1: int, leg2: int, suffix: str) -> str:
        """Generate YAML key that matches v1.0.10 unique_id format.

        In v1.0.10, unique_ids were: span_{serial}_synthetic_{circuits}_{key}
        With prefix: span_{serial}_synthetic_{circuits}
        The YAML key should be: solar_inverter_{suffix}

        Args:
            leg1: First solar inverter leg circuit number
            leg2: Second solar inverter leg circuit number
            suffix: Sensor type suffix (instant_power, energy_produced, energy_consumed)

        Returns:
            YAML key for configuration

        """
        # Generate key matching v1.0.10 format: solar_inverter_{suffix}
        return f"solar_inverter_{suffix}"

    def _merge_solar_into_config(
        self, existing_config: dict[str, Any], solar_sensors: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge solar sensors into existing configuration, replacing any existing solar sensors."""
        # Ensure the config has the right structure
        if "sensors" not in existing_config:
            existing_config["sensors"] = {}

        # Remove any existing solar sensors first (to handle circuit number changes)
        # Solar sensors have keys that start with "solar_inverter_" (v1.0.10 format)
        # or "span_panel_solar_inverter_" (old circuit-based format)
        solar_keys_to_remove = [
            key
            for key in existing_config["sensors"]
            if key.startswith("solar_inverter_") or key.startswith("span_panel_solar_inverter_")
        ]

        for key in solar_keys_to_remove:
            del existing_config["sensors"][key]

        # Add the new solar sensors
        for key, value in solar_sensors.items():
            existing_config["sensors"][key] = value

        # Ensure version is set
        existing_config["version"] = "1.0"

        return existing_config

    async def _migrate_entity_id_patterns_if_needed(self) -> None:
        """Migrate entity ID patterns when device prefix setting changes.

        This handles the case where a legacy installation (USE_DEVICE_PREFIX: False)
        changes to modern naming (USE_DEVICE_PREFIX: True), requiring entity IDs
        to be updated from 'sensor.solar_inverter_*' to 'sensor.span_panel_solar_inverter_*'.
        """
        if not self.config_file_path.exists():
            _LOGGER.debug("No YAML config file exists to migrate")
            return

        # Get coordinator data
        coordinator, span_panel = self._get_coordinator_data(DOMAIN)
        if not coordinator or not span_panel:
            _LOGGER.debug("No coordinator data available for entity ID migration")
            return

        # Check if device prefix is now enabled (potential migration scenario)
        use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)
        if not use_device_prefix:
            # Device prefix is disabled, no migration needed
            return

        # Read existing YAML
        try:

            def _read_yaml() -> dict[str, Any] | None:
                with open(self.config_file_path, encoding="utf-8") as f:
                    result = yaml.safe_load(f)
                    return cast(dict[str, Any], result) if isinstance(result, dict) else None

            config = await self._hass.async_add_executor_job(_read_yaml)
        except (yaml.YAMLError, OSError) as e:
            _LOGGER.error("Failed to read YAML config for migration: %s", e)
            return

        if not config or "sensors" not in config:
            _LOGGER.debug("No sensors section found in YAML config")
            return

        updated = False

        # Look for legacy solar inverter entity IDs that need migration
        legacy_patterns = [
            "sensor.solar_inverter_instant_power",
            "sensor.solar_inverter_energy_produced",
            "sensor.solar_inverter_energy_consumed",
        ]

        # Check both sensor entity_ids and variable references
        for sensor_key, sensor_config in config["sensors"].items():
            # Migrate sensor entity_id if it matches legacy pattern
            if "entity_id" in sensor_config:
                old_entity_id = sensor_config["entity_id"]
                if old_entity_id in legacy_patterns:
                    # Convert to modern entity ID with device prefix
                    new_entity_id = f"sensor.span_panel_{old_entity_id.replace('sensor.', '')}"
                    sensor_config["entity_id"] = new_entity_id
                    updated = True
                    _LOGGER.info(
                        "Migrated sensor entity_id in %s: %s -> %s",
                        sensor_key,
                        old_entity_id,
                        new_entity_id,
                    )

            # Migrate variable references
            if "variables" in sensor_config:
                for var_name, entity_id in sensor_config["variables"].items():
                    if entity_id in legacy_patterns:
                        # Convert to modern entity ID with device prefix
                        new_entity_id = f"sensor.span_panel_{entity_id.replace('sensor.', '')}"
                        sensor_config["variables"][var_name] = new_entity_id
                        updated = True
                        _LOGGER.info(
                            "Migrated variable %s in sensor %s: %s -> %s",
                            var_name,
                            sensor_key,
                            entity_id,
                            new_entity_id,
                        )

        # Write updated YAML if changes were made
        if updated:
            try:

                def _write_yaml() -> None:
                    with open(self.config_file_path, "w", encoding="utf-8") as f:
                        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

                await self._hass.async_add_executor_job(_write_yaml)
                _LOGGER.info("Migrated YAML entity IDs to use device prefix")
            except OSError as e:
                _LOGGER.error("Failed to write migrated YAML config: %s", e)
