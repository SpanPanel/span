"""Helper functions for Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from .span_panel import SpanPanel
from .util import panel_to_device_info

if TYPE_CHECKING:
    from .coordinator import SpanPanelCoordinator
    from .span_panel_circuit import SpanPanelCircuit

_LOGGER = logging.getLogger(__name__)


def sanitize_name_for_entity_id(name: str) -> str:
    """Sanitize a name for use in entity IDs."""
    return name.lower().replace(" ", "_").replace("-", "_")


def get_circuit_number(circuit: SpanPanelCircuit) -> int | str:
    """Extract circuit number (tab position) from circuit object.

    Args:
        circuit: SpanPanelCircuit object

    Returns:
        Circuit number (tab position) or circuit_id if no tabs

    """
    return circuit.tabs[0] if circuit.tabs else circuit.circuit_id


def construct_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    circuit_name: str,
    circuit_number: int | str,
    suffix: str,
) -> str | None:
    """Construct entity ID based on integration configuration flags.

    This function handles entity naming for individual circuit entities based on the
    USE_CIRCUIT_NUMBERS and USE_DEVICE_PREFIX configuration flags.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        circuit_name: Human-readable circuit name
        circuit_number: Circuit number/identifier
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    config_entry = coordinator.config_entry
    if config_entry is None:
        raise RuntimeError("Config entry missing from coordinator - integration improperly set up")

    # For existing installations with empty options, default to False for backward compatibility
    # For new installations, these will be explicitly set to True in create_new_entry()
    if not config_entry.options:
        # Empty options = existing installation, use legacy defaults
        use_device_prefix = False
        use_circuit_numbers = False
    else:
        # Has options = either new installation or existing installation that went through options flow
        use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)
        use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, True)

    # Get device info for device name
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name")

    if use_circuit_numbers:
        # New installation (v1.0.9+) - stable circuit-based entity IDs
        # Format: sensor.span_panel_circuit_15_power
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            return f"{platform}.{device_name}_circuit_{circuit_number}_{suffix}"
        else:
            return None

    elif use_device_prefix:
        # Post-1.0.4 installation - friendly names with device prefix
        # Format: sensor.span_panel_kitchen_outlets_power
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
        # Circuit sensor API field mappings
        "instantPowerW": "power",
        "producedEnergyWh": "energy_produced",
        "consumedEnergyWh": "energy_consumed",
        "importedEnergyWh": "energy_imported",
        "exportedEnergyWh": "energy_exported",
        "circuit_priority": "priority",
    }
    return suffix_mapping.get(description_key, description_key.lower())


def construct_synthetic_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    circuit_numbers: list[int],
    suffix: str,
    friendly_name: str | None = None,
) -> str | None:
    """Construct synthetic entity ID for multi-circuit entities using stable naming.

    This function handles entity naming for synthetic sensors that combine multiple circuits,
    such as solar inverters or custom circuit groups. For backward compatibility, synthetic
    sensors respect the USE_DEVICE_PREFIX setting, unlike individual circuit entities.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        circuit_numbers: List of circuit numbers to combine (e.g., [30, 32] for solar inverter)
        suffix: Entity-specific suffix ("instant_power", "energy_produced", etc.)
        friendly_name: Optional friendly name for name-based entity

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    config_entry = coordinator.config_entry
    if config_entry is None:
        raise RuntimeError("Config entry missing from coordinator - integration improperly set up")

    # Get device info for device name
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name")

    # Construct the entity name part
    # Synthetic sensors always use friendly names regardless of USE_CIRCUIT_NUMBERS
    if friendly_name:
        # Convert friendly name to entity ID format (e.g., "Solar Inverter" -> "solar_inverter")
        entity_name = sanitize_name_for_entity_id(friendly_name)
        if suffix:
            entity_name = f"{entity_name}_{suffix}"
    else:
        # Fallback to generic synthetic naming if no friendly name provided
        valid_circuits = [str(num) for num in circuit_numbers if num > 0]
        entity_name = f"synthetic_sensor_{'_'.join(valid_circuits)}_{suffix}"

    # Check if device prefix should be used (for backward compatibility)
    # For existing installations with empty options, default to False for backward compatibility
    if not config_entry.options:
        # Empty options = existing installation, use legacy defaults
        use_device_prefix = False
    else:
        # Has options = either new installation or existing installation that went through options flow
        use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)

    if use_device_prefix and device_name_raw:
        device_name = sanitize_name_for_entity_id(device_name_raw)
        return f"{platform}.{device_name}_{entity_name}"
    else:
        return f"{platform}.{entity_name}"


def construct_synthetic_friendly_name(
    circuit_numbers: list[int],
    suffix_description: str,
    user_friendly_name: str | None = None,
) -> str:
    """Construct friendly display name for synthetic sensors.

    Args:
        circuit_numbers: List of circuit numbers (e.g., [30, 32] for solar inverter)
        suffix_description: Human-readable suffix (e.g., "Instant Power", "Energy Produced")
        user_friendly_name: Optional user-provided name (e.g., "Solar Production")

    Returns:
        Friendly name for display in Home Assistant

    """
    if user_friendly_name:
        # User provided a custom name - use it with the suffix
        return f"{user_friendly_name} {suffix_description}"

    # Fallback to circuit-based name
    valid_circuits = [str(num) for num in circuit_numbers if num > 0]
    if len(valid_circuits) > 1:
        circuit_spec = "-".join(valid_circuits)
        return f"Circuit {circuit_spec} {suffix_description}"
    elif len(valid_circuits) == 1:
        return f"Circuit {valid_circuits[0]} {suffix_description}"
    else:
        return f"Unknown Circuit {suffix_description}"


def construct_panel_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
) -> str | None:
    """Construct entity ID for panel-level entities based on integration configuration flags.

    This function handles entity naming for panel-level entities based on the
    USE_DEVICE_PREFIX configuration flag.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("current_power", "dsm_state", etc.)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    config_entry = coordinator.config_entry
    if config_entry is None:
        raise RuntimeError("Config entry missing from coordinator - integration improperly set up")

    # For existing installations with empty options, default to False for backward compatibility
    # For new installations, these will be explicitly set to True in create_new_entry()
    if not config_entry.options:
        # Empty options = existing installation, use legacy defaults
        use_device_prefix = False
    else:
        # Has options = either new installation or existing installation that went through options flow
        use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)

    if use_device_prefix:
        # With device prefix - Format: sensor.span_panel_current_power
        device_info = panel_to_device_info(span_panel)
        device_name_raw = device_info.get("name")
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            return f"{platform}.{device_name}_{suffix}"
        else:
            return None
    else:
        # Without device prefix - Format: sensor.current_power
        return f"{platform}.{suffix}"
