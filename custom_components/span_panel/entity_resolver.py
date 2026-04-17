"""Entity resolver functions for Span Panel integration.

This module contains functions that depend on the coordinator, entity registry,
or config entry options to resolve entity IDs and unique IDs. These are the
"entry-aware" wrappers around the pure ID builders in id_builder.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
from span_panel_api import SpanCircuitSnapshot, SpanEvseSnapshot, SpanPanelSnapshot

from .const import DOMAIN, USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from .id_builder import (
    build_bess_unique_id,
    build_binary_sensor_unique_id,
    build_circuit_unique_id,
    build_evse_unique_id,
    build_panel_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
    construct_synthetic_unique_id,
)
from .util import snapshot_to_device_info

if TYPE_CHECKING:
    from .coordinator import SpanPanelCoordinator

_LOGGER = logging.getLogger(__name__)


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


def get_device_identifier_for_entry(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
) -> str:
    """Public helper to get the per-entry device identifier used in unique_ids and storage."""
    return _get_device_identifier_for_unique_ids(coordinator, snapshot, device_name)


def construct_multi_circuit_entity_id(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    platform: str,
    suffix: str,
    circuit_numbers: list[int],
    friendly_name: str | None = None,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for multi-circuit sensors (like solar inverters).

    Args:
        coordinator: The coordinator instance
        snapshot: The panel snapshot data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        circuit_numbers: List of circuit numbers this sensor combines
        friendly_name: Descriptive name for this sensor (required if unique_id is None)
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(platform, DOMAIN, unique_id)

        _LOGGER.debug(
            "Multi-circuit helper registry lookup (switches/selects) - unique_id=%s, found_entity_id=%s",
            unique_id,
            existing_entity_id,
        )

        if existing_entity_id:
            return existing_entity_id
        # During migration, unique_id lookup should always succeed
        raise ValueError(
            f"Registry lookup failed for unique_id '{unique_id}' during migration. Entity should exist in registry."
        )
    _LOGGER.debug(
        "Multi-circuit helper (switches/selects) - no unique_id provided, skipping registry lookup"
    )

    # Get device name from config entry data
    device_name = coordinator.config_entry.data.get("device_name", coordinator.config_entry.title)
    if not device_name:
        return None

    use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    # If no unique_id provided, friendly_name is required when not using circuit numbers
    if unique_id is None and not use_circuit_numbers and not friendly_name:
        _LOGGER.error(
            "Friendly_name is required when unique_id is None and not using circuit numbers for multi-circuit entity"
        )
        return None

    if use_circuit_numbers:
        # Use circuit number pattern: sensor.span_panel_circuit_30_32_power
        if circuit_numbers:
            sorted_circuits = sorted([num for num in circuit_numbers if num > 0])
        else:
            sorted_circuits = []
        if sorted_circuits:
            if len(sorted_circuits) == 1:
                circuit_part = f"circuit_{sorted_circuits[0]}"
            else:
                circuit_list = "_".join(str(num) for num in sorted_circuits)
                circuit_part = f"circuit_{circuit_list}"
        else:
            raise ValueError(
                f"Circuit-based naming is enabled but no valid circuit numbers provided. "
                f"Got circuit_numbers={circuit_numbers}. Multi-circuit entities require valid circuit numbers when USE_CIRCUIT_NUMBERS is True."
            )
    else:
        # Use friendly name pattern: sensor.span_panel_solar_inverter_power
        circuit_part = slugify(friendly_name)

    # Build the entity ID.
    # `False` default preserves legacy installs (no device prefix). Single-circuit
    # entities default to True — see construct_single_circuit_entity_id below —
    # because they never had a prefix-less form historically; multi-circuit
    # synthetics did.
    use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
    parts = []

    if use_device_prefix:
        if device_name:
            # Sanitize device name for entity ID use
            sanitized_device_name = slugify(device_name)
            parts.append(sanitized_device_name)

    parts.append(circuit_part)

    # Add suffix if not already in circuit_part
    if suffix and not circuit_part.endswith(f"_{suffix}"):
        parts.append(suffix)

    return f"{platform}.{'_'.join(parts)}"


def construct_single_circuit_entity_id(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    platform: str,
    suffix: str,
    circuit_data: SpanCircuitSnapshot,
    unique_id: str | None = None,
    device_name: str | None = None,
) -> str | None:
    """Construct entity ID for single-circuit sensors.

    Args:
        coordinator: The coordinator instance
        snapshot: The panel snapshot data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        circuit_data: Circuit data object
        unique_id: The unique ID for this entity (None to skip registry lookup)
        device_name: Device name for entity ID construction (None to use from config entry)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(platform, DOMAIN, unique_id)

        _LOGGER.debug(
            "Circuit helper registry lookup - unique_id=%s, found_entity_id=%s",
            unique_id,
            existing_entity_id,
        )

        if existing_entity_id:
            return existing_entity_id
        # FATAL ERROR: Expected unique_id not found in registry
        raise ValueError(
            f"REGISTRY LOOKUP ERROR: Expected unique_id '{unique_id}' not found in registry. "
            f"This indicates a migration or configuration mismatch."
        )
    _LOGGER.debug("Circuit helper - no unique_id provided, skipping registry lookup")

    # Get device info
    device_info = snapshot_to_device_info(snapshot, device_name)
    if not device_info or not device_info.get("name"):
        return None

    use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    if use_circuit_numbers:
        # Check if this is a 240V circuit (2 tabs) or 120V circuit (1 tab)
        if circuit_data.tabs and len(circuit_data.tabs) == 2:
            # 240V circuit - use both tab numbers
            sorted_tabs = sorted(circuit_data.tabs)
            circuit_part = f"circuit_{sorted_tabs[0]}_{sorted_tabs[1]}"
        elif circuit_data.tabs and len(circuit_data.tabs) == 1:
            # 120V circuit - use single tab number
            circuit_part = f"circuit_{circuit_data.tabs[0]}"
        else:
            # No tabs available — use the API circuit_id as fallback
            circuit_part = (
                f"circuit_{circuit_data.circuit_id}"
                if circuit_data.circuit_id
                else "circuit_unknown"
            )
    # Use friendly name pattern: sensor.span_panel_solar_east_power
    elif circuit_data.name:
        circuit_part = slugify(circuit_data.name)
    else:
        circuit_part = "single_circuit"

    # Build the entity ID (only for non-voltage-specific cases)
    use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)
    parts = []

    if use_device_prefix:
        device_name = device_info.get("name")
        if device_name:
            # Sanitize device name for entity ID use
            sanitized_device_name = slugify(device_name)
            parts.append(sanitized_device_name)

    parts.append(circuit_part)

    # Add suffix if not already in circuit_part
    if suffix and not circuit_part.endswith(f"_{suffix}"):
        parts.append(suffix)

    return f"{platform}.{'_'.join(parts)}"


def construct_unmapped_entity_id(
    snapshot: SpanPanelSnapshot,
    circuit_id: str,
    suffix: str,
    device_name: str | None = None,
) -> str:
    """Construct entity ID for unmapped tab with consistent modern naming.

    Args:
        snapshot: The panel snapshot data
        circuit_id: Circuit ID (e.g., "unmapped_tab_32")
        suffix: Sensor suffix (e.g., "power", "energy_produced")
        device_name: The device name to use for entity ID construction

    Returns:
        Entity ID string like "sensor.span_panel_unmapped_tab_32_power"

    """
    # Always use device prefix for unmapped entities
    # circuit_id is "unmapped_tab_32", add device prefix and suffix to create
    # "sensor.span_panel_unmapped_tab_32_power"
    device_info = snapshot_to_device_info(snapshot, device_name)
    device_name_raw = device_info.get("name")
    _LOGGER.debug(
        "construct_unmapped_entity_id: circuit_id=%s, suffix=%s, device_name_raw=%s",
        circuit_id,
        suffix,
        device_name_raw,
    )
    if device_name_raw:
        # Sanitize device name for entity ID use
        sanitized_device_name = slugify(device_name_raw)
        result = f"sensor.{sanitized_device_name}_{circuit_id}_{suffix}"
        _LOGGER.debug("construct_unmapped_entity_id result with device: %s", result)
        return result
    result = f"sensor.{circuit_id}_{suffix}"
    _LOGGER.debug("construct_unmapped_entity_id result without device: %s", result)
    return result


def get_unmapped_circuit_entity_id(
    snapshot: SpanPanelSnapshot,
    tab_number: int,
    suffix: str,
    device_name: str | None = None,
) -> str | None:
    """Get entity ID for an unmapped circuit based on tab number.

    This helper function constructs the entity ID for native unmapped circuit sensors
    that should already exist in Home Assistant. It's useful for synthetic sensors
    that need to reference these native entities in formulas.

    Args:
        snapshot: The panel snapshot data
        tab_number: The tab number (e.g., 30, 32)
        suffix: The sensor suffix (e.g., "power", "energy_produced", "energy_consumed")
        device_name: The device name to use for entity ID construction

    Returns:
        Entity ID string like "sensor.span_panel_unmapped_tab_30_power"
        or None if the circuit doesn't exist

    Examples:
        get_unmapped_circuit_entity_id(snapshot, 30, "power")
        # Returns: "sensor.span_panel_unmapped_tab_30_power"

        get_unmapped_circuit_entity_id(snapshot, 32, "energy_produced")
        # Returns: "sensor.span_panel_unmapped_tab_32_energy_produced"

    """
    circuit_id = f"unmapped_tab_{tab_number}"

    # Verify the circuit exists in the panel data
    if circuit_id not in snapshot.circuits:
        _LOGGER.debug("Unmapped circuit %s not found in circuits list", circuit_id)
        return None

    result_entity_id = construct_unmapped_entity_id(snapshot, circuit_id, suffix, device_name)
    _LOGGER.debug("Generated unmapped entity ID: %s", result_entity_id)
    return result_entity_id


def construct_unmapped_friendly_name(
    circuit_number: int | str, sensor_description_name: str
) -> str:
    """Construct friendly name for unmapped circuit sensors."""
    # Format: "Unmapped Tab 32 Consumed Energy"
    return f"Unmapped Tab {circuit_number} {sensor_description_name}"
