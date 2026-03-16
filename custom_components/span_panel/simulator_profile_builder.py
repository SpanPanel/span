"""Build per-circuit usage profiles from HA recorder statistics.

Queries the recorder's long-term statistics for each circuit's power
sensor, derives time-of-day patterns, monthly seasonality, duty cycle,
and average consumption, then packages them as a dict keyed by simulator
template name (``clone_{tab}``).

The result is sent to the simulator via Socket.IO so the clone config
reflects real consumption patterns rather than synthetic noise.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import math
from typing import TYPE_CHECKING, Literal

from homeassistant.components.recorder import get_instance as get_recorder
from homeassistant.components.recorder.statistics import (
    statistics_during_period,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .helpers import build_circuit_unique_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Minimum hours of recorder data before a circuit is included
_MIN_HOURLY_POINTS = 24

# Minimum distinct months before monthly_factors are emitted
_MIN_MONTHS_FOR_SEASONAL = 3

# Circuits with duty_cycle >= this are considered always-on; skip the field
_DUTY_CYCLE_CEILING = 0.8

# Hardware-driven modes whose power profiles should not be overridden
_SKIP_DEVICE_TYPES = frozenset({"pv", "bess"})


async def build_usage_profiles(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, dict[str, object]]:
    """Derive per-circuit usage profiles from recorder statistics.

    Returns a dict keyed by simulator template name (``clone_{tab}``)
    with sub-dicts containing any combination of:

    - ``typical_power`` (float, watts)
    - ``power_variation`` (float, 0.0–1.0)
    - ``hour_factors`` (dict[int, float], 0–23 → 0.0–1.0)
    - ``duty_cycle`` (float, 0.0–1.0)
    - ``monthly_factors`` (dict[int, float], 1–12 → 0.0–1.0)
    """
    if not hasattr(config_entry, "runtime_data") or config_entry.runtime_data is None:
        _LOGGER.warning(
            "Config entry %s has no runtime data (not yet set up?)", config_entry.entry_id
        )
        return {}
    snapshot = config_entry.runtime_data.coordinator.data
    if snapshot is None:
        _LOGGER.warning("No snapshot available for profile building")
        return {}

    serial = snapshot.serial_number
    entity_reg = er.async_get(hass)

    # Map each circuit to (template_name, entity_id) for power sensor lookup
    circuit_map: list[tuple[str, str, str]] = []  # (template_name, entity_id, circuit_id)

    for circuit_id, circuit in snapshot.circuits.items():
        if circuit_id.startswith("unmapped_tab_"):
            continue

        # Skip hardware-driven device types
        if getattr(circuit, "device_type", "circuit") in _SKIP_DEVICE_TYPES:
            continue

        tabs = getattr(circuit, "tabs", None)
        if not tabs:
            continue

        template_name = f"clone_{min(tabs)}"

        # Look up the power sensor entity_id via entity registry
        unique_id = build_circuit_unique_id(serial, circuit_id, "instantPowerW")
        entity_id = entity_reg.async_get_entity_id("sensor", "span_panel", unique_id)
        if entity_id is None:
            _LOGGER.debug(
                "No power sensor entity for circuit %s (unique_id=%s)",
                circuit_id,
                unique_id,
            )
            continue

        circuit_map.append((template_name, entity_id, circuit_id))

    if not circuit_map:
        _LOGGER.info("No circuits eligible for profile building")
        return {}

    stat_ids = {entity_id for _, entity_id, _ in circuit_map}
    now = dt_util.utcnow()

    # Query 1: hourly stats for the last 30 days
    hourly_start = now - timedelta(days=30)
    hourly_stats = await _query_statistics(
        hass, hourly_start, now, stat_ids, "hour", {"mean", "min", "max"}
    )

    # Query 2: monthly stats for the last 12 months
    monthly_start = now - timedelta(days=365)
    monthly_stats = await _query_statistics(hass, monthly_start, now, stat_ids, "month", {"mean"})

    # Build profiles
    profiles: dict[str, dict[str, object]] = {}

    for template_name, entity_id, circuit_id in circuit_map:
        hourly_rows = hourly_stats.get(entity_id, [])
        if len(hourly_rows) < _MIN_HOURLY_POINTS:
            _LOGGER.debug(
                "Circuit %s has only %d hourly points, skipping",
                circuit_id,
                len(hourly_rows),
            )
            continue

        profile = _derive_profile(hourly_rows, monthly_stats.get(entity_id, []))
        if profile:
            profiles[template_name] = profile

    _LOGGER.info(
        "Built usage profiles for %d/%d circuits",
        len(profiles),
        len(circuit_map),
    )
    return profiles


_StatPeriod = Literal["5minute", "day", "hour", "week", "month"]
_StatType = Literal["change", "last_reset", "max", "mean", "min", "state", "sum"]


async def _query_statistics(
    hass: HomeAssistant,
    start_time: datetime,
    end_time: datetime,
    stat_ids: set[str],
    period: _StatPeriod,
    stat_types: set[_StatType],
) -> dict[str, list[dict[str, object]]]:
    """Run statistics_during_period on the recorder's executor."""
    return await get_recorder(hass).async_add_executor_job(
        statistics_during_period,  # type: ignore[arg-type]
        hass,
        start_time,
        end_time,
        stat_ids,
        period,
        None,
        stat_types,
    )


def _derive_profile(
    hourly_rows: list[dict[str, object]],
    monthly_rows: list[dict[str, object]],
) -> dict[str, object]:
    """Compute profile parameters from raw statistics rows."""
    profile: dict[str, object] = {}

    # Extract hourly means and maxes
    hourly_means: list[float] = []
    hourly_maxes: list[float] = []
    hour_buckets: dict[int, list[float]] = {h: [] for h in range(24)}

    for row in hourly_rows:
        mean_val = row.get("mean")
        max_val = row.get("max")
        start = row.get("start")

        if mean_val is None or not isinstance(mean_val, int | float):
            continue

        abs_mean = abs(float(mean_val))
        hourly_means.append(abs_mean)

        if max_val is not None and isinstance(max_val, int | float):
            hourly_maxes.append(abs(float(max_val)))

        # Bucket by hour-of-day
        if isinstance(start, datetime):
            hour_buckets[start.hour].append(abs_mean)

    if not hourly_means:
        return profile

    # typical_power: mean of hourly means
    typical_power = sum(hourly_means) / len(hourly_means)
    profile["typical_power"] = round(typical_power, 1)

    # power_variation: coefficient of variation, clamped to [0.0, 1.0]
    if typical_power > 0:
        variance = sum((v - typical_power) ** 2 for v in hourly_means) / len(hourly_means)
        stddev = math.sqrt(variance)
        cv = stddev / typical_power
        profile["power_variation"] = round(min(max(cv, 0.0), 1.0), 3)

    # hour_factors: average by hour-of-day, normalized so peak = 1.0
    hour_averages: dict[int, float] = {}
    for h in range(24):
        bucket = hour_buckets[h]
        if bucket:
            hour_averages[h] = sum(bucket) / len(bucket)
        else:
            hour_averages[h] = 0.0

    peak_hour = max(hour_averages.values()) if hour_averages else 0.0
    if peak_hour > 0:
        hour_factors = {h: round(v / peak_hour, 3) for h, v in hour_averages.items()}
        profile["hour_factors"] = hour_factors

    # duty_cycle: mean(hourly_means) / mean(hourly_maxes)
    if hourly_maxes:
        mean_of_maxes = sum(hourly_maxes) / len(hourly_maxes)
        if mean_of_maxes > 0:
            duty = typical_power / mean_of_maxes
            if duty < _DUTY_CYCLE_CEILING:
                profile["duty_cycle"] = round(duty, 3)

    # monthly_factors from monthly stats (requires 3+ distinct months)
    if len(monthly_rows) >= _MIN_MONTHS_FOR_SEASONAL:
        monthly_means: dict[int, float] = {}
        for row in monthly_rows:
            mean_val = row.get("mean")
            start = row.get("start")
            if (
                mean_val is not None
                and isinstance(mean_val, int | float)
                and isinstance(start, datetime)
            ):
                monthly_means[start.month] = abs(float(mean_val))

        if len(monthly_means) >= _MIN_MONTHS_FOR_SEASONAL:
            peak_month = max(monthly_means.values())
            if peak_month > 0:
                monthly_factors = {m: round(v / peak_month, 3) for m, v in monthly_means.items()}
                profile["monthly_factors"] = monthly_factors

    return profile
