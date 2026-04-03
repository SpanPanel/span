"""Alert dispatch logic for SPAN Panel current monitoring.

Handles notification dispatch through event bus, notify services, and
persistent notifications.  All functions take ``hass`` and configuration
as explicit parameters rather than relying on instance state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE,
    DEFAULT_NOTIFICATION_PRIORITY,
    DEFAULT_NOTIFICATION_TITLE_TEMPLATE,
    EVENT_CURRENT_ALERT,
)
from .options import (
    NOTIFICATION_MESSAGE_TEMPLATE,
    NOTIFICATION_PRIORITY,
    NOTIFICATION_TITLE_TEMPLATE,
    NOTIFY_TARGETS,
)

EVENT_BUS_TARGET = "event_bus"

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def format_notification(
    *,
    alert_type: str,
    alert_name: str,
    alert_id: str,
    current_a: float,
    breaker_rating_a: float,
    threshold_pct: int,
    utilization_pct: float,
    window_duration_s: int | None,
    title_template: str,
    message_template: str,
    local_time: str | None = None,
) -> tuple[str, str]:
    """Format notification title and message using templates.

    Available placeholders:
        {name}            - Circuit/mains friendly name
        {entity_id}       - Entity ID (e.g. sensor.kitchen_current)
        {alert_type}      - "spike" or "continuous_overload"
        {current_a}       - Current draw in amps (e.g. 18.3)
        {breaker_rating_a}- Breaker rating in amps (e.g. 20)
        {threshold_pct}   - Configured threshold percentage
        {utilization_pct} - Actual utilization percentage
        {window_m}        - Window duration in minutes (continuous only)
        {local_time}      - Local time of the alert (e.g. 2:15 PM)
    """
    window_m = (window_duration_s or 0) // 60
    template_vars = {
        "name": alert_name,
        "entity_id": alert_id,
        "alert_type": alert_type,
        "current_a": f"{current_a:.1f}",
        "breaker_rating_a": f"{breaker_rating_a:.0f}",
        "threshold_pct": str(threshold_pct),
        "utilization_pct": str(utilization_pct),
        "window_m": str(window_m),
        "local_time": local_time or "",
    }
    try:
        title = title_template.format_map(template_vars)
    except (KeyError, ValueError):
        title = f"SPAN: {alert_name} {alert_type}"
    try:
        message = message_template.format_map(template_vars)
    except (KeyError, ValueError):
        message = (
            f"{alert_name} at {current_a:.1f}A "
            f"({utilization_pct}% of {breaker_rating_a:.0f}A rating)"
        )
    return title, message


def build_push_data(priority: str) -> dict[str, Any]:
    """Build platform-specific push data for the given priority level.

    Returns a dict suitable for the ``data`` parameter of a notify service
    call.  Includes keys for both iOS (``push.interruption-level``) and
    Android (``priority``, ``channel``) so the correct one is picked up
    regardless of the receiving device platform.
    """
    if priority == "default":
        return {}

    android_priority_map = {
        "passive": "low",
        "active": "default",
        "time-sensitive": "high",
        "critical": "high",
    }
    data: dict[str, Any] = {
        "push": {"interruption-level": priority},
        "priority": android_priority_map.get(priority, "default"),
    }
    if priority == "critical":
        data["push"]["sound"] = {
            "name": "default",
            "critical": 1,
            "volume": 1.0,
        }
        data["channel"] = "alarm_stream"
    elif priority == "time-sensitive":
        data["channel"] = "alarm_stream_other"
    return data


async def dispatch_to_target(
    hass: HomeAssistant,
    target: str,
    title: str,
    message: str,
    push_data: dict[str, Any],
) -> None:
    """Send a notification to a target.

    Entity-based targets (present in ``hass.states``) use
    ``notify.send_message`` with an ``entity_id``.  Service-only targets
    (e.g. ``notify.persistent_notification`` on older HA versions) fall
    back to calling the service directly.
    """
    service_data: dict[str, Any] = {"title": title, "message": message}
    if push_data:
        service_data["data"] = push_data

    if target.startswith("notify.") and hass.states.get(target):
        service_data["entity_id"] = target
        await hass.services.async_call("notify", "send_message", service_data)
    else:
        domain = target.split(".")[0] if "." in target else "notify"
        service = target.split(".")[1] if "." in target else "notify"
        await hass.services.async_call(domain, service, service_data)


def dispatch_alert(
    hass: HomeAssistant,
    settings: dict[str, Any],
    *,
    alert_type: str,
    alert_name: str,
    alert_id: str,
    alert_source: str,
    current_a: float,
    breaker_rating_a: float,
    threshold_pct: int,
    utilization_pct: float,
    panel_serial: str,
    window_duration_s: int | None = None,
    over_threshold_since: str | None = None,
) -> None:
    """Dispatch alert through all enabled notification channels."""
    local_time = dt_util.now().strftime("%-I:%M %p")

    event_data: dict[str, Any] = {
        "alert_source": alert_source,
        "alert_id": alert_id,
        "alert_name": alert_name,
        "alert_type": alert_type,
        "current_a": round(current_a, 1),
        "breaker_rating_a": breaker_rating_a,
        "threshold_pct": threshold_pct,
        "utilization_pct": utilization_pct,
        "panel_serial": panel_serial,
        "local_time": local_time,
    }
    if window_duration_s is not None:
        event_data["window_duration_s"] = window_duration_s
    if over_threshold_since is not None:
        event_data["over_threshold_since"] = over_threshold_since

    raw_targets = settings.get(NOTIFY_TARGETS, "")
    if isinstance(raw_targets, str):
        all_targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
    else:
        all_targets = list(raw_targets)

    if EVENT_BUS_TARGET in all_targets:
        hass.bus.async_fire(EVENT_CURRENT_ALERT, event_data)

    title, message = format_notification(
        alert_type=alert_type,
        alert_name=alert_name,
        alert_id=alert_id,
        current_a=current_a,
        breaker_rating_a=breaker_rating_a,
        threshold_pct=threshold_pct,
        utilization_pct=utilization_pct,
        window_duration_s=window_duration_s,
        local_time=local_time,
        title_template=settings.get(
            NOTIFICATION_TITLE_TEMPLATE, DEFAULT_NOTIFICATION_TITLE_TEMPLATE
        ),
        message_template=settings.get(
            NOTIFICATION_MESSAGE_TEMPLATE, DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE
        ),
    )

    notify_targets = [t for t in all_targets if t != EVENT_BUS_TARGET]

    if hass.state is not CoreState.running:
        _LOGGER.debug(
            "Skipping alert notifications during startup (state=%s)",
            hass.state,
        )
    else:
        priority = settings.get(NOTIFICATION_PRIORITY, DEFAULT_NOTIFICATION_PRIORITY)
        push_data = build_push_data(priority)

        for target in notify_targets:
            hass.async_create_task(dispatch_to_target(hass, target, title, message, push_data))

    _LOGGER.warning(
        "Current alert: %s — %s at %.1fA (%.1f%% of %.0fA rating)",
        alert_name,
        alert_type,
        current_a,
        utilization_pct,
        breaker_rating_a,
    )


def dispatch_test_alert(
    hass: HomeAssistant,
    settings: dict[str, Any],
) -> None:
    """Dispatch a test notification using sample values through all enabled channels."""
    alert_type = "spike"
    alert_name = "Kitchen Oven"
    alert_id = "sensor.kitchen_oven_current"
    alert_source = "circuit"
    current_a = 18.3
    breaker_rating_a = 20.0
    threshold_pct = 100
    utilization_pct = 91.5
    panel_serial = "TEST"
    window_duration_s = 300
    local_time = dt_util.now().strftime("%-I:%M %p")

    raw_targets = settings.get(NOTIFY_TARGETS, "")
    if isinstance(raw_targets, str):
        all_targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
    else:
        all_targets = list(raw_targets)

    event_data: dict[str, Any] = {
        "alert_source": alert_source,
        "alert_id": alert_id,
        "alert_name": alert_name,
        "alert_type": alert_type,
        "current_a": current_a,
        "breaker_rating_a": breaker_rating_a,
        "threshold_pct": threshold_pct,
        "utilization_pct": utilization_pct,
        "panel_serial": panel_serial,
        "window_duration_s": window_duration_s,
        "local_time": local_time,
        "test": True,
    }

    if EVENT_BUS_TARGET in all_targets:
        hass.bus.async_fire(EVENT_CURRENT_ALERT, event_data)

    title, message = format_notification(
        alert_type=alert_type,
        alert_name=alert_name,
        alert_id=alert_id,
        current_a=current_a,
        breaker_rating_a=breaker_rating_a,
        threshold_pct=threshold_pct,
        utilization_pct=utilization_pct,
        window_duration_s=window_duration_s,
        local_time=local_time,
        title_template=settings.get(
            NOTIFICATION_TITLE_TEMPLATE, DEFAULT_NOTIFICATION_TITLE_TEMPLATE
        ),
        message_template=settings.get(
            NOTIFICATION_MESSAGE_TEMPLATE, DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE
        ),
    )

    notify_targets = [t for t in all_targets if t != EVENT_BUS_TARGET]
    priority = settings.get(NOTIFICATION_PRIORITY, DEFAULT_NOTIFICATION_PRIORITY)
    push_data = build_push_data(priority)

    for target in notify_targets:
        hass.async_create_task(dispatch_to_target(hass, target, title, message, push_data))

    _LOGGER.info("Test notification dispatched to %d target(s)", len(all_targets))
