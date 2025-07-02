"""Support for Span Panel monitor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
import logging
from typing import Any, Generic, TypeVar

from ha_synthetic_sensors.config_manager import ConfigManager
from ha_synthetic_sensors.name_resolver import NameResolver
from ha_synthetic_sensors.sensor_manager import SensorManager, SensorManagerConfig
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    COORDINATOR,
    DOMAIN,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_circuit_unique_id,
    construct_panel_entity_id,
    construct_panel_friendly_name,
    construct_panel_unique_id,
    construct_status_friendly_name,
    construct_unmapped_entity_id,
    construct_unmapped_friendly_name,
    get_user_friendly_suffix,
    panel_to_device_info,
)
from .options import INVERTER_ENABLE, INVERTER_LEG1, INVERTER_LEG2
from .sensor_definitions import (
    PANEL_DATA_STATUS_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelStatusSensorEntityDescription,
)
from .solar_synthetic_sensors import SolarSyntheticSensors
from .solar_tab_manager import SolarTabManager
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus

ICON = "mdi:flash"
_LOGGER: logging.Logger = logging.getLogger(__name__)

T = TypeVar("T", bound=SensorEntityDescription)
D = TypeVar("D")  # For the type returned by get_data_source


class SpanSensorBase(CoordinatorEntity[SpanPanelCoordinator], SensorEntity, Generic[T, D], ABC):
    """Abstract base class for Span Panel Sensors with overrideable methods."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Sensor base entity."""
        super().__init__(data_coordinator, context=description)
        # See developer_attrtribute_readme.md for why we use
        # entity_description instead of _attr_entity_descriptio
        self.entity_description = description

        if hasattr(description, "device_class"):
            self._attr_device_class = description.device_class

        device_info: DeviceInfo = panel_to_device_info(span_panel)
        self._attr_device_info = device_info  # Re-enable device info

        # Use abstract methods to generate entity properties
        self._attr_name = self._generate_friendly_name(span_panel, description)

        if span_panel.status.serial_number and description.key:
            self._attr_unique_id = self._generate_unique_id(span_panel, description)

        # Generate entity_id using abstract method
        entity_id = self._generate_entity_id(data_coordinator, span_panel, description)
        if entity_id:
            self.entity_id = entity_id

        self._attr_icon = "mdi:flash"

        # Set entity registry defaults if they exist in the description
        if hasattr(description, "entity_registry_enabled_default"):
            self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        if hasattr(description, "entity_registry_visible_default"):
            self._attr_entity_registry_visible_default = description.entity_registry_visible_default

        _LOGGER.debug("CREATE SENSOR SPAN [%s]", self._attr_name)

    @abstractmethod
    def _generate_unique_id(self, span_panel: SpanPanel, description: T) -> str:
        """Generate unique ID for the sensor.

        Subclasses must implement this to define their unique ID strategy.

        Args:
            span_panel: The span panel data
            description: The sensor description

        Returns:
            Unique ID string

        """

    @abstractmethod
    def _generate_friendly_name(self, span_panel: SpanPanel, description: T) -> str:
        """Generate friendly name for the sensor.

        Subclasses must implement this to define their naming strategy.

        Args:
            span_panel: The span panel data
            description: The sensor description

        Returns:
            Friendly name string

        """

    @abstractmethod
    def _generate_entity_id(
        self, coordinator: SpanPanelCoordinator, span_panel: SpanPanel, description: T
    ) -> str | None:
        """Generate entity ID for the sensor.

        Subclasses must implement this to define their entity ID strategy.

        Args:
            coordinator: The coordinator instance
            span_panel: The span panel data
            description: The sensor description

        Returns:
            Entity ID string or None

        """

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "STATUS_SENSOR_DEBUG: _handle_coordinator_update called for %s", self._attr_name
        )
        self._update_native_value()
        super()._handle_coordinator_update()

    def _update_native_value(self) -> None:
        """Update the native value of the sensor."""
        _LOGGER.debug("STATUS_SENSOR_DEBUG: _update_native_value called for %s", self._attr_name)

        if not self.coordinator.last_update_success:
            _LOGGER.debug(
                "STATUS_SENSOR_DEBUG: Coordinator update not successful for %s", self._attr_name
            )
            self._attr_native_value = None
            return

        value_function: Callable[[D], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            _LOGGER.debug("STATUS_SENSOR_DEBUG: No value_function for %s", self._attr_name)
            self._attr_native_value = None
            return

        try:
            data_source: D = self.get_data_source(self.coordinator.data)

            # Debug logging specifically for circuit power sensors
            if hasattr(self, "id") and hasattr(data_source, "instant_power"):
                circuit_id = getattr(self, "id", "unknown")
                instant_power = getattr(data_source, "instant_power", None)
                description_key = getattr(self.entity_description, "key", "unknown")
                _LOGGER.debug(
                    "CIRCUIT_POWER_DEBUG: Circuit %s, sensor %s, instant_power=%s, data_source type=%s",
                    circuit_id,
                    description_key,
                    instant_power,
                    type(data_source).__name__,
                )

            raw_value: float | int | str | None = value_function(data_source)

            _LOGGER.debug("native_value:[%s] [%s]", self._attr_name, raw_value)

            if raw_value is None:
                self._attr_native_value = None
            elif isinstance(raw_value, float | int):
                self._attr_native_value = float(raw_value)
            else:
                # For string values, keep as string - this is valid for Home Assistant sensors
                self._attr_native_value = str(raw_value)  # type: ignore[assignment]
        except (AttributeError, KeyError, IndexError) as e:
            _LOGGER.debug(
                "CIRCUIT_POWER_ERROR: Error in _update_native_value for %s: %s",
                self._attr_name,
                e,
            )
            self._attr_native_value = None

    def get_data_source(self, span_panel: SpanPanel) -> D:
        """Get the data source for the sensor."""
        raise NotImplementedError("Subclasses must implement this method")


class SpanPanelPanelStatus(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]):
    """Span Panel data status sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the Span Panel data status sensor."""
        super().__init__(data_coordinator, description, span_panel)
        _LOGGER.debug(
            "STATUS_SENSOR_DEBUG: SpanPanelPanelStatus initialized for %s", description.name
        )

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel data sensors."""
        return construct_panel_unique_id(span_panel, description.key)

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel data sensors."""
        return construct_panel_friendly_name(description.name)

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for panel data sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            return construct_panel_entity_id(coordinator, span_panel, "sensor", entity_suffix)
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel data status sensor."""
        return span_panel.panel


class SpanPanelStatus(
    SpanSensorBase[SpanPanelStatusSensorEntityDescription, SpanPanelHardwareStatus]
):
    """Span Panel hardware status sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelStatusSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the Span Panel hardware status sensor."""
        _LOGGER.debug("STATUS_SENSOR_DEBUG: Initializing SpanPanelStatus for %s", description.name)
        super().__init__(data_coordinator, description, span_panel)
        _LOGGER.debug("STATUS_SENSOR_DEBUG: SpanPanelStatus initialized for %s", description.name)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelStatusSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel status sensors."""
        return construct_panel_unique_id(span_panel, description.key)

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelStatusSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel status sensors."""
        return construct_status_friendly_name(description.name)

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelStatusSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for panel status sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            return construct_panel_entity_id(coordinator, span_panel, "sensor", entity_suffix)
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        """Get the data source for the panel status sensor."""
        return span_panel.status


class SpanUnmappedCircuitSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Span Panel unmapped circuit sensor entity - native sensors for synthetic calculations."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        span_panel: SpanPanel,
        circuit_id: str,
    ) -> None:
        """Initialize the Span Panel unmapped circuit sensor."""
        self.circuit_id = circuit_id
        # Store the original description key for unique ID and entity ID generation
        self.original_key = description.key

        # Override the description key to use the circuit_id for data lookup
        description_with_circuit = SpanPanelCircuitsSensorEntityDescription(
            key=circuit_id,  # Use circuit_id for data source lookup
            name=description.name,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            device_class=description.device_class,
            value_fn=description.value_fn,
            entity_registry_enabled_default=True,  # Enabled but invisible
            entity_registry_visible_default=False,  # Hidden from UI
        )

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for unmapped circuit sensors."""
        # Unmapped tab sensors are regular circuit sensors, use standard circuit unique ID pattern
        # circuit_id is already "unmapped_tab_32", so this creates "span_{serial}_unmapped_tab_32_{suffix}"
        # Use the original key (e.g., "instantPowerW") instead of the modified description.key
        return construct_circuit_unique_id(span_panel, self.circuit_id, self.original_key)

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for unmapped circuit sensors."""
        tab_number = self.circuit_id.replace("unmapped_tab_", "")
        description_name = str(description.name) if description.name else "Sensor"
        return construct_unmapped_friendly_name(tab_number, description_name)

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for unmapped circuit sensors."""
        # Pass the full circuit_id to the helper (e.g., "unmapped_tab_32")
        # Use the original key instead of the modified description.key
        sensor_suffix = get_user_friendly_suffix(self.original_key)
        return construct_unmapped_entity_id(span_panel, self.circuit_id, sensor_suffix)

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the unmapped circuit sensor."""
        return span_panel.circuits[self.circuit_id]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    _LOGGER.debug("SENSOR_SETUP_DEBUG: Starting sensor platform setup")
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data
    _LOGGER.debug("SENSOR_SETUP_DEBUG: Got coordinator and span_panel data")

    # First, create all the native sensors that synthetic sensors will depend on
    entities: list[SpanSensorBase[Any, Any]] = []

    # Add panel data status sensors (DSM State, DSM Grid State, etc.)
    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Add unmapped circuit sensors (native sensors for synthetic calculations)
    # These are invisible sensors that provide stable entity IDs for solar synthetics
    for circuit_id in span_panel.circuits:
        if circuit_id.startswith("unmapped_tab_"):
            _LOGGER.debug("Creating unmapped circuit sensors for circuit: %s", circuit_id)
            for unmapped_description in UNMAPPED_SENSORS:
                # UNMAPPED_SENSORS contains SpanPanelCircuitsSensorEntityDescription
                entities.append(
                    SpanUnmappedCircuitSensor(
                        coordinator, unmapped_description, span_panel, circuit_id
                    )
                )

    # Add hardware status sensors (Door State, WiFi, Cellular, etc.)
    for description_ss in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description_ss, span_panel))

    # Add the status sensor entities FIRST
    async_add_entities(entities)

    # Ensure unmapped tab entities are enabled in the entity registry
    # This is necessary because existing disabled entities in the registry
    # override the entity_registry_enabled_default setting
    entity_registry = er.async_get(hass)
    for entity in entities:
        # Check if this is an unmapped tab circuit sensor
        if (
            hasattr(entity, "unique_id")
            and entity.unique_id
            and "unmapped_tab_" in entity.unique_id
        ):
            entity_id = entity.entity_id
            registry_entry = entity_registry.async_get(entity_id)
            if registry_entry and registry_entry.disabled:
                _LOGGER.info("Enabling previously disabled unmapped tab entity: %s", entity_id)
                entity_registry.async_update_entity(entity_id, disabled_by=None)

    # Handle solar configuration - generate YAML for solar synthetic sensors if enabled
    # This happens AFTER native sensors are available but BEFORE synthetic sensors are created
    solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
    inverter_leg1 = config_entry.options.get(INVERTER_LEG1, 0)
    inverter_leg2 = config_entry.options.get(INVERTER_LEG2, 0)

    _LOGGER.info(
        "Solar sensor setup - enabled: %s, options: %s",
        solar_enabled,
        config_entry.options,
    )

    if solar_enabled:
        # Enable the required tab circuits (but keep them hidden)
        tab_manager = SolarTabManager(hass, config_entry)
        await tab_manager.enable_solar_tabs(inverter_leg1, inverter_leg2)

        # Generate synthetic sensors YAML configuration
        solar_sensors = SolarSyntheticSensors(hass, config_entry)
        await solar_sensors.generate_config(inverter_leg1, inverter_leg2)

        # Validate the generated configuration
        if await solar_sensors.validate_config():
            _LOGGER.debug("Solar synthetic sensors configuration generated successfully")
        else:
            _LOGGER.error("Failed to validate solar synthetic sensors configuration")
    else:
        # Clean up solar configuration when disabled
        tab_manager = SolarTabManager(hass, config_entry)
        solar_sensors = SolarSyntheticSensors(hass, config_entry)

        await tab_manager.disable_solar_tabs()
        await solar_sensors.remove_config()

    # NOW set up synthetic sensors after native entities are added and available in HA
    # Set up synthetic sensors if they were prepared in __init__.py
    try:
        synthetic_manager = data.get("synthetic_manager")
        backing_entities = data.get("backing_entities")
        yaml_path = data.get("yaml_path")

        _LOGGER.debug("SENSOR_SETUP_DEBUG: - synthetic_manager: %s", synthetic_manager is not None)
        _LOGGER.debug(
            "SENSOR_SETUP_DEBUG: - backing_entities: %s (count: %d)",
            backing_entities is not None,
            len(backing_entities) if backing_entities else 0,
        )
        _LOGGER.debug("SENSOR_SETUP_DEBUG: - yaml_path: %s", yaml_path)

        if synthetic_manager and backing_entities:
            _LOGGER.debug(
                "SENSOR_SETUP_DEBUG: Setting up synthetic sensors with %d backing entities",
                len(backing_entities),
            )

            # Create data provider callback
            data_provider_callback = synthetic_manager.create_data_provider_callback(
                coordinator, coordinator.data
            )

            # Create name resolver and device info
            name_resolver = NameResolver(hass, variables={})  # type: ignore[misc]
            device_info = panel_to_device_info(coordinator.data)

            # Create SensorManager with data provider configuration
            manager_config = SensorManagerConfig(  # type: ignore[misc]
                device_info=device_info,
                unique_id_prefix="",
                lifecycle_managed_externally=True,
                data_provider_callback=data_provider_callback,
                integration_domain=DOMAIN,  # PHASE 1: Add integration domain
            )

            sensor_manager = SensorManager(hass, name_resolver, async_add_entities, manager_config)  # type: ignore[misc]
            _LOGGER.debug("SENSOR_SETUP_DEBUG: Created SensorManager with data provider callback")

            # Register backing entities with the sensor manager
            sensor_manager.register_data_provider_entities(backing_entities)  # type: ignore[misc]
            _LOGGER.debug(
                "SENSOR_SETUP_DEBUG: Registered %d backing entities with sensor manager",
                len(backing_entities),
            )

            # Load YAML configuration into the sensor manager
            config_manager = ConfigManager(hass)
            config = await config_manager.async_load_from_file(yaml_path)  # type: ignore[misc]
            _LOGGER.debug("SENSOR_SETUP_DEBUG: Loaded YAML configuration from %s", yaml_path)

            # Load configuration to create synthetic sensors
            await sensor_manager.load_configuration(config)  # type: ignore[misc]
            _LOGGER.info("SENSOR_SETUP_DEBUG: Synthetic sensors created successfully")

        # Also set up solar synthetic sensors if they were generated
        if solar_enabled:
            try:
                # Create a separate sensor manager for solar sensors
                solar_device_info = panel_to_device_info(coordinator.data)

                # Configure sensor manager for solar sensors
                circuit_spec = "_".join(
                    str(num) for num in [inverter_leg1, inverter_leg2] if num > 0
                )
                unique_id_prefix = (
                    f"span_{span_panel.status.serial_number}_synthetic_{circuit_spec}"
                )

                solar_manager_config = SensorManagerConfig(  # type: ignore[misc]
                    device_info=solar_device_info,
                    unique_id_prefix=unique_id_prefix,
                    lifecycle_managed_externally=True,
                    integration_domain=DOMAIN,
                )

                solar_sensor_manager = SensorManager(
                    hass, name_resolver, async_add_entities, solar_manager_config
                )  # type: ignore[misc]

                # Load the solar YAML configuration
                solar_yaml_path = solar_sensors.config_file_path
                if solar_yaml_path and solar_yaml_path.exists():
                    solar_config_manager = ConfigManager(hass)
                    solar_config = await solar_config_manager.async_load_from_file(
                        str(solar_yaml_path)
                    )  # type: ignore[misc]
                    await solar_sensor_manager.load_configuration(solar_config)  # type: ignore[misc]
                    _LOGGER.debug(
                        "Solar synthetic sensors loaded successfully from %s", solar_yaml_path
                    )
                else:
                    _LOGGER.error(
                        "Solar synthetic sensors YAML file not found at %s", solar_yaml_path
                    )

            except Exception as e:
                _LOGGER.error("Failed to set up solar synthetic sensors: %s", e)

    except Exception as e:
        _LOGGER.error(
            "SENSOR_SETUP_DEBUG: Failed to set up synthetic sensors: %s", e, exc_info=True
        )
