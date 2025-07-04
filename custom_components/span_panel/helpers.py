"""Helper functions for Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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


def get_friendly_name_from_registry(
    hass: HomeAssistant, unique_id: str | None, default_name: str
) -> str:
    """Check entity registry for user's customized friendly name.

    If a user has customized the friendly name of an entity in Home Assistant,
    this function will return the user's custom name instead of the default one.
    This prevents the integration from overriding user customizations.

    Args:
        hass: Home Assistant instance
        unique_id: The unique ID to look up in the registry (None to skip registry check)
        default_name: The default friendly name to use if not found in registry

    Returns:
        The user's custom friendly name from registry if found, otherwise the default name

    """
    # If no unique_id provided, return default name immediately
    if unique_id is None:
        return default_name

    entity_registry = er.async_get(hass)

    # First get the entity_id using the unique_id
    existing_entity_id = entity_registry.async_get_entity_id("sensor", "span_panel", unique_id)

    if existing_entity_id:
        # Now get the full entity entry using the entity_id
        entity_entry = entity_registry.entities.get(existing_entity_id)

        if entity_entry and entity_entry.name:
            _LOGGER.debug(
                "Found custom friendly name in registry: unique_id=%s -> name=%s",
                unique_id,
                entity_entry.name,
            )
            return entity_entry.name

    return default_name


def construct_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    circuit_name: str,
    circuit_number: int | str,
    suffix: str,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID based on integration configuration flags.

    This function handles entity naming for individual circuit entities based on the
    USE_CIRCUIT_NUMBERS and USE_DEVICE_PREFIX configuration flags. It also checks
    the entity registry to respect user customizations when unique_id is provided.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        circuit_name: Human-readable circuit name
        circuit_number: Circuit number/identifier
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id("sensor", "span_panel", unique_id)

        if existing_entity_id:
            _LOGGER.debug(
                "Found existing entity in registry: unique_id=%s -> entity_id=%s",
                unique_id,
                existing_entity_id,
            )
            return existing_entity_id

    # Construct default entity_id
    config_entry = coordinator.config_entry
    device_info = panel_to_device_info(span_panel)

    if not device_info or not device_info.get("name"):
        return None

    device_name = device_info.get("name")
    if not device_name:
        return None

    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

    # Build entity ID components
    parts = []

    if use_device_prefix:
        parts.append(device_name.lower().replace(" ", "_"))

    if use_circuit_numbers:
        parts.append(f"circuit_{circuit_number}")
    else:
        parts.append(circuit_name.lower().replace(" ", "_"))

    if suffix:
        parts.append(suffix)

    entity_id = f"{platform}.{'_'.join(parts)}"
    return entity_id


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


def constuct_synthetic_unique_id(serial: str, sensor_name: str) -> str:
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
    suffix: str,
    friendly_name: str | None = None,
    unique_id: str | None = None,
) -> str | None:
    """Construct synthetic entity ID for multi-circuit entities using stable naming.

    This function handles entity naming for synthetic sensors that combine multiple circuits,
    such as solar inverters or custom circuit groups. For backward compatibility, synthetic
    sensors respect the USE_DEVICE_PREFIX setting, unlike individual circuit entities.
    It also checks the entity registry to respect user customizations when unique_id is provided.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        friendly_name: Optional friendly name for the entity
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id("sensor", "span_panel", unique_id)

        if existing_entity_id:
            _LOGGER.debug(
                "Found existing entity in registry: unique_id=%s -> entity_id=%s",
                unique_id,
                existing_entity_id,
            )
            return existing_entity_id

    # Construct default entity_id
    config_entry = coordinator.config_entry
    device_info = panel_to_device_info(span_panel)

    if not device_info or not device_info.get("name"):
        return None

    device_name = device_info.get("name")
    if not device_name:
        return None

    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

    # Build entity ID components
    parts = []

    if use_device_prefix:
        parts.append(device_name.lower().replace(" ", "_"))

    # Use friendly name if provided, otherwise use a default synthetic pattern
    if friendly_name:
        # Use slugified friendly name for entity ID
        slugified_name = slugify(friendly_name)
        parts.append(slugified_name)

        # Only add suffix if the slugified friendly name doesn't already end with it
        if suffix and not slugified_name.endswith(f"_{suffix}"):
            parts.append(suffix)
    else:
        # Default pattern for synthetic sensors without friendly name
        parts.append("synthetic_sensor")
        if suffix:
            parts.append(suffix)

    entity_id = f"{platform}.{'_'.join(parts)}"
    return entity_id


def construct_panel_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for panel-level sensors based on integration configuration flags.

    This function handles entity naming for panel-level entities based on the
    USE_DEVICE_PREFIX configuration flag. It also checks the entity registry
    to respect user customizations when unique_id is provided.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("current_power", "feed_through_power", etc.)
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id("sensor", "span_panel", unique_id)

        if existing_entity_id:
            _LOGGER.debug(
                "Found existing entity in registry: unique_id=%s -> entity_id=%s",
                unique_id,
                existing_entity_id,
            )
            return existing_entity_id

    # Construct default entity_id
    config_entry = coordinator.config_entry
    device_info = panel_to_device_info(span_panel)

    if not device_info or not device_info.get("name"):
        return None

    device_name = device_info.get("name")
    if not device_name:
        return None

    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

    # Build entity ID components
    parts = []

    if use_device_prefix:
        parts.append(device_name.lower().replace(" ", "_"))

    parts.append(suffix)

    entity_id = f"{platform}.{'_'.join(parts)}"
    return entity_id


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
