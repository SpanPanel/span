"""Bridge to ha-synthetic-sensors for SPAN Panel solar sensors."""

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .helpers import (
    construct_entity_id,
    construct_solar_inverter_entity_id,
    get_user_friendly_suffix,
    sanitize_name_for_entity_id,
)
from .util import panel_to_device_info

_LOGGER = logging.getLogger(__name__)


class SyntheticSensorsBridge:
    """Bridge to manage ha-synthetic-sensors YAML configuration for SPAN Panel."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        config_dir: str | None = None,
    ):
        """Initialize the synthetic sensors bridge.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for the integration
            config_dir: Optional directory to store config files. If None, uses integration directory.

        """
        self._hass = hass
        self._config_entry = config_entry

        # Use provided config_dir or default to integration directory
        if config_dir is not None:
            # Use provided directory
            self._config_file = Path(config_dir) / "span-ha-synthetic.yaml"
        else:
            # Default: place YAML file in the integration directory so it gets cleaned up
            # when the integration is deleted
            hass_config_dir = hass.config.config_dir
            if hass_config_dir is None:
                raise ValueError("Home Assistant config directory is not available")

            integration_dir = Path(hass_config_dir) / "custom_components" / "span_panel"
            self._config_file = integration_dir / "span-ha-synthetic.yaml"

    @property
    def config_file_path(self) -> Path:
        """Get the path to the synthetic sensors config file."""
        return self._config_file

    async def generate_solar_config(self, leg1: int, leg2: int) -> None:
        """Generate YAML configuration for solar inverter sensors."""
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

        # Build the configuration
        config = self._build_solar_config(
            leg1,
            leg2,
            coordinator,
            span_panel,
            power_entities,
            produced_entities,
            consumed_entities,
        )

        # Write the configuration file
        await self._write_solar_config(config)

    async def remove_solar_config(self) -> None:
        """Remove the solar configuration file."""
        try:
            await self._hass.async_add_executor_job(self._remove_config_file)
            _LOGGER.info("Removed solar synthetic sensors config: %s", self._config_file)
        except Exception as e:
            _LOGGER.error("Failed to remove synthetic sensors config: %s", e)

    async def validate_config(self) -> bool:
        """Validate the generated YAML configuration."""
        try:
            config = await self._hass.async_add_executor_job(self._read_config_file)
            if config is None:
                return False

            # Basic validation
            if not isinstance(config, dict):
                return False

            if "version" not in config or "sensors" not in config:
                return False

            _LOGGER.debug("Synthetic sensors config validation passed")
            return True

        except Exception as e:
            _LOGGER.error("Synthetic sensors config validation failed: %s", e)
            return False

    def _write_config_file(self, yaml_content: str) -> None:
        """Write YAML content to config file (blocking, for executor)."""
        # Ensure the directory exists
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_file, "w") as file:
            file.write(yaml_content)

    def _remove_config_file(self) -> None:
        """Remove config file (blocking, for executor)."""
        if self._config_file.exists():
            os.remove(self._config_file)

    def _read_config_file(self) -> dict[str, Any] | None:
        """Read and parse config file (blocking, for executor)."""
        if not self._config_file.exists():
            return None

        with open(self._config_file) as file:
            content = yaml.safe_load(file)
            # Ensure we return the expected type
            if isinstance(content, dict):
                return content
            return None

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
        """Construct entity ID for unmapped tab with consistent modern naming."""
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

    def _build_solar_config(
        self,
        leg1: int,
        leg2: int,
        coordinator: Any,
        span_panel: Any,
        power_entities: dict[str, str | None],
        produced_entities: dict[str, str | None],
        consumed_entities: dict[str, str | None],
    ) -> dict[str, Any]:
        """Build the complete solar configuration."""
        config: dict[str, Any] = {"version": "1.0", "sensors": {}}

        # Construct synthetic entity IDs using the integration's solar inverter helper
        power_entity_id = construct_solar_inverter_entity_id(
            coordinator,
            span_panel,
            "sensor",
            leg1,
            leg2,
            "instant_power",
            "Solar Inverter",
        )
        produced_entity_id = construct_solar_inverter_entity_id(
            coordinator,
            span_panel,
            "sensor",
            leg1,
            leg2,
            "energy_produced",
            "Solar Inverter",
        )
        consumed_entity_id = construct_solar_inverter_entity_id(
            coordinator,
            span_panel,
            "sensor",
            leg1,
            leg2,
            "energy_consumed",
            "Solar Inverter",
        )

        # Create power sensor - use full entity ID as key (without sensor. prefix)
        power_formula, power_variables = self._create_formula_and_variables(power_entities, "power")
        if power_entity_id:
            # Remove 'sensor.' prefix for YAML key
            power_key = power_entity_id.replace("sensor.", "")
            config["sensors"][power_key] = {
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
            # Remove 'sensor.' prefix for YAML key
            produced_key = produced_entity_id.replace("sensor.", "")
            config["sensors"][produced_key] = {
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
            # Remove 'sensor.' prefix for YAML key
            consumed_key = consumed_entity_id.replace("sensor.", "")
            config["sensors"][consumed_key] = {
                "name": "Solar Inverter Energy Consumed",
                "entity_id": consumed_entity_id,
                "formula": consumed_formula,
                "variables": consumed_variables,
                "unit_of_measurement": "Wh",
                "device_class": "energy",
                "state_class": "total_increasing",
            }

        return config

    async def _write_solar_config(self, config: dict[str, Any]) -> None:
        """Write the solar configuration to file."""
        try:
            yaml_content = yaml.dump(config, default_flow_style=False, sort_keys=False)
            await self._hass.async_add_executor_job(self._write_config_file, yaml_content)
            _LOGGER.info("Generated solar synthetic sensors config: %s", self._config_file)
        except Exception as e:
            _LOGGER.error("Failed to write synthetic sensors config: %s", e)
