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
        raise RuntimeError(
            "Config entry missing from coordinator - integration improperly set up"
        )

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
    """Construct synthetic entity ID for multi-circuit entities based on integration configuration flags.

    This function handles entity naming for synthetic sensors that combine multiple circuits,
    such as solar inverters or custom circuit groups (Phase 3). The naming pattern is determined
    entirely by the USE_CIRCUIT_NUMBERS and USE_DEVICE_PREFIX configuration flags.

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
        raise RuntimeError(
            "Config entry missing from coordinator - integration improperly set up"
        )

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
        # Format: sensor.span_panel_circuit_30_32_instant_power
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            # Filter out zero/invalid circuit numbers and create circuit specification
            valid_circuits = [str(num) for num in circuit_numbers if num > 0]
            circuit_spec = "_".join(valid_circuits) if valid_circuits else "unknown"
            return f"{platform}.{device_name}_circuit_{circuit_spec}_{suffix}"
        else:
            return None

    else:
        # named based entity - use friendly name to construct entity ID
        if friendly_name:
            # Convert friendly name to entity ID format (e.g., "Solar Production" -> "solar_production")
            entity_name = sanitize_name_for_entity_id(friendly_name)
        else:
            # Fallback to circuit-based naming if no friendly name provided
            valid_circuits = [str(num) for num in circuit_numbers if num > 0]
            entity_name = f"circuit_group_{'_'.join(valid_circuits)}"

        # Format: sensor.span_panel_solar_production_instant_power (with device prefix)
        # Format: sensor.solar_production_instant_power (without device prefix)
        if use_device_prefix and device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
            return f"{platform}.{device_name}_{entity_name}_{suffix}"
        else:
            return f"{platform}.{entity_name}_{suffix}"


def construct_solar_inverter_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    inverter_leg1: int,
    inverter_leg2: int,
    suffix: str,
    friendly_name: str | None = None,
) -> str | None:
    """Construct solar inverter entity ID based on integration configuration flags.

    This is a convenience wrapper around construct_synthetic_entity_id for solar inverters.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor")
        inverter_leg1: First circuit/leg number
        inverter_leg2: Second circuit/leg number
        suffix: Entity-specific suffix ("instant_power", "energy_produced", etc.)
        friendly_name: Optional friendly name for legacy installations (e.g., "Solar Production")

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Convert solar inverter legs to circuit numbers list
    circuit_numbers = [inverter_leg1]
    if inverter_leg2 > 0:
        circuit_numbers.append(inverter_leg2)

    return construct_synthetic_entity_id(
        coordinator=coordinator,
        span_panel=span_panel,
        platform=platform,
        circuit_numbers=circuit_numbers,
        suffix=suffix,
        friendly_name=friendly_name,
    )


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
