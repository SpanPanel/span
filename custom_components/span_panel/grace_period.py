"""Grace period calculation helpers and persistence for Span energy sensors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, Self

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State
from homeassistant.helpers.restore_state import ExtraStoredData

_LOGGER = logging.getLogger(__name__)


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

    # Ensure last_changed is a UTC-aware datetime to match our tracking
    last_changed: datetime | None = None
    if state.last_changed is not None:
        last_changed = (
            state.last_changed.replace(tzinfo=UTC)
            if state.last_changed.tzinfo is None
            else state.last_changed.astimezone(UTC)
        )
    return value, last_changed


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
    energy_offset: float | None = None
    last_panel_reading: float | None = None
    last_dip_delta: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {
            "native_value": self.native_value,
            "native_unit_of_measurement": self.native_unit_of_measurement,
            "last_valid_state": self.last_valid_state,
            "last_valid_changed": self.last_valid_changed,
            "energy_offset": self.energy_offset,
            "last_panel_reading": self.last_panel_reading,
            "last_dip_delta": self.last_dip_delta,
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
                energy_offset=restored.get("energy_offset"),
                last_panel_reading=restored.get("last_panel_reading"),
                last_dip_delta=restored.get("last_dip_delta"),
            )
        except (AttributeError, KeyError, TypeError):
            return None


def coerce_grace_period_minutes(raw_value: Any) -> int:
    """Ensure grace period minutes is a non-negative integer.

    Args:
        raw_value: The raw config value for grace period minutes.

    Returns:
        Validated integer (defaults to 15 if invalid, clamps to 0 minimum).

    """
    try:
        minutes = int(raw_value)
    except (TypeError, ValueError):
        minutes = 15

    if minutes < 0:
        minutes = 0

    return minutes


def handle_offline_grace_period(
    last_valid_state: float | None,
    last_valid_changed: datetime | None,
    current_native_value: Any,
    grace_minutes: int,
) -> tuple[Any, float | None, datetime | None]:
    """Handle grace period logic when panel is offline.

    Args:
        last_valid_state: The last known good numeric state.
        last_valid_changed: When the last valid state was recorded.
        current_native_value: The current native value on the entity.
        grace_minutes: Already-coerced grace period in minutes.

    Returns:
        Tuple of (new_native_value, updated_last_valid_state, updated_last_valid_changed).

    """
    # If we don't yet have a tracked valid state, fall back to the current
    # native value (e.g., restored state) to avoid returning None during a
    # brief offline period immediately after startup.
    if last_valid_state is None and isinstance(current_native_value, int | float):
        last_valid_state = float(current_native_value)
        last_valid_changed = last_valid_changed or datetime.now(tz=UTC)

    if last_valid_state is None:
        # No previous valid state, set to None (HA reports unknown)
        return None, None, last_valid_changed

    if last_valid_changed is None:
        last_valid_changed = datetime.now(tz=UTC)

    try:
        time_since_last_valid = datetime.now(tz=UTC) - last_valid_changed
        grace_period_duration = timedelta(minutes=grace_minutes)
    except Exception as err:  # noqa: BLE001  # pragma: no cover - defensive
        _LOGGER.debug("Grace period calculation failed: %s", err)
        return last_valid_state, last_valid_state, last_valid_changed

    if time_since_last_valid <= grace_period_duration:
        # Still within grace period - use last valid state
        return last_valid_state, last_valid_state, last_valid_changed

    # Grace period expired - set to None (makes sensor unknown)
    return None, last_valid_state, last_valid_changed


def initialize_from_last_state(
    last_state: State | None,
) -> tuple[float | None, datetime | None]:
    """Seed grace tracking from HA's last stored state when extra data is missing.

    Args:
        last_state: The HA state object from async_get_last_state().

    Returns:
        Tuple of (last_valid_state, last_valid_changed) or (None, None).

    """
    restored_value, restored_changed = _parse_numeric_state(last_state)
    if restored_value is None:
        return None, None

    return restored_value, restored_changed or datetime.now(tz=UTC)
