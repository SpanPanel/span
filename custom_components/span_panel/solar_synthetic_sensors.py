"""Solar-specific synthetic sensor configuration generator for SPAN Panel integration."""

import logging
from pathlib import Path
from typing import Any, TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import yaml

from .const import DOMAIN
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_synthetic_entity_id,
    construct_synthetic_unique_id,
    construct_unmapped_entity_id,
    get_user_friendly_suffix,
)
from .solar_tab_manager import SolarTabManager
from .span_panel import SpanPanel
from .synthetic_config_manager import SyntheticConfigManager

_LOGGER = logging.getLogger(__name__)


class SolarSensorConfig(TypedDict):
    """Type definition for solar synthetic sensor configuration."""

    name: str
    entity_id: str
    formula: str
    variables: dict[str, str]
    unit_of_measurement: str
    device_class: str
    state_class: str
    device_identifier: str


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
        self._config_dir = config_dir
        self._config_manager: SyntheticConfigManager | None = None

        # Validate that we have a config directory available
        if config_dir is None and not hass.config.config_dir:
            raise ValueError("Home Assistant config directory is not available")

    async def setup_solar_sensors(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        async_add_entities: AddEntitiesCallback,
        inverter_leg1: int,
        inverter_leg2: int,
    ) -> None:
        """Set up complete solar sensor infrastructure.

        This method handles:
        1. Enabling required tab circuits
        2. Generating YAML configuration
        3. Creating synthetic sensors

        Args:
            coordinator: The SPAN panel coordinator instance
            span_panel: The SPAN panel data instance
            async_add_entities: Callback to add entities
            inverter_leg1: First solar inverter leg circuit number
            inverter_leg2: Second solar inverter leg circuit number

        """
        try:
            _LOGGER.info(
                "Setting up solar sensors - leg1: %s, leg2: %s",
                inverter_leg1,
                inverter_leg2,
            )

            # Step 1: Enable the required tab circuits (but keep them hidden)
            tab_manager = SolarTabManager(self._hass, self._config_entry)
            await tab_manager.enable_solar_tabs(inverter_leg1, inverter_leg2)

            # Step 2: Generate synthetic sensors YAML configuration
            await self._generate_solar_config(coordinator, span_panel, inverter_leg1, inverter_leg2)

            # Step 3: Validate the generated configuration
            if not await self.validate_config():
                _LOGGER.error("Failed to validate solar synthetic sensors configuration")
                return

            _LOGGER.debug("Solar synthetic sensors configuration generated successfully")

            # The synthetic sensors integration will automatically pick up the YAML file
            # No need to manually create sensors here

        except Exception as e:
            _LOGGER.error("Failed to set up solar sensors: %s", e)

    async def cleanup_solar_sensors(self) -> None:
        """Clean up solar configuration when disabled.

        This method handles:
        1. Disabling tab circuits
        2. Removing configuration files
        """
        try:
            # Clean up solar configuration when disabled
            tab_manager = SolarTabManager(self._hass, self._config_entry)
            await tab_manager.disable_solar_tabs()
            await self.remove_config()

        except Exception as e:
            _LOGGER.error("Failed to clean up solar sensors: %s", e)

    async def _generate_solar_config(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel, leg1: int, leg2: int
    ) -> None:
        """Generate YAML configuration for solar inverter sensors using existing helpers.

        This simplified version leverages existing helper functions instead of duplicating logic.
        """
        _LOGGER.debug("Generating simplified solar synthetic sensors configuration")

        # Early return if no valid legs or no span panel
        if (leg1 <= 0 and leg2 <= 0) or span_panel is None:
            _LOGGER.debug("No valid solar legs configured or no span panel data")
            return

        panel_serial = span_panel.status.serial_number
        config_manager = await self._get_config_manager()

        # Get entity IDs for unmapped circuits using existing helpers
        power_entities = self._get_unmapped_entity_ids(span_panel, leg1, leg2, "instantPowerW")
        produced_entities = self._get_unmapped_entity_ids(
            span_panel, leg1, leg2, "producedEnergyWh"
        )
        consumed_entities = self._get_unmapped_entity_ids(
            span_panel, leg1, leg2, "consumedEnergyWh"
        )

        # Validate we have at least power entities
        if not any(power_entities.values()):
            _LOGGER.error("No valid solar legs configured")
            return

        # Build the solar sensor configurations using existing helpers
        solar_sensors = self._build_simplified_solar_sensors(
            coordinator,
            span_panel,
            leg1,
            leg2,
            power_entities,
            produced_entities,
            consumed_entities,
        )

        # Create each sensor with the config manager
        device_id = panel_serial
        for sensor_key, sensor_config in solar_sensors.items():
            # Type cast to dict[str, Any] for compatibility with create_sensor
            await config_manager.create_sensor(device_id, sensor_key, dict(sensor_config))

        _LOGGER.debug("Generated %d solar sensors for panel %s", len(solar_sensors), panel_serial)

    def _get_unmapped_entity_ids(
        self, span_panel: SpanPanel, leg1: int, leg2: int, field: str
    ) -> dict[str, str | None]:
        """Get entity IDs for unmapped circuits using existing helpers.

        This replaces the complex _get_entity_ids_for_field method.
        """
        entities: dict[str, str | None] = {"leg1": None, "leg2": None}
        suffix = get_user_friendly_suffix(field)

        # Use existing helper for unmapped entity IDs
        if leg1 > 0:
            circuit_id = f"unmapped_tab_{leg1}"
            if circuit_id in span_panel.circuits:
                entities["leg1"] = construct_unmapped_entity_id(span_panel, circuit_id, suffix)

        if leg2 > 0:
            circuit_id = f"unmapped_tab_{leg2}"
            if circuit_id in span_panel.circuits:
                entities["leg2"] = construct_unmapped_entity_id(span_panel, circuit_id, suffix)

        return entities

    def _build_simplified_solar_sensors(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        leg1: int,
        leg2: int,
        power_entities: dict[str, str | None],
        produced_entities: dict[str, str | None],
        consumed_entities: dict[str, str | None],
    ) -> dict[str, SolarSensorConfig]:
        """Build solar sensor configurations using existing helpers.

        This replaces the complex _build_solar_sensors method with a simplified version.
        """
        solar_sensors: dict[str, SolarSensorConfig] = {}
        device_identifier = span_panel.status.serial_number
        circuit_numbers = [num for num in [leg1, leg2] if num > 0]

        # Solar sensor definitions with consistent naming using helper functions
        sensor_definitions: list[dict[str, Any]] = [
            {
                "key": construct_synthetic_unique_id(span_panel, "solar_inverter_power"),
                "name": "Solar Inverter Power",
                "entities": power_entities,
                "var_prefix": "power",
                "unit": "W",
                "device_class": "power",
                "state_class": "measurement",
                "suffix": "power",
            },
            {
                "key": construct_synthetic_unique_id(span_panel, "solar_inverter_energy_produced"),
                "name": "Solar Inverter Energy Produced",
                "entities": produced_entities,
                "var_prefix": "produced",
                "unit": "Wh",
                "device_class": "energy",
                "state_class": "total_increasing",
                "suffix": "energy_produced",
            },
            {
                "key": construct_synthetic_unique_id(span_panel, "solar_inverter_energy_consumed"),
                "name": "Solar Inverter Energy Consumed",
                "entities": consumed_entities,
                "var_prefix": "consumed",
                "unit": "Wh",
                "device_class": "energy",
                "state_class": "total_increasing",
                "suffix": "energy_consumed",
            },
        ]

        # Create each sensor using existing helpers
        for sensor_def in sensor_definitions:
            # Create formula and variables
            formula, variables = self._create_formula_and_variables(
                sensor_def["entities"], sensor_def["var_prefix"]
            )

            # Skip if no valid entities
            if not variables:
                continue

            # Use existing helper for synthetic entity ID
            entity_id = construct_synthetic_entity_id(
                coordinator=coordinator,
                span_panel=span_panel,
                platform="sensor",
                circuit_numbers=circuit_numbers,
                suffix=sensor_def["suffix"],
                friendly_name="Solar Inverter",
            )

            if entity_id:
                solar_sensors[sensor_def["key"]] = {
                    "name": sensor_def["name"],
                    "entity_id": entity_id,
                    "formula": formula,
                    "variables": variables,
                    "unit_of_measurement": sensor_def["unit"],
                    "device_class": sensor_def["device_class"],
                    "state_class": sensor_def["state_class"],
                    "device_identifier": device_identifier,
                }

        return solar_sensors

    def _create_formula_and_variables(
        self, entities: dict[str, str | None], var_prefix: str
    ) -> tuple[str, dict[str, str]]:
        """Create formula and variables for a sensor type."""
        leg1_entity = entities["leg1"]
        leg2_entity = entities["leg2"]

        variables: dict[str, str] = {}

        if leg1_entity and leg2_entity:
            formula = f"leg1_{var_prefix} + leg2_{var_prefix}"
            variables[f"leg1_{var_prefix}"] = leg1_entity
            variables[f"leg2_{var_prefix}"] = leg2_entity
        elif leg1_entity:
            formula = f"leg1_{var_prefix}"
            variables[f"leg1_{var_prefix}"] = leg1_entity
        elif leg2_entity:
            formula = f"leg2_{var_prefix}"
            variables[f"leg2_{var_prefix}"] = leg2_entity
        else:
            formula = "0"

        return formula, variables

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

    async def remove_config(self) -> None:
        """Remove the solar configuration for this panel."""
        try:
            # Get the coordinator and span panel data
            coordinator_data = self._hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
            coordinator = coordinator_data.get("coordinator")
            if not coordinator:
                return

            span_panel = coordinator.data
            if not span_panel:
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
        """Validate the generated solar configuration."""
        try:
            config_file_path = self.config_file_path
            if not config_file_path.exists():
                return False

            def _read_and_validate_yaml() -> bool:
                try:
                    with open(config_file_path, encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                    return config is not None and "sensors" in config
                except Exception:
                    return False

            return await self._hass.async_add_executor_job(_read_and_validate_yaml)

        except Exception as e:
            _LOGGER.error("Failed to validate solar config: %s", e)
            return False
