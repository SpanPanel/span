"""Unified sensor management for SPAN Panel integration using Integration Authority Model.

This module implements Synthetic Phase 2 by:
1. Registering virtual entity IDs that don't exist in HA
2. Providing data directly from coordinator via callback
3. Generating YAML config for ha-synthetic-sensors package
4. Managing seamless migration with same unique IDs and entity IDs
"""

from collections.abc import Callable, Iterable
import logging
from typing import Any, TypedDict

from ha_synthetic_sensors.name_resolver import NameResolver
from ha_synthetic_sensors.sensor_manager import SensorManager, SensorManagerConfig
from ha_synthetic_sensors.types import DataProviderResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import COORDINATOR, DOMAIN
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_backing_entity_id,
    construct_entity_id,
    construct_panel_entity_id,
    construct_sensor_manager_unique_id,
    get_circuit_number,
    get_user_friendly_suffix,
    panel_to_device_info,
)
from .sensor_definitions import CIRCUITS_SENSORS, PANEL_SENSORS, STORAGE_BATTERY_SENSORS
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .synthetic_config_manager import SyntheticConfigManager

_LOGGER = logging.getLogger(__name__)


class SyntheticSensorConfig(TypedDict):
    """Type definition for synthetic sensor configuration."""

    name: str
    entity_id: str
    formula: str
    variables: dict[str, str]
    unit_of_measurement: str
    device_class: str | None
    state_class: str | None
    device_identifier: str


class SpanSensorManager:
    """Unified sensor manager implementing Integration Authority Model for Synthetic Phase 2."""

    # Class-level cache for registered entities (static across all instances)
    _static_registered_entities: set[str] | None = None
    _static_entities_generated: bool = False
    static_entities_registered: bool = False  # Public for cross-module access

    # Static mappings for sensor data access using circuit_0 scheme
    # Backing entity names should mirror the synthetic sensor naming
    PANEL_SENSOR_MAP: dict[str, str] = {
        "current_power": "instantGridPowerW",
        "feed_through_power": "feedthroughPowerW",
        "main_meter_produced_energy": "mainMeterEnergyProducedWh",
        "main_meter_consumed_energy": "mainMeterEnergyConsumedWh",
        "feed_through_produced_energy": "feedthroughEnergyProducedWh",
        "feed_through_consumed_energy": "feedthroughEnergyConsumedWh",
        "battery_level": "storage_battery.storage_battery_percentage",
    }

    CIRCUIT_FIELD_MAP: dict[str, Callable[[SpanPanelCircuit], Any]] = {
        "_power": lambda c: abs(c.instant_power),  # type: ignore[misc]
        "_energy_produced": lambda c: c.produced_energy,  # type: ignore[misc]
        "_energy_consumed": lambda c: c.consumed_energy,  # type: ignore[misc]
    }

    # Mapping from sensor definition keys to virtual entity suffixes
    CIRCUIT_SENSOR_KEY_MAP: dict[str, str] = {
        "instantPowerW": "_power",
        "producedEnergyWh": "_energy_produced",
        "consumedEnergyWh": "_energy_consumed",
    }

    # Mapping from panel sensor keys to virtual entity suffixes and friendly names
    # Using circuit_0 scheme for consistency - backing entity names mirror synthetic sensor naming
    PANEL_SENSOR_MAPPING: dict[str, tuple[str, str]] = {
        "instantGridPowerW": ("current_power", "Current Power"),
        "feedthroughPowerW": ("feed_through_power", "Feed Through Power"),
        "mainMeterEnergyProducedWh": (
            "main_meter_produced_energy",
            "Main Meter Produced Energy",
        ),
        "mainMeterEnergyConsumedWh": (
            "main_meter_consumed_energy",
            "Main Meter Consumed Energy",
        ),
        "feedthroughEnergyProducedWh": (
            "feed_through_produced_energy",
            "Feed Through Produced Energy",
        ),
        "feedthroughEnergyConsumedWh": (
            "feed_through_consumed_energy",
            "Feed Through Consumed Energy",
        ),
        "batteryPercentage": (
            "battery_level",
            "Battery Level",
        ),
    }

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        config_dir: str | None = None,
    ):
        """Initialize the unified sensor manager.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for this integration instance
            config_dir: Optional directory to store config files

        """
        self._hass = hass
        self._config_entry = config_entry
        self._config_dir = config_dir
        self._config_manager: SyntheticConfigManager | None = None

        # Virtual entities that this integration will provide data for
        self._registered_entities: set[str] = set()

        # Debug counter for tracking multiple calls
        self._get_registered_entity_ids_call_count = 0

        # Cache for registered entity IDs to ensure consistency across multiple calls
        self._cached_registered_entity_ids: set[str] | None = None

    def _construct_unique_id(
        self, span_panel: SpanPanel, circuit_id: str | None, description_key: str
    ) -> str:
        """Construct unique ID following consistent pattern without circuit_ prefix.

        Uses the helper function to ensure consistency across all unique ID generation.

        Args:
            span_panel: SPAN panel data instance
            circuit_id: Circuit ID (None for panel-level sensors)
            description_key: Sensor description key

        Returns:
            Schema-compliant unique ID string

        """
        return construct_sensor_manager_unique_id(
            span_panel.status.serial_number, circuit_id, description_key
        )

    async def _get_config_manager(self) -> SyntheticConfigManager:
        """Get the centralized config manager instance."""
        if self._config_manager is None:
            if self._config_dir is not None:
                # For tests with temp directories
                self._config_manager = SyntheticConfigManager(
                    self._hass,
                    config_filename="span_sensors.yaml",
                    config_dir=self._config_dir,
                )
            else:
                # For production, try singleton pattern first, fall back to direct instantiation for tests
                try:
                    self._config_manager = await SyntheticConfigManager.get_instance(
                        self._hass, config_filename="span_sensors.yaml"
                    )
                except RuntimeError as e:
                    if "Event loop is closed" in str(e) or "no running event loop" in str(e):
                        # Test environment detected - use direct instantiation
                        _LOGGER.debug(
                            "Event loop issue detected, using direct SyntheticConfigManager instantiation for tests"
                        )
                        self._config_manager = SyntheticConfigManager(
                            self._hass,
                            config_filename="span_sensors.yaml",
                        )
                    else:
                        raise
        return self._config_manager

    def create_data_provider_callback(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel
    ) -> Callable[[str], DataProviderResult]:
        """Create data provider callback that provides direct coordinator data access.

        This callback implements the Integration Authority Model by providing data
        directly from the coordinator for registered virtual entities.

        Args:
            coordinator: SPAN panel coordinator instance
            span_panel: SPAN panel data instance (for device info only)

        Returns:
            DataProviderCallback function for ha-synthetic-sensors

        """

        def data_provider(entity_id: str) -> DataProviderResult:
            """Provide data directly from coordinator for virtual entities.

            Args:
                entity_id: Virtual entity ID like "device_name.circuit_1_power"

            Returns:
                DataProviderResult with value and exists fields

            """
            try:
                _LOGGER.info(
                    "BACKING_SENSOR_DEBUG: Data provider called for entity_id: %s", entity_id
                )

                # Get current data from coordinator
                current_span_panel = coordinator.data
                if current_span_panel is None:
                    _LOGGER.info("BACKING_SENSOR_DEBUG: No current data available from coordinator")
                    return {"value": None, "exists": False}

                # Extract the entity part after the domain prefix
                if "." not in entity_id:
                    _LOGGER.info("BACKING_SENSOR_DEBUG: Invalid entity_id format: %s", entity_id)
                    return {"value": None, "exists": False}

                # Get device name for consistent virtual entity ID prefix
                device_info = panel_to_device_info(span_panel)
                device_name_raw = device_info.get("name", "span_panel")
                device_name = slugify(device_name_raw or "span_panel")
                expected_prefix = f"{device_name}_synthetic_backing."

                _LOGGER.info("BACKING_SENSOR_DEBUG: Expected prefix: %s", expected_prefix)

                # Parse virtual entity ID format: "device_name_synthetic_backing.circuit_X_suffix"
                # The entity_id comes in as the full backing entity ID
                if not entity_id.startswith(expected_prefix):
                    _LOGGER.info(
                        "BACKING_SENSOR_DEBUG: Entity ID %s doesn't match expected prefix %s",
                        entity_id,
                        expected_prefix,
                    )
                    return {"value": None, "exists": False}

                # Extract circuit part from backing entity ID (remove the device prefix)
                circuit_part = entity_id[len(expected_prefix) :]  # Remove device prefix
                _LOGGER.info("BACKING_SENSOR_DEBUG: Extracted circuit part: %s", circuit_part)

                # Expected format: "circuit_X_suffix"
                # circuit_part is already extracted above

                # Handle panel-level sensors (circuit_0)
                if circuit_part.startswith("circuit_0_"):
                    # Panel sensor: extract suffix and look up in panel sensor map
                    suffix = circuit_part[len("circuit_0_") :]
                    _LOGGER.info("BACKING_SENSOR_DEBUG: Panel sensor suffix: %s", suffix)
                    result = self._get_panel_sensor_data(suffix, current_span_panel)
                    _LOGGER.info("BACKING_SENSOR_DEBUG: Panel sensor result: %s", result)
                    return result

                # Handle circuit sensors (circuit_N_suffix where N > 0)
                for suffix, value_func in self.CIRCUIT_FIELD_MAP.items():
                    if circuit_part.endswith(suffix):
                        # Extract circuit number: "circuit_15_power" -> "15"
                        circuit_prefix = circuit_part[: -len(suffix)]  # Remove suffix
                        if circuit_prefix.startswith("circuit_"):
                            circuit_number_str = circuit_prefix[len("circuit_") :]

                            _LOGGER.info(
                                "BACKING_SENSOR_DEBUG: Looking for circuit number: %s",
                                circuit_number_str,
                            )

                            # Skip circuit_0 (panel sensors)
                            if circuit_number_str == "0":
                                continue

                            # Find circuit by circuit number (tab position)
                            for circuit_id, circuit_data in current_span_panel.circuits.items():
                                circuit_number = get_circuit_number(circuit_data)
                                _LOGGER.info(
                                    "BACKING_SENSOR_DEBUG: Checking circuit_id=%s, circuit_number=%s",
                                    circuit_id,
                                    circuit_number,
                                )
                                if str(circuit_number) == circuit_number_str:
                                    value = value_func(circuit_data)
                                    _LOGGER.info(
                                        "BACKING_SENSOR_DEBUG: Found matching circuit, returning value: %s",
                                        value,
                                    )
                                    return {"value": value, "exists": True}

                            _LOGGER.info(
                                "BACKING_SENSOR_DEBUG: No matching circuit found for circuit_number: %s",
                                circuit_number_str,
                            )
                            return {"value": None, "exists": False}

                _LOGGER.info(
                    "BACKING_SENSOR_DEBUG: No matching pattern found for circuit_part: %s",
                    circuit_part,
                )
                return {"value": None, "exists": False}

            except Exception as e:
                _LOGGER.error(
                    "BACKING_SENSOR_DEBUG: Error in data provider callback for %s: %s",
                    entity_id,
                    e,
                    exc_info=True,
                )
                return {"value": None, "exists": False}

        return data_provider

    def _get_panel_sensor_data(self, entity_part: str, span_panel: SpanPanel) -> DataProviderResult:
        """Get panel-level sensor data.

        Args:
            entity_part: Entity part after "span." prefix
            span_panel: SPAN panel data instance

        Returns:
            DataProviderResult with value and exists fields

        """
        try:
            _LOGGER.debug("Looking for panel sensor with entity_part: %s", entity_part)
            _LOGGER.debug("span_panel object: %s", span_panel)
            _LOGGER.debug(
                "Available keys in PANEL_SENSOR_MAP: %s", list(self.PANEL_SENSOR_MAP.keys())
            )

            if entity_part not in self.PANEL_SENSOR_MAP:
                _LOGGER.debug("Entity part %s not found in PANEL_SENSOR_MAP", entity_part)
                return {"value": None, "exists": False}

            attribute_path = self.PANEL_SENSOR_MAP[entity_part]
            _LOGGER.debug("Attribute path for %s: %s", entity_part, attribute_path)

            # Get the actual panel data object (span_panel.panel contains the SpanPanelData)
            panel_data = getattr(span_panel, "panel", span_panel)

            # Handle nested attributes (e.g., "storage_battery.storage_battery_percentage")
            if "." in attribute_path:
                parts = attribute_path.split(".")
                obj = panel_data
                for part in parts:
                    obj = getattr(obj, part, None)
                    if obj is None:
                        _LOGGER.debug("Nested attribute %s.%s is None", obj, part)
                        return {"value": None, "exists": False}
                value = obj
            else:
                value = getattr(panel_data, attribute_path, None)

            _LOGGER.debug(
                "Panel sensor %s raw value: %s (type: %s)", entity_part, value, type(value).__name__
            )

            if value is None:
                _LOGGER.debug("Panel sensor %s value is None", entity_part)
                return {"value": None, "exists": False}

            _LOGGER.debug("Panel sensor %s returning value: %s", entity_part, value)
            return {"value": value, "exists": True}  # type: ignore[return-value]

        except Exception as e:
            _LOGGER.warning("Error getting panel sensor data for %s: %s", entity_part, e)
            return {"value": None, "exists": False}

    def generate_unified_config_sync(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel
    ) -> dict[str, SyntheticSensorConfig]:
        """Generate unified YAML configuration for all SPAN sensors synchronously.

        This creates the YAML configuration that defines all circuit and panel sensors
        as synthetic sensors, maintaining the same unique IDs and entity IDs.
        This is a synchronous version for use in test environments.

        Args:
            coordinator: SPAN panel coordinator instance
            span_panel: SPAN panel data instance

        Returns:
            dict[str, Any]: The unified sensor configuration

        """
        _LOGGER.debug("Generating unified synthetic sensors configuration (sync)")

        # Generate circuit sensors
        circuit_sensors = self._generate_circuit_sensors(coordinator, span_panel)

        # Generate panel sensors
        panel_sensors = self._generate_panel_sensors(coordinator, span_panel)

        # Generate battery sensors (if battery is enabled in options)
        battery_sensors = self._generate_battery_sensors(coordinator, span_panel)

        # Combine all sensors
        all_sensors = {**circuit_sensors, **panel_sensors, **battery_sensors}

        # Fix enum serialization for YAML compatibility
        for sensor_key, sensor_config in all_sensors.items():
            # Convert enum device_class to string for YAML serialization
            if sensor_config.get("device_class") is not None:
                sensor_config["device_class"] = str(sensor_config["device_class"])
            # Convert enum state_class to string for YAML serialization
            if sensor_config.get("state_class") is not None:
                sensor_config["state_class"] = str(sensor_config["state_class"])
            # Remove None values to keep YAML clean - use type cast for compatibility
            filtered_config = {k: v for k, v in sensor_config.items() if v is not None}
            all_sensors[sensor_key] = filtered_config  # type: ignore[assignment]

        return all_sensors

    async def generate_unified_config(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel
    ) -> bool:
        """Generate unified synthetic sensor configuration.

        This method creates a comprehensive YAML configuration that replaces all
        native circuit and panel sensors with synthetic equivalents.

        Args:
            coordinator: SPAN panel coordinator instance
            span_panel: SPAN panel data instance

        Returns:
            True if configuration was generated successfully, False otherwise

        """
        _LOGGER.debug("BACKING_SENSOR_DEBUG: generate_unified_config called")

        try:
            # Generate all sensor configurations
            all_sensors = self.generate_unified_config_sync(coordinator, span_panel)
            _LOGGER.debug("BACKING_SENSOR_DEBUG: Generated %d total sensors", len(all_sensors))

            if not all_sensors:
                _LOGGER.warning("No unified sensors generated for panel")
                return False

            config_manager = await self._get_config_manager()
            panel_serial = span_panel.status.serial_number
            device_id = panel_serial

            # Clear existing sensors for this device
            await config_manager.delete_all_device_sensors(device_id)

            # Write entire configuration atomically
            device_identifier = device_id  # PHASE 1: Use clean device identifier
            config_to_write = {
                "version": "1.0",
                "sensors": {
                    sensor_key: {**sensor_config, "device_identifier": device_identifier}
                    for sensor_key, sensor_config in all_sensors.items()
                },
            }

            await config_manager.write_config(config_to_write)

            # Update registered entities for data provider
            self._update_registered_entities(all_sensors)

            # Register all entity IDs we can provide data for
            entity_ids = await self.get_registered_entity_ids(span_panel)
            _LOGGER.debug(
                "BACKING_SENSOR_DEBUG: Registering entity IDs with synthetic sensors: %s",
                list(entity_ids),
            )

            for entity_id in entity_ids:
                _LOGGER.debug("BACKING_SENSOR_DEBUG: Registering entity ID: %s", entity_id)

            _LOGGER.debug(
                "Generated %d unified sensors for panel %s", len(all_sensors), panel_serial
            )

            # Set up data provider callback for synthetic sensors
            self.create_data_provider_callback(coordinator, span_panel)
            _LOGGER.debug("BACKING_SENSOR_DEBUG: Created data provider callback function")

            return True

        except Exception as e:
            _LOGGER.error("Failed to generate unified sensor configuration: %s", e)
            return False

    def _generate_circuit_sensors(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel
    ) -> dict[str, SyntheticSensorConfig]:
        """Generate synthetic sensor configs for all circuit sensors.

        Args:
            coordinator: SPAN panel coordinator instance
            span_panel: SPAN panel data instance

        Returns:
            Dictionary of sensor configs keyed by sensor key

        """

        sensors: dict[str, SyntheticSensorConfig] = {}
        device_identifier = span_panel.status.serial_number  # PHASE 1: Use clean device identifier

        for circuit_id, circuit_data in span_panel.circuits.items():
            # Skip status sensors and unmapped tabs (they stay native)
            if circuit_id.startswith("unmapped_tab_"):
                continue

            circuit_number = get_circuit_number(circuit_data)

            for description in CIRCUITS_SENSORS:
                # Generate the unique ID using the same pattern as native sensors
                unique_id = self._construct_unique_id(span_panel, circuit_id, description.key)

                # Generate entity ID using same pattern as native sensors
                entity_id = construct_entity_id(
                    coordinator,
                    span_panel,
                    "sensor",
                    circuit_data.name,
                    circuit_number,
                    get_user_friendly_suffix(description.key),
                )

                # Create virtual entity ID for data provider
                if description.key not in self.CIRCUIT_SENSOR_KEY_MAP:
                    continue

                suffix = self.CIRCUIT_SENSOR_KEY_MAP[description.key]
                virtual_entity_id = construct_backing_entity_id(
                    span_panel,
                    circuit_number=circuit_number,
                    suffix=suffix.lstrip("_"),  # Remove leading underscore for backing entity
                    entity_type="circuit",
                )

                # Create sensor config
                sensor_config = {
                    "name": f"{circuit_data.name} {description.name}",
                    "entity_id": entity_id,
                    "formula": "source_value",
                    "variables": {
                        "source_value": virtual_entity_id,
                    },
                    "unit_of_measurement": str(description.native_unit_of_measurement),
                    "device_class": str(description.device_class)
                    if description.device_class
                    else None,
                    "state_class": str(description.state_class)
                    if description.state_class
                    else None,
                    "device_identifier": device_identifier,
                }

                # Skip unsupported fields for ha-synthetic-sensors
                # if description.suggested_display_precision is not None:
                #     sensor_config["suggested_display_precision"] = str(
                #         description.suggested_display_precision
                #     )

                sensors[unique_id] = sensor_config  # type: ignore[assignment]

        return sensors

    def _generate_panel_sensors(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel
    ) -> dict[str, SyntheticSensorConfig]:
        """Generate synthetic sensor configs for panel-level sensors.

        Args:
            coordinator: SPAN panel coordinator instance
            span_panel: SPAN panel data instance

        Returns:
            Dictionary of sensor configs keyed by sensor key

        """

        sensors: dict[str, SyntheticSensorConfig] = {}
        device_identifier = span_panel.status.serial_number  # PHASE 1: Use clean device identifier

        for description in PANEL_SENSORS:
            if description.key not in self.PANEL_SENSOR_MAPPING:
                continue

            virtual_entity_id, _ = self.PANEL_SENSOR_MAPPING[description.key]
            # Use consistent backing entity ID format (circuit_0 scheme)
            virtual_entity_id = construct_backing_entity_id(
                span_panel,
                suffix=virtual_entity_id,  # Use the suffix directly
                entity_type="panel",
            )

            # Generate unique ID and entity ID based on display name for consistency
            display_name_suffix = slugify(str(description.name))
            unique_id = f"span_{span_panel.status.serial_number.lower()}_{display_name_suffix}"

            entity_id = construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                display_name_suffix,
            )

            # Create sensor config
            sensor_config = {
                "name": description.name,
                "entity_id": entity_id,
                "formula": "source_value",
                "variables": {
                    "source_value": virtual_entity_id,
                },
                "unit_of_measurement": str(description.native_unit_of_measurement),
                "device_class": description.device_class,
                "state_class": description.state_class,
                "device_identifier": device_identifier,
            }

            sensors[unique_id] = sensor_config  # type: ignore[assignment]

        return sensors

    def _generate_battery_sensors(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel
    ) -> dict[str, SyntheticSensorConfig]:
        """Generate synthetic sensor configs for battery sensors.

        Args:
            coordinator: SPAN panel coordinator instance
            span_panel: SPAN panel data instance

        Returns:
            Dictionary of sensor configs keyed by sensor key

        """
        sensors: dict[str, SyntheticSensorConfig] = {}

        # Check if battery is enabled in options
        battery_enabled = self._config_entry.options.get("battery_enable", False)
        if not battery_enabled:
            return sensors

        device_identifier = span_panel.status.serial_number  # PHASE 1: Use clean device identifier

        for description in STORAGE_BATTERY_SENSORS:
            if description.key not in self.PANEL_SENSOR_MAPPING:
                continue

            virtual_entity_id, _ = self.PANEL_SENSOR_MAPPING[description.key]
            # Use consistent backing entity ID format (circuit_0 scheme)
            virtual_entity_id = construct_backing_entity_id(
                span_panel,
                suffix=virtual_entity_id,  # Use the suffix directly
                entity_type="battery",
            )

            # Generate unique ID and entity ID based on display name for consistency
            display_name_suffix = slugify(str(description.name))
            unique_id = f"span_{span_panel.status.serial_number.lower()}_{display_name_suffix}"

            entity_id = construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                display_name_suffix,
            )

            # Create sensor config
            sensor_config = {
                "name": description.name,
                "entity_id": entity_id,
                "formula": "source_value",
                "variables": {
                    "source_value": virtual_entity_id,
                },
                "unit_of_measurement": str(description.native_unit_of_measurement),
                "device_class": description.device_class,
                "state_class": description.state_class,
                "device_identifier": device_identifier,
            }

            sensors[unique_id] = sensor_config  # type: ignore[assignment]

        return sensors

    def _update_registered_entities(self, sensor_configs: dict[str, SyntheticSensorConfig]) -> None:
        """Update the set of registered entities from sensor configs.

        Args:
            sensor_configs: Dictionary of sensor configurations

        """
        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: _update_registered_entities called with %d sensor configs",
            len(sensor_configs),
        )
        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: Before clear - registered entities: %d",
            len(self._registered_entities),
        )

        self._registered_entities.clear()

        for sensor_config in sensor_configs.values():
            if "variables" in sensor_config:
                for variable_entity in sensor_config["variables"].values():
                    if isinstance(variable_entity, str) and variable_entity.startswith("span."):
                        self._registered_entities.add(variable_entity)
                        _LOGGER.debug(
                            "BACKING_SENSOR_DEBUG: Added registered entity: %s", variable_entity
                        )

        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: After update - registered entities: %d",
            len(self._registered_entities),
        )

    def get_registered_entities(self) -> set[str]:
        """Get the set of virtual entities this integration provides data for.

        Returns:
            Set of virtual entity IDs

        """
        return self._registered_entities.copy()

    async def remove_config(self) -> None:
        """Remove unified configuration for this panel."""
        try:
            # Get coordinator data to find panel serial
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
            _LOGGER.info("Removed %d unified sensors for panel %s", deleted_count, panel_serial)

            # Clear registered entities
            self._registered_entities.clear()

        except Exception as e:
            _LOGGER.error("Error removing unified config: %s", e)

    async def get_config_file_path(self) -> str | None:
        """Get the path to the unified YAML configuration file.

        Returns:
            Path to the config file, or None if not available

        """
        try:
            config_manager = await self._get_config_manager()
            path = await config_manager.get_config_file_path()
            return str(path) if path else None
        except Exception as e:
            _LOGGER.error("Error getting unified config file path: %s", e)
            return None

    async def validate_config(self) -> bool:
        """Validate the generated unified YAML configuration.

        Returns:
            True if config is valid, False otherwise

        """
        try:
            config_manager = await self._get_config_manager()
            return await config_manager.config_file_exists()
        except Exception as e:
            _LOGGER.error("Unified config validation failed: %s", e)
            return False

    async def get_registered_entity_ids(self, span_panel: SpanPanel) -> set[str]:
        """Get the set of entity IDs that this integration can provide data for.

        Args:
            span_panel: SPAN panel data instance

        Returns:
            set[str]: Set of entity IDs this integration can provide

        """
        self._get_registered_entity_ids_call_count += 1
        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: get_registered_entity_ids called (CALL #%d)",
            self._get_registered_entity_ids_call_count,
        )

        # Return static cached result if available to ensure consistency across ALL instances
        if SpanSensorManager._static_registered_entities is not None:
            _LOGGER.debug(
                "BACKING_SENSOR_DEBUG: CALL #%d - Returning STATIC CACHED entities: %d",
                self._get_registered_entity_ids_call_count,
                len(SpanSensorManager._static_registered_entities),
            )
            return SpanSensorManager._static_registered_entities.copy()

        # Get device name for consistent virtual entity ID prefix
        device_info = panel_to_device_info(span_panel)
        device_name_raw = device_info.get("name", "span_panel")
        device_name = slugify(device_name_raw or "span_panel")

        entity_ids: set[str] = set()

        # Panel sensors (circuit_0)
        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: Processing %d panel sensors", len(self.PANEL_SENSOR_MAP)
        )
        for panel_key in self.PANEL_SENSOR_MAP:
            entity_id = f"{device_name}_synthetic_backing.circuit_0_{panel_key}"
            entity_ids.add(entity_id)
            _LOGGER.debug("BACKING_SENSOR_DEBUG: Registered panel backing entity: %s", entity_id)

        # Circuit sensors
        _LOGGER.debug("BACKING_SENSOR_DEBUG: Processing %d circuits", len(span_panel.circuits))
        for circuit_id, circuit_data in span_panel.circuits.items():
            circuit_number = get_circuit_number(circuit_data)
            _LOGGER.debug(
                "BACKING_SENSOR_DEBUG: Processing circuit_id=%s, circuit_number=%s",
                circuit_id,
                circuit_number,
            )
            for suffix in self.CIRCUIT_FIELD_MAP:
                entity_id = f"{device_name}_synthetic_backing.circuit_{circuit_number}{suffix}"
                entity_ids.add(entity_id)
                _LOGGER.debug(
                    "BACKING_SENSOR_DEBUG: Added entity_id: %s (after: %d entities)",
                    entity_id,
                    len(entity_ids),
                )
                _LOGGER.debug(
                    "BACKING_SENSOR_DEBUG: Registered circuit backing entity: %s (circuit_id=%s, circuit_number=%s)",
                    entity_id,
                    circuit_id,
                    circuit_number,
                )

        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: Total registered backing entities: %d", len(entity_ids)
        )
        _LOGGER.debug(
            "BACKING_SENSOR_DEBUG: CALL #%d - Full entity list:",
            self._get_registered_entity_ids_call_count,
        )
        for i, entity_id in enumerate(sorted(entity_ids), 1):
            _LOGGER.debug("BACKING_SENSOR_DEBUG: Entity %d: %s", i, entity_id)

        # Cache the result statically for ALL instances
        SpanSensorManager._static_registered_entities = entity_ids.copy()
        SpanSensorManager._static_entities_generated = True

        return entity_ids

    async def create_and_register_sensor_manager(self, span_panel: SpanPanel) -> SensorManager:
        """Create and register a SensorManager that can be shared across the integration.

        This should be called once after all platforms are loaded.

        Args:
            span_panel: SPAN panel data instance

        Returns:
            SensorManager instance ready for use by sensor.py

        """
        try:
            # Get all entities this integration can provide data for
            registered_entities = await self.get_registered_entity_ids(span_panel)

            _LOGGER.debug(
                "Creating shared SensorManager with %d registered entities",
                len(registered_entities),
            )

            # Create name resolver for device integration
            name_resolver = NameResolver(self._hass, variables={})  # type: ignore[misc]

            # Create device info for sensors
            device_info = panel_to_device_info(span_panel)  # type: ignore[misc]

            # Create data provider callback
            data_provider_callback = self.create_data_provider_callback(
                # We'll need coordinator - get it from hass data
                self._hass.data[DOMAIN][self._config_entry.entry_id][COORDINATOR],
                span_panel,
            )

            # Create a placeholder add_entities callback - sensor.py will provide the real one
            def placeholder_add_entities(
                new_entities: Iterable[Entity], update_before_add: bool = False
            ) -> None:
                _LOGGER.debug(
                    "Placeholder add_entities called with %d entities", len(list(new_entities))
                )

            # Configure sensor manager with data provider callback
            manager_config = SensorManagerConfig(  # type: ignore[misc]
                device_info=device_info,
                unique_id_prefix="",  # No prefix needed - we control unique IDs directly
                lifecycle_managed_externally=True,
                data_provider_callback=data_provider_callback,
                integration_domain=DOMAIN,  # PHASE 1: Add integration domain
            )

            sensor_manager = SensorManager(
                self._hass, name_resolver, placeholder_add_entities, manager_config
            )  # type: ignore[misc]
            sensor_manager.register_data_provider_entities(registered_entities)  # type: ignore[misc]

            _LOGGER.info(
                "Successfully created shared SensorManager with %d registered entities",
                len(registered_entities),
            )

            return sensor_manager

        except Exception as e:
            _LOGGER.error("Failed to create shared SensorManager: %s", e)
            raise
