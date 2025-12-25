"""Service to undo statistics adjustments made by the cleanup service.

This service can reverse adjustments made by cleanup_energy_spikes, or manually
create adjustments for testing purposes. It supports two modes:
1. Reverse cleanup: Undo all adjustments from a cleanup_energy_spikes result
2. Manual adjustment: Create a specific adjustment for testing
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from custom_components.span_panel.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Service name
SERVICE_UNDO_STATS_ADJUSTMENTS = "undo_stats_adjustments"

# Service schema - accepts either direct parameters OR cleanup result JSON
SERVICE_UNDO_STATS_ADJUSTMENTS_SCHEMA = vol.Schema(
    {
        # Option 1: Direct parameters for manual simulation
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("reset_time"): cv.datetime,
        vol.Optional("drop_amount_wh"): vol.Any(None, vol.Coerce(float)),
        # Option 2: Reverse adjustments from cleanup service result
        vol.Optional("cleanup_result"): vol.Any(dict, str),  # dict or JSON string
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_undo_stats_adjustments_service(hass: HomeAssistant) -> None:
    """Register the undo_stats_adjustments service.

    This function is safe to call multiple times.
    The service will only be registered once.
    """
    # Guard against multiple registrations
    service_key = f"{DOMAIN}_undo_stats_adjustments_service_registered"
    if hass.data.get(service_key):
        _LOGGER.debug(
            "Service %s.%s already registered, skipping",
            DOMAIN,
            SERVICE_UNDO_STATS_ADJUSTMENTS,
        )
        return

    async def handle_undo_stats_adjustments(call: ServiceCall) -> dict[str, Any]:
        """Handle the service call."""
        cleanup_result = call.data.get("cleanup_result")

        # If cleanup_result is provided, reverse those adjustments
        if cleanup_result:
            # Parse if it's a JSON string
            if isinstance(cleanup_result, str):
                try:
                    cleanup_result = json.loads(cleanup_result)
                except json.JSONDecodeError as e:
                    _LOGGER.error("Invalid JSON in cleanup_result: %s", e)
                    return {
                        "success": False,
                        "error": f"Invalid JSON in cleanup_result: {e}",
                    }

            return await reverse_cleanup_adjustments(hass, cleanup_result)

        # Otherwise, use direct parameters for manual simulation
        entity_id = call.data.get("entity_id")
        reset_time = call.data.get("reset_time")
        drop_amount_wh = call.data.get("drop_amount_wh")

        if not entity_id or not reset_time:
            return {
                "success": False,
                "error": "Either 'cleanup_result' or both 'entity_id' and 'reset_time' must be provided",
            }

        return await simulate_firmware_reset(
            hass,
            entity_id=entity_id,
            reset_time=reset_time,
            drop_amount_wh=drop_amount_wh,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_UNDO_STATS_ADJUSTMENTS,
        handle_undo_stats_adjustments,
        schema=SERVICE_UNDO_STATS_ADJUSTMENTS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.data[service_key] = True
    _LOGGER.debug("Registered %s.%s service", DOMAIN, SERVICE_UNDO_STATS_ADJUSTMENTS)


async def simulate_firmware_reset(
    hass: HomeAssistant,
    entity_id: str,
    reset_time: datetime,
    drop_amount_wh: float | None = None,
) -> dict[str, Any]:
    """Simulate a firmware reset by adjusting statistics downward.

    This creates a negative delta in the statistics at the specified time,
    simulating what happens when SPAN firmware resets and loses cumulative
    energy history.

    Args:
        hass: Home Assistant instance
        entity_id: Entity ID of the sensor to simulate reset for
        reset_time: Local time when the reset should occur
        drop_amount_wh: Amount to drop (Wh). If None, drops to 0.

    Returns:
        Summary of the simulation operation.

    """
    _LOGGER.info(
        "SIMULATE RESET: entity_id=%s, reset_time=%s, drop_amount_wh=%s",
        entity_id,
        reset_time,
        drop_amount_wh,
    )

    # Convert local time to UTC
    if reset_time.tzinfo is None:
        local_tz = dt_util.get_time_zone(hass.config.time_zone)
        reset_time_local = reset_time.replace(tzinfo=local_tz)
        reset_time_utc = dt_util.as_utc(reset_time_local)
    else:
        reset_time_utc = dt_util.as_utc(reset_time)

    # Query statistics to find the value at reset_time
    # Look back a bit to find the previous entry
    start_time = reset_time_utc - timedelta(hours=1)
    end_time = reset_time_utc + timedelta(hours=1)

    try:
        stats_result = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start_time,
            end_time,
            {entity_id},
            "5minute",  # Use 5-minute stats for precision
            None,
            {"sum", "state"},
        )
    except Exception as e:
        _LOGGER.error("Error querying statistics: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"Failed to query statistics: {e}",
        }

    if not stats_result or entity_id not in stats_result:
        return {
            "success": False,
            "error": f"No statistics found for {entity_id}",
        }

    sensor_stats = stats_result[entity_id]
    if not sensor_stats:
        return {
            "success": False,
            "error": f"No statistics entries found for {entity_id}",
        }

    # Find the entry at or just before reset_time
    previous_sum: float | None = None
    reset_entry_time: datetime | None = None

    for entry in sensor_stats:
        entry_start = entry.get("start")
        if entry_start is None:
            continue
        entry_time = dt_util.utc_from_timestamp(entry_start)
        entry_sum = entry.get("sum")

        if entry_sum is None:
            continue

        if entry_time <= reset_time_utc:
            previous_sum = entry_sum
            reset_entry_time = entry_time
        else:
            # We've passed the reset time, stop looking
            break

    if previous_sum is None or reset_entry_time is None:
        return {
            "success": False,
            "error": f"Could not find statistics entry at or before {reset_time_utc}",
        }

    # Calculate drop amount
    if drop_amount_wh is None:
        # Drop to 0 (simulate complete reset)
        adjustment = -previous_sum
        new_sum = 0.0
    else:
        # Drop by specified amount
        adjustment = -drop_amount_wh
        new_sum = previous_sum - drop_amount_wh
        if new_sum < 0:
            new_sum = 0.0
            adjustment = -previous_sum

    _LOGGER.info(
        "SIMULATE RESET: Found sum=%.2f Wh at %s, adjusting by %.2f Wh to %.2f Wh",
        previous_sum,
        reset_entry_time.isoformat(),
        adjustment,
        new_sum,
    )

    # Apply the adjustment using async_adjust_statistics
    # This will propagate to all subsequent entries
    try:
        get_instance(hass).async_adjust_statistics(
            statistic_id=entity_id,
            start_time=reset_entry_time,
            sum_adjustment=float(adjustment),
            adjustment_unit="Wh",
        )
        _LOGGER.info(
            "SIMULATE RESET: Successfully applied adjustment of %.2f Wh at %s",
            adjustment,
            reset_entry_time.isoformat(),
        )
    except Exception as e:
        _LOGGER.error("SIMULATE RESET: Failed to adjust statistics: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"Failed to adjust statistics: {e}",
        }

    return {
        "success": True,
        "entity_id": entity_id,
        "reset_time": reset_entry_time.isoformat(),
        "previous_sum": previous_sum,
        "adjustment": adjustment,
        "new_sum": new_sum,
        "message": f"Simulated firmware reset: dropped {abs(adjustment):.2f} Wh at {reset_entry_time.isoformat()}",
    }


async def reverse_cleanup_adjustments(
    hass: HomeAssistant, cleanup_result: dict[str, Any]
) -> dict[str, Any]:
    """Reverse adjustments made by the cleanup_energy_spikes service.

    Takes the result from cleanup_energy_spikes service and reverses all
    adjustments that were made, effectively undoing the cleanup operation.

    Args:
        hass: Home Assistant instance
        cleanup_result: Result dictionary from cleanup_energy_spikes service
            Must contain an "adjustments" list with entries containing:
            - entity_id: Entity ID that was adjusted
            - timestamp_utc: ISO timestamp when adjustment was made
            - adjustment_wh: The adjustment amount (positive value)

    Returns:
        Summary of reversal operation.

    """
    _LOGGER.info("REVERSE: Reversing cleanup adjustments from result")

    adjustments = cleanup_result.get("adjustments", [])
    if not adjustments:
        return {
            "success": False,
            "error": "No adjustments found in cleanup_result. Was dry_run=true?",
        }

    _LOGGER.info("REVERSE: Found %d adjustment(s) to reverse", len(adjustments))

    reversed_count = 0
    errors: list[str] = []

    for adjustment in adjustments:
        entity_id = adjustment.get("entity_id")
        timestamp_str = adjustment.get("timestamp_utc")
        adjustment_wh = adjustment.get("adjustment_wh")

        if not entity_id or not timestamp_str or adjustment_wh is None:
            error_msg = f"Invalid adjustment record: {adjustment}"
            _LOGGER.warning("REVERSE: %s", error_msg)
            errors.append(error_msg)
            continue

        # Parse timestamp
        try:
            timestamp_utc = dt_util.parse_datetime(timestamp_str)
            if timestamp_utc is None:
                raise ValueError(f"Could not parse timestamp: {timestamp_str}")
        except Exception as e:
            error_msg = f"Error parsing timestamp {timestamp_str}: {e}"
            _LOGGER.warning("REVERSE: %s", error_msg)
            errors.append(error_msg)
            continue

        # Reverse the adjustment (negate it)
        reverse_adjustment = -adjustment_wh

        _LOGGER.info(
            "REVERSE: Reversing adjustment for %s at %s: %.2f Wh -> %.2f Wh",
            entity_id,
            timestamp_utc.isoformat(),
            adjustment_wh,
            reverse_adjustment,
        )

        try:
            get_instance(hass).async_adjust_statistics(
                statistic_id=entity_id,
                start_time=timestamp_utc,
                sum_adjustment=float(reverse_adjustment),
                adjustment_unit="Wh",
            )
            reversed_count += 1
            _LOGGER.info(
                "REVERSE: Successfully reversed adjustment for %s",
                entity_id,
            )
        except Exception as e:
            error_msg = (
                f"Failed to reverse adjustment for {entity_id} at {timestamp_utc.isoformat()}: {e}"
            )
            _LOGGER.error("REVERSE: %s", error_msg, exc_info=True)
            errors.append(error_msg)

    result: dict[str, Any] = {
        "success": reversed_count > 0,
        "reversed_count": reversed_count,
        "total_adjustments": len(adjustments),
    }

    if errors:
        result["errors"] = errors
        result["error"] = (
            f"Reversed {reversed_count} of {len(adjustments)} adjustments. {len(errors)} error(s)."
        )
    else:
        result["message"] = f"Successfully reversed {reversed_count} adjustment(s)."

    return result
