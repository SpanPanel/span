"""Support for Span Panel monitor."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
import logging
from typing import Any, Generic, TypeVar

from ha_synthetic_sensors import StorageManager
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from custom_components.span_panel.options import INVERTER_ENABLE, INVERTER_LEG1, INVERTER_LEG2
from custom_components.span_panel.synthetic_sensors import (
    async_setup_synthetic_sensors,
    setup_synthetic_configuration,
)
from custom_components.span_panel.synthetic_solar import (
    handle_solar_options_change,
)

from .const import (
    COORDINATOR,
    DOMAIN,
    SENSOR_SET,
    SIGNAL_STAGE_NATIVE_SENSORS,
    STORAGE_MANAGER,
    USE_DEVICE_PREFIX,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_circuit_unique_id_for_entry,
    construct_panel_entity_id,
    construct_panel_friendly_name,
    construct_panel_unique_id_for_entry,
    construct_sensor_set_id,
    construct_status_friendly_name,
    construct_unmapped_friendly_name,
    get_device_identifier_for_entry,
    get_user_friendly_suffix,
)
from .options import BATTERY_ENABLE
from .sensor_definitions import (
    BATTERY_SENSOR,
    PANEL_DATA_STATUS_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
    SpanPanelBatterySensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelStatusSensorEntityDescription,
)
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery
from .synthetic_sensors import (
    SyntheticSensorCoordinator,
    _synthetic_coordinators,
    find_synthetic_coordinator_for,
)
from .util import panel_to_device_info

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

        # Get device name from config entry data
        self._device_name = data_coordinator.config_entry.data.get(
            "device_name", data_coordinator.config_entry.title
        )

        device_info: DeviceInfo = panel_to_device_info(span_panel, self._device_name)
        self._attr_device_info = device_info  # Re-enable device info

        self._attr_name = self._generate_friendly_name(span_panel, description)

        if span_panel.status.serial_number and description.key:
            self._attr_unique_id = self._generate_unique_id(span_panel, description)

        entity_id = self._generate_entity_id(data_coordinator, span_panel, description)
        if entity_id:
            self.entity_id = entity_id

        self._attr_icon = "mdi:flash"

        # Set entity registry defaults if they exist in the description
        if hasattr(description, "entity_registry_enabled_default"):
            self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        if hasattr(description, "entity_registry_visible_default"):
            self._attr_entity_registry_visible_default = description.entity_registry_visible_default

        # Subscribe native sensors to the third stage. Schedule on the event
        # loop to keep async_write_ha_state on the loop thread. Synthetic
        # sensors subscribe in synthetic_sensors.py for the fourth stage.
        def _on_stage() -> None:
            if self.hass is None:
                return

            def _run_on_loop() -> None:
                self._update_native_value()
                self.async_write_ha_state()

            self.hass.loop.call_soon_threadsafe(_run_on_loop)

        self._unsub_stage = async_dispatcher_connect(
            data_coordinator.hass, SIGNAL_STAGE_NATIVE_SENSORS, _on_stage
        )

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

    def _construct_sensor_unmapped_entity_id(self, circuit_id: str, suffix: str) -> str:
        """Construct entity ID for unmapped tab sensors in sensor platform."""
        # Always use device prefix for unmapped entities
        if self._device_name:
            device_name_slug = slugify(self._device_name)
            return f"sensor.{device_name_slug}_{circuit_id}_{suffix}"
        else:
            return f"sensor.{circuit_id}_{suffix}"

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_native_value()
        super()._handle_coordinator_update()

    def _update_native_value(self) -> None:
        """Update the native value of the sensor."""

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

            if raw_value is None:
                self._attr_native_value = None
            elif isinstance(raw_value, float | int):
                self._attr_native_value = float(raw_value)
            else:
                # For string values, keep as string - this is valid for Home Assistant sensors
                self._attr_native_value = str(raw_value)
        except (AttributeError, KeyError, IndexError):
            self._attr_native_value = None

    def get_data_source(self, span_panel: SpanPanel) -> D:
        """Get the data source for the sensor."""
        raise NotImplementedError("Subclasses must implement this method")

    def __del__(self) -> None:
        """Clean up dispatcher subscription on object destruction."""
        # Best-effort disconnect of dispatcher subscription
        try:
            if hasattr(self, "_unsub_stage") and self._unsub_stage is not None:
                self._unsub_stage()
        except Exception as e:  # pragma: no cover – defensive
            _LOGGER.debug("Failed to cleanup dispatcher subscription: %s", e)


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

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel data sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

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
            # Get the device prefix setting from config entry options
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                self._attr_unique_id,
                use_device_prefix,
            )
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
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelStatusSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel status sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

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
            # Get the device prefix setting from config entry options
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                self._attr_unique_id,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        """Get the data source for the panel status sensor."""
        try:
            result = span_panel.status
            return result
        except Exception as e:
            _LOGGER.error("HARDWARE_STATUS_DEBUG: Error getting status data: %s", e)
            raise


class SpanPanelBattery(
    SpanSensorBase[SpanPanelBatterySensorEntityDescription, SpanPanelStorageBattery]
):
    """Span Panel battery sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelBatterySensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the Span Panel battery sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelBatterySensorEntityDescription
    ) -> str:
        """Generate unique ID for battery sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelBatterySensorEntityDescription
    ) -> str:
        """Generate friendly name for battery sensors."""
        return construct_panel_friendly_name(description.name)

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelBatterySensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for battery sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            # Get the device prefix setting from config entry options
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                self._attr_unique_id,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelStorageBattery:
        """Get the data source for the battery sensor."""
        _LOGGER.debug("BATTERY_DEBUG: get_data_source called for battery sensor")
        try:
            result = span_panel.storage_battery
            _LOGGER.debug("BATTERY_DEBUG: Successfully got battery data: %s", type(result).__name__)
            return result
        except Exception as e:
            _LOGGER.error("BATTERY_DEBUG: Error getting battery data: %s", e)
            raise


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
            key=circuit_id,
            name=description.name,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            device_class=description.device_class,
            value_fn=description.value_fn,
            entity_registry_enabled_default=True,
            entity_registry_visible_default=False,
        )

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for unmapped circuit sensors."""
        # Unmapped tab sensors are regular circuit sensors, use standard circuit unique ID pattern
        # circuit_id is already "unmapped_tab_32", so this creates "span_{serial}_unmapped_tab_32_{suffix}"
        # Use the original key (e.g., "instantPowerW") instead of the modified description.key
        return construct_circuit_unique_id_for_entry(
            self.coordinator, span_panel, self.circuit_id, self.original_key, self._device_name
        )

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
        return self._construct_sensor_unmapped_entity_id(self.circuit_id, sensor_suffix)

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the unmapped circuit sensor."""
        return span_panel.circuits[self.circuit_id]


def _get_migration_mode(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Check if migration mode is enabled for this config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    return bool(entry_data.get("migration_mode") or config_entry.options.get("migration_mode"))


def _create_native_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[SpanPanelPanelStatus | SpanUnmappedCircuitSensor | SpanPanelStatus | SpanPanelBattery]:
    """Create all native sensors for the platform."""
    entities: list[
        SpanPanelPanelStatus | SpanUnmappedCircuitSensor | SpanPanelStatus | SpanPanelBattery
    ] = []

    # Add panel data status sensors (DSM State, DSM Grid State, etc.)
    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Add unmapped circuit sensors (native sensors for synthetic calculations)
    # These are invisible sensors that provide stable entity IDs for solar synthetics
    unmapped_circuits = [cid for cid in span_panel.circuits if cid.startswith("unmapped_tab_")]
    for circuit_id in unmapped_circuits:
        for unmapped_description in UNMAPPED_SENSORS:
            # UNMAPPED_SENSORS contains SpanPanelCircuitsSensorEntityDescription
            entities.append(
                SpanUnmappedCircuitSensor(coordinator, unmapped_description, span_panel, circuit_id)
            )

    # Add hardware status sensors (Door State, WiFi, Cellular, etc.)
    for description_ss in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description_ss, span_panel))

    # Add battery sensor if enabled
    battery_enabled = config_entry.options.get(BATTERY_ENABLE, False)
    if battery_enabled:
        entities.append(SpanPanelBattery(coordinator, BATTERY_SENSOR, span_panel))

    return entities


def _enable_unmapped_tab_entities(hass: HomeAssistant, entities: list) -> None:
    """Enable unmapped tab entities in the entity registry if they were disabled."""
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
                _LOGGER.debug("Enabling previously disabled unmapped tab entity: %s", entity_id)
                entity_registry.async_update_entity(entity_id, disabled_by=None)


async def _handle_migration_solar_setup(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: SpanPanelCoordinator,
    sensor_set: Any,
) -> bool:
    """Handle solar sensor setup during migration.

    Returns:
        True if solar setup was performed successfully, False otherwise.

    """
    solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
    if not solar_enabled or sensor_set is None:
        return False

    # Coerce leg options to integers to handle any legacy string-stored values
    leg1_raw = config_entry.options.get(INVERTER_LEG1, 0)
    leg2_raw = config_entry.options.get(INVERTER_LEG2, 0)
    try:
        leg1 = int(leg1_raw)
    except (TypeError, ValueError):
        leg1 = 0
    try:
        leg2 = int(leg2_raw)
    except (TypeError, ValueError):
        leg2 = 0
    # Get device name from config entry like sensors do
    device_name = config_entry.data.get("device_name", config_entry.title)

    _LOGGER.debug(
        "Solar enabled during initial setup - setting up solar sensors (leg1: %s, leg2: %s)",
        leg1,
        leg2,
    )

    try:
        result = await handle_solar_options_change(
            hass,
            config_entry,
            coordinator,
            sensor_set,
            solar_enabled,
            leg1,
            leg2,
            device_name,
            migration_mode=True,
        )
        if result:
            _LOGGER.debug("Initial solar sensor setup completed successfully")
            return True
        else:
            _LOGGER.warning("Initial solar sensor setup failed")
            return False
    except Exception as e:
        _LOGGER.error("Failed to set up initial solar sensors: %s", e, exc_info=True)
        return False


def _clear_migration_flags(
    hass: HomeAssistant, config_entry: ConfigEntry, migration_mode: bool
) -> None:
    """Clear migration flags after setup is complete."""
    if not migration_mode:
        return

    try:
        entry_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
        # Prefer persisted option, but clear both option and transient flag
        if entry_data.get("migration_mode") or config_entry.options.get("migration_mode"):
            entry_data.pop("migration_mode", None)
            # Clear persisted option flag
            try:
                new_options = dict(config_entry.options)
                if "migration_mode" in new_options:
                    del new_options["migration_mode"]
                    hass.config_entries.async_update_entry(config_entry, options=new_options)
            except Exception as opt_err:
                _LOGGER.debug(
                    "Failed to clear persisted migration flag for %s: %s",
                    config_entry.entry_id,
                    opt_err,
                )
            _LOGGER.info(
                "Migration mode completed for entry %s: cleared per-entry flag",
                config_entry.entry_id,
            )
    except Exception as e:
        _LOGGER.debug("Failed to clear migration flag for %s: %s", config_entry.entry_id, e)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    try:
        data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
        coordinator: SpanPanelCoordinator = data[COORDINATOR]
        span_panel: SpanPanel = coordinator.data

        # Check migration mode early to pass to all functions that need it
        migration_mode = _get_migration_mode(hass, config_entry)

        # Create all native sensors
        entities = _create_native_sensors(coordinator, span_panel, config_entry)

        # Add all native sensor entities
        async_add_entities(entities)

        # Enable unmapped tab entities if they were disabled
        _enable_unmapped_tab_entities(hass, entities)

        # Delegate synthetic sensor setup to ha-synthetic-sensors package
        try:
            # Ensure coordinator has valid data before setting up synthetic sensors
            if not coordinator.last_update_success or not coordinator.data:
                _LOGGER.warning(
                    "Coordinator not ready for synthetic sensor setup - "
                    "attempting refresh before proceeding"
                )
                await coordinator.async_refresh()

                if not coordinator.last_update_success or not coordinator.data:
                    _LOGGER.error(
                        "Failed to get valid coordinator data - synthetic sensors may not have initial values"
                    )

            # Set up synthetic sensor configuration
            # Check if migration just occurred - if so, use existing YAML instead of regenerating
            storage_manager = StorageManager(hass, DOMAIN, integration_domain=DOMAIN)
            await storage_manager.async_load()

            # If sensor sets already exist (from migration), use them directly
            sensor_sets = storage_manager.list_sensor_sets()
            _LOGGER.info("SENSOR SETUP DEBUG: Found %d existing sensor sets", len(sensor_sets))
            for sensor_set in sensor_sets:
                _LOGGER.info(
                    "SENSOR SETUP DEBUG: Sensor set - id=%s, device=%s",
                    sensor_set.sensor_set_id,
                    sensor_set.device_identifier,
                )

            if sensor_sets:
                _LOGGER.info(
                    "SENSOR SETUP DEBUG: Using existing sensor configuration from migration"
                )
                # For migration, we still need to create the SyntheticSensorCoordinator
                # even though we're not generating new configuration
                device_name = config_entry.data.get("device_name", config_entry.title)
                synthetic_coord = SyntheticSensorCoordinator(hass, coordinator, device_name)
                _synthetic_coordinators[config_entry.entry_id] = synthetic_coord

                # Determine the correct sensor_set_id for THIS entry and ensure it exists
                current_identifier = get_device_identifier_for_entry(
                    coordinator, coordinator.data, device_name
                )
                current_sensor_set_id = construct_sensor_set_id(current_identifier)
                if not storage_manager.sensor_set_exists(current_sensor_set_id):
                    _LOGGER.info(
                        "SENSOR SETUP DEBUG: Sensor set %s not found; generating configuration for this entry",
                        current_sensor_set_id,
                    )
                    storage_manager = await setup_synthetic_configuration(
                        hass, config_entry, coordinator, migration_mode
                    )
                # Initialize the synthetic coordinator configuration so backing metadata is populated
                await synthetic_coord.setup_configuration(config_entry, migration_mode)
                # Prime the coordinator so downstream setup uses the correct set
                synthetic_coord.sensor_set_id = current_sensor_set_id
                synthetic_coord.device_identifier = current_identifier
            else:
                # Fresh install - generate new configuration
                _LOGGER.info("SENSOR SETUP DEBUG: Fresh install - generating new configuration")
                storage_manager = await setup_synthetic_configuration(
                    hass, config_entry, coordinator, migration_mode
                )
            # Use simplified setup interface that handles everything
            sensor_manager = await async_setup_synthetic_sensors(
                hass=hass,
                config_entry=config_entry,
                async_add_entities=async_add_entities,
                coordinator=coordinator,
                storage_manager=storage_manager,
            )
            # Get and store the SensorSet for this device
            # Use the per-entry device_identifier chosen during synthetic setup
            synth_coord = find_synthetic_coordinator_for(coordinator)
            device_identifier = (
                synth_coord.device_identifier
                if synth_coord and synth_coord.device_identifier
                else coordinator.data.status.serial_number
            )
            sensor_set_id = construct_sensor_set_id(device_identifier)
            sensor_set = storage_manager.get_sensor_set(sensor_set_id)

            if sensor_set is None:
                _LOGGER.error("Sensor set not found: %s", sensor_set_id)
                _LOGGER.debug("Available sensor sets: %s", storage_manager.list_sensor_sets())

            # Store managers and sensor set for potential reload functionality
            data["sensor_manager"] = sensor_manager
            data[STORAGE_MANAGER] = storage_manager
            data[SENSOR_SET] = sensor_set

            _LOGGER.debug(
                "Successfully set up synthetic sensors and cached SensorSet: %s", sensor_set_id
            )

            # Handle migration completion
            if migration_mode:
                # Handle solar sensor setup during migration if solar is enabled
                solar_setup_result = await _handle_migration_solar_setup(
                    hass, config_entry, coordinator, sensor_set
                )

                # Always clear migration flags first
                _clear_migration_flags(hass, config_entry, migration_mode)

                # If solar was set up during migration, schedule a reload to pick up the newly added
                # solar sensors, but don't block the initial startup
                if solar_setup_result:
                    _LOGGER.debug(
                        "Solar setup completed during migration, scheduling reload to load solar sensors"
                    )

                    async def _scheduled_reload() -> None:
                        """Reload the config entry after a brief delay to allow startup to complete."""
                        await asyncio.sleep(1.0)  # Brief delay to let initial setup finish
                        _LOGGER.debug("Executing scheduled reload for solar sensors")
                        await hass.config_entries.async_reload(config_entry.entry_id)

                    hass.async_create_task(_scheduled_reload())

        except Exception as e:
            _LOGGER.error("Failed to set up synthetic sensors: %s", e, exc_info=True)

        # Force immediate coordinator refresh to ensure all sensors (native and synthetic) update right away
        await coordinator.async_request_refresh()

        _LOGGER.debug("Sensor platform setup completed")
    except Exception as e:
        _LOGGER.error("SENSOR_ENTRY_DEBUG: Error in async_setup_entry: %s", e, exc_info=True)
        raise
