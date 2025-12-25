"""Base sensor classes for Span Panel integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import logging
from typing import Any, Generic, Self, TypeVar

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.options import ENERGY_REPORTING_GRACE_PERIOD
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.util import panel_to_device_info

_LOGGER: logging.Logger = logging.getLogger(__name__)

T = TypeVar("T", bound=SensorEntityDescription)
D = TypeVar("D")  # For the type returned by get_data_source


def _parse_numeric_state(state: State | None) -> tuple[float | None, datetime | None]:
    """Extract a numeric value and naive timestamp from a restored HA state.

    Returns (None, None) when the state is unknown/unavailable or not numeric.
    """

    if state is None:
        return None, None

    if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
        return None, None

    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None, None

    # Normalize last_changed to naive datetime to match existing tracking
    last_changed = state.last_changed.replace(tzinfo=None) if state.last_changed else None
    return value, last_changed


class SpanSensorBase(CoordinatorEntity[SpanPanelCoordinator], SensorEntity, Generic[T, D], ABC):
    """Abstract base class for Span Panel Sensors with overrideable methods."""

    _attr_has_entity_name = True

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

        # Check if entity already exists in registry for name sync
        if span_panel.status.serial_number and description.key:
            self._attr_unique_id = self._generate_unique_id(span_panel, description)

            # Check if entity exists for name sync logic
            entity_registry = er.async_get(data_coordinator.hass)
            existing_entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, self._attr_unique_id
            )

            if existing_entity_id:
                # Entity exists - use panel name for sync
                self._attr_name = self._generate_panel_name(span_panel, description)
            else:
                # Initial install - use flag-based name
                self._attr_name = self._generate_friendly_name(span_panel, description)
        else:
            # Fallback for entities without unique_id
            self._attr_name = self._generate_friendly_name(span_panel, description)

        self._attr_icon = "mdi:flash"

        # Set entity registry defaults if they exist in the description
        if hasattr(description, "entity_registry_enabled_default"):
            self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        if hasattr(description, "entity_registry_visible_default"):
            self._attr_entity_registry_visible_default = description.entity_registry_visible_default

        # Initialize name sync tracking
        # Only set to None if entity doesn't exist in registry (true first time)
        if span_panel.status.serial_number and description.key and self._attr_unique_id:
            entity_registry = er.async_get(data_coordinator.hass)
            existing_entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, self._attr_unique_id
            )
            if not existing_entity_id:
                self._previous_circuit_name = None
            else:
                # Entity exists, get current circuit name for comparison
                if hasattr(self, "circuit_id"):
                    circuit = span_panel.circuits.get(getattr(self, "circuit_id", ""))
                    self._previous_circuit_name = circuit.name if circuit else None
                else:
                    self._previous_circuit_name = None
        else:
            self._previous_circuit_name = None

        # Use standard coordinator pattern - entities will update automatically
        # when coordinator data changes

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

    def _generate_panel_name(self, span_panel: SpanPanel, description: T) -> str:
        """Generate panel name for the sensor (always uses panel circuit name).

        This method is used for name sync - it always uses the panel circuit name
        regardless of user preferences.

        Args:
            span_panel: The span panel data
            description: The sensor description

        Returns:
            Panel name string

        """
        # This should be implemented by subclasses that need name sync
        # For now, fall back to friendly name
        return self._generate_friendly_name(span_panel, description)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check for circuit name changes for name sync (only for circuit sensors)
        if hasattr(self, "circuit_id") and hasattr(self.coordinator.data, "circuits"):
            circuit = self.coordinator.data.circuits.get(getattr(self, "circuit_id", ""))
            if circuit:
                current_circuit_name = circuit.name

                if self._previous_circuit_name is None:
                    # First update - sync to panel name
                    _LOGGER.info(
                        "First update: syncing sensor name to panel name '%s', requesting reload",
                        current_circuit_name,
                    )
                    # Update stored previous name for next comparison
                    self._previous_circuit_name = current_circuit_name
                    # Request integration reload to persist name change
                    self.coordinator.request_reload()
                elif current_circuit_name != self._previous_circuit_name:
                    _LOGGER.info(
                        "Auto-sync detected circuit name change from '%s' to '%s' for sensor, requesting integration reload",
                        self._previous_circuit_name,
                        current_circuit_name,
                    )
                    # Update stored previous name for next comparison
                    self._previous_circuit_name = current_circuit_name
                    # Request integration reload for next update cycle
                    self.coordinator.request_reload()

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
        except Exception as err:  # pragma: no cover - defensive
            # Avoid noisy stack traces from value functions; fall back to unknown
            _LOGGER.warning(
                "Value function failed for %s (%s); reporting unknown",
                self._attr_name,
                err,
            )
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


@dataclass
class SpanEnergyExtraStoredData(ExtraStoredData):
    """Extra stored data for Span energy sensors with grace period tracking.

    This data is persisted across Home Assistant restarts to maintain
    grace period state for energy sensors, preventing statistics spikes
    when the panel is offline at startup.
    """

    native_value: float | None
    native_unit_of_measurement: str | None
    last_valid_state: float | None
    last_valid_changed: str | None  # ISO format datetime string

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {
            "native_value": self.native_value,
            "native_unit_of_measurement": self.native_unit_of_measurement,
            "last_valid_state": self.last_valid_state,
            "last_valid_changed": self.last_valid_changed,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> Self | None:
        """Initialize extra stored data from a dict.

        Args:
            restored: Dictionary containing the stored data

        Returns:
            SpanEnergyExtraStoredData instance or None if restoration fails

        """
        try:
            return cls(
                native_value=restored.get("native_value"),
                native_unit_of_measurement=restored.get("native_unit_of_measurement"),
                last_valid_state=restored.get("last_valid_state"),
                last_valid_changed=restored.get("last_valid_changed"),
            )
        except (KeyError, TypeError):
            return None


class SpanEnergySensorBase(SpanSensorBase[T, D], RestoreSensor, ABC):
    """Base class for energy sensors that includes grace period tracking.

    This class extends SpanSensorBase with:
    - Grace period tracking for offline scenarios
    - State restoration across HA restarts via RestoreSensor mixin
    - Automatic persistence of last_valid_state and last_valid_changed
    """

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
        # Track if we've restored data (used for logging)
        self._restored_from_storage: bool = False

    async def async_added_to_hass(self) -> None:
        """Restore grace period state when entity is added to Home Assistant.

        This method is called when the entity is added to HA, which happens
        during startup or when the integration is reloaded. We use this
        opportunity to restore the grace period tracking state from storage.
        """
        await super().async_added_to_hass()

        # Try to restore the grace period state from storage
        if (last_extra_data := await self.async_get_last_extra_data()) is not None:
            restored = SpanEnergyExtraStoredData.from_dict(last_extra_data.as_dict())
            if restored:
                # Restore last_valid_state
                if restored.last_valid_state is not None:
                    self._last_valid_state = restored.last_valid_state

                # Restore last_valid_changed timestamp
                if restored.last_valid_changed is not None:
                    try:
                        self._last_valid_changed = datetime.fromisoformat(
                            restored.last_valid_changed
                        )
                        self._restored_from_storage = True
                        _LOGGER.debug(
                            "Restored grace period state for %s: "
                            "last_valid_state=%s, last_valid_changed=%s",
                            self.entity_id or self._attr_unique_id,
                            self._last_valid_state,
                            self._last_valid_changed,
                        )
                    except (ValueError, TypeError) as e:
                        _LOGGER.warning(
                            "Failed to parse restored last_valid_changed for %s: %s",
                            self.entity_id or self._attr_unique_id,
                            e,
                        )

        # Seed grace period tracking from the last stored HA state when extra data
        # is missing (e.g., after first install or early offline event).
        await self._initialize_grace_period_from_last_state()

    async def _initialize_grace_period_from_last_state(self) -> None:
        """Seed grace tracking from HA's last stored state when extra data is missing."""

        if self._last_valid_state is not None:
            return

        try:
            last_state = await self.async_get_last_state()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug(
                "Grace period restore: failed to fetch last state for %s: %s",
                self.entity_id or self._attr_unique_id,
                err,
            )
            return

        restored_value, restored_changed = _parse_numeric_state(last_state)
        if restored_value is None:
            return

        self._last_valid_state = restored_value
        self._last_valid_changed = restored_changed or datetime.now()
        self._restored_from_storage = True
        _LOGGER.debug(
            "Grace period initialized from last state for %s: value=%s, changed=%s",
            self.entity_id or self._attr_unique_id,
            self._last_valid_state,
            self._last_valid_changed,
        )

    @property
    def extra_restore_state_data(self) -> SpanEnergyExtraStoredData:
        """Return sensor-specific state data to be restored.

        This data is automatically saved by Home Assistant when the
        integration is unloaded or HA shuts down, and restored when
        the entity is added back to HA.
        """
        return SpanEnergyExtraStoredData(
            native_value=(
                float(self._attr_native_value)
                if isinstance(self._attr_native_value, int | float)
                else None
            ),
            native_unit_of_measurement=self.native_unit_of_measurement,
            last_valid_state=self._last_valid_state,
            last_valid_changed=(
                self._last_valid_changed.isoformat() if self._last_valid_changed else None
            ),
        )

    def _update_native_value(self) -> None:
        """Update the native value with grace period logic for energy sensors."""
        if self.coordinator.panel_offline:
            # Use grace period logic when offline
            self._handle_offline_grace_period()
            return

        # Panel is online - use normal update logic from parent class
        super()._update_native_value()

        self._track_valid_state(self._attr_native_value)

    def _track_valid_state(self, value: StateType | date | Decimal | None) -> None:
        """Update last valid state tracking when a numeric value is available."""
        if value is not None and isinstance(value, int | float | Decimal):
            self._last_valid_state = float(value)
            self._last_valid_changed = datetime.now()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator with grace period tracking."""
        # Check for circuit name changes for name sync (only for circuit sensors)
        if hasattr(self, "circuit_id") and hasattr(self.coordinator.data, "circuits"):
            circuit = self.coordinator.data.circuits.get(getattr(self, "circuit_id", ""))
            if circuit:
                current_circuit_name = circuit.name

                if self._previous_circuit_name is None:
                    # First update - sync to panel name
                    _LOGGER.info(
                        "First update: syncing energy sensor name to panel name '%s', requesting reload",
                        current_circuit_name,
                    )
                    # Update stored previous name for next comparison
                    self._previous_circuit_name = current_circuit_name
                    # Request integration reload to persist name change
                    self.coordinator.request_reload()
                elif current_circuit_name != self._previous_circuit_name:
                    _LOGGER.info(
                        "Auto-sync detected circuit name change from '%s' to '%s' for energy sensor, requesting integration reload",
                        self._previous_circuit_name,
                        current_circuit_name,
                    )
                    # Update stored previous name for next comparison
                    self._previous_circuit_name = current_circuit_name
                    # Request integration reload for next update cycle
                    self.coordinator.request_reload()

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
        # If we don't yet have a tracked valid state, fall back to the current
        # native value (e.g., restored state) to avoid returning None during a
        # brief offline period immediately after startup.
        if self._last_valid_state is None and isinstance(self._attr_native_value, int | float):
            self._last_valid_state = float(self._attr_native_value)
            self._last_valid_changed = self._last_valid_changed or datetime.now()

        if self._last_valid_state is None:
            # No previous valid state, set to None (HA reports unknown)
            self._attr_native_value = None
            return

        if self._last_valid_changed is None:
            self._last_valid_changed = datetime.now()

        grace_minutes = self._coerce_grace_period_minutes()

        try:
            time_since_last_valid = datetime.now() - self._last_valid_changed
            grace_period_duration = timedelta(minutes=grace_minutes)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Grace period calculation failed: %s", err)
            self._attr_native_value = self._last_valid_state
            return

        if time_since_last_valid <= grace_period_duration:
            # Still within grace period - use last valid state
            self._attr_native_value = self._last_valid_state
        else:
            # Grace period expired - set to None (makes sensor unknown)
            self._attr_native_value = None

    def _coerce_grace_period_minutes(self) -> int:
        """Ensure grace period minutes is a non-negative integer."""

        try:
            minutes = int(self._grace_period_minutes)
        except (TypeError, ValueError):
            minutes = 15
            self._grace_period_minutes = minutes

        if minutes < 0:
            minutes = 0
            self._grace_period_minutes = minutes

        return minutes

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
            grace_minutes = self._coerce_grace_period_minutes()
            if grace_minutes > 0:
                time_since_last_valid = datetime.now() - self._last_valid_changed
                grace_period_duration = timedelta(minutes=grace_minutes)
                remaining_seconds = (grace_period_duration - time_since_last_valid).total_seconds()
                remaining_minutes = max(0, int(remaining_seconds / 60))
                attributes["grace_period_remaining"] = str(remaining_minutes)

                # Indicate if we're currently using grace period
                panel_offline = getattr(self.coordinator, "panel_offline", False)
                if panel_offline and remaining_seconds > 0:
                    attributes["using_grace_period"] = "True"

        return attributes if attributes else None
