"""Helper functions for Span Panel integration."""

from __future__ import annotations

import logging

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    entity_registry as er,  # noqa: F401 — re-exported for patch compatibility
)
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from .entity_resolver import (  # noqa: F401
    build_bess_unique_id_for_entry,
    build_binary_sensor_unique_id_for_entry,
    build_evse_unique_id_for_entry,
    build_select_unique_id_for_entry,
    build_switch_unique_id_for_entry,
    construct_circuit_unique_id_for_entry,
    construct_multi_circuit_entity_id,
    construct_panel_unique_id_for_entry,
    construct_single_circuit_entity_id,
    construct_synthetic_unique_id_for_entry,
    construct_unmapped_entity_id,
    construct_unmapped_friendly_name,
    get_device_identifier_for_entry,
    get_unmapped_circuit_entity_id,
    resolve_evse_display_suffix,
)
from .id_builder import (  # noqa: F401
    ALL_SUFFIX_MAPPINGS,
    CIRCUIT_SUFFIX_MAPPING,
    PANEL_ENTITY_SUFFIX_MAPPING,
    PANEL_SUFFIX_MAPPING,
    build_bess_unique_id,
    build_binary_sensor_unique_id,
    build_circuit_unique_id,
    build_evse_unique_id,
    build_panel_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
    construct_binary_sensor_unique_id,
    construct_circuit_unique_id,
    construct_panel_unique_id,
    construct_select_unique_id,
    construct_switch_unique_id,
    construct_synthetic_unique_id,
    get_panel_entity_suffix,
    get_suffix_from_sensor_key,
    get_user_friendly_suffix,
    is_panel_level_sensor_key,
)

__all__ = [
    "ALL_SUFFIX_MAPPINGS",
    "CIRCUIT_SUFFIX_MAPPING",
    "PANEL_ENTITY_SUFFIX_MAPPING",
    "PANEL_SUFFIX_MAPPING",
    "async_create_span_notification",
    "build_bess_unique_id",
    "build_bess_unique_id_for_entry",
    "build_binary_sensor_unique_id",
    "build_binary_sensor_unique_id_for_entry",
    "build_circuit_unique_id",
    "build_evse_unique_id",
    "build_evse_unique_id_for_entry",
    "build_panel_unique_id",
    "build_select_unique_id",
    "build_select_unique_id_for_entry",
    "build_switch_unique_id",
    "build_switch_unique_id_for_entry",
    "construct_binary_sensor_unique_id",
    "construct_circuit_identifier_from_tabs",
    "construct_circuit_unique_id",
    "construct_circuit_unique_id_for_entry",
    "construct_multi_circuit_entity_id",
    "construct_panel_unique_id",
    "construct_panel_unique_id_for_entry",
    "construct_select_unique_id",
    "construct_single_circuit_entity_id",
    "construct_switch_unique_id",
    "construct_synthetic_unique_id",
    "construct_synthetic_unique_id_for_entry",
    "construct_tabs_attribute",
    "construct_unmapped_entity_id",
    "construct_unmapped_friendly_name",
    "construct_voltage_attribute",
    "detect_capabilities",
    "er",
    "get_device_identifier_for_entry",
    "get_panel_entity_suffix",
    "get_suffix_from_sensor_key",
    "get_unmapped_circuit_entity_id",
    "get_user_friendly_suffix",
    "has_bess",
    "has_evse",
    "has_power_flows",
    "has_pv",
    "is_panel_level_sensor_key",
    "resolve_evse_display_suffix",
]

_LOGGER = logging.getLogger(__name__)


async def async_create_span_notification(
    hass: HomeAssistant,
    message: str,
    title: str,
    notification_id: str,
    level: str = "warning",
) -> None:
    """Create a persistent notification for SPAN Panel issues.

    Args:
        hass: Home Assistant instance
        message: Notification message content
        title: Notification title
        notification_id: Unique identifier for the notification
        level: Severity level (info, warning, error)

    """
    _LOGGER.log(
        getattr(logging, level.upper(), logging.WARNING),
        "SPAN Panel %s: %s - %s",
        level,
        title,
        message,
    )

    async_create(
        hass,
        message=message,
        title=title,
        notification_id=notification_id,
    )


def construct_circuit_identifier_from_tabs(tabs: list[int], circuit_id: str = "") -> str:
    """Build a human-readable circuit identifier from tab positions.

    Used as a fallback when a circuit has no panel-assigned name.

    Args:
        tabs: List of tab numbers (1 for 120V, 2 for 240V dipole)
        circuit_id: Fallback identifier when tabs are unavailable

    Returns:
        String like "Circuit 30 32" for 240V or "Circuit 15" for 120V

    """
    if tabs and len(tabs) == 2:
        sorted_tabs = sorted(tabs)
        return f"Circuit {sorted_tabs[0]} {sorted_tabs[1]}"
    if tabs and len(tabs) == 1:
        return f"Circuit {tabs[0]}"
    return f"Circuit {circuit_id}"


def construct_tabs_attribute(circuit: SpanCircuitSnapshot) -> str | None:
    """Construct tabs attribute string from circuit data.

    For US electrical systems, circuits can only have 1 tab (120V) or 2 tabs (240V).

    Args:
        circuit: SpanCircuitSnapshot object with tabs information

    Returns:
        Tabs attribute string like "tabs [30:32]" for 240V or "tabs [28]" for 120V,
        or None if no tabs information is available

    Examples:
        Single tab (120V): "tabs [28]"
        Two tabs (240V): "tabs [30:32]"
        No tabs: None

    """
    if not circuit.tabs:
        return None

    # Sort tabs for consistent ordering
    sorted_tabs = sorted(circuit.tabs)

    if len(sorted_tabs) == 1:
        # Single tab (120V)
        return f"tabs [{sorted_tabs[0]}]"
    if len(sorted_tabs) == 2:
        # Two tabs (240V) - format as range
        return f"tabs [{sorted_tabs[0]}:{sorted_tabs[1]}]"
    # More than 2 tabs is not valid for US electrical system
    _LOGGER.warning(
        "Circuit %s has %d tabs, which is not valid for US electrical system (expected 1 or 2)",
        circuit.circuit_id,
        len(sorted_tabs),
    )
    return None


def construct_voltage_attribute(circuit: SpanCircuitSnapshot) -> int | None:
    """Construct voltage attribute for a circuit based on tab count.

    For US electrical systems, circuits can only have 1 tab (120V) or 2 tabs (240V).

    Args:
        circuit: SpanCircuitSnapshot object with tabs information

    Returns:
        Voltage in volts (120 for single tab, 240 for double tab), or None if no tabs information

    Examples:
        Single tab (120V): 120
        Two tabs (240V): 240
        No tabs: None

    """
    if not circuit.tabs:
        return None

    if len(circuit.tabs) == 1:
        return 120
    if len(circuit.tabs) == 2:
        return 240
    # More than 2 tabs is not valid for US electrical system
    _LOGGER.warning(
        "Circuit %s has %d tabs, which is not valid for US electrical system (expected 1 or 2)",
        circuit.circuit_id,
        len(circuit.tabs),
    )
    return None


def has_bess(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether a BESS (battery energy storage system) is commissioned.

    Only soe_percentage is a reliable signal — the power-flows node publishes
    battery=0.0 even on panels without a commissioned BESS.
    """
    return snapshot.battery.soe_percentage is not None


def has_pv(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether PV (solar) is commissioned."""
    return snapshot.power_flow_pv is not None or any(
        c.device_type == "pv" for c in snapshot.circuits.values()
    )


def has_power_flows(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the power-flows node is publishing data."""
    return snapshot.power_flow_site is not None


def has_evse(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether an EVSE (EV charger) is commissioned."""
    return len(snapshot.evse) > 0


def detect_capabilities(snapshot: SpanPanelSnapshot) -> frozenset[str]:
    """Derive the set of optional capabilities present in the snapshot.

    Used by the coordinator to detect when new hardware (BESS, PV, EVSE) appears
    and trigger a reload so new sensors are created.
    """
    caps: set[str] = set()
    if has_bess(snapshot):
        caps.add("bess")
    if has_pv(snapshot):
        caps.add("pv")
    if has_power_flows(snapshot):
        caps.add("power_flows")
    if has_evse(snapshot):
        caps.add("evse")
    return frozenset(caps)
