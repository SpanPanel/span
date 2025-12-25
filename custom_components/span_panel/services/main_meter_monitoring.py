"""Main meter monitoring for SPAN Panel firmware reset detection.

This module provides monitoring of the main meter energy sensor to detect
firmware resets (any decrease in the cumulative energy value). When detected,
it sends a persistent notification to alert the user.
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)

_LOGGER = logging.getLogger(__name__)


def find_main_meter_entity(hass: HomeAssistant, entry_id: str | None = None) -> str | None:
    """Find the main meter consumed energy sensor entity ID.

    Searches for SPAN energy sensors that match the main meter pattern.
    If entry_id is provided, only searches for entities belonging to that config entry.

    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID to filter entities (optional)

    Returns:
        Entity ID of the main meter consumed energy sensor, or None if not found.

    """
    # If entry_id provided, use entity registry to filter
    if entry_id:
        registry = er.async_get(hass)
        entry_entities = {
            entry.entity_id
            for entry in registry.entities.values()
            if entry.config_entry_id == entry_id
        }
    else:
        entry_entities = None

    for entity_id in hass.states.async_entity_ids("sensor"):
        if not entity_id.startswith("sensor.span_panel_"):
            continue

        # Filter by config entry if specified
        if entry_entities is not None and entity_id not in entry_entities:
            continue

        state = hass.states.get(entity_id)
        if state is None:
            continue

        # Check for TOTAL_INCREASING state class (energy sensors)
        state_class = state.attributes.get("state_class")
        if state_class != SensorStateClass.TOTAL_INCREASING:
            continue

        # Look for main meter consumed energy sensor
        if "main_meter" in entity_id and "consumed" in entity_id:
            return str(entity_id)

    return None


async def async_setup_main_meter_monitoring(
    hass: HomeAssistant, entry_id: str | None = None
) -> CALLBACK_TYPE | None:
    """Set up monitoring of the main meter for firmware reset detection.

    Automatically finds the main meter consumed energy sensor and sets up
    monitoring for value decreases (firmware resets).

    Args:
        hass: Home Assistant instance.
        entry_id: Config entry ID to filter entities (optional).

    Returns:
        Unsubscribe callback to remove the listener, or None if setup failed.

    """
    main_meter_entity_id = find_main_meter_entity(hass, entry_id)

    if not main_meter_entity_id:
        _LOGGER.debug("Main meter consumed energy sensor not found - monitoring will not be set up")
        return None

    @callback
    def _async_main_meter_state_changed(event: Event[EventStateChangedData]) -> None:
        """Monitor main meter for firmware resets (any decrease in TOTAL_INCREASING)."""
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")

        if not new_state or not old_state:
            return

        # Skip if either state is unavailable/unknown
        if new_state.state in ("unavailable", "unknown", None):
            return
        if old_state.state in ("unavailable", "unknown", None):
            return

        try:
            new_value = float(new_state.state)
            old_value = float(old_state.state)
            delta = new_value - old_value

            # ANY decrease in TOTAL_INCREASING sensor = firmware reset
            if delta < 0:
                _LOGGER.warning(
                    "SPAN Panel firmware reset detected: main meter energy decreased "
                    "from %s Wh to %s Wh (delta: %s Wh)",
                    old_value,
                    new_value,
                    delta,
                )

                # Create persistent notification
                hass.async_create_task(
                    _create_reset_notification(hass, abs(delta), old_value, new_value)
                )

        except (ValueError, TypeError) as e:
            _LOGGER.debug(
                "Could not parse main meter values: old=%s, new=%s, error=%s",
                old_state.state,
                new_state.state,
                e,
            )

    # Register listener and store the unsubscribe callback
    unsub = async_track_state_change_event(
        hass,
        [main_meter_entity_id],
        _async_main_meter_state_changed,
    )
    _LOGGER.info(
        "Set up main meter monitoring for firmware reset detection on %s",
        main_meter_entity_id,
    )
    return unsub


async def _create_reset_notification(
    hass: HomeAssistant,
    delta: float,
    old_value: float,
    new_value: float,
) -> None:
    """Create a persistent notification about the detected firmware reset."""
    title = "⚠️ SPAN Panel Firmware Reset Detected"
    message = (
        f"The main meter energy value decreased by **{delta:,.0f} Wh**.\n"
        f"(from {old_value:,.0f} Wh to {new_value:,.0f} Wh)\n\n"
        f"This typically indicates a panel firmware update or reset.\n\n"
        f"**To clean up negative spikes in the Energy Dashboard:**\n"
        f"1. Open **Developer Tools → Services**\n"
        f"2. Search for `span_panel.cleanup_energy_spikes`\n"
        f"3. Run with `dry_run: true` to preview\n"
        f"4. Review the results\n"
        f"5. Run again with `dry_run: false` to apply cleanup"
    )

    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": title,
            "message": message,
            "notification_id": "span_panel_firmware_reset_detected",
        },
    )
