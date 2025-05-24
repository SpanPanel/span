"""Helper functions for Span Panel integration."""

from __future__ import annotations

import logging

from .const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from .coordinator import SpanPanelCoordinator
from .span_panel import SpanPanel
from .util import panel_to_device_info

_LOGGER = logging.getLogger(__name__)


def sanitize_name_for_entity_id(name: str) -> str:
    """Sanitize a name for use in entity IDs."""
    return name.lower().replace(" ", "_").replace("-", "_")


def construct_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    circuit_name: str,
    circuit_number: str | int,
    suffix: str,
) -> str | None:
    """
    Construct entity ID based on integration configuration flags.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        circuit_name: Human-readable circuit name
        circuit_number: Circuit number (tab position)
        suffix: Entity-specific suffix ("power", "breaker", "priority", etc.)

    Returns:
        Constructed entity ID string or None if device info unavailable
    """
    config_entry = coordinator.config_entry
    if config_entry is None:
        raise RuntimeError(
            "Config entry missing from coordinator - integration improperly set up"
        )

    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)
    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    # Get device info for device name
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name")

    if use_circuit_numbers:
        # New installation (v1.0.9+) - stable circuit-based entity IDs
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            return f"{platform}.{device_name}_circuit_{circuit_number}_{suffix}"
        else:
            return None

    elif use_device_prefix:
        # Post-1.0.4 installation - device prefix with circuit names
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            circuit_name_sanitized = sanitize_name_for_entity_id(circuit_name)
            return f"{platform}.{device_name}_{circuit_name_sanitized}_{suffix}"
        else:
            return None

    else:
        # Pre-1.0.4 installation - no device prefix, just circuit names
        circuit_name_sanitized = sanitize_name_for_entity_id(circuit_name)
        return f"{platform}.{circuit_name_sanitized}_{suffix}"


def get_user_friendly_suffix(description_key: str) -> str:
    """Convert API field names to user-friendly entity ID suffixes."""
    suffix_mapping = {
        "instantPowerW": "power",
        "producedEnergyWh": "energy_produced",
        "consumedEnergyWh": "energy_consumed",
        "importedEnergyWh": "energy_imported",
        "exportedEnergyWh": "energy_exported",
        "circuit_priority": "priority",
    }
    return suffix_mapping.get(description_key, description_key.lower())
