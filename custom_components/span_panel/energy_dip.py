"""Energy dip detection and compensation logic for Span Panel energy sensors.

Pure functions for detecting energy value dips (typically caused by panel firmware
resets) and computing the compensated value to maintain monotonic TOTAL_INCREASING
sensor readings.
"""

from __future__ import annotations

from typing import Any


def process_energy_dip(
    raw_value: float,
    last_panel_reading: float | None,
    current_offset: float,
) -> tuple[float, float | None, float]:
    """Detect an energy dip and return updated offset, optional dip delta, and compensated value.

    A "dip" occurs when the panel reports a raw energy value that is at least 1.0
    lower than the previous reading (e.g., after a firmware reset).  When detected,
    the difference is added to the cumulative offset so downstream sensors keep a
    monotonically increasing total.

    Args:
        raw_value: The current raw energy reading from the panel.
        last_panel_reading: The previous raw reading, or None if this is the first.
        current_offset: The cumulative compensation offset so far.

    Returns:
        A 3-tuple of (new_offset, dip_delta_or_none, compensated_value).

    """
    if last_panel_reading is not None and last_panel_reading - raw_value >= 1.0:
        dip = last_panel_reading - raw_value
        new_offset = current_offset + dip
        return (new_offset, dip, raw_value + new_offset)

    return (current_offset, None, raw_value + current_offset)


def build_dip_attributes(
    energy_offset: float,
    last_dip_delta: float | None,
    is_total_increasing: bool,
    dip_enabled: bool,
) -> dict[str, Any]:
    """Build extra_state_attributes dict for energy dip compensation diagnostics.

    Returns an empty dict when dip compensation is disabled or the sensor is not
    TOTAL_INCREASING.

    Args:
        energy_offset: Cumulative dip compensation offset.
        last_dip_delta: Size of the most recent dip, or None if none observed.
        is_total_increasing: Whether the sensor uses TOTAL_INCREASING state class.
        dip_enabled: Whether energy dip compensation is enabled in options.

    Returns:
        A dict of attribute key/value pairs (may be empty).

    """
    if not dip_enabled or not is_total_increasing:
        return {}

    attrs: dict[str, Any] = {}
    if energy_offset > 0:
        attrs["energy_offset"] = round(energy_offset, 1)
    if last_dip_delta is not None:
        attrs["last_dip_delta"] = round(last_dip_delta, 1)
    return attrs
