"""Cleanup energy spikes service for SPAN Panel integration.

This service detects and removes negative energy spikes from Home Assistant's
statistics database that occur when the SPAN panel undergoes firmware updates.

When the panel resets, it may temporarily report incorrect energy values,
causing massive spikes when it recovers. This service identifies those
timestamps and removes the problematic entries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Literal

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import (
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.recorder import session_scope
from homeassistant.util import dt as dt_util
import voluptuous as vol

from custom_components.span_panel.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Service name
SERVICE_CLEANUP_ENERGY_SPIKES = "cleanup_energy_spikes"

# Service schema
SERVICE_CLEANUP_ENERGY_SPIKES_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Optional("days_back", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
        vol.Optional("dry_run", default=True): cv.boolean,
    }
)


async def async_setup_cleanup_energy_spikes_service(hass: HomeAssistant) -> None:
    """Register the cleanup_energy_spikes service.

    This function is safe to call multiple times (e.g., for multi-panel setups).
    The service will only be registered once.
    """
    # Guard against multiple registrations for multi-panel setups
    # Use hass.data flag instead of has_service to avoid interfering with service metadata
    service_key = f"{DOMAIN}_cleanup_service_registered"
    if hass.data.get(service_key):
        _LOGGER.debug(
            "Service %s.%s already registered, skipping",
            DOMAIN,
            SERVICE_CLEANUP_ENERGY_SPIKES,
        )
        return

    async def handle_cleanup_energy_spikes(call: ServiceCall) -> dict[str, Any]:
        """Handle the service call."""
        _LOGGER.info("Service called with data: %s", call.data)
        config_entry_id = call.data["config_entry_id"]
        days_back = call.data.get("days_back", 1)
        dry_run = call.data.get("dry_run", True)
        _LOGGER.info(
            "Parsed values: config_entry_id=%s, days_back=%s, dry_run=%s",
            config_entry_id,
            days_back,
            dry_run,
        )

        return await cleanup_energy_spikes(
            hass, config_entry_id=config_entry_id, days_back=days_back, dry_run=dry_run
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_ENERGY_SPIKES,
        handle_cleanup_energy_spikes,
        schema=SERVICE_CLEANUP_ENERGY_SPIKES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.data[service_key] = True
    _LOGGER.debug("Registered %s.%s service", DOMAIN, SERVICE_CLEANUP_ENERGY_SPIKES)


async def cleanup_energy_spikes(
    hass: HomeAssistant,
    config_entry_id: str,
    days_back: int = 1,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Detect and remove firmware reset spikes from a specific SPAN panel's energy sensors.

    Uses the panel's main meter to detect reset timestamps, then deletes
    entries only for that panel's sensors.

    Args:
        hass: Home Assistant instance
        config_entry_id: Config entry ID of the SPAN panel to process
        days_back: How many days to scan (default: 1)
        dry_run: Preview mode without making changes (default: True)

    Returns:
        Summary of spikes found and removed for the specified panel.

    """
    _LOGGER.warning(
        "SERVICE CALLED: cleanup_energy_spikes - config_entry_id: %s, days_back: %s, dry_run: %s",
        config_entry_id,
        days_back,
        dry_run,
    )

    # Validate config entry exists and is a SPAN panel
    span_entries = {entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)}
    if config_entry_id not in span_entries:
        _LOGGER.error("Config entry %s is not a SPAN panel integration", config_entry_id)
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": 0,
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "error": f"Config entry {config_entry_id} is not a SPAN panel integration",
        }

    # Calculate time range
    end_time = dt_util.utcnow()
    start_time = end_time - timedelta(days=days_back)

    # Get all SPAN energy sensors (TOTAL_INCREASING only)
    all_span_sensors = _get_span_energy_sensors(hass)

    if not all_span_sensors:
        _LOGGER.warning("No SPAN energy sensors found")
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": 0,
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "error": "No SPAN energy sensors found",
        }

    # Group sensors by config entry and filter to the target entry
    sensors_by_entry = _group_sensors_by_config_entry(hass, all_span_sensors)

    if config_entry_id not in sensors_by_entry:
        _LOGGER.warning("No sensors found for config entry %s", config_entry_id)
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": 0,
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "error": f"No sensors found for config entry {config_entry_id}",
        }

    entry_sensors = sensors_by_entry[config_entry_id]
    _LOGGER.info(
        "Found %d SPAN energy sensors for panel (config entry: %s)",
        len(entry_sensors),
        config_entry_id,
    )

    # Find the main meter for this specific panel
    main_meter_entity = _find_main_meter_sensor(entry_sensors)

    if not main_meter_entity:
        _LOGGER.warning(
            "No main meter found for config entry %s",
            config_entry_id,
        )
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": len(entry_sensors),
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "error": f"No main meter sensor found for config entry {config_entry_id}",
        }

    _LOGGER.debug("Using main meter sensor: %s", main_meter_entity)

    # Get statistics for this panel's main meter to find reset timestamps
    try:
        reset_timestamps = await _find_reset_timestamps(
            hass, main_meter_entity, start_time, end_time
        )
    except Exception as e:
        _LOGGER.error(
            "Error finding reset timestamps for %s: %s", main_meter_entity, e, exc_info=True
        )
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": len(entry_sensors),
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "error": f"Failed to query statistics: {e}",
        }

    if not reset_timestamps:
        _LOGGER.info("No firmware reset spikes detected for panel %s", config_entry_id)
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": len(entry_sensors),
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "message": "No firmware reset spikes detected.",
        }

    _LOGGER.info(
        "Found %d reset timestamp(s) for panel %s",
        len(reset_timestamps),
        config_entry_id,
    )

    # Collect spike details for this panel's sensors
    details = await _collect_spike_details(
        hass, entry_sensors, reset_timestamps, start_time, end_time
    )

    sensors_adjusted = 0
    adjustment_error: str | None = None

    # Adjust statistics if not in dry run mode
    if not dry_run:
        _LOGGER.warning(
            "ADJUST MODE: Adjusting statistics for %d sensors at %d timestamps",
            len(entry_sensors),
            len(reset_timestamps),
        )
        try:
            sensors_adjusted = await _adjust_statistics_sums(
                hass, entry_sensors, reset_timestamps, start_time, end_time
            )
            _LOGGER.info("Adjusted %d sensors", sensors_adjusted)
        except Exception as e:
            _LOGGER.error(
                "Error adjusting statistics: %s",
                e,
                exc_info=True,
            )
            adjustment_error = str(e)

    # Build result
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "config_entry_id": config_entry_id,
        "entities_processed": len(entry_sensors),
        "reset_timestamps": [
            {
                "utc": ts.isoformat(),
                "local": dt_util.as_local(ts).isoformat(),
            }
            for ts in reset_timestamps
        ],
        "sensors_adjusted": sensors_adjusted,
        "details": details,
    }

    if adjustment_error:
        result["error"] = adjustment_error
        result["message"] = (
            f"Found {len(reset_timestamps)} spike(s) but adjustment failed: {adjustment_error}"
        )
    elif dry_run:
        result["message"] = (
            f"Found {len(reset_timestamps)} spike(s) affecting {len(entry_sensors)} sensors. "
            f"Run with dry_run: false to adjust sums and remove spikes."
        )
    else:
        result["message"] = (
            f"Adjusted {sensors_adjusted} sensors at {len(reset_timestamps)} "
            f"timestamp(s). Spikes should now be removed from Energy Dashboard."
        )

    return result


def _get_span_energy_sensors(hass: HomeAssistant) -> list[str]:
    """Get all SPAN energy sensors with TOTAL_INCREASING state class."""
    span_energy_sensors = []

    for entity_id in hass.states.async_entity_ids("sensor"):
        if not entity_id.startswith("sensor.span_panel_"):
            continue

        state = hass.states.get(entity_id)
        if state is None:
            continue

        state_class = state.attributes.get("state_class")
        if state_class == SensorStateClass.TOTAL_INCREASING:
            span_energy_sensors.append(entity_id)
            _LOGGER.debug("Found TOTAL_INCREASING sensor: %s", entity_id)

    return span_energy_sensors


def _group_sensors_by_config_entry(
    hass: HomeAssistant, sensor_list: list[str]
) -> dict[str, list[str]]:
    """Group sensors by their config entry ID.

    This allows processing each SPAN panel independently since they may
    reset at different times.

    Args:
        hass: Home Assistant instance
        sensor_list: List of entity IDs to group

    Returns:
        Dict mapping config_entry_id to list of entity IDs.
        Sensors without a config entry are grouped under "unknown".

    """
    registry = er.async_get(hass)
    grouped: dict[str, list[str]] = {}

    for entity_id in sensor_list:
        entry = registry.async_get(entity_id)
        config_entry_id = entry.config_entry_id if entry else None

        if config_entry_id is None:
            config_entry_id = "unknown"

        if config_entry_id not in grouped:
            grouped[config_entry_id] = []
        grouped[config_entry_id].append(entity_id)

    _LOGGER.debug(
        "Grouped %d sensors into %d config entries: %s",
        len(sensor_list),
        len(grouped),
        {k: len(v) for k, v in grouped.items()},
    )

    return grouped


def _find_main_meter_sensor(span_energy_sensors: list[str]) -> str | None:
    """Find the main meter consumed energy sensor from the list."""
    # Look for the main meter consumed energy sensor
    # Common patterns: main_meter_consumed_energy, consumed_energy (panel level)
    for entity_id in span_energy_sensors:
        if "main_meter" in entity_id and "consumed" in entity_id:
            return entity_id

    # Fallback: look for any main meter energy sensor
    for entity_id in span_energy_sensors:
        if "main_meter" in entity_id and "energy" in entity_id:
            return entity_id

    return None


async def _find_reset_timestamps(
    hass: HomeAssistant,
    main_meter_entity: str,
    start_time: datetime,
    end_time: datetime,
) -> list[datetime]:
    """Find timestamps where the main meter value decreased (firmware reset).

    Uses HOURLY statistics to match what the Energy Dashboard displays.
    The Energy Dashboard shows hourly bars where each bar = sum[hour+1] - sum[hour].
    A spike in the "X:00 - Y:00" bar means the entry at hour Y has a problematic value.

    Returns the timestamps of entries that should be deleted to remove dashboard spikes.

    Raises:
        Exception: If statistics query fails.

    """
    reset_timestamps: list[datetime] = []

    # Query HOURLY statistics - this matches what the Energy Dashboard displays
    # Each hourly entry's sum is used to calculate the bar: bar[X-Y] = sum[Y] - sum[X]
    stats = await get_instance(hass).async_add_executor_job(
        _query_statistics,
        hass,
        start_time,
        end_time,
        {main_meter_entity},
        "hour",  # Use hourly to match Energy Dashboard display
    )

    if not stats or main_meter_entity not in stats:
        _LOGGER.debug("No hourly statistics found for %s", main_meter_entity)
        return reset_timestamps

    sensor_stats = stats[main_meter_entity]
    if len(sensor_stats) < 2:
        _LOGGER.debug("Not enough hourly statistics entries to detect resets")
        return reset_timestamps

    # Look for any decrease in the cumulative value (sum)
    # A decrease means the hour entry after the reset has a lower sum than before
    for i in range(1, len(sensor_stats)):
        current = sensor_stats[i]
        previous = sensor_stats[i - 1]

        current_sum = current.get("sum")
        previous_sum = previous.get("sum")

        if current_sum is None or previous_sum is None:
            continue

        delta = current_sum - previous_sum

        # Any negative delta in a TOTAL_INCREASING sensor = firmware reset
        # The Energy Dashboard shows this as a negative spike in the bar ending at current_time
        if delta < 0:
            reset_time = dt_util.utc_from_timestamp(current["start"])
            reset_timestamps.append(reset_time)
            _LOGGER.info(
                "Detected firmware reset spike in hourly stats at %s: %s -> %s (delta: %s Wh). "
                "Energy Dashboard shows this as spike in hour ending at this time.",
                reset_time.isoformat(),
                previous_sum,
                current_sum,
                delta,
            )

    return reset_timestamps


def _query_statistics(
    hass: HomeAssistant,
    start_time: datetime,
    end_time: datetime,
    entity_ids: set[str],
    period: Literal["5minute", "hour", "day", "week", "month"],
) -> dict[str, list[dict[str, Any]]]:
    """Query statistics from recorder (runs in executor)."""
    result = statistics_during_period(
        hass,
        start_time,
        end_time,
        statistic_ids=entity_ids,
        period=period,
        units=None,
        types={"sum", "state"},
    )
    # Convert to plain dict for type compatibility
    return {k: [dict(row) for row in v] for k, v in result.items()}


async def _collect_spike_details(
    hass: HomeAssistant,
    span_energy_sensors: list[str],
    reset_timestamps: list[datetime],
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """Collect detailed information about spikes at reset timestamps."""
    details: list[dict[str, Any]] = []

    try:
        # Query statistics for all sensors
        stats = await get_instance(hass).async_add_executor_job(
            _query_statistics,
            hass,
            start_time,
            end_time,
            set(span_energy_sensors),
            "5minute",
        )

        for entity_id in span_energy_sensors:
            if entity_id not in stats:
                continue

            sensor_stats = stats[entity_id]
            spikes: list[dict[str, Any]] = []

            # Find entries at reset timestamps
            for stat_entry in sensor_stats:
                entry_time = dt_util.utc_from_timestamp(stat_entry["start"])

                for reset_time in reset_timestamps:
                    # Match within 2 minutes to identify the specific problematic entry
                    # This matches our narrower deletion window (statistics are 5-minute periods)
                    if abs((entry_time - reset_time).total_seconds()) <= 120:
                        # Find the previous entry to show the drop
                        idx = sensor_stats.index(stat_entry)
                        current_val = stat_entry.get("sum")

                        # Skip if current value is None
                        if current_val is None:
                            continue

                        # Try to get previous entry for delta calculation
                        if idx > 0:
                            prev_entry = sensor_stats[idx - 1]
                            prev_val = prev_entry.get("sum")

                            if prev_val is not None:
                                delta = current_val - prev_val
                                spikes.append(
                                    {
                                        "timestamp_utc": entry_time.isoformat(),
                                        "timestamp_local": dt_util.as_local(entry_time).isoformat(),
                                        "current_value": current_val,
                                        "previous_value": prev_val,
                                        "delta": delta,
                                    }
                                )
                            else:
                                # Previous entry exists but has no value
                                spikes.append(
                                    {
                                        "timestamp_utc": entry_time.isoformat(),
                                        "timestamp_local": dt_util.as_local(entry_time).isoformat(),
                                        "current_value": current_val,
                                        "previous_value": None,
                                        "delta": None,
                                    }
                                )
                        else:
                            # First entry - no previous value to compare
                            # Still include it in preview since it will be deleted
                            spikes.append(
                                {
                                    "timestamp_utc": entry_time.isoformat(),
                                    "timestamp_local": dt_util.as_local(entry_time).isoformat(),
                                    "current_value": current_val,
                                    "previous_value": None,
                                    "delta": None,
                                    "note": "First entry in query range",
                                }
                            )

            if spikes:
                details.append(
                    {
                        "entity_id": entity_id,
                        "spikes": spikes,
                    }
                )

    except Exception as e:
        _LOGGER.error("Error collecting spike details: %s", e, exc_info=True)

    return details


async def _adjust_statistics_sums(
    hass: HomeAssistant,
    span_energy_sensors: list[str],
    reset_timestamps: list[datetime],
    start_time: datetime,
    end_time: datetime,
) -> int:
    """Adjust statistics sums to remove spikes caused by firmware resets.

    For each sensor at each reset timestamp, calculates the drop amount and
    calls recorder.adjust_sum_statistics to add back the dropped value.
    This propagates to all subsequent entries, making sums continuous.

    Returns the number of sensors adjusted.
    """
    sensors_adjusted = 0
    _LOGGER.warning(
        "ADJUST: Starting adjustment for %d sensors at %d timestamps",
        len(span_energy_sensors),
        len(reset_timestamps),
    )

    # Query statistics for all sensors to find drop amounts
    try:
        stats = await get_instance(hass).async_add_executor_job(
            _query_statistics,
            hass,
            start_time,
            end_time,
            set(span_energy_sensors),
            "hour",  # Use hourly stats to match detection
        )
    except Exception as e:
        _LOGGER.error("Error querying statistics for adjustment: %s", e)
        raise

    for entity_id in span_energy_sensors:
        if entity_id not in stats:
            _LOGGER.debug("No statistics found for %s", entity_id)
            continue

        sensor_stats = stats[entity_id]
        if len(sensor_stats) < 2:
            continue

        for reset_time in reset_timestamps:
            _LOGGER.debug(
                "Looking for reset timestamp %s in %s stats",
                reset_time.isoformat(),
                entity_id,
            )
            # Find the entry at or near the reset timestamp
            for i, stat_entry in enumerate(sensor_stats):
                entry_time = dt_util.utc_from_timestamp(stat_entry["start"])
                time_diff = abs((entry_time - reset_time).total_seconds())

                # Match within 2 minutes
                if time_diff <= 120:
                    current_sum = stat_entry.get("sum")
                    _LOGGER.debug(
                        "Found matching entry at %s (diff: %.1fs), sum=%.2f",
                        entry_time.isoformat(),
                        time_diff,
                        current_sum if current_sum else 0,
                    )

                    # Get previous entry
                    if i > 0 and current_sum is not None:
                        prev_entry = sensor_stats[i - 1]
                        prev_sum = prev_entry.get("sum")

                        if prev_sum is not None and prev_sum > current_sum:
                            # Calculate adjustment needed (add back the drop)
                            adjustment = prev_sum - current_sum

                            _LOGGER.warning(
                                "ADJUST: Found spike in %s: prev_sum=%.2f, "
                                "current_sum=%.2f, adjustment=+%.2f",
                                entity_id,
                                prev_sum,
                                current_sum,
                                adjustment,
                            )

                            # Call the recorder's async_adjust_statistics directly
                            # Energy sensors use kWh as unit
                            _LOGGER.warning(
                                "ADJUST: Adjusting %s at %s: +%.2f kWh",
                                entity_id,
                                entry_time.isoformat(),
                                adjustment,
                            )
                            try:
                                get_instance(hass).async_adjust_statistics(
                                    statistic_id=entity_id,
                                    start_time=entry_time,
                                    sum_adjustment=float(adjustment),
                                    adjustment_unit="kWh",
                                )
                                sensors_adjusted += 1
                                _LOGGER.warning(
                                    "ADJUST: Successfully queued adjustment for %s", entity_id
                                )
                            except Exception as e:
                                _LOGGER.error("ADJUST FAILED: %s: %s", entity_id, e, exc_info=True)
                    break  # Found the reset entry for this timestamp

    _LOGGER.warning("ADJUST: Completed - adjusted %d sensors", sensors_adjusted)
    return sensors_adjusted


async def _delete_statistics_entries(
    hass: HomeAssistant,
    span_energy_sensors: list[str],
    reset_timestamps: list[datetime],
) -> int:
    """Delete statistics entries at the specified timestamps.

    Returns the number of entries deleted.

    Raises:
        Exception: If deletion fails.

    """
    _LOGGER.info("_delete_statistics_entries called")

    recorder = get_instance(hass)
    _LOGGER.info("Got recorder instance, calling sync delete function")

    # Run deletion in executor thread (required for database operations)
    entries_deleted = await recorder.async_add_executor_job(
        _delete_statistics_entries_sync,
        hass,
        span_energy_sensors,
        reset_timestamps,
    )
    _LOGGER.info("Sync delete returned %d", entries_deleted)

    return entries_deleted


def _delete_statistics_entries_sync(
    hass: HomeAssistant,
    span_energy_sensors: list[str],
    reset_timestamps: list[datetime],
) -> int:
    """Delete statistics entries synchronously (runs in executor)."""
    entries_deleted = 0

    _LOGGER.info(
        "Starting deletion for %d sensors at %d reset timestamp(s)",
        len(span_energy_sensors),
        len(reset_timestamps),
    )

    # Use session_scope for proper transaction handling (commits on exit, rollback on error)
    try:
        with session_scope(hass=hass) as session:
            # Get metadata IDs for all sensors
            metadata_query = session.query(StatisticsMeta).filter(
                StatisticsMeta.statistic_id.in_(span_energy_sensors)
            )
            metadata_map = {m.statistic_id: m.id for m in metadata_query.all()}

            _LOGGER.info(
                "Found %d metadata entries for %d sensors",
                len(metadata_map),
                len(span_energy_sensors),
            )

            # Log missing metadata entries
            missing_metadata = set(span_energy_sensors) - set(metadata_map.keys())
            if missing_metadata:
                _LOGGER.warning(
                    "Missing metadata entries for %d sensor(s): %s",
                    len(missing_metadata),
                    missing_metadata,
                )

            for reset_time in reset_timestamps:
                # Convert to timestamp for comparison
                # reset_time is already hour-aligned from hourly stats detection
                reset_ts = reset_time.timestamp()

                # The Energy Dashboard calculates bar "X:00 - Y:00" = sum[Y:00] - sum[X:00]
                # A firmware reset creates a discontinuity in sum values:
                #   - Before reset: high cumulative sum
                #   - After reset: low cumulative sum (panel counter reset)
                #
                # To fully remove the spike, we need to delete entries so that NO bar
                # can span from a "high" entry to a "low" entry. The dashboard won't
                # show bars for periods with missing data.
                #
                # Strategy: Delete a window of entries covering:
                #   - 2 hours BEFORE the detected reset (captures the last "high" entries)
                #   - The reset hour itself
                #   - 2 hours AFTER the reset (captures early "low" entries and any recovery spike)
                # Total: ~5 hour window to create a clean gap

                window_before = 2 * 3600  # 2 hours before
                window_after = 2 * 3600  # 2 hours after

                # For short-term stats (5-minute), delete within the same window
                short_term_window_start = reset_ts - window_before
                short_term_window_end = reset_ts + window_after

                # For long-term (hourly) stats, same window with small buffer
                long_term_window_start = reset_ts - window_before - 120
                long_term_window_end = reset_ts + window_after + 120

                # Log in local time for user visibility
                local_time = dt_util.as_local(reset_time)
                start_local = dt_util.as_local(dt_util.utc_from_timestamp(reset_ts - window_before))
                end_local = dt_util.as_local(dt_util.utc_from_timestamp(reset_ts + window_after))
                _LOGGER.info(
                    "Processing spike at %s (local: %s). "
                    "Will delete entries in 5-hour window from %s to %s.",
                    reset_time.isoformat(),
                    local_time.isoformat(),
                    start_local.isoformat(),
                    end_local.isoformat(),
                )

                for entity_id in span_energy_sensors:
                    if entity_id not in metadata_map:
                        _LOGGER.debug(
                            "Skipping %s - no metadata entry found",
                            entity_id,
                        )
                        continue

                    metadata_id = metadata_map[entity_id]

                    # First, query to see what entries exist (for debugging)
                    # Short-term uses 5-minute window, long-term uses hour-aligned window
                    short_term_entries = (
                        session.query(StatisticsShortTerm)
                        .filter(
                            StatisticsShortTerm.metadata_id == metadata_id,
                            StatisticsShortTerm.start_ts >= short_term_window_start,
                            StatisticsShortTerm.start_ts <= short_term_window_end,
                        )
                        .all()
                    )
                    short_term_count = len(short_term_entries)

                    long_term_entries = (
                        session.query(Statistics)
                        .filter(
                            Statistics.metadata_id == metadata_id,
                            Statistics.start_ts >= long_term_window_start,
                            Statistics.start_ts <= long_term_window_end,
                        )
                        .all()
                    )
                    long_term_count = len(long_term_entries)

                    _LOGGER.info(
                        "Found %d short-term and %d long-term entries for %s at %s (local: %s)",
                        short_term_count,
                        long_term_count,
                        entity_id,
                        reset_time.isoformat(),
                        dt_util.as_local(reset_time).isoformat(),
                    )

                    # Log actual timestamps found for debugging
                    if short_term_entries:
                        _LOGGER.debug(
                            "Short-term entry timestamps: %s",
                            [entry.start_ts for entry in short_term_entries[:5]],  # First 5
                        )
                    if long_term_entries:
                        _LOGGER.debug(
                            "Long-term entry timestamps: %s",
                            [entry.start_ts for entry in long_term_entries[:5]],  # First 5
                        )

                    # Delete from short-term statistics (5-minute window)
                    deleted_short = (
                        session.query(StatisticsShortTerm)
                        .filter(
                            StatisticsShortTerm.metadata_id == metadata_id,
                            StatisticsShortTerm.start_ts >= short_term_window_start,
                            StatisticsShortTerm.start_ts <= short_term_window_end,
                        )
                        .delete(synchronize_session=False)
                    )

                    # Delete from long-term statistics (hour-aligned window)
                    deleted_long = (
                        session.query(Statistics)
                        .filter(
                            Statistics.metadata_id == metadata_id,
                            Statistics.start_ts >= long_term_window_start,
                            Statistics.start_ts <= long_term_window_end,
                        )
                        .delete(synchronize_session=False)
                    )

                    # Flush to ensure deletes are processed before commit
                    session.flush()

                    total_deleted = deleted_short + deleted_long
                    if total_deleted > 0:
                        _LOGGER.info(
                            "Deleted %d entries for %s at %s (local: %s) - short: %d, long: %d",
                            total_deleted,
                            entity_id,
                            reset_time.isoformat(),
                            dt_util.as_local(reset_time).isoformat(),
                            deleted_short,
                            deleted_long,
                        )
                        entries_deleted += total_deleted
                    else:
                        _LOGGER.warning(
                            "No entries deleted for %s at %s (local: %s) despite finding "
                            "%d short-term entries (window: %.1f-%.1f) and "
                            "%d long-term entries (window: %.1f-%.1f)",
                            entity_id,
                            reset_time.isoformat(),
                            dt_util.as_local(reset_time).isoformat(),
                            short_term_count,
                            short_term_window_start,
                            short_term_window_end,
                            long_term_count,
                            long_term_window_start,
                            long_term_window_end,
                        )

            _LOGGER.info("Committed deletion of %d statistics entries", entries_deleted)

    except Exception as e:
        _LOGGER.error(
            "Error during statistics deletion: %s",
            e,
            exc_info=True,
        )
        raise

    return entries_deleted
