"""Support for Span Panel monitor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any, Generic, TypeVar

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from custom_components.span_panel.options import (
    ENERGY_REPORTING_GRACE_PERIOD,
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)

from .const import (
    COORDINATOR,
    DOMAIN,
    SIGNAL_STAGE_NATIVE_SENSORS,
    USE_DEVICE_PREFIX,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_circuit_unique_id_for_entry,
    construct_panel_entity_id,
    construct_panel_friendly_name,
    construct_panel_unique_id_for_entry,
    construct_single_circuit_entity_id,
    construct_status_friendly_name,
    construct_synthetic_unique_id_for_entry,
    construct_tabs_attribute,
    construct_unmapped_friendly_name,
    construct_voltage_attribute,
    get_panel_entity_suffix,
    get_user_friendly_suffix,
)
from .options import BATTERY_ENABLE
from .sensor_definitions import (
    BATTERY_SENSOR,
    CIRCUIT_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_ENERGY_SENSORS,
    PANEL_POWER_SENSORS,
    SOLAR_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
    SpanPanelBatterySensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelStatusSensorEntityDescription,
    SpanSolarSensorEntityDescription,
)
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery
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

    @property
    def available(self) -> bool:
        """Return entity availability.

        Keep entities available during a panel_offline condition so sensors can show
        their grace period state (last_valid_state) or None when grace period expires.
        """
        try:
            if getattr(self.coordinator, "panel_offline", False):
                return True
        except AttributeError as err:
            # If coordinator is missing expected attribute, log and fall back
            _LOGGER.debug("Availability check: missing coordinator attribute: %s", err)
        except Exception as err:  # pragma: no cover - defensive
            # Any unexpected error shouldn't crash the availability check
            _LOGGER.debug("Availability check: unexpected error: %s", err)
        return super().available

    def _update_native_value(self) -> None:
        """Update the native value of the sensor."""

        if self.coordinator.panel_offline:
            _LOGGER.debug("STATUS_SENSOR_DEBUG: Panel is offline for %s", self._attr_name)

            # For power sensors, set to 0.0 when offline (instantaneous values)
            # For energy sensors, set to None when offline (HA will report as unknown)
            # For other sensors, set to STATE_UNKNOWN when offline
            device_class = getattr(self.entity_description, "device_class", None)
            if device_class == "power":
                self._attr_native_value = 0.0
            elif device_class == "energy":
                self._attr_native_value = None
            else:
                self._attr_native_value = STATE_UNKNOWN
            return

        value_function: Callable[[D], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            _LOGGER.debug("STATUS_SENSOR_DEBUG: No value_function for %s", self._attr_name)
            self._attr_native_value = STATE_UNKNOWN
            return

        try:
            data_source: D = self.get_data_source(self.coordinator.data)

            # Only do debug logging if we have valid data and the panel is online
            if (
                not self.coordinator.panel_offline
                and hasattr(self, "id")
                and hasattr(data_source, "instant_power")
            ):
                circuit_id = getattr(self, "id", STATE_UNKNOWN)
                instant_power = getattr(data_source, "instant_power", None)
                description_key = getattr(self.entity_description, "key", STATE_UNKNOWN)
                _LOGGER.debug(
                    "CIRCUIT_POWER_DEBUG: Circuit %s, sensor %s, instant_power=%s, data_source type=%s",
                    circuit_id,
                    description_key,
                    instant_power,
                    type(data_source).__name__,
                )

            raw_value: float | int | str | None = value_function(data_source)

            if raw_value is None:
                self._attr_native_value = STATE_UNKNOWN
            elif isinstance(raw_value, float | int):
                self._attr_native_value = float(raw_value)
            else:
                # For string values, keep as string - this is valid for Home Assistant sensors
                self._attr_native_value = str(raw_value)
        except (AttributeError, KeyError, IndexError):
            self._attr_native_value = STATE_UNKNOWN

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


class SpanEnergySensorBase(SpanSensorBase[T, D], ABC):
    """Base class for energy sensors that includes grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the energy sensor with grace period tracking."""
        super().__init__(data_coordinator, description, span_panel)
        self._last_valid_state: float | None = None
        self._last_valid_changed: datetime | None = None
        self._grace_period_minutes = data_coordinator.config_entry.options.get(
            ENERGY_REPORTING_GRACE_PERIOD, 15
        )

    def _update_native_value(self) -> None:
        """Update the native value with grace period logic for energy sensors."""
        if self.coordinator.panel_offline:
            # Use grace period logic when offline
            self._handle_offline_grace_period()
            return

        # Panel is online - use normal update logic from parent class
        super()._update_native_value()

        # Track valid state for grace period (only when we have a valid value)
        if self._attr_native_value is not None and isinstance(self._attr_native_value, int | float):
            self._last_valid_state = float(self._attr_native_value)
            self._last_valid_changed = datetime.now()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator with grace period tracking."""
        # Update grace period from options in case it changed
        self._grace_period_minutes = self.coordinator.config_entry.options.get(
            ENERGY_REPORTING_GRACE_PERIOD, 15
        )

        # Use the overridden _update_native_value method which handles grace period
        self._update_native_value()

        # Call the parent's parent class coordinator update to avoid the intermediate parent's logic
        super(SpanSensorBase, self)._handle_coordinator_update()

    def _handle_offline_grace_period(self) -> None:
        """Handle grace period logic when panel is offline."""
        if self._last_valid_changed is None or self._last_valid_state is None:
            # No previous valid state, set to None (HA reports unknonwn)
            self._attr_native_value = None
            return

        # Check if we're still within the grace period
        time_since_last_valid = datetime.now() - self._last_valid_changed
        grace_period_duration = timedelta(minutes=self._grace_period_minutes)

        if time_since_last_valid <= grace_period_duration:
            # Still within grace period - use last valid state
            self._attr_native_value = self._last_valid_state
        else:
            # Grace period expired - set to None (makes sensor unknown)
            self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period info."""
        attributes = {}

        # Always show grace period information if we have valid tracking data
        if self._last_valid_changed is not None:
            if self._last_valid_state is not None:
                attributes["last_valid_state"] = str(self._last_valid_state)
            attributes["last_valid_changed"] = self._last_valid_changed.isoformat()

            # Calculate grace period remaining
            if self._grace_period_minutes > 0:
                time_since_last_valid = datetime.now() - self._last_valid_changed
                grace_period_duration = timedelta(minutes=self._grace_period_minutes)
                remaining_seconds = (grace_period_duration - time_since_last_valid).total_seconds()
                remaining_minutes = max(0, int(remaining_seconds / 60))
                attributes["grace_period_remaining"] = str(remaining_minutes)

                # Indicate if we're currently using grace period
                panel_offline = getattr(self.coordinator, "panel_offline", False)
                if panel_offline and remaining_seconds > 0:
                    attributes["using_grace_period"] = "True"

        return attributes if attributes else None


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

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
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

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        """Get the data source for the panel status sensor."""
        try:
            result = span_panel.status
            return result
        except Exception as e:
            _LOGGER.error("HARDWARE_STATUS: Error getting status data: %s", e)
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

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelStorageBattery:
        """Get the data source for the battery sensor."""
        _LOGGER.debug("BATTERY_DEBUG: get_data_source called for battery sensor")
        try:
            result = span_panel.storage_battery
            _LOGGER.debug("Successfully got battery data: %s", type(result).__name__)
            return result
        except Exception as e:
            _LOGGER.error("Error getting battery data: %s", e)
            raise


class SpanPanelPowerSensor(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]):
    """Enhanced panel power sensor with amperage attribute calculation."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the enhanced panel power sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel power sensors."""
        # Use the same logic as migration: get entity suffix and use synthetic unique_id

        entity_suffix = get_panel_entity_suffix(description.key)
        unique_id = construct_synthetic_unique_id_for_entry(
            self.coordinator, span_panel, entity_suffix, self._device_name
        )

        return unique_id

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel power sensors."""
        return construct_panel_friendly_name(description.name)

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for panel power sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel power sensor."""
        return span_panel.panel

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including amperage calculation."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None

        attributes = {}

        # Add voltage attribute (standard panel voltage)
        attributes["voltage"] = "240"

        # Calculate amperage from power (P = V * I, so I = P / V)
        if self.native_value is not None and isinstance(self.native_value, int | float):
            try:
                amperage = float(self.native_value) / 240.0
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"
        else:
            attributes["amperage"] = "0.0"

        return attributes


class SpanPanelEnergySensor(
    SpanEnergySensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]
):
    """Panel energy sensor with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the panel energy sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel energy sensors."""
        # Use the same logic as migration: get entity suffix and use synthetic unique_id

        entity_suffix = get_panel_entity_suffix(description.key)
        return construct_synthetic_unique_id_for_entry(
            self.coordinator, span_panel, entity_suffix, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel energy sensors."""
        return str(description.name or "Panel Energy Sensor")

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for panel energy sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel energy sensor."""
        return span_panel.panel

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and voltage."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add voltage attribute (standard panel voltage)
        attributes["voltage"] = "240"

        return attributes if attributes else None


class SpanCircuitPowerSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Enhanced circuit power sensor with amperage and tabs attributes."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        span_panel: SpanPanel,
        circuit_id: str,
    ) -> None:
        """Initialize the enhanced circuit power sensor."""
        self.circuit_id = circuit_id
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
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
        )

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for circuit power sensors."""
        # Use the original API key that migration normalized from
        api_key = "instantPowerW"  # This maps to "power" suffix
        return construct_circuit_unique_id_for_entry(
            self.coordinator, span_panel, self.circuit_id, api_key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for circuit power sensors."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if circuit and circuit.name:
            return f"{circuit.name} {description.name or 'Sensor'}"
        return construct_unmapped_friendly_name(self.circuit_id, str(description.name or "Sensor"))

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for circuit power sensors."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if circuit:
            # Use the helper functions for entity ID generation

            # Only pass unique_id during migration - during initial setup, skip registry lookup
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_single_circuit_entity_id(
                coordinator=coordinator,
                span_panel=span_panel,
                platform="sensor",
                suffix=slugify(str(description.name or "sensor")),
                circuit_data=circuit,
                unique_id=unique_id_for_lookup,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the circuit power sensor."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if circuit is None:
            raise ValueError(f"Circuit {self.circuit_id} not found in panel data")
        return circuit

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including amperage and tabs."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None

        circuit = self.coordinator.data.circuits.get(self.circuit_id)
        if not circuit:
            return None

        attributes = {}

        # Add tabs attribute

        tabs_result = construct_tabs_attribute(circuit)
        if tabs_result is not None:
            attributes["tabs"] = str(tabs_result)

        # Add voltage attribute
        voltage = construct_voltage_attribute(circuit) or 240
        attributes["voltage"] = str(voltage)

        # Calculate amperage from power (P = V * I, so I = P / V)
        if self.native_value is not None and isinstance(self.native_value, int | float):
            try:
                amperage = float(self.native_value) / float(voltage)
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"
        else:
            attributes["amperage"] = "0.0"

        return attributes


class SpanCircuitEnergySensor(
    SpanEnergySensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Circuit energy sensor with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        span_panel: SpanPanel,
        circuit_id: str,
    ) -> None:
        """Initialize the circuit energy sensor."""
        self.circuit_id = circuit_id
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
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
        )

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for circuit energy sensors."""
        # Map new description keys to original API keys that migration normalized from
        api_key_mapping = {
            "circuit_energy_produced": "producedEnergyWh",
            "circuit_energy_consumed": "consumedEnergyWh",
            "circuit_energy_net": "netEnergyWh",
        }
        api_key = api_key_mapping.get(self.original_key, self.original_key)
        return construct_circuit_unique_id_for_entry(
            self.coordinator, span_panel, self.circuit_id, api_key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for circuit energy sensors."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if circuit and circuit.name:
            return f"{circuit.name} {description.name}"
        return f"Circuit {self.circuit_id} {description.name}"

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for circuit energy sensors."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if not circuit:
            return None

        # Use the helper functions for entity ID generation

        # Only pass unique_id during migration - during initial setup, skip registry lookup
        # Exception: Never pass unique_id for net energy sensors since they are completely new
        migration_mode = coordinator.config_entry.options.get("migration_mode", False)
        is_net_energy_sensor = self.original_key == "circuit_energy_net"
        unique_id_for_lookup = (
            None if is_net_energy_sensor else (self._attr_unique_id if migration_mode else None)
        )

        return construct_single_circuit_entity_id(
            coordinator=coordinator,
            span_panel=span_panel,
            platform="sensor",
            suffix=slugify(str(description.name or "sensor")),
            circuit_data=circuit,
            unique_id=unique_id_for_lookup,
        )

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the circuit energy sensor."""
        return span_panel.circuits[self.circuit_id]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and circuit info."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add circuit-specific attributes if we have data
        if self.coordinator.data:
            span_panel = self.coordinator.data
            circuit = span_panel.circuits.get(self.circuit_id)

            if circuit:
                # Add tabs and voltage attributes

                tabs = construct_tabs_attribute(circuit)
                if tabs is not None:
                    attributes["tabs"] = tabs

                voltage = construct_voltage_attribute(circuit) or 240
                attributes["voltage"] = voltage

        return attributes if attributes else None


class SpanSolarSensor(SpanSensorBase[SpanSolarSensorEntityDescription, SpanPanel]):
    """Solar sensor that combines values from leg1 and leg2 circuits."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanSolarSensorEntityDescription,
        span_panel: SpanPanel,
        leg1_circuit_id: str,
        leg2_circuit_id: str,
    ) -> None:
        """Initialize the solar sensor."""
        self.leg1_circuit_id = leg1_circuit_id
        self.leg2_circuit_id = leg2_circuit_id
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate unique ID for solar sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate friendly name for solar sensors."""
        return str(description.name or "Solar Sensor")

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanSolarSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for solar sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanel:
        """Get the data source for the solar sensor."""
        return span_panel

    def _update_native_value(self) -> None:
        """Update the native value by combining leg1 and leg2 circuit values."""
        if self.coordinator.panel_offline:
            _LOGGER.debug("SOLAR_SENSOR_DEBUG: Panel is offline for %s", self._attr_name)
            # For solar power sensors, set to 0.0 when offline (instantaneous values)
            # For energy sensors, set to None when offline (HA will report as unknown)
            # For other sensors, set to STATE_UNKNOWN when offline
            device_class = getattr(self.entity_description, "device_class", None)
            if device_class == "power":
                self._attr_native_value = 0.0
            elif device_class == "energy":
                self._attr_native_value = None
            else:
                self._attr_native_value = STATE_UNKNOWN
            return

        if not self.coordinator.last_update_success or not self.coordinator.data:
            self._attr_native_value = STATE_UNKNOWN
            return

        span_panel = self.coordinator.data
        leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
        leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

        if not leg1_circuit or not leg2_circuit:
            self._attr_native_value = STATE_UNKNOWN
            return

        try:
            # Get the appropriate attribute based on the sensor type
            description = self.entity_description
            assert isinstance(description, SpanSolarSensorEntityDescription)
            if description.key == "solar_current_power":
                leg1_value = getattr(leg1_circuit, "instant_power", 0) or 0
                leg2_value = getattr(leg2_circuit, "instant_power", 0) or 0
            elif description.key == "solar_produced_energy":
                leg1_value = getattr(leg1_circuit, "produced_energy", 0) or 0
                leg2_value = getattr(leg2_circuit, "produced_energy", 0) or 0
            elif description.key == "solar_consumed_energy":
                leg1_value = getattr(leg1_circuit, "consumed_energy", 0) or 0
                leg2_value = getattr(leg2_circuit, "consumed_energy", 0) or 0
            elif description.key == "solar_net_energy":
                # Net energy = produced - consumed for each leg, then sum
                leg1_produced = getattr(leg1_circuit, "produced_energy", 0) or 0
                leg1_consumed = getattr(leg1_circuit, "consumed_energy", 0) or 0
                leg2_produced = getattr(leg2_circuit, "produced_energy", 0) or 0
                leg2_consumed = getattr(leg2_circuit, "consumed_energy", 0) or 0
                leg1_value = leg1_produced - leg1_consumed
                leg2_value = leg2_produced - leg2_consumed
            else:
                leg1_value = 0
                leg2_value = 0

            # Combine the values
            if hasattr(description, "calculation_type") and description.calculation_type == "sum":
                self._attr_native_value = float(leg1_value) + float(leg2_value)
            else:
                self._attr_native_value = float(leg1_value) + float(leg2_value)

        except (ValueError, TypeError, AttributeError) as e:
            _LOGGER.warning("Error calculating solar sensor value for %s: %s", description.key, e)
            self._attr_native_value = STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including tabs and voltage."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None

        span_panel = self.coordinator.data
        leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
        leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

        if not leg1_circuit or not leg2_circuit:
            return None

        attributes = {}

        # Add tabs attribute combining both legs

        # Combine tabs from both circuits into a single tabs attribute
        all_tabs = []
        if leg1_circuit.tabs:
            all_tabs.extend(leg1_circuit.tabs)
        if leg2_circuit.tabs:
            all_tabs.extend(leg2_circuit.tabs)

        if all_tabs:
            # Sort tabs for consistent ordering and remove duplicates
            sorted_unique_tabs = sorted(set(all_tabs))
            if len(sorted_unique_tabs) == 1:
                attributes["tabs"] = f"tabs [{sorted_unique_tabs[0]}]"
            elif len(sorted_unique_tabs) == 2:
                attributes["tabs"] = f"tabs [{sorted_unique_tabs[0]}:{sorted_unique_tabs[1]}]"
            else:
                # Multiple non-contiguous tabs - list them
                tab_list = ", ".join(str(tab) for tab in sorted_unique_tabs)
                attributes["tabs"] = f"tabs [{tab_list}]"

        # Add voltage attribute based on total number of unique tabs
        if all_tabs:
            unique_tab_count = len(sorted_unique_tabs)
            if unique_tab_count == 1:
                voltage = 120
            elif unique_tab_count == 2:
                voltage = 240
            else:
                # More than 2 tabs is not valid for US electrical system
                voltage = 240  # Default to 240V for invalid configurations
        else:
            voltage = 240  # Default to 240V if no tabs information
        attributes["voltage"] = str(voltage)

        # Calculate amperage for power sensors
        if (
            self.entity_description.key == "solar_current_power"
            and self.native_value is not None
            and isinstance(self.native_value, int | float)
        ):
            try:
                amperage = float(self.native_value) / float(voltage)
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"

        return attributes


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


class SpanSolarEnergySensor(SpanEnergySensorBase[SpanSolarSensorEntityDescription, SpanPanel]):
    """Solar energy sensor that combines values from leg1 and leg2 circuits with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanSolarSensorEntityDescription,
        span_panel: SpanPanel,
        leg1_circuit_id: str,
        leg2_circuit_id: str,
    ) -> None:
        """Initialize the solar energy sensor."""
        self.leg1_circuit_id = leg1_circuit_id
        self.leg2_circuit_id = leg2_circuit_id
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate unique ID for solar energy sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate friendly name for solar energy sensors."""
        return str(description.name or "Solar Energy Sensor")

    def _generate_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description: SpanSolarSensorEntityDescription,
    ) -> str | None:
        """Generate entity ID for solar energy sensors."""
        if hasattr(description, "name") and description.name:
            entity_suffix = slugify(str(description.name))
            use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

            # Only pass unique_id during migration - during normal operation, respect current flags
            migration_mode = coordinator.config_entry.options.get("migration_mode", False)
            unique_id_for_lookup = self._attr_unique_id if migration_mode else None

            return construct_panel_entity_id(
                coordinator,
                span_panel,
                "sensor",
                entity_suffix,
                self._device_name,
                unique_id_for_lookup,
                use_device_prefix,
            )
        return None

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanel:
        """Get the data source for the solar energy sensor."""
        return span_panel

    def _update_native_value(self) -> None:
        """Update the native value by combining leg1 and leg2 circuit values."""
        if self.coordinator.panel_offline:
            _LOGGER.debug(
                "SOLAR_ENERGY_SENSOR_DEBUG: Panel is offline for %s, using grace period logic",
                self._attr_name,
            )
            # Use grace period logic when offline
            self._handle_offline_grace_period()
            return

        if not self.coordinator.last_update_success or not self.coordinator.data:
            self._attr_native_value = STATE_UNKNOWN
            return

        span_panel = self.coordinator.data
        leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
        leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

        if not leg1_circuit or not leg2_circuit:
            self._attr_native_value = STATE_UNKNOWN
            return

        try:
            # Get the appropriate attribute based on the sensor type
            description = self.entity_description
            assert isinstance(description, SpanSolarSensorEntityDescription)
            if description.key == "solar_produced_energy":
                leg1_value = getattr(leg1_circuit, "produced_energy", 0) or 0
                leg2_value = getattr(leg2_circuit, "produced_energy", 0) or 0
            elif description.key == "solar_consumed_energy":
                leg1_value = getattr(leg1_circuit, "consumed_energy", 0) or 0
                leg2_value = getattr(leg2_circuit, "consumed_energy", 0) or 0
            elif description.key == "solar_net_energy":
                # Net energy = produced - consumed for each leg, then sum
                leg1_produced = getattr(leg1_circuit, "produced_energy", 0) or 0
                leg1_consumed = getattr(leg1_circuit, "consumed_energy", 0) or 0
                leg2_produced = getattr(leg2_circuit, "produced_energy", 0) or 0
                leg2_consumed = getattr(leg2_circuit, "consumed_energy", 0) or 0
                leg1_value = leg1_produced - leg1_consumed
                leg2_value = leg2_produced - leg2_consumed
            else:
                leg1_value = 0
                leg2_value = 0

            # Combine the values
            if hasattr(description, "calculation_type") and description.calculation_type == "sum":
                self._attr_native_value = float(leg1_value) + float(leg2_value)
            else:
                self._attr_native_value = float(leg1_value) + float(leg2_value)

            # Track valid state for grace period (only when we have a valid value)
            if self._attr_native_value is not None and isinstance(
                self._attr_native_value, int | float
            ):
                self._last_valid_state = float(self._attr_native_value)
                self._last_valid_changed = datetime.now()

        except (ValueError, TypeError, AttributeError) as e:
            _LOGGER.warning(
                "Error calculating solar energy sensor value for %s: %s", description.key, e
            )
            self._attr_native_value = STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and solar info."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add solar-specific attributes if we have data
        if self.coordinator.data:
            span_panel = self.coordinator.data
            leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
            leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

            if leg1_circuit and leg2_circuit:
                # Add tabs attribute combining both legs

                # Combine tabs from both circuits into a single tabs attribute
                all_tabs = []
                if leg1_circuit.tabs:
                    all_tabs.extend(leg1_circuit.tabs)
                if leg2_circuit.tabs:
                    all_tabs.extend(leg2_circuit.tabs)

                if all_tabs:
                    # Sort tabs for consistent ordering and remove duplicates
                    sorted_unique_tabs = sorted(set(all_tabs))
                    if len(sorted_unique_tabs) == 1:
                        attributes["tabs"] = f"tabs [{sorted_unique_tabs[0]}]"
                    elif len(sorted_unique_tabs) == 2:
                        attributes["tabs"] = (
                            f"tabs [{sorted_unique_tabs[0]}:{sorted_unique_tabs[1]}]"
                        )
                    else:
                        # Multiple non-contiguous tabs - list them
                        tab_list = ", ".join(str(tab) for tab in sorted_unique_tabs)
                        attributes["tabs"] = f"tabs [{tab_list}]"

                # Add voltage attribute based on total number of unique tabs
                if all_tabs:
                    unique_tab_count = len(sorted_unique_tabs)
                    if unique_tab_count == 1:
                        voltage = 120
                    elif unique_tab_count == 2:
                        voltage = 240
                    else:
                        # More than 2 tabs is not valid for US electrical system
                        voltage = 240  # Default to 240V for invalid configurations
                else:
                    voltage = 240  # Default to 240V if no tabs information
                attributes["voltage"] = str(voltage)

        return attributes if attributes else None


def _create_native_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[
    SpanPanelPanelStatus
    | SpanUnmappedCircuitSensor
    | SpanPanelStatus
    | SpanPanelBattery
    | SpanPanelPowerSensor
    | SpanPanelEnergySensor
    | SpanCircuitPowerSensor
    | SpanCircuitEnergySensor
    | SpanSolarSensor
    | SpanSolarEnergySensor
]:
    """Create all native sensors for the platform."""
    entities: list[
        SpanPanelPanelStatus
        | SpanUnmappedCircuitSensor
        | SpanPanelStatus
        | SpanPanelBattery
        | SpanPanelPowerSensor
        | SpanPanelEnergySensor
        | SpanCircuitPowerSensor
        | SpanCircuitEnergySensor
        | SpanSolarSensor
        | SpanSolarEnergySensor
    ] = []

    # Add panel data status sensors (DSM State, DSM Grid State, etc.)
    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Add panel power sensors (replacing synthetic ones)
    for description in PANEL_POWER_SENSORS:
        entities.append(SpanPanelPowerSensor(coordinator, description, span_panel))

    # Add panel energy sensors (replacing synthetic ones)
    for description in PANEL_ENERGY_SENSORS:
        entities.append(SpanPanelEnergySensor(coordinator, description, span_panel))

    # Add circuit sensors for all named circuits (replacing synthetic ones)
    named_circuits = [cid for cid in span_panel.circuits if not cid.startswith("unmapped_tab_")]
    for circuit_id in named_circuits:
        for circuit_description in CIRCUIT_SENSORS:
            if circuit_description.key == "circuit_power":
                # Use enhanced power sensor for power measurements
                entities.append(
                    SpanCircuitPowerSensor(coordinator, circuit_description, span_panel, circuit_id)
                )
            else:
                # Use energy sensor with grace period tracking for energy measurements
                entities.append(
                    SpanCircuitEnergySensor(
                        coordinator, circuit_description, span_panel, circuit_id
                    )
                )

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

    # Add solar sensors if enabled
    solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
    if solar_enabled:
        # Get leg circuit IDs from options
        leg1_raw = config_entry.options.get(INVERTER_LEG1, 0)
        leg2_raw = config_entry.options.get(INVERTER_LEG2, 0)

        try:
            leg1_tab = int(leg1_raw)
            leg2_tab = int(leg2_raw)
        except (TypeError, ValueError):
            leg1_tab = 0
            leg2_tab = 0

        if leg1_tab > 0 and leg2_tab > 0:
            # Find the circuit IDs for the specified tabs
            leg1_circuit_id = None
            leg2_circuit_id = None

            for circuit_id, circuit in span_panel.circuits.items():
                if hasattr(circuit, "tabs") and circuit.tabs:
                    if leg1_tab in circuit.tabs:
                        leg1_circuit_id = circuit_id
                    if leg2_tab in circuit.tabs:
                        leg2_circuit_id = circuit_id

            # Create solar sensors if both legs found
            if leg1_circuit_id and leg2_circuit_id:
                for solar_description in SOLAR_SENSORS:
                    if solar_description.key == "solar_current_power":
                        # Use regular solar sensor for power measurements
                        entities.append(
                            SpanSolarSensor(
                                coordinator,
                                solar_description,
                                span_panel,
                                leg1_circuit_id,
                                leg2_circuit_id,
                            )
                        )
                    else:
                        # Use energy sensor with grace period tracking for energy measurements
                        entities.append(
                            SpanSolarEnergySensor(
                                coordinator,
                                solar_description,
                                span_panel,
                                leg1_circuit_id,
                                leg2_circuit_id,
                            )
                        )

    return entities


def _enable_unmapped_tab_entities(hass: HomeAssistant, entities: list[Any]) -> None:
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

        # Create all native sensors (now includes panel, circuit, and solar sensors)
        entities = _create_native_sensors(coordinator, span_panel, config_entry)

        # Add all native sensor entities
        async_add_entities(entities)

        # Enable unmapped tab entities if they were disabled
        _enable_unmapped_tab_entities(hass, entities)

        # Force immediate coordinator refresh to ensure all sensors update right away
        await coordinator.async_request_refresh()

        _LOGGER.debug("Native sensor platform setup completed with %d entities", len(entities))
    except Exception as e:
        _LOGGER.error("Error in async_setup_entry: %s", e, exc_info=True)
        raise
