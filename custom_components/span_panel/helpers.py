"""Helper functions for Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.util import slugify

from .const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from .span_panel import SpanPanel
from .util import panel_to_device_info

if TYPE_CHECKING:
    from .coordinator import SpanPanelCoordinator
    from .span_panel_circuit import SpanPanelCircuit

_LOGGER = logging.getLogger(__name__)


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
            device_name = slugify(device_name_raw)
            return f"{platform}.{device_name}_circuit_{circuit_number}_{suffix}"
        else:
            return None

    elif use_device_prefix:
        # Post-1.0.4 installation - friendly names with device prefix
        # Format: sensor.span_panel_kitchen_outlets_power
        if device_name_raw:
            device_name = slugify(device_name_raw)
            circuit_name_sanitized = slugify(circuit_name)
            return f"{platform}.{device_name}_{circuit_name_sanitized}_{suffix}"
        else:
            return None

    else:
        # Pre-1.0.4 installation - no device prefix, just circuit names
        circuit_name_sanitized = slugify(circuit_name)
        return f"{platform}.{circuit_name_sanitized}_{suffix}"


def get_user_friendly_suffix(description_key: str) -> str:
    """Convert API description keys to user-friendly suffixes for consistent naming."""
    suffix_mapping = {
        # Circuit sensor API field mappings
        "instantPowerW": "power",
        "producedEnergyWh": "energy_produced",
        "consumedEnergyWh": "energy_consumed",
        "importedEnergyWh": "energy_imported",
        "exportedEnergyWh": "energy_exported",
        "circuit_priority": "priority",
        # Panel sensor API field mappings - CONSISTENT PATTERN
        "instantGridPowerW": "grid_power",  # Descriptive to differentiate from other power types
        "feedthroughPowerW": "feed_through_power",
        "mainMeterEnergyProducedWh": "main_meter_energy_produced",  # Consistent naming
        "mainMeterEnergyConsumedWh": "main_meter_energy_consumed",  # Consistent naming
        "feedthroughEnergyProducedWh": "feed_through_energy_produced",  # Consistent naming
        "feedthroughEnergyConsumedWh": "feed_through_energy_consumed",  # Consistent naming
        "batteryPercentage": "battery_percentage",
        "dsmState": "dsm_state",
    }
    # If we have a direct mapping, use it
    if description_key in suffix_mapping:
        return suffix_mapping[description_key]

    # Otherwise, sanitize by converting dots to underscores and making lowercase
    return description_key.replace(".", "_").lower()


def build_circuit_unique_id(serial: str, circuit_id: str, description_key: str) -> str:
    """Build unique ID for circuit sensors using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        circuit_id: Circuit ID from panel API (UUID or tab number)
        description_key: Sensor description key (e.g., "instantPowerW")

    Returns:
        Unique ID like "span_{serial}_{circuit_id}_{consistent_suffix}"

    """
    consistent_suffix = get_user_friendly_suffix(description_key)
    return f"span_{serial.lower()}_{circuit_id}_{consistent_suffix}"


def build_panel_unique_id(serial: str, description_key: str) -> str:
    """Build unique ID for panel-level sensors using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        description_key: Sensor description key (e.g., "instantGridPowerW")

    Returns:
        Unique ID like "span_{serial}_{consistent_suffix}"

    """
    consistent_suffix = get_user_friendly_suffix(description_key)
    return f"span_{serial.lower()}_{consistent_suffix}"


def build_switch_unique_id(serial: str, circuit_id: str) -> str:
    """Build unique ID for switch entities using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        circuit_id: Circuit ID from panel API

    Returns:
        Unique ID like "span_{serial}_relay_{circuit_id}"

    """
    return f"span_{serial}_relay_{circuit_id}"


def build_binary_sensor_unique_id(serial: str, description_key: str) -> str:
    """Build unique ID for binary sensor entities using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        description_key: Sensor description key (e.g., "doorState")

    Returns:
        Unique ID like "span_{serial}_{description_key}"

    """
    return f"span_{serial}_{description_key}"


def build_select_unique_id(serial: str, select_id: str) -> str:
    """Build unique ID for select entities using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        select_id: Select entity identifier

    Returns:
        Unique ID like "span_{serial}_select_{select_id}"

    """
    return f"span_{serial}_select_{select_id}"


def build_synthetic_unique_id(serial: str, sensor_name: str) -> str:
    """Build unique ID for synthetic sensors using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        sensor_name: Complete sensor name with suffix (e.g., "solar_inverter_power")

    Returns:
        Unique ID like "span_{serial}_{sensor_name}"

    """
    return f"span_{serial.lower()}_{sensor_name}"


def construct_circuit_unique_id(
    span_panel: SpanPanel, circuit_id: str, description_key: str
) -> str:
    """Construct unique ID for circuit sensors using consistent pattern.

    Args:
        span_panel: The span panel data
        circuit_id: Circuit ID from panel API (UUID or tab number)
        description_key: Sensor description key (e.g., "instantPowerW")

    Returns:
        Unique ID like "span_{serial}_{circuit_id}_{consistent_suffix}"

    Examples:
        span_abc123_0dad2f16cd514812ae1807b0457d473e_power
        span_abc123_circuit_15_energy_produced

    """
    return build_circuit_unique_id(span_panel.status.serial_number, circuit_id, description_key)


def construct_panel_unique_id(span_panel: SpanPanel, description_key: str) -> str:
    """Construct unique ID for panel-level sensors using consistent pattern.

    Args:
        span_panel: The span panel data
        description_key: Sensor description key (e.g., "instantGridPowerW")

    Returns:
        Unique ID like "span_{serial}_{consistent_suffix}" (uses descriptive consistent names)

    Examples:
        span_abc123_grid_power
        span_abc123_feed_through_power
        span_abc123_dsm_state

    """
    return build_panel_unique_id(span_panel.status.serial_number, description_key)


def construct_switch_unique_id(span_panel: SpanPanel, circuit_id: str) -> str:
    """Construct unique ID for switch entities using consistent pattern.

    Args:
        span_panel: The span panel data
        circuit_id: Circuit ID from panel API

    Returns:
        Unique ID like "span_{serial}_relay_{circuit_id}"

    Examples:
        span_abc123_relay_0dad2f16cd514812ae1807b0457d473e

    """
    return build_switch_unique_id(span_panel.status.serial_number, circuit_id)


def construct_binary_sensor_unique_id(span_panel: SpanPanel, description_key: str) -> str:
    """Construct unique ID for binary sensor entities using consistent pattern.

    Args:
        span_panel: The span panel data
        description_key: Sensor description key (e.g., "doorState")

    Returns:
        Unique ID like "span_{serial}_{description_key}"

    Examples:
        span_abc123_doorState
        span_abc123_eth0Link

    """
    return build_binary_sensor_unique_id(span_panel.status.serial_number, description_key)


def construct_select_unique_id(span_panel: SpanPanel, select_id: str) -> str:
    """Construct unique ID for select entities using consistent pattern.

    Args:
        span_panel: The span panel data
        select_id: Select entity identifier

    Returns:
        Unique ID like "span_{serial}_select_{select_id}"

    Examples:
        span_abc123_select_priority_mode

    """
    return build_select_unique_id(span_panel.status.serial_number, select_id)


def construct_synthetic_unique_id(span_panel: SpanPanel, sensor_name: str) -> str:
    """Construct unique ID for synthetic sensors using consistent pattern.

    Args:
        span_panel: The span panel data
        sensor_name: Complete sensor name with suffix (e.g., "solar_inverter_power")

    Returns:
        Unique ID like "span_{serial}_{sensor_name}"

    Examples:
        span_abc123_solar_inverter_power
        span_abc123_backup_circuits_power
        span_abc123_whole_house_net_power

    """
    return build_synthetic_unique_id(span_panel.status.serial_number, sensor_name)


def construct_sensor_manager_unique_id(
    serial_number: str, circuit_id: str | None, description_key: str
) -> str:
    """Construct unique ID for sensor manager (synthetic sensors) using consistent pattern.

    This function generates unique IDs that match the native sensor patterns
    using consistent suffixes for compatibility with migration logic.

    Args:
        serial_number: Panel serial number
        circuit_id: Circuit ID (None for panel-level sensors)
        description_key: Sensor description key

    Returns:
        Unique ID string following consistent pattern

    Examples:
        Circuit: span_abc123_0dad2f16cd514812ae1807b0457d473e_power
        Panel: span_abc123_power

    """
    if circuit_id:
        # Circuit sensor: use build_circuit_unique_id for consistency
        return build_circuit_unique_id(serial_number, circuit_id, description_key)
    else:
        # Panel sensor: use build_panel_unique_id for consistency
        return build_panel_unique_id(serial_number, description_key)


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

    # Get device info for device name
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name")

    # Construct the entity name part
    # Synthetic sensors always use friendly names regardless of USE_CIRCUIT_NUMBERS
    if friendly_name:
        # Convert friendly name to entity ID format (e.g., "Solar Inverter" -> "solar_inverter")
        entity_name = slugify(friendly_name)
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
        device_name = slugify(device_name_raw)
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
    """Construct entity ID for panel-level sensors based on integration configuration flags.

    This function handles entity naming for panel-level entities based on the
    USE_DEVICE_PREFIX configuration flag.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("current_power", "feed_through_power", etc.)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    config_entry = coordinator.config_entry

    # For existing installations with empty options, default to False for backward compatibility
    # For new installations, these will be explicitly set to True in create_new_entry()
    if not config_entry.options:
        # Empty options = existing installation, use legacy defaults
        use_device_prefix = False
    else:
        # Has options = either new installation or existing installation that went through options flow
        use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)

    # Get device info for device name
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name")

    if use_device_prefix:
        # Installation with device prefix enabled
        # Format: sensor.span_panel_current_power
        if device_name_raw:
            device_name = slugify(device_name_raw)
            return f"{platform}.{device_name}_{suffix}"
        else:
            return None
    else:
        # Installation without device prefix
        # Format: sensor.current_power
        return f"{platform}.{suffix}"


def construct_backing_entity_id(
    span_panel: SpanPanel,
    circuit_number: str | int | None = None,
    circuit_name: str | None = None,
    suffix: str = "",
    entity_type: str = "circuit",
) -> str:
    """Construct human-readable backing entity ID for internal data provider use.

    These are internal references used only within synthetic sensor YAML configuration
    and are never registered in Home Assistant. They provide clear, readable names
    for humans working with the YAML files.

    For circuit sensors, uses only circuit numbers (tab positions) to create stable
    references that don't change when users rename circuits in the SPAN mobile app.
    Friendly names are used only in user-facing synthetic sensors, not in backing entity IDs.

    Args:
        span_panel: The span panel data
        circuit_number: Circuit number/tab position (for circuit sensors)
        circuit_name: Human-readable circuit name (for circuit sensors - not used in backing IDs)
        suffix: Sensor type suffix ("power", "energy_produced", etc.)
        entity_type: Type of entity ("circuit", "panel", "battery")

    Returns:
        Backing entity ID like "span_panel_synthetic_backing.circuit_15_power"

    Examples:
        Circuit: "span_panel_synthetic_backing.circuit_15_power"
        Panel: "span_panel_synthetic_backing.panel_grid_power"
        Battery: "span_panel_synthetic_backing.battery_percentage"

    """
    # Get device name for consistent prefix
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name", "span_panel")
    device_name = slugify(device_name_raw or "span_panel")

    # Construct the backing entity ID parts
    base_prefix = f"{device_name}_synthetic_backing"

    if entity_type == "circuit" and circuit_number:
        # Circuit sensors: use circuit number for stable reference
        # Friendly names are only used in user-facing synthetic sensors, not backing entities
        entity_part = f"circuit_{circuit_number}"
        if suffix:
            entity_part = f"{entity_part}_{suffix}"
    elif entity_type == "panel":
        # Panel sensors: use circuit_0 for consistency with circuit sensor format
        # This keeps all backing entity IDs in the same format: circuit_X_suffix
        entity_part = f"circuit_0_{suffix}" if suffix else "circuit_0"
    elif entity_type == "battery":
        # Battery sensors: use circuit_0 for consistency (batteries are panel-level)
        # This keeps all backing entity IDs in the same format: circuit_X_suffix
        entity_part = f"circuit_0_{suffix}" if suffix else "circuit_0"
    else:
        # Fallback for other types
        entity_part = f"{entity_type}_{suffix}" if suffix else entity_type

    return f"{base_prefix}.{entity_part}"


def construct_unmapped_unique_id(
    span_panel: SpanPanel, circuit_number: int | str, suffix: str
) -> str:
    """Construct unique ID for unmapped circuit sensors."""
    # Always use consistent unique ID pattern for unmapped circuits
    # Format: span_{serial}_unmapped_tab_{circuit_number}_{suffix}
    return f"span_{span_panel.status.serial_number}_unmapped_tab_{circuit_number}_{suffix}"


def construct_unmapped_entity_id(span_panel: SpanPanel, circuit_id: str, suffix: str) -> str:
    """Construct entity ID for unmapped tab with consistent modern naming."""
    # Always use device prefix for unmapped entities
    # circuit_id is "unmapped_tab_32", add device prefix and suffix to create
    # "sensor.span_panel_unmapped_tab_32_power"
    device_info = panel_to_device_info(span_panel)
    device_name_raw = device_info.get("name")
    if device_name_raw:
        device_name = slugify(device_name_raw)
        return f"sensor.{device_name}_{circuit_id}_{suffix}"
    else:
        return f"sensor.{circuit_id}_{suffix}"


def construct_unmapped_friendly_name(
    circuit_number: int | str, sensor_description_name: str
) -> str:
    """Construct friendly name for unmapped circuit sensors."""
    # Format: "Unmapped Tab 32 Consumed Energy"
    return f"Unmapped Tab {circuit_number} {sensor_description_name}"


def construct_panel_friendly_name(description_name: Any) -> str:
    """Construct friendly name for panel-level sensors.

    Args:
        description_name: The sensor description name (can be str, None, or UndefinedType)

    Returns:
        Friendly name string

    """
    return str(description_name) if description_name else ""


def construct_status_friendly_name(description_name: Any) -> str:
    """Construct friendly name for status sensors.

    Args:
        description_name: The sensor description name (can be str, None, or UndefinedType)

    Returns:
        Friendly name string

    """
    return str(description_name) if description_name else ""
