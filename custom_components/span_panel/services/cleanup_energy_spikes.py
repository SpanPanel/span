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
from homeassistant.helpers import config_validation as cv
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
        vol.Optional("days_back", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
        vol.Optional("dry_run", default=True): cv.boolean,
    }
)


async def async_setup_cleanup_energy_spikes_service(hass: HomeAssistant) -> None:
    """Register the cleanup_energy_spikes service."""

    async def handle_cleanup_energy_spikes(call: ServiceCall) -> dict[str, Any]:
        """Handle the service call."""
        _LOGGER.info("Service called with data: %s", call.data)
        days_back = call.data.get("days_back", 1)
        dry_run = call.data.get("dry_run", True)
        _LOGGER.info(
            "Parsed values: days_back=%s, dry_run=%s (type=%s)",
            days_back,
            dry_run,
            type(dry_run).__name__,
        )

        return await cleanup_energy_spikes(hass, days_back=days_back, dry_run=dry_run)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_ENERGY_SPIKES,
        handle_cleanup_energy_spikes,
        schema=SERVICE_CLEANUP_ENERGY_SPIKES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.debug("Registered %s.%s service", DOMAIN, SERVICE_CLEANUP_ENERGY_SPIKES)


async def cleanup_energy_spikes(
    hass: HomeAssistant,
    days_back: int = 1,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Detect and remove firmware reset spikes from all SPAN energy sensors.

    Uses the main meter to detect reset timestamps (any decrease in value),
    then deletes all SPAN TOTAL_INCREASING sensor entries at those timestamps.

    Args:
        hass: Home Assistant instance
        days_back: How many days to scan (default: 1)
        dry_run: Preview mode without making changes (default: True)

    Returns:
        Summary of spikes found and removed.

    """
    _LOGGER.info(
        "Starting energy spike cleanup - days_back: %s, dry_run: %s",
        days_back,
        dry_run,
    )

    # Calculate time range
    end_time = dt_util.utcnow()
    start_time = end_time - timedelta(days=days_back)

    # Get all SPAN energy sensors (TOTAL_INCREASING only)
    span_energy_sensors = _get_span_energy_sensors(hass)

    if not span_energy_sensors:
        _LOGGER.warning("No SPAN energy sensors found")
        return {
            "dry_run": dry_run,
            "entities_processed": 0,
            "reset_timestamps": [],
            "entries_deleted": 0,
            "details": [],
            "error": "No SPAN energy sensors found",
        }

    # Find the main meter consumed energy sensor
    main_meter_entity = _find_main_meter_sensor(span_energy_sensors)

    if not main_meter_entity:
        _LOGGER.warning("Main meter consumed energy sensor not found")
        return {
            "dry_run": dry_run,
            "entities_processed": 0,
            "reset_timestamps": [],
            "entries_deleted": 0,
            "details": [],
            "error": "Main meter consumed energy sensor not found",
        }

    _LOGGER.debug("Using main meter sensor: %s", main_meter_entity)
    _LOGGER.debug("Found %d SPAN energy sensors", len(span_energy_sensors))

    # Get statistics for the main meter to find reset timestamps
    try:
        reset_timestamps = await _find_reset_timestamps(
            hass, main_meter_entity, start_time, end_time
        )
    except Exception as e:
        _LOGGER.error("Error finding reset timestamps: %s", e, exc_info=True)
        return {
            "dry_run": dry_run,
            "entities_processed": 0,
            "reset_timestamps": [],
            "entries_deleted": 0,
            "details": [],
            "error": f"Failed to query statistics: {e}",
        }

    if not reset_timestamps:
        _LOGGER.info("No firmware reset spikes detected in the specified time range")
        return {
            "dry_run": dry_run,
            "entities_processed": len(span_energy_sensors),
            "reset_timestamps": [],
            "entries_deleted": 0,
            "details": [],
            "message": "No firmware reset spikes detected",
        }

    _LOGGER.info("Found %d reset timestamp(s)", len(reset_timestamps))

    # Collect spike details for all affected sensors
    details = await _collect_spike_details(
        hass, span_energy_sensors, reset_timestamps, start_time, end_time
    )

    # Delete entries if not in dry run mode
    entries_deleted = 0
    deletion_error: str | None = None
    _LOGGER.info("dry_run=%s, will delete=%s", dry_run, not dry_run)
    if not dry_run:
        _LOGGER.info(
            "Calling _delete_statistics_entries with %d sensors and %d timestamps",
            len(span_energy_sensors),
            len(reset_timestamps),
        )
        try:
            entries_deleted = await _delete_statistics_entries(
                hass, span_energy_sensors, reset_timestamps
            )
            _LOGGER.info("Deleted %d statistics entries", entries_deleted)
        except Exception as e:
            _LOGGER.error("Error deleting statistics entries: %s", e, exc_info=True)
            deletion_error = str(e)

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "entities_processed": len(span_energy_sensors),
        "reset_timestamps": [
            {
                "utc": ts.isoformat(),
                "local": dt_util.as_local(ts).isoformat(),
            }
            for ts in reset_timestamps
        ],
        "entries_deleted": entries_deleted,
        "details": details,
    }

    if deletion_error:
        result["error"] = f"Failed to delete statistics entries: {deletion_error}"
        result["message"] = (
            f"Found {len(reset_timestamps)} spike(s) but deletion failed. "
            "Check Home Assistant logs for details."
        )
    elif dry_run:
        result["message"] = (
            f"Would delete {len(reset_timestamps)} spike(s) "
            f"from {len(span_energy_sensors)} sensors. "
            "Run with dry_run: false to apply."
        )
    else:
        result["message"] = (
            f"Deleted {entries_deleted} entries at {len(reset_timestamps)} "
            f"timestamp(s) from {len(span_energy_sensors)} sensors."
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

    These timestamps indicate firmware resets that affect all sensors.

    Raises:
        Exception: If statistics query fails.

    """
    reset_timestamps: list[datetime] = []

    # Query statistics for the main meter
    # Use 5-minute period for more granular detection
    stats = await get_instance(hass).async_add_executor_job(
        _query_statistics,
        hass,
        start_time,
        end_time,
        {main_meter_entity},
        "5minute",
    )

    if not stats or main_meter_entity not in stats:
        _LOGGER.debug("No statistics found for %s", main_meter_entity)
        return reset_timestamps

    sensor_stats = stats[main_meter_entity]
    if len(sensor_stats) < 2:
        _LOGGER.debug("Not enough statistics entries to detect resets")
        return reset_timestamps

    # Look for any decrease in the cumulative value (sum)
    for i in range(1, len(sensor_stats)):
        current = sensor_stats[i]
        previous = sensor_stats[i - 1]

        current_sum = current.get("sum")
        previous_sum = previous.get("sum")

        if current_sum is None or previous_sum is None:
            continue

        delta = current_sum - previous_sum

        # Any negative delta in a TOTAL_INCREASING sensor = firmware reset
        if delta < 0:
            # The reset itself (decrease) is not the visible spike - it's the recovery that creates the spike
            # Check if the next entry shows a large recovery spike (the visible spike in the chart)
            if i + 1 < len(sensor_stats):
                next_entry = sensor_stats[i + 1]
                next_sum = next_entry.get("sum")
                if next_sum is not None:
                    recovery_delta = next_sum - current_sum
                    # The recovery creates a huge positive delta (visible spike in Energy Dashboard)
                    # Only delete the recovery entry (the spike), not the reset entry
                    if (
                        recovery_delta > abs(delta) * 1.2
                    ):  # Recovery is significantly larger than the reset
                        recovery_time = dt_util.utc_from_timestamp(next_entry["start"])
                        reset_timestamps.append(recovery_time)
                        _LOGGER.info(
                            "Detected firmware reset spike at %s: reset was %s -> %s (delta: %s Wh), "
                            "recovery spike %s -> %s (delta: %s Wh) - deleting recovery entry to remove visible spike",
                            recovery_time.isoformat(),
                            previous_sum,
                            current_sum,
                            delta,
                            current_sum,
                            next_sum,
                            recovery_delta,
                        )
                    else:
                        # If no large recovery spike, the reset itself might be the visible issue
                        reset_time = dt_util.utc_from_timestamp(current["start"])
                        reset_timestamps.append(reset_time)
                        _LOGGER.info(
                            "Detected firmware reset at %s: %s -> %s (delta: %s Wh, no large recovery spike)",
                            reset_time.isoformat(),
                            previous_sum,
                            current_sum,
                            delta,
                        )
            else:
                # No next entry, delete the reset entry itself
                reset_time = dt_util.utc_from_timestamp(current["start"])
                reset_timestamps.append(reset_time)
                _LOGGER.info(
                    "Detected firmware reset at %s: %s -> %s (delta: %s Wh, no recovery entry found)",
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

    # Get the delete function - we need to use the recorder's internal APIs
    entries_deleted = await recorder.async_add_executor_job(
        _delete_statistics_entries_sync,
        hass,
        recorder,
        span_energy_sensors,
        reset_timestamps,
    )
    _LOGGER.info("Sync delete returned %d", entries_deleted)

    return entries_deleted


def _delete_statistics_entries_sync(
    hass: HomeAssistant,
    recorder: Any,
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

    # Use session_scope context manager for proper session management
    # get_session() returns a Session object directly
    try:
        with session_scope(session=recorder.get_session()) as session:
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
                # reset_time is already UTC datetime, timestamp() gives UTC Unix timestamp
                reset_ts = reset_time.timestamp()

                # Use a narrow window (2 minutes) to only delete the specific problematic entry
                # Statistics are stored in 5-minute periods, so we target the period containing
                # the reset timestamp. A 2-minute window ensures we catch the right period
                # even with slight timestamp variations, while minimizing gaps in the chart.
                window_start = reset_ts - 120  # 2 minutes before
                window_end = reset_ts + 120  # 2 minutes after

                # Log in local time for user visibility, but use UTC for database operations
                local_time = dt_util.as_local(reset_time)
                _LOGGER.info(
                    "Processing reset at %s (local: %s, UTC ts=%.1f), deletion window: %.1f to %.1f (2 minutes)",
                    reset_time.isoformat(),
                    local_time.isoformat(),
                    reset_ts,
                    window_start,
                    window_end,
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
                    # Get actual entries to log their timestamps
                    short_term_entries = (
                        session.query(StatisticsShortTerm)
                        .filter(
                            StatisticsShortTerm.metadata_id == metadata_id,
                            StatisticsShortTerm.start_ts >= window_start,
                            StatisticsShortTerm.start_ts <= window_end,
                        )
                        .all()
                    )
                    short_term_count = len(short_term_entries)

                    long_term_entries = (
                        session.query(Statistics)
                        .filter(
                            Statistics.metadata_id == metadata_id,
                            Statistics.start_ts >= window_start,
                            Statistics.start_ts <= window_end,
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

                    # Delete from short-term statistics
                    deleted_short = (
                        session.query(StatisticsShortTerm)
                        .filter(
                            StatisticsShortTerm.metadata_id == metadata_id,
                            StatisticsShortTerm.start_ts >= window_start,
                            StatisticsShortTerm.start_ts <= window_end,
                        )
                        .delete(synchronize_session=False)
                    )

                    # Delete from long-term statistics
                    deleted_long = (
                        session.query(Statistics)
                        .filter(
                            Statistics.metadata_id == metadata_id,
                            Statistics.start_ts >= window_start,
                            Statistics.start_ts <= window_end,
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
                            "No entries deleted for %s at %s (local: %s) despite finding %d short-term and %d long-term entries (window: %.1f to %.1f)",
                            entity_id,
                            reset_time.isoformat(),
                            dt_util.as_local(reset_time).isoformat(),
                            short_term_count,
                            long_term_count,
                            window_start,
                            window_end,
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
