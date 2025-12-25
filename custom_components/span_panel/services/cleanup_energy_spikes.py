"""Cleanup energy spikes service for SPAN Panel integration.

This service detects and removes negative energy spikes from Home Assistant's
statistics database that occur when the SPAN panel undergoes firmware updates.

When the panel resets, it may temporarily report incorrect energy values,
causing massive spikes when it recovers. This service identifies those
timestamps and removes the problematic entries.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any, Literal

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
import voluptuous as vol

from custom_components.span_panel.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Service name
SERVICE_CLEANUP_ENERGY_SPIKES = "cleanup_energy_spikes"

# Lock for thread-safe service registration
_registration_lock = asyncio.Lock()

# Service schema
SERVICE_CLEANUP_ENERGY_SPIKES_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("start_time"): cv.datetime,
        vol.Required("end_time"): cv.datetime,
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

    # Use lock to prevent race condition in concurrent multi-panel setups
    async with _registration_lock:
        # Check again inside lock (double-check pattern)
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
            start_time = call.data["start_time"]
            end_time = call.data["end_time"]
            dry_run = call.data.get("dry_run", True)
            _LOGGER.info(
                "Parsed values: config_entry_id=%s, start_time=%s, end_time=%s, dry_run=%s",
                config_entry_id,
                start_time,
                end_time,
                dry_run,
            )

            return await cleanup_energy_spikes(
                hass,
                config_entry_id=config_entry_id,
                start_time=start_time,
                end_time=end_time,
                dry_run=dry_run,
            )

        try:
            hass.services.async_register(
                DOMAIN,
                SERVICE_CLEANUP_ENERGY_SPIKES,
                handle_cleanup_energy_spikes,
                schema=SERVICE_CLEANUP_ENERGY_SPIKES_SCHEMA,
                supports_response=SupportsResponse.OPTIONAL,
            )
            # Only set flag after successful registration
            hass.data[service_key] = True
            _LOGGER.debug("Registered %s.%s service", DOMAIN, SERVICE_CLEANUP_ENERGY_SPIKES)
        except Exception as e:
            _LOGGER.error(
                "Failed to register %s.%s service: %s",
                DOMAIN,
                SERVICE_CLEANUP_ENERGY_SPIKES,
                e,
            )
            raise


async def cleanup_energy_spikes(
    hass: HomeAssistant,
    config_entry_id: str,
    start_time: datetime,
    end_time: datetime,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Detect and remove firmware reset spikes from a specific SPAN panel's energy sensors.

    Uses the panel's main meter to detect reset timestamps, then deletes
    entries only for that panel's sensors within the specified time range.

    Args:
        hass: Home Assistant instance
        config_entry_id: Config entry ID of the SPAN panel to process
        start_time: Local start time for the time range to scan
        end_time: Local end time for the time range to scan
        dry_run: Preview mode without making changes (default: True)

    Returns:
        Summary of spikes found and removed for the specified panel.

    """
    _LOGGER.info(
        "SERVICE CALLED: cleanup_energy_spikes - config_entry_id: %s, start_time: %s, end_time: %s, dry_run: %s",
        config_entry_id,
        start_time,
        end_time,
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

    # Convert local time inputs to UTC for database queries
    # Input times are in local timezone (cv.datetime provides naive datetimes in local time)
    # Convert to UTC for database queries
    if start_time.tzinfo is None:
        # Naive datetime is assumed to be in local timezone
        # Get local timezone and localize the naive datetime
        local_tz = dt_util.get_time_zone(hass.config.time_zone)
        start_time_local = start_time.replace(tzinfo=local_tz)
        start_time_utc = dt_util.as_utc(start_time_local)
    else:
        # Already timezone-aware, convert to UTC
        start_time_utc = dt_util.as_utc(start_time)

    if end_time.tzinfo is None:
        # Naive datetime is assumed to be in local timezone
        # Get local timezone and localize the naive datetime
        local_tz = dt_util.get_time_zone(hass.config.time_zone)
        end_time_local = end_time.replace(tzinfo=local_tz)
        end_time_utc = dt_util.as_utc(end_time_local)
    else:
        # Already timezone-aware, convert to UTC
        end_time_utc = dt_util.as_utc(end_time)

    # Validate time range - allow start_time == end_time for exact spike targeting
    if start_time_utc > end_time_utc:
        _LOGGER.error(
            "Invalid time range: start_time (%s) must be before or equal to end_time (%s)",
            start_time,
            end_time,
        )
        return {
            "dry_run": dry_run,
            "config_entry_id": config_entry_id,
            "entities_processed": 0,
            "reset_timestamps": [],
            "sensors_adjusted": 0,
            "details": [],
            "error": "Invalid time range: start_time must be before or equal to end_time",
        }

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

    # Expand query window by 1 hour before start_time for spike detection
    # This ensures we can detect spikes AT start_time by having the previous entry to compare.
    # Example: If user specifies 6PM and spike is at 6PM, we need the 5PM entry to detect
    # the drop from 5PM -> 6PM. The actual spike reporting/adjustment will still respect
    # the user's original time range by filtering detected timestamps.
    query_start_time_utc = start_time_utc - timedelta(hours=1)

    # Also expand end_time by 1 hour to ensure we capture spikes at the exact end boundary
    query_end_time_utc = end_time_utc + timedelta(hours=1)

    _LOGGER.debug(
        "Expanded query window: user range=[%s to %s], query range=[%s to %s] (1 hour buffer)",
        start_time_utc.isoformat(),
        end_time_utc.isoformat(),
        query_start_time_utc.isoformat(),
        query_end_time_utc.isoformat(),
    )

    # Get statistics for this panel's main meter to find reset timestamps
    try:
        reset_timestamps = await _find_reset_timestamps(
            hass, main_meter_entity, query_start_time_utc, query_end_time_utc
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

    # Filter timestamps to only include those within the user's original time range
    # The expanded query window may have found spikes outside the requested range
    original_reset_timestamps = reset_timestamps
    reset_timestamps = [ts for ts in reset_timestamps if start_time_utc <= ts <= end_time_utc]

    if original_reset_timestamps and not reset_timestamps:
        _LOGGER.info(
            "Found %d spike(s) in expanded window, but none within user's requested range [%s to %s]",
            len(original_reset_timestamps),
            start_time_utc.isoformat(),
            end_time_utc.isoformat(),
        )

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
        "Found %d reset timestamp(s) for panel %s within requested range",
        len(reset_timestamps),
        config_entry_id,
    )

    # Collect spike details for this panel's sensors
    # Use expanded query window to ensure we have comparison data for spike detection
    details = await _collect_spike_details(
        hass, entry_sensors, reset_timestamps, query_start_time_utc, query_end_time_utc
    )

    adjustments_made = 0
    adjustments_list: list[dict[str, Any]] = []
    adjustment_error: str | None = None

    # Adjust statistics if not in dry run mode
    if not dry_run:
        _LOGGER.info(
            "ADJUST MODE: Adjusting statistics for %d sensors at %d timestamps",
            len(entry_sensors),
            len(reset_timestamps),
        )
        try:
            # Adjust all sensors that have negative spikes
            # Each sensor experiences its own drop during firmware reset and should be
            # corrected individually by adding back the exact negative delta it experienced
            sensors_with_spikes = {
                detail["entity_id"]
                for detail in details
                for spike in detail.get("spikes", [])
                if spike.get("delta") is not None and spike["delta"] < 0
            }

            _LOGGER.info(
                "ADJUST: Adjusting %d sensors with negative spikes: %s",
                len(sensors_with_spikes),
                sorted(sensors_with_spikes),
            )
            # Use expanded query window to ensure we have comparison data for adjustment
            adjustment_count, adjustment_records = await _adjust_statistics_sums(
                hass,
                list(sensors_with_spikes),
                reset_timestamps,
                query_start_time_utc,
                query_end_time_utc,
            )
            adjustments_made = adjustment_count
            adjustments_list = adjustment_records
            _LOGGER.info("Made %d adjustment(s) across sensors", adjustments_made)
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
        "sensors_adjusted": adjustments_made,  # Keep key name for backward compatibility
        "details": details,
    }

    # Include adjustments made (for potential reversal)
    if not dry_run and adjustments_list:
        result["adjustments"] = adjustments_list

    if adjustment_error:
        result["error"] = adjustment_error
        result["message"] = (
            f"Found {len(reset_timestamps)} spike(s) but adjustment failed: {adjustment_error}"
        )
    elif dry_run:
        result["message"] = (
            f"Found {len(reset_timestamps)} decrease(s) in energy value in main meter consumed sensor. "
            f"Will attempt fix across {len(entry_sensors)} TOTAL_INCREASING sensors. "
            f"Run with dry_run: false to apply corrections."
        )
    else:
        result["message"] = (
            f"Made {adjustments_made} adjustment(s) at {len(reset_timestamps)} "
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

        # Skip if either value is zero or unavailable
        # Zero values indicate the sensor hasn't started accumulating or data is invalid
        if current_sum == 0 or previous_sum == 0:
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
    """Collect detailed information about all negative deltas (drops) in sensors.

    Scans all entries chronologically to find all negative deltas, matching
    what the adjustment logic will actually fix. This provides an accurate
    preview of what will be adjusted.
    """
    details: list[dict[str, Any]] = []

    try:
        # Query statistics for all sensors
        # Use hourly stats to match detection and adjustment logic
        stats = await get_instance(hass).async_add_executor_job(
            _query_statistics,
            hass,
            start_time,
            end_time,
            set(span_energy_sensors),
            "hour",  # Match detection and adjustment period
        )

        for entity_id in span_energy_sensors:
            if entity_id not in stats:
                continue

            sensor_stats = stats[entity_id]
            if len(sensor_stats) < 2:
                continue

            spikes: list[dict[str, Any]] = []

            # Scan all entries chronologically to find all negative deltas (drops)
            # This matches what the adjustment logic does
            for i in range(1, len(sensor_stats)):
                current_entry = sensor_stats[i]
                prev_entry = sensor_stats[i - 1]

                current_sum = current_entry.get("sum")
                prev_sum = prev_entry.get("sum")

                if current_sum is None or prev_sum is None:
                    continue

                # Skip if either value is zero or unavailable
                # Zero values indicate the sensor hasn't started accumulating or data is invalid
                if current_sum == 0 or prev_sum == 0:
                    continue

                # Find negative deltas (drops) - these indicate firmware resets
                if prev_sum > current_sum:
                    # Validation: Skip if either value is negative (corrupted data)
                    if current_sum < 0 or prev_sum < 0:
                        continue

                    # Any negative delta in a TOTAL_INCREASING sensor is invalid
                    # Include all drops to match adjustment logic
                    entry_time = dt_util.utc_from_timestamp(current_entry["start"])
                    delta = current_sum - prev_sum

                    spikes.append(
                        {
                            "timestamp_utc": entry_time.isoformat(),
                            "timestamp_local": dt_util.as_local(entry_time).isoformat(),
                            "current_value": current_sum,
                            "previous_value": prev_sum,
                            "delta": delta,
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


async def _async_delay(hass: HomeAssistant, delay_seconds: float) -> None:
    """Delay execution using Home Assistant's async_call_later.

    Args:
        hass: Home Assistant instance
        delay_seconds: Number of seconds to delay

    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[None] = loop.create_future()

    def _callback(_now: datetime) -> None:
        if not future.done():
            future.set_result(None)

    async_call_later(hass, delay_seconds, _callback)
    await future


async def _adjust_statistics_sums(
    hass: HomeAssistant,
    span_energy_sensors: list[str],
    reset_timestamps: list[datetime],
    start_time: datetime,
    end_time: datetime,
) -> tuple[int, list[dict[str, Any]]]:
    """Adjust statistics sums to remove spikes caused by firmware resets.

    For each sensor, iteratively:
    1. Query statistics
    2. Find the first negative delta (drop) chronologically
    3. Adjust it (which propagates to all subsequent entries)
    4. Re-query statistics to see updated values
    5. Repeat until no more drops are found

    This iterative approach is necessary because adjustments propagate to
    subsequent entries, so we must re-query after each adjustment to see the
    updated state.

    Returns:
        Tuple of (number of adjustments made, list of adjustment records).
        Each adjustment record contains: entity_id, timestamp_utc, adjustment_wh

    """
    total_adjustments = 0
    adjustments_made: list[dict[str, Any]] = []
    _LOGGER.info(
        "ADJUST: Starting adjustment for %d sensors",
        len(span_energy_sensors),
    )

    # Process each sensor independently
    for entity_id in span_energy_sensors:
        adjustments_for_sensor = 0
        max_iterations = 100  # Safety limit to prevent infinite loops
        iteration = 0
        # Track which entry times we've already adjusted to avoid double-adjusting
        # (adjustments are queued and may not be reflected in re-queries immediately)
        adjusted_entry_times: set[datetime] = set()

        _LOGGER.debug("ADJUST: Processing sensor %s", entity_id)

        # Iteratively find and fix drops until none remain
        while iteration < max_iterations:
            iteration += 1

            # Query statistics for this sensor
            try:
                stats = await get_instance(hass).async_add_executor_job(
                    _query_statistics,
                    hass,
                    start_time,
                    end_time,
                    {entity_id},
                    "hour",  # Use hourly stats to match Energy Dashboard
                )
            except Exception as e:
                _LOGGER.error("Error querying statistics for %s: %s", entity_id, e)
                break

            if entity_id not in stats or not stats[entity_id]:
                _LOGGER.debug("No statistics found for %s", entity_id)
                break

            sensor_stats = stats[entity_id]
            if len(sensor_stats) < 2:
                break

            # Find the first drop chronologically that we haven't already adjusted
            drop_found = False
            for i in range(1, len(sensor_stats)):
                current_entry = sensor_stats[i]
                prev_entry = sensor_stats[i - 1]

                current_sum = current_entry.get("sum")
                prev_sum = prev_entry.get("sum")

                if current_sum is None or prev_sum is None:
                    continue

                # Skip if either value is zero or unavailable
                # Zero values indicate the sensor hasn't started accumulating or data is invalid
                if current_sum == 0 or prev_sum == 0:
                    _LOGGER.debug(
                        "ADJUST: Skipping %s - zero or unavailable value "
                        "(prev_sum=%.2f, current_sum=%.2f)",
                        entity_id,
                        prev_sum,
                        current_sum,
                    )
                    continue

                entry_time = dt_util.utc_from_timestamp(current_entry["start"])

                # Skip if we've already adjusted this entry
                if entry_time in adjusted_entry_times:
                    _LOGGER.debug(
                        "ADJUST: Skipping %s at %s - already adjusted",
                        entity_id,
                        entry_time.isoformat(),
                    )
                    continue

                # Find negative deltas (drops) - these indicate firmware resets
                if prev_sum > current_sum:
                    # Validation: Skip if either value is negative (corrupted data)
                    # TOTAL_INCREASING sensors should never have negative values
                    if current_sum < 0 or prev_sum < 0:
                        _LOGGER.warning(
                            "ADJUST: Skipping %s - corrupted data "
                            "(prev_sum=%.2f, current_sum=%.2f - negative values)",
                            entity_id,
                            prev_sum,
                            current_sum,
                        )
                        continue

                    # Calculate discontinuity (always needed to fix the drop)
                    discontinuity = prev_sum - current_sum

                    # Try to enhance adjustment with missing energy estimate
                    # Look for next entry to calculate post-reset consumption rate
                    missing_energy = 0.0
                    if i + 1 < len(sensor_stats):
                        next_entry = sensor_stats[i + 1]
                        next_sum = next_entry.get("sum")

                        if next_sum is not None and next_sum > current_sum:
                            # Calculate rate from post-reset behavior
                            next_time = dt_util.utc_from_timestamp(next_entry["start"])
                            time_delta_hours = (next_time - entry_time).total_seconds() / 3600

                            if time_delta_hours > 0:
                                rate_wh_per_hour = (next_sum - current_sum) / time_delta_hours

                                # Calculate gap duration between last good and spike
                                prev_time = dt_util.utc_from_timestamp(prev_entry["start"])
                                gap_duration_hours = (entry_time - prev_time).total_seconds() / 3600

                                # Estimate missing energy during gap
                                missing_energy = rate_wh_per_hour * gap_duration_hours

                                _LOGGER.debug(
                                    "ADJUST: Enhanced calculation for %s: "
                                    "rate=%.2f Wh/hr (from next entry), gap=%.2f hr, "
                                    "missing_energy=%.2f Wh",
                                    entity_id,
                                    rate_wh_per_hour,
                                    gap_duration_hours,
                                    missing_energy,
                                )

                    # Total adjustment = fix discontinuity + recover missing energy
                    adjustment = discontinuity + missing_energy

                    # Any negative delta in a TOTAL_INCREASING sensor is invalid
                    # and should be corrected, regardless of size

                    prev_time = dt_util.utc_from_timestamp(prev_entry["start"])

                    # Log the entry times for debugging
                    prev_time_local = dt_util.as_local(prev_time)
                    entry_time_local = dt_util.as_local(entry_time)

                    if missing_energy > 0:
                        _LOGGER.info(
                            "ADJUST: Found drop in %s (iteration %d): "
                            "prev_entry[%s / %s]=%.2f, current_entry[%s / %s]=%.2f, "
                            "delta=%.2f, discontinuity=+%.2f, missing_energy=+%.2f, "
                            "total_adjustment=+%.2f",
                            entity_id,
                            iteration,
                            prev_time.isoformat(),
                            prev_time_local.strftime("%Y-%m-%d %I:%M %p"),
                            prev_sum,
                            entry_time.isoformat(),
                            entry_time_local.strftime("%Y-%m-%d %I:%M %p"),
                            current_sum,
                            prev_sum - current_sum,
                            discontinuity,
                            missing_energy,
                            adjustment,
                        )
                    else:
                        _LOGGER.info(
                            "ADJUST: Found drop in %s (iteration %d): "
                            "prev_entry[%s / %s]=%.2f, current_entry[%s / %s]=%.2f, "
                            "delta=%.2f, adjustment=+%.2f (no next entry for rate calculation)",
                            entity_id,
                            iteration,
                            prev_time.isoformat(),
                            prev_time_local.strftime("%Y-%m-%d %I:%M %p"),
                            prev_sum,
                            entry_time.isoformat(),
                            entry_time_local.strftime("%Y-%m-%d %I:%M %p"),
                            current_sum,
                            prev_sum - current_sum,
                            adjustment,
                        )

                    # Call the recorder's async_adjust_statistics directly
                    # Energy sensors use Wh as native unit
                    _LOGGER.info(
                        "ADJUST: Adjusting %s at %s: +%.2f Wh",
                        entity_id,
                        entry_time.isoformat(),
                        adjustment,
                    )
                    try:
                        # Schedule the adjustment (this queues it for commit)
                        get_instance(hass).async_adjust_statistics(
                            statistic_id=entity_id,
                            start_time=entry_time,
                            sum_adjustment=float(adjustment),
                            adjustment_unit="Wh",
                        )
                        # Record the adjustment for potential reversal
                        adjustments_made.append(
                            {
                                "entity_id": entity_id,
                                "timestamp_utc": entry_time.isoformat(),
                                "adjustment_wh": float(adjustment),
                            }
                        )
                        # Mark this entry as adjusted to avoid double-adjusting
                        # This prevents re-detection even if the adjustment hasn't been
                        # committed yet when we re-query statistics
                        adjusted_entry_times.add(entry_time)
                        adjustments_for_sensor += 1
                        total_adjustments += 1
                        drop_found = True
                        _LOGGER.info(
                            "ADJUST: Successfully queued adjustment for %s "
                            "(total adjustments for this sensor: %d)",
                            entity_id,
                            adjustments_for_sensor,
                        )
                        # Wait for the recorder to commit the adjustment before re-querying
                        # This prevents detecting the same drop again if the adjustment
                        # hasn't been reflected in statistics queries yet
                        await _async_delay(hass, 0.5)
                        # Break to re-query statistics after this adjustment
                        break
                    except Exception as e:
                        _LOGGER.error("ADJUST FAILED: %s: %s", entity_id, e, exc_info=True)
                        # Continue to next drop even if this one failed
                        continue

            # If no drop was found, we're done with this sensor
            if not drop_found:
                if adjustments_for_sensor > 0:
                    _LOGGER.info(
                        "ADJUST: Completed %s - fixed %d drop(s)",
                        entity_id,
                        adjustments_for_sensor,
                    )
                break

        if iteration >= max_iterations:
            _LOGGER.error(
                "ADJUST: Reached max iterations (%d) for %s - stopping to prevent infinite loop",
                max_iterations,
                entity_id,
            )

    _LOGGER.info("ADJUST: Completed - made %d total adjustment(s)", total_adjustments)
    return total_adjustments, adjustments_made
