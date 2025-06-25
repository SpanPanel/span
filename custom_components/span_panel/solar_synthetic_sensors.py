"""Solar-specific synthetic sensor configuration generator for SPAN Panel integration."""

import logging
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import yaml

from .const import DOMAIN, USE_DEVICE_PREFIX
from .helpers import (
    construct_entity_id,
    construct_synthetic_entity_id,
    get_user_friendly_suffix,
    sanitize_name_for_entity_id,
)

# Import at module level to avoid linter issues
from .synthetic_config_manager import SyntheticConfigManager
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
        """Initialize the solar synthetic sensors manager.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for this integration instance
            config_dir: Optional directory to store config files (unused, kept for compatibility)

        """
        self._hass = hass
        self._config_entry = config_entry
        self._config_dir = config_dir  # Store for passing to config manager
        self._config_manager: SyntheticConfigManager | None = None

        # Validate that we have a config directory available
        if config_dir is None and not hass.config.config_dir:
            raise ValueError("Home Assistant config directory is not available")

    async def _get_config_manager(self) -> SyntheticConfigManager:
        """Get the centralized config manager instance."""
        if self._config_manager is None:
            # Create a custom instance for this specific config directory
            if self._config_dir is not None:
                # For tests with temp directories, create a unique instance
                self._config_manager = SyntheticConfigManager(
                    self._hass,
                    config_filename="solar_synthetic_sensors.yaml",
                    config_dir=self._config_dir,
                )
            else:
                # For production, use the singleton pattern
                self._config_manager = await SyntheticConfigManager.get_instance(
                    self._hass, config_filename="solar_synthetic_sensors.yaml"
                )
        return self._config_manager

    @property
    def config_file_path(self) -> Path:
        """Get the path to the solar synthetic sensors config file."""
        if self._config_dir is not None:
            # For tests with custom config directories
            return Path(self._config_dir) / "solar_synthetic_sensors.yaml"
        else:
            # For production use
            return (
                Path(self._hass.config.config_dir)
                / "custom_components"
                / "span_panel"
                / "solar_synthetic_sensors.yaml"
            )

    async def generate_config(self, leg1: int, leg2: int) -> None:
        """Generate YAML configuration for solar inverter sensors.

        Args:
            leg1: First solar inverter leg circuit number
            leg2: Second solar inverter leg circuit number

        """
        _LOGGER.debug("Generating solar synthetic sensors configuration")

        # Migrate entity ID patterns if device prefix setting changed
        await self._migrate_entity_id_patterns_if_needed()

        # Update YAML variables to reflect current coordinator circuit data
        await self._update_yaml_variables_from_coordinator()

        # Get the coordinator and span panel data
        coordinator, span_panel = self._get_coordinator_data(DOMAIN)
        if not coordinator or not span_panel:
            return

        panel_serial = span_panel.status.serial_number
        config_manager = await self._get_config_manager()

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

        # Create solar sensors using the config manager
        device_id = span_panel.status.serial_number

        # Create each sensor with v1.0.10 compatible keys (no device scoping in keys)
        for sensor_key, sensor_config in solar_sensors.items():
            await config_manager.create_sensor(device_id, sensor_key, sensor_config)

        _LOGGER.debug("Generated %d solar sensors for panel %s", len(solar_sensors), panel_serial)

    async def remove_config(self) -> None:
        """Remove the solar configuration for this panel."""
        try:
            # Get the coordinator and span panel data
            coordinator, span_panel = self._get_coordinator_data(DOMAIN)
            if not coordinator or not span_panel:
                return

            panel_serial = span_panel.status.serial_number
            config_manager = await self._get_config_manager()

            # Remove all sensors for this panel
            deleted_count = await config_manager.delete_all_device_sensors(panel_serial)
            _LOGGER.info("Removed %d solar sensors for panel %s", deleted_count, panel_serial)
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                _LOGGER.debug(
                    "Event loop closed during solar config removal, this is expected during tests"
                )
            else:
                _LOGGER.error("Error removing solar config: %s", e)
        except Exception as e:
            _LOGGER.error("Error removing solar config: %s", e)

    async def validate_config(self) -> bool:
        """Validate the generated solar YAML configuration."""
        try:
            config_manager = await self._get_config_manager()
            # Check if config file exists and is readable
            if not await config_manager.config_file_exists():
                return False

            # Try to read the raw YAML file to check for syntax errors
            config_file_path = await config_manager.get_config_file_path()

            def _read_and_validate_yaml() -> bool:
                try:
                    with open(config_file_path, encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                        # Check if it's a valid dict and has required fields
                        if not isinstance(config, dict):
                            return False
                        return "version" in config and "sensors" in config
                except yaml.YAMLError:
                    return False
                except (OSError, TypeError, ValueError):
                    return False

            return await self._hass.async_add_executor_job(_read_and_validate_yaml)

        except Exception as e:
            _LOGGER.error("Solar config validation failed: %s", e)
            return False

    async def _write_solar_config(self, config: dict[str, Any]) -> None:
        """Write solar configuration to file (compatibility method for tests)."""
        try:
            config_file = self.config_file_path

            def _write_yaml() -> None:
                # Ensure directory exists
                config_file.parent.mkdir(parents=True, exist_ok=True)

                with open(config_file, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            await self._hass.async_add_executor_job(_write_yaml)
            _LOGGER.debug("Generated solar synthetic sensors config using compatibility method")
        except Exception as e:
            _LOGGER.error("Failed to write synthetic sensors config: %s", e)

    async def _reload_synthetic_sensors_for_removal(self) -> bool:
        """Reload ha-synthetic-sensors integration after removing solar config (compatibility method).

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

    async def _update_yaml_variables_from_coordinator(self) -> None:
        """Update YAML variable references when circuit names change in coordinator.

        This method updates the entity_id references in the YAML file to reflect
        current circuit names from the coordinator data.
        """
        try:
            config_manager = await self._get_config_manager()

            # Check if config file exists
            if not await config_manager.config_file_exists():
                _LOGGER.debug("No config file exists to update")
                return

            # Get coordinator data
            coordinator, span_panel = self._get_coordinator_data(DOMAIN)
            if not coordinator or not span_panel:
                _LOGGER.error("Could not get coordinator data for YAML variable update")
                return

            panel_serial = span_panel.status.serial_number

            # Get current config
            config = await config_manager.read_config()
            if "sensors" not in config:
                return

            # Update entity references for all sensors (not just this panel)
            # This handles the case where SPAN circuit names change and we need to update
            # all references to those circuits across all sensors
            updated = False

            for sensor_config in config["sensors"].values():
                # Update variables that reference SPAN circuits
                if "variables" in sensor_config:
                    variables = sensor_config["variables"]
                    for var_name, entity_id in variables.items():
                        if isinstance(entity_id, str) and entity_id.startswith(
                            "sensor.span_panel_"
                        ):
                            # This is a SPAN entity reference - update it based on current circuit names
                            # Extract circuit information and regenerate entity ID
                            updated_entity_id = self._update_entity_reference(
                                entity_id, coordinator, span_panel
                            )
                            if updated_entity_id and updated_entity_id != entity_id:
                                variables[var_name] = updated_entity_id
                                updated = True
                                _LOGGER.debug(
                                    "Updated variable %s: %s -> %s",
                                    var_name,
                                    entity_id,
                                    updated_entity_id,
                                )

                # Also update formula references (direct entity references in formulas)
                if "formula" in sensor_config:
                    formula = sensor_config["formula"]
                    updated_formula = self._update_formula_entity_references(
                        formula, coordinator, span_panel
                    )
                    if updated_formula != formula:
                        sensor_config["formula"] = updated_formula
                        updated = True
                        _LOGGER.debug(
                            "Updated formula: %s -> %s",
                            formula,
                            updated_formula,
                        )

            # Write back if we made changes
            if updated:
                # Create a temporary config file and replace the existing one

                config_file_path = await config_manager.get_config_file_path()

                def _write_temp_config() -> None:
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yaml", delete=False
                    ) as temp_file:
                        yaml.dump(config, temp_file, default_flow_style=False, sort_keys=False)
                        temp_path = temp_file.name

                    # Replace the original file
                    shutil.move(temp_path, str(config_file_path))

                await self._hass.async_add_executor_job(_write_temp_config)
                _LOGGER.info("Updated YAML variables for panel %s", panel_serial)

        except Exception as e:
            _LOGGER.error("Failed to update YAML variables from coordinator: %s", e)

    def _update_entity_reference(
        self, entity_id: str, coordinator: Any, span_panel: Any
    ) -> str | None:
        """Update a single entity reference based on current coordinator data.

        Args:
            entity_id: Original entity ID to update
            coordinator: Coordinator instance
            span_panel: Span panel data

        Returns:
            Updated entity ID or None if no update needed

        """
        try:
            # Parse the entity_id to extract circuit information
            # Expected format: sensor.span_panel_{circuit_identifier}_{suffix}
            if not entity_id.startswith("sensor.span_panel_"):
                return entity_id

            # Extract the part after sensor.span_panel_
            entity_part = entity_id[len("sensor.span_panel_") :]

            # Determine what suffix this entity has
            suffix = None
            if entity_part.endswith("_power"):
                suffix = "power"
                circuit_part = entity_part[: -len("_power")]
            elif entity_part.endswith("_energy_produced"):
                suffix = "energy_produced"
                circuit_part = entity_part[: -len("_energy_produced")]
            elif entity_part.endswith("_energy_consumed"):
                suffix = "energy_consumed"
                circuit_part = entity_part[: -len("_energy_consumed")]
            else:
                # Unknown suffix, can't update
                return entity_id

            # Try to find the circuit that matches this entity
            matching_circuit = None

            # First, try to match by circuit_id directly
            for circuit in span_panel.circuits.values():
                if hasattr(circuit, "circuit_id") and circuit.circuit_id == circuit_part:
                    matching_circuit = circuit
                    break

            # If no direct match, try to match by sanitized name
            if not matching_circuit:
                for circuit in span_panel.circuits.values():
                    circuit_name_sanitized = circuit.name.lower().replace(" ", "_")
                    if circuit_name_sanitized == circuit_part:
                        matching_circuit = circuit
                        break

            # If we found a matching circuit, generate new entity ID
            if matching_circuit:
                # Construct new entity ID using current circuit name
                new_entity_id = construct_entity_id(
                    coordinator, span_panel, "sensor", matching_circuit.name, 0, suffix
                )
                return new_entity_id

            return entity_id
        except Exception as e:
            _LOGGER.warning("Failed to update entity reference %s: %s", entity_id, e)
            return entity_id

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

        # Get device identifier for this panel to associate sensors with the correct device
        device_identifier = f"span_panel_{span_panel.status.serial_number}"

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
                "device_identifier": device_identifier,
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
                "device_identifier": device_identifier,
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
                "device_identifier": device_identifier,
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

    def _update_formula_entity_references(
        self, formula: str, coordinator: Any, span_panel: Any
    ) -> str:
        """Update direct entity references in formulas.

        Args:
            formula: Original formula string
            coordinator: Coordinator instance
            span_panel: Span panel data

        Returns:
            Updated formula string

        """
        try:
            # Look for direct sensor references in the formula (pattern: sensor.span_panel_*)

            # Find all sensor.span_panel_* references in the formula
            pattern = r"sensor\.span_panel_[a-zA-Z0-9_]+"
            matches = re.findall(pattern, formula)

            updated_formula = formula
            for entity_ref in matches:
                updated_entity_ref = self._update_entity_reference(
                    entity_ref, coordinator, span_panel
                )
                if updated_entity_ref and updated_entity_ref != entity_ref:
                    updated_formula = updated_formula.replace(entity_ref, updated_entity_ref)

            return updated_formula
        except Exception as e:
            _LOGGER.warning("Failed to update formula entity references in '%s': %s", formula, e)
            return formula
