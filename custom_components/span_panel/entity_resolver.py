"""Entity resolver functions for Span Panel integration.

This module contains functions that depend on the coordinator or the config
entry to resolve unique IDs. These are the "entry-aware" wrappers around the
pure ID builders in id_builder.py. Entity ids are not built here: an entity's
id is composed by Home Assistant from the base `naming.py` supplies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api import SpanEvseSnapshot, SpanPanelSnapshot

from .id_builder import (
    build_bess_unique_id,
    build_binary_sensor_unique_id,
    build_circuit_unique_id,
    build_evse_unique_id,
    build_mid_unique_id,
    build_panel_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
    construct_synthetic_unique_id,
)

if TYPE_CHECKING:
    from .coordinator import SpanPanelCoordinator


def resolve_evse_display_suffix(
    evse: SpanEvseSnapshot,
    snapshot: SpanPanelSnapshot,
    use_circuit_numbers: bool,
) -> str | None:
    """Resolve the display suffix for an EVSE device name.

    Friendly names mode: returns the fed circuit's panel name (e.g., "Garage").
    Circuit numbers mode: returns the EVSE serial number (e.g., "SN-EVSE-001").
    Returns None when no meaningful suffix is available (prevents empty parens).
    """
    if use_circuit_numbers:
        serial: str | None = evse.serial_number
        return serial
    fed_circuit = snapshot.circuits.get(evse.feed_circuit_id)
    if fed_circuit and fed_circuit.name:
        name: str = fed_circuit.name
        return name
    return None


def _get_device_identifier_for_unique_ids(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
) -> str:
    """Return the panel serial used as the device segment in unique_ids."""
    serial: str = snapshot.serial_number
    return serial


def construct_panel_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build panel unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_panel_unique_id(identifier, description_key)


def construct_circuit_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    circuit_id: str,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build circuit unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_circuit_unique_id(identifier, circuit_id, description_key)


def build_switch_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    circuit_id: str,
    device_name: str | None = None,
) -> str:
    """Build switch unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_switch_unique_id(identifier, circuit_id)


def build_select_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    select_id: str,
    device_name: str | None = None,
) -> str:
    """Build select unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_select_unique_id(identifier, select_id)


def build_binary_sensor_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build binary_sensor unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_binary_sensor_unique_id(identifier, description_key)


def construct_synthetic_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    sensor_name: str,
    device_name: str | None = None,
) -> str:
    """Build synthetic sensor unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return construct_synthetic_unique_id(identifier, sensor_name)


def build_evse_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    evse_id: str,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build EVSE unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_evse_unique_id(identifier, evse_id, description_key)


def build_bess_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build BESS unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_bess_unique_id(identifier, description_key)


def build_mid_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build MID unique_id using the panel serial from the snapshot."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)
    return build_mid_unique_id(identifier, description_key)


def get_device_identifier_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
) -> str:
    """Public helper to get the per-entry device identifier used in unique_ids and storage."""
    return _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)


def construct_unmapped_friendly_name(
    circuit_number: int | str, sensor_description_name: str
) -> str:
    """Construct friendly name for unmapped circuit sensors."""
    # Format: "Unmapped Tab 32 Consumed Energy"
    return f"Unmapped Tab {circuit_number} {sensor_description_name}"
