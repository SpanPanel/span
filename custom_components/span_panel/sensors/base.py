"""Base sensor classes for Span Panel integration."""

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
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from custom_components.span_panel.const import SIGNAL_STAGE_NATIVE_SENSORS
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.options import ENERGY_REPORTING_GRACE_PERIOD
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.util import panel_to_device_info

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
            self._handle_offline_state()
            return

        self._handle_online_state()

    def _handle_offline_state(self) -> None:
        """Handle sensor state when panel is offline."""
        _LOGGER.debug("STATUS_SENSOR_DEBUG: Panel is offline for %s", self._attr_name)

        # For power sensors, set to 0.0 when offline (instantaneous values)
        # For energy sensors, set to None when offline (HA will report as unknown)
        # For numeric sensors (battery, etc.), set to None when offline (HA will report as unknown)
        # For other sensors, set to STATE_UNKNOWN when offline
        device_class = getattr(self.entity_description, "device_class", None)
        state_class = getattr(self.entity_description, "state_class", None)
        if device_class == "power":
            self._attr_native_value = 0.0
        elif device_class == "energy":
            self._attr_native_value = None
        elif state_class is not None:
            # Any sensor with a state_class (measurement, total, etc.) expects numeric values
            self._attr_native_value = None
        else:
            self._attr_native_value = STATE_UNKNOWN

    def _handle_online_state(self) -> None:
        """Handle sensor state when panel is online."""
        value_function: Callable[[D], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            _LOGGER.debug("STATUS_SENSOR_DEBUG: No value_function for %s", self._attr_name)
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN
            return

        try:
            data_source: D = self.get_data_source(self.coordinator.data)
            self._log_debug_info(data_source)
            raw_value: float | int | str | None = value_function(data_source)
            self._process_raw_value(raw_value)
        except (AttributeError, KeyError, IndexError):
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN

    def _log_debug_info(self, data_source: D) -> None:
        """Log debug information for circuit sensors."""
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

    def _process_raw_value(self, raw_value: float | int | str | None) -> None:
        """Process the raw value from the value function."""
        if raw_value is None:
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN
        elif isinstance(raw_value, float | int):
            self._attr_native_value = float(raw_value)
        else:
            # For string values, keep as string - this is valid for Home Assistant sensors
            self._attr_native_value = str(raw_value)

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
