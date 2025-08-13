"""Helper functions for Span Panel integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.persistent_notification import async_create
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

# Global suffix mappings for API description keys to user-friendly suffixes
# These are extracted from the helper functions for consistency and reverse mapping

# Circuit sensor API field mappings (used by get_user_friendly_suffix)
CIRCUIT_SUFFIX_MAPPING = {
    "instantPowerW": "power",
    "producedEnergyWh": "energy_produced",
    "consumedEnergyWh": "energy_consumed",
    "importedEnergyWh": "energy_imported",
    "exportedEnergyWh": "energy_exported",
    "circuit_priority": "priority",
}

# Panel sensor API field mappings (used by get_user_friendly_suffix)
PANEL_SUFFIX_MAPPING = {
    "instantGridPowerW": "grid_power",  # Descriptive to differentiate from other power types
    "feedthroughPowerW": "feed_through_power",
    "mainMeterEnergyProducedWh": "main_meter_energy_produced",  # Consistent naming
    "mainMeterEnergyConsumedWh": "main_meter_energy_consumed",  # Consistent naming
    "feedthroughEnergyProducedWh": "feed_through_energy_produced",  # Consistent naming
    "feedthroughEnergyConsumedWh": "feed_through_energy_consumed",  # Consistent naming
    "batteryPercentage": "battery_percentage",
    "dsmState": "dsm_state",
}

# Panel entity suffix mappings (used by get_panel_entity_suffix)
PANEL_ENTITY_SUFFIX_MAPPING = {
    "instantGridPowerW": "current_power",
    "feedthroughPowerW": "feed_through_power",
    "mainMeterEnergyProducedWh": "main_meter_produced_energy",
    "mainMeterEnergyConsumedWh": "main_meter_consumed_energy",
    "feedthroughEnergyProducedWh": "feed_through_produced_energy",
    "feedthroughEnergyConsumedWh": "feed_through_consumed_energy",
    "batteryPercentage": "battery_level",
    "dsmState": "dsm_state",
}

# Combined mapping for general suffix lookup
ALL_SUFFIX_MAPPINGS = {**CIRCUIT_SUFFIX_MAPPING, **PANEL_SUFFIX_MAPPING}


def get_api_description_key_from_suffix(suffix: str) -> str | None:
    """Reverse map from user-friendly suffix back to API description key.

    This is used for migration when we need to extract the original API description key
    from an entity_id suffix to call the helper functions correctly.

    Args:
        suffix: User-friendly suffix extracted from entity_id (e.g., "power", "energy_produced")

    Returns:
        API description key (e.g., "instantPowerW", "producedEnergyWh") or None if not found

    Examples:
        get_api_description_key_from_suffix("power") → "instantPowerW"
        get_api_description_key_from_suffix("energy_produced") → "producedEnergyWh"
        get_api_description_key_from_suffix("current_power") → "instantGridPowerW"

    """
    # Create reverse mapping from all suffix mappings
    reverse_mapping = {}

    # Add circuit suffix mappings
    for api_key, user_suffix in CIRCUIT_SUFFIX_MAPPING.items():
        reverse_mapping[user_suffix] = api_key

    # Add panel suffix mappings
    for api_key, user_suffix in PANEL_SUFFIX_MAPPING.items():
        reverse_mapping[user_suffix] = api_key

    # Add panel entity suffix mappings (these take precedence for panel sensors)
    for api_key, entity_suffix in PANEL_ENTITY_SUFFIX_MAPPING.items():
        reverse_mapping[entity_suffix] = api_key

    return reverse_mapping.get(suffix)


def get_suffix_from_sensor_key(sensor_key: str) -> str:
    """Extract the suffix from a sensor key for use with entity ID helpers.

    Args:
        sensor_key: Sensor key like "span_abc123_solar_inverter_power" or "span_abc123_house_total_consumption"

    Returns:
        User-friendly suffix like "power" or "consumption"

    Examples:
        get_suffix_from_sensor_key("span_abc123_solar_inverter_power") → "power"
        get_suffix_from_sensor_key("span_abc123_solar_inverter_energy_produced") → "energy_produced"
        get_suffix_from_sensor_key("span_abc123_house_total_consumption") → "consumption"

    """
    # Remove device prefix (span_{serial}_) from sensor key
    # Sensor keys follow pattern: span_{serial}_{actual_sensor_name}
    parts = sensor_key.split("_")
    if len(parts) >= 3 and parts[0] == "span":
        # Reconstruct the sensor name without the device prefix
        sensor_name = "_".join(parts[2:])
    else:
        # Fallback if pattern doesn't match expected format
        sensor_name = sensor_key

    # For solar sensors, the suffix is the last part after "solar_inverter_"
    if sensor_name.startswith("solar_inverter_"):
        return sensor_name.replace("solar_inverter_", "")

    # For other sensors, the suffix is typically the last part or last few parts
    # Look for well-established suffix patterns
    established_suffixes = [
        "energy_produced",
        "energy_consumed",
        "current_power",
        "grid_power",
        "total_power",
        "instant_power",
        "consumption",
        "production",
        "power",
        "energy",
    ]

    # Check if the sensor name ends with any established suffix
    for suffix in established_suffixes:
        if sensor_name.endswith(suffix):
            return suffix

    # If no established pattern matches, return the last part after the last underscore
    name_parts = sensor_name.split("_")
    return name_parts[-1] if name_parts else sensor_name


def is_solar_sensor_key(sensor_key: str) -> bool:
    """Check if a sensor key represents a solar sensor.

    Args:
        sensor_key: Sensor key to check (e.g., "span_abc123_solar_inverter_power")

    Returns:
        True if this is a solar sensor key

    Examples:
        is_solar_sensor_key("span_abc123_solar_inverter_power") → True
        is_solar_sensor_key("span_abc123_house_total_consumption") → False

    """
    # Remove device prefix to get the actual sensor name
    parts = sensor_key.split("_")
    if len(parts) >= 3 and parts[0] == "span":
        sensor_name = "_".join(parts[2:])
    else:
        sensor_name = sensor_key

    return sensor_name.startswith("solar_inverter_") or "solar" in sensor_name.lower()


def is_panel_level_sensor_key(sensor_key: str) -> bool:
    """Check if a sensor key represents a panel-level sensor.

    Panel-level sensors have the form: span_{device_identifier}_{sensor_type}
    Circuit sensors have the form: span_{device_identifier}_{circuit_id}_{sensor_type}

    Args:
        sensor_key: Sensor key to check (e.g., "span_sp3-simulation-001_current_power" or
                   "span_sp3-simulation-001_12ce227695cd44338864b0ef2ec4168b_power")

    Returns:
        True if this is a panel-level sensor (no circuit ID)

    Examples:
        is_panel_level_sensor_key("span_sp3-simulation-001_current_power") → True
        is_panel_level_sensor_key("span_sp3-simulation-001_12ce227695cd44338864b0ef2ec4168b_power") → False

    """

    # Must start with "span_"
    if not sensor_key.startswith("span_"):
        return False

    # Look for UUID pattern (32 hex characters) anywhere in the string after "span_"
    # Circuit IDs in SPAN are typically formatted as 32 lowercase hex characters without dashes
    uuid_pattern = re.compile(r"_[a-f0-9]{32}_")

    # If we find a UUID pattern, this is a circuit sensor
    if uuid_pattern.search(sensor_key):
        return False
    else:
        # No UUID pattern found, this is a panel-level sensor
        return True


def extract_solar_info_from_sensor_key(
    sensor_key: str, sensor_config: dict[str, Any]
) -> dict[str, Any] | None:
    """Extract solar sensor information from sensor key and config.

    Args:
        sensor_key: Solar sensor key like "span_abc123_solar_inverter_instant_power"
        sensor_config: Sensor configuration dictionary

    Returns:
        Dictionary with solar info: {"friendly_name": str, "leg1": int, "leg2": int}

    Examples:
        extract_solar_info_from_sensor_key("span_abc123_solar_inverter_instant_power", config)
        → {"friendly_name": "Solar Inverter", "leg1": 30, "leg2": 32}

    """

    if not is_solar_sensor_key(sensor_key):
        return None

    # Extract friendly name from sensor name, removing the suffix
    name = sensor_config.get("name", "")
    if name:
        # Remove common suffixes from the name to get the base friendly name
        for suffix in [" Instant Power", " Energy Produced", " Energy Consumed", " Power"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        friendly_name = name
    else:
        friendly_name = "Solar Inverter"

    # Extract circuit numbers from variables that reference backing entities
    leg1 = 0
    leg2 = 0
    variables = sensor_config.get("variables", {})

    # Look for patterns like "sensor.span_panel_solar_east_power" or "sensor.span_panel_circuit_30_power"
    for _var_name, entity_id in variables.items():
        if isinstance(entity_id, str) and "circuit_" in entity_id:
            # Extract circuit number from entity_id like "sensor.span_panel_circuit_30_power"
            parts = entity_id.split("_")
            for i, part in enumerate(parts):
                if part == "circuit" and i + 1 < len(parts):
                    try:
                        circuit_num = int(parts[i + 1])
                        if leg1 == 0:
                            leg1 = circuit_num
                        elif leg2 == 0:
                            leg2 = circuit_num
                        break
                    except ValueError:
                        continue

    return {
        "friendly_name": friendly_name,
        "leg1": leg1,
        "leg2": leg2,
    }


def construct_panel_synthetic_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    device_name: str,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for synthetic panel-level sensors with device prefix logic.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", etc.)
        suffix: Entity-specific suffix ("current_power", etc.)
        device_name: Device name for the panel
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(platform, "span_panel", unique_id)
        if existing_entity_id:
            return existing_entity_id

    config_entry = coordinator.config_entry
    if not device_name:
        return None
    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)
    parts = []
    if use_device_prefix:
        parts.append(device_name.lower().replace(" ", "_"))
    parts.append(suffix)
    entity_id = f"{platform}.{'_'.join(parts)}"
    return entity_id


def construct_240v_synthetic_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    friendly_name: str,
    tab1: int = 0,
    tab2: int = 0,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for synthetic 240V circuits using tab numbers.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        friendly_name: Descriptive name for this synthetic circuit
        tab1: First tab number (0 if not used)
        tab2: Second tab number (0 if not used)
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Build tab numbers list
    tab_numbers = []
    if tab1 > 0:
        tab_numbers.append(tab1)
    if tab2 > 0:
        tab_numbers.append(tab2)

    # Use the multi-circuit helper
    return construct_multi_circuit_entity_id(
        coordinator=coordinator,
        span_panel=span_panel,
        platform=platform,
        suffix=suffix,
        circuit_numbers=tab_numbers,
        friendly_name=friendly_name,
        unique_id=unique_id,
    )


def construct_120v_synthetic_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    friendly_name: str,
    tab: int = 0,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for synthetic 120V circuits using tab number.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        friendly_name: Descriptive name for this synthetic circuit
        tab: Tab number
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Use the 240V helper with only one tab
    return construct_240v_synthetic_entity_id(
        coordinator=coordinator,
        span_panel=span_panel,
        platform=platform,
        suffix=suffix,
        friendly_name=friendly_name,
        tab1=tab,
        tab2=0,
        unique_id=unique_id,
    )


def get_circuit_number(circuit: SpanPanelCircuit) -> int | str:
    """Extract circuit number (tab position) from circuit object.

    Args:
        circuit: SpanPanelCircuit object

    Returns:
        Circuit number (tab position) or circuit_id if no tabs

    """
    return circuit.tabs[0] if circuit.tabs else circuit.circuit_id


def get_friendly_name_from_registry(
    hass: HomeAssistant, unique_id: str | None, default_name: str, platform: str = "sensor"
) -> str:
    """Check entity registry for user's customized friendly name.

    If a user has customized the friendly name of an entity in Home Assistant,
    this function will return the user's custom name instead of the default one.
    This prevents the integration from overriding user customizations.

    Args:
        hass: Home Assistant instance
        unique_id: The unique ID to look up in the registry (None to skip registry check)
        default_name: The default friendly name to use if not found in registry
        platform: Platform name ("sensor", "switch", "binary_sensor", "select")

    Returns:
        The user's custom friendly name from registry if found, otherwise the default name

    """
    # If no unique_id provided, return default name immediately
    if unique_id is None:
        return default_name

    entity_registry = er.async_get(hass)

    # First get the entity_id using the unique_id
    existing_entity_id = entity_registry.async_get_entity_id(platform, "span_panel", unique_id)

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

    Used by switch, binary_sensor, and select entities.
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
        existing_entity_id = entity_registry.async_get_entity_id(platform, "span_panel", unique_id)

        if existing_entity_id:
            return existing_entity_id

    # Construct default entity_id
    config_entry = coordinator.config_entry

    # Use the config entry title as the device name for entity ID construction
    device_name = config_entry.title
    if not device_name:
        return None

    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, True)
    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)

    # Build entity ID components
    parts = []

    if use_device_prefix:
        parts.append(device_name.lower().replace(" ", "_"))

    if use_circuit_numbers:
        parts.append(f"circuit_{circuit_number}")
    else:
        circuit_name_slug = circuit_name.lower().replace(" ", "_")
        parts.append(circuit_name_slug)

    # Only add suffix if it's different from the last word in the circuit name
    # This prevents duplication like "current_power_power"
    if suffix:
        circuit_name_words = circuit_name.lower().split()
        last_word = circuit_name_words[-1] if circuit_name_words else ""

        # Convert last word to same format as suffix for comparison
        last_word_normalized = last_word.replace(" ", "_")

        # Only add suffix if it's not the same as the last word in the name
        if suffix != last_word_normalized:
            parts.append(suffix)

    entity_id = f"{platform}.{'_'.join(parts)}"
    return entity_id


def get_user_friendly_suffix(description_key: str) -> str:
    """Convert API description keys to user-friendly suffixes for consistent naming."""
    # If we have a direct mapping, use it
    if description_key in ALL_SUFFIX_MAPPINGS:
        return ALL_SUFFIX_MAPPINGS[description_key]

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


def get_panel_entity_suffix(description_key: str) -> str:
    """Convert panel API description keys to entity ID suffixes for unique ID consistency.

    This ensures panel unique IDs use the same suffix as entity IDs for consistency.
    """
    # If we have a direct mapping, use it
    if description_key in PANEL_ENTITY_SUFFIX_MAPPING:
        return PANEL_ENTITY_SUFFIX_MAPPING[description_key]

    # Otherwise, fall back to the general suffix mapping
    return get_user_friendly_suffix(description_key)


def build_panel_unique_id(serial: str, description_key: str) -> str:
    """Build unique ID for panel-level sensors using entity ID suffix pattern (pure function).

    Args:
        serial: Panel serial number
        description_key: Sensor description key (e.g., "instantGridPowerW")

    Returns:
        Unique ID like "span_{serial}_{entity_suffix}" (matches entity ID suffix)

    """
    entity_suffix = get_panel_entity_suffix(description_key)
    return f"span_{serial.lower()}_{entity_suffix}"


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


def construct_synthetic_unique_id(serial: str, sensor_name: str) -> str:
    """Build unique ID for synthetic sensors using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        sensor_name: Complete sensor name with suffix (e.g., "solar_inverter_power")

    Returns:
        Unique ID like "span_{serial}_{sensor_name}"

    """
    return f"span_{serial.lower()}_{sensor_name}"


def construct_sensor_set_id(device_identifier: str) -> str:
    """Build sensor set ID for synthetic sensors using consistent pattern (pure function).

    Args:
        device_identifier: Device identifier (serial number for real panels, slugified name for simulators)

    Returns:
        Sensor set ID like "{device_identifier}_sensors"

    """
    return f"{device_identifier}_sensors"


def _get_device_identifier_for_unique_ids(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    device_name: str | None = None,
) -> str:
    """Compute per-entry device identifier for unique_ids.

    - Live panels: use true serial number
    - Simulator entries: use slugified device name to avoid cross-entry collisions
    """
    is_simulator = bool(coordinator.config_entry.data.get("simulation_mode", False))
    if is_simulator:
        # For simulators, ALWAYS use the config entry title first since it's guaranteed unique
        # device_name parameter might not be unique between multiple simulator configs
        effective_name = coordinator.config_entry.title or device_name or coordinator.config_entry.data.get("device_name")
        return slugify(effective_name) if effective_name else span_panel.status.serial_number
    return span_panel.status.serial_number


def construct_panel_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build panel unique_id using per-entry identifier (handles simulators)."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    return build_panel_unique_id(identifier, description_key)


def construct_circuit_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    circuit_id: str,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build circuit unique_id using per-entry identifier (handles simulators)."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    return build_circuit_unique_id(identifier, circuit_id, description_key)


def build_switch_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    circuit_id: str,
    device_name: str | None = None,
) -> str:
    """Build switch unique_id using per-entry identifier (handles simulators)."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    return build_switch_unique_id(identifier, circuit_id)


def build_select_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    select_id: str,
    device_name: str | None = None,
) -> str:
    """Build select unique_id using per-entry identifier (handles simulators)."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    return build_select_unique_id(identifier, select_id)


def build_binary_sensor_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    description_key: str,
    device_name: str | None = None,
) -> str:
    """Build binary_sensor unique_id using per-entry identifier (handles simulators)."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    return build_binary_sensor_unique_id(identifier, description_key)


def construct_synthetic_unique_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    sensor_name: str,
    device_name: str | None = None,
) -> str:
    """Build synthetic sensor unique_id using per-entry identifier (handles simulators)."""
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    return construct_synthetic_unique_id(identifier, sensor_name)


def get_device_identifier_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    device_name: str | None = None,
) -> str:
    """Public helper to get the per-entry device identifier used in unique_ids and storage."""
    return _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)


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


def construct_multi_circuit_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    circuit_numbers: list[int],
    friendly_name: str | None = None,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for multi-circuit sensors (like solar inverters).

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
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
        existing_entity_id = entity_registry.async_get_entity_id(platform, "span_panel", unique_id)

        if existing_entity_id:
            return existing_entity_id

    # Get device name from config entry title
    device_name = coordinator.config_entry.title
    if not device_name:
        return None

    use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    # If no unique_id provided, friendly_name is required when not using circuit numbers
    if unique_id is None and not use_circuit_numbers and not friendly_name:
        _LOGGER.error(
            "friendly_name is required when unique_id is None and not using circuit numbers for multi-circuit entity"
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

    # Build the entity ID
    use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
    parts = []

    if use_device_prefix:
        if device_name:
            parts.append(device_name.lower().replace(" ", "_"))

    parts.append(circuit_part)

    # Add suffix if not already in circuit_part
    if suffix and not circuit_part.endswith(f"_{suffix}"):
        parts.append(suffix)

    return f"{platform}.{'_'.join(parts)}"


def construct_single_circuit_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    circuit_data: SpanPanelCircuit,
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for single-circuit sensors.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        suffix: Entity-specific suffix ("power", "energy_produced", etc.)
        circuit_data: Circuit data object
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(platform, "span_panel", unique_id)

        if existing_entity_id:
            return existing_entity_id

    # Get device info
    device_info = panel_to_device_info(span_panel)
    if not device_info or not device_info.get("name"):
        return None

    use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    if use_circuit_numbers:
        # Use circuit number pattern: sensor.span_panel_circuit_30_power
        circuit_number = get_circuit_number(circuit_data)
        if circuit_number:
            circuit_part = f"circuit_{circuit_number}"
        else:
            circuit_part = "circuit_unknown"
    else:
        # Use friendly name pattern: sensor.span_panel_solar_east_power
        if circuit_data.name:
            circuit_part = slugify(circuit_data.name)
        else:
            circuit_part = "single_circuit"

    # Build the entity ID
    use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
    parts = []

    if use_device_prefix:
        device_name = device_info.get("name")
        if device_name:
            parts.append(device_name.lower().replace(" ", "_"))

    parts.append(circuit_part)

    # Add suffix if not already in circuit_part
    if suffix and not circuit_part.endswith(f"_{suffix}"):
        parts.append(suffix)

    return f"{platform}.{'_'.join(parts)}"


def construct_panel_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    suffix: str,
    device_name: str,
    unique_id: str | None = None,
    use_device_prefix: bool | None = None,
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
        device_name: Device name for the panel
        unique_id: The unique ID for this entity (None to skip registry lookup)
        use_device_prefix: Whether to include device name prefix in entity ID (None to use config option)

    Returns:
        Constructed entity ID string or None if device info unavailable

    """
    # Check registry first only if unique_id is provided
    if unique_id is not None:
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(platform, "span_panel", unique_id)

        if existing_entity_id:
            return existing_entity_id

    # Construct default entity_id
    config_entry = coordinator.config_entry

    if not device_name:
        return None

    if use_device_prefix is None:
        use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)

    # Build entity ID components
    parts = []

    if use_device_prefix:
        parts.append(device_name.lower().replace(" ", "_"))

    parts.append(suffix)

    entity_id = f"{platform}.{'_'.join(parts)}"
    return entity_id


def construct_backing_entity_id(
    span_panel: SpanPanel,
    circuit_id: str | None = None,
    suffix: str = "",
) -> str:
    """Construct backing entity ID following document pattern for synthetic sensor references.

    These are internal references used only within synthetic sensor YAML configuration
    and are never registered in Home Assistant. They follow the document pattern:
    sensor.span_{serial}_{circuit_id}_backing_{description_key}

    Args:
        span_panel: The span panel data
        circuit_id: Circuit ID from panel API (UUID for circuits, "0" for panel-level)
        suffix: Sensor type suffix ("power", "energy_produced", etc.)

    Returns:
        Backing entity ID following document pattern with sensor prefix

    Examples:
        Circuit: "sensor.span_abc123_0dad2f16cd514812ae1807b0457d473e_backing_power"
        Panel: "sensor.span_abc123_0_backing_current_power"

    """
    serial = span_panel.status.serial_number.lower()

    # Use circuit_id directly, defaulting to "0" for panel-level sensors
    circuit_part = circuit_id if circuit_id is not None else "0"

    return f"sensor.span_{serial}_{circuit_part}_backing_{suffix}"


def construct_backing_entity_id_for_entry(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    circuit_id: str | None,
    suffix: str,
    device_name: str | None = None,
) -> str:
    """Construct backing entity ID using per-entry device identifier.

    Pattern remains: sensor.span_{identifier}_{circuit_id}_backing_{suffix}
    """
    identifier = _get_device_identifier_for_unique_ids(coordinator, span_panel, device_name)
    circuit_part = circuit_id if circuit_id is not None else "0"
    return f"sensor.span_{identifier.lower()}_{circuit_part}_backing_{suffix}"


def construct_unmapped_unique_id(
    span_panel: SpanPanel, circuit_number: int | str, suffix: str
) -> str:
    """Construct unique ID for unmapped circuit sensors."""
    # Always use consistent unique ID pattern for unmapped circuits
    # Format: span_{serial}_unmapped_tab_{circuit_number}_{suffix}
    return f"span_{span_panel.status.serial_number}_unmapped_tab_{circuit_number}_{suffix}"


def construct_unmapped_entity_id(
    span_panel: SpanPanel, circuit_id: str, suffix: str, device_name: str | None = None
) -> str:
    """Construct entity ID for unmapped tab with consistent modern naming.

    Args:
        span_panel: The span panel data
        circuit_id: Circuit ID (e.g., "unmapped_tab_32")
        suffix: Sensor suffix (e.g., "power", "energy_produced")
        device_name: The device name to use for entity ID construction

    Returns:
        Entity ID string like "sensor.span_panel_unmapped_tab_32_power"

    """
    # Always use device prefix for unmapped entities
    # circuit_id is "unmapped_tab_32", add device prefix and suffix to create
    # "sensor.span_panel_unmapped_tab_32_power"
    device_info = panel_to_device_info(span_panel, device_name)
    device_name_raw = device_info.get("name")
    _LOGGER.debug(
        "construct_unmapped_entity_id: circuit_id=%s, suffix=%s, device_name_raw=%s",
        circuit_id,
        suffix,
        device_name_raw,
    )
    if device_name_raw:
        device_name = slugify(device_name_raw)
        result = f"sensor.{device_name}_{circuit_id}_{suffix}"
        _LOGGER.debug("construct_unmapped_entity_id result with device: %s", result)
        return result
    else:
        result = f"sensor.{circuit_id}_{suffix}"
        _LOGGER.debug("construct_unmapped_entity_id result without device: %s", result)
        return result


def get_unmapped_circuit_entity_id(
    span_panel: SpanPanel, tab_number: int, suffix: str, device_name: str | None = None
) -> str | None:
    """Get entity ID for an unmapped circuit based on tab number.

    This helper function constructs the entity ID for native unmapped circuit sensors
    that should already exist in Home Assistant. It's useful for synthetic sensors
    that need to reference these native entities in formulas.

    Args:
        span_panel: The span panel data
        tab_number: The tab number (e.g., 30, 32)
        suffix: The sensor suffix (e.g., "power", "energy_produced", "energy_consumed")
        device_name: The device name to use for entity ID construction

    Returns:
        Entity ID string like "sensor.span_panel_unmapped_tab_30_power"
        or None if the circuit doesn't exist

    Examples:
        get_unmapped_circuit_entity_id(span_panel, 30, "power")
        # Returns: "sensor.span_panel_unmapped_tab_30_power"

        get_unmapped_circuit_entity_id(span_panel, 32, "energy_produced")
        # Returns: "sensor.span_panel_unmapped_tab_32_energy_produced"

    """
    circuit_id = f"unmapped_tab_{tab_number}"

    # Debug logging to understand what circuits are available
    _LOGGER.debug(
        "get_unmapped_circuit_entity_id called for tab_number=%s, suffix=%s", tab_number, suffix
    )
    _LOGGER.debug(
        "Looking for circuit_id: %s in circuits: %s", circuit_id, list(span_panel.circuits.keys())
    )
    _LOGGER.debug("FULL CIRCUITS LIST: %s", span_panel.circuits)

    # Verify the circuit exists in the panel data
    if circuit_id not in span_panel.circuits:
        _LOGGER.debug("Unmapped circuit %s not found in circuits list", circuit_id)
        return None

    result_entity_id = construct_unmapped_entity_id(span_panel, circuit_id, suffix, device_name)
    _LOGGER.debug("Generated unmapped entity ID: %s", result_entity_id)
    return result_entity_id


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


def construct_unmapped_circuit_id(circuit_number: int | str) -> str:
    """Construct circuit ID for unmapped circuits.

    This returns just the circuit ID part (e.g., "unmapped_tab_30"), not a full entity ID.
    Used for API circuit references and internal circuit identification.

    Args:
        circuit_number: The tab number (e.g., 30, 32)

    Returns:
        Circuit ID string like "unmapped_tab_30"

    Examples:
        construct_unmapped_circuit_id(30) -> "unmapped_tab_30"
        construct_unmapped_circuit_id(32) -> "unmapped_tab_32"

    """
    return f"unmapped_tab_{circuit_number}"


def sanitize_device_name_for_yaml(device_name: str) -> str:
    """Sanitize device name for use in YAML keys.

    Removes or replaces characters that would create invalid YAML keys.

    Args:
        device_name: The device name to sanitize

    Returns:
        Sanitized device name safe for YAML keys

    """
    # Replace problematic characters with underscores
    sanitized = device_name.lower()
    sanitized = sanitized.replace(" ", "_")
    sanitized = sanitized.replace("(", "")
    sanitized = sanitized.replace(")", "")
    sanitized = sanitized.replace(":", "")
    sanitized = sanitized.replace("-", "_")
    sanitized = sanitized.replace(".", "_")
    sanitized = sanitized.replace(",", "_")
    sanitized = sanitized.replace("'", "")
    sanitized = sanitized.replace('"', "")
    sanitized = sanitized.replace("&", "_and_")
    sanitized = sanitized.replace("+", "_plus_")
    sanitized = sanitized.replace("=", "_equals_")
    sanitized = sanitized.replace("!", "")
    sanitized = sanitized.replace("?", "")
    sanitized = sanitized.replace("@", "_at_")
    sanitized = sanitized.replace("#", "_hash_")
    sanitized = sanitized.replace("$", "_dollar_")
    sanitized = sanitized.replace("%", "_percent_")
    sanitized = sanitized.replace("^", "")
    sanitized = sanitized.replace("*", "_star_")
    sanitized = sanitized.replace("[", "")
    sanitized = sanitized.replace("]", "")
    sanitized = sanitized.replace("{", "")
    sanitized = sanitized.replace("}", "")
    sanitized = sanitized.replace("|", "_or_")
    sanitized = sanitized.replace("\\", "_")
    sanitized = sanitized.replace("/", "_")
    sanitized = sanitized.replace("<", "_lt_")
    sanitized = sanitized.replace(">", "_gt_")
    sanitized = sanitized.replace("~", "_tilde_")
    sanitized = sanitized.replace("`", "")

    # Remove multiple consecutive underscores
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")

    # Remove leading/trailing underscores
    sanitized = sanitized.strip("_")

    return sanitized


def construct_tabs_attribute(circuit: SpanPanelCircuit) -> str | None:
    """Construct tabs attribute string from circuit data.

    For US electrical systems, circuits can only have 1 tab (120V) or 2 tabs (240V).

    Args:
        circuit: SpanPanelCircuit object with tabs information

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
    elif len(sorted_tabs) == 2:
        # Two tabs (240V) - format as range
        return f"tabs [{sorted_tabs[0]}:{sorted_tabs[1]}]"
    else:
        # More than 2 tabs is not valid for US electrical system
        _LOGGER.warning(
            "Circuit %s has %d tabs, which is not valid for US electrical system (expected 1 or 2)",
            circuit.circuit_id,
            len(sorted_tabs),
        )
        return None


def parse_tabs_attribute(tabs_attr: str) -> list[int] | None:
    """Parse tabs attribute string back to list of tab numbers.

    For US electrical systems, only 1 tab (120V) or 2 tabs (240V) are valid.

    Args:
        tabs_attr: Tabs attribute string like "tabs [30:32]" or "tabs [28]"

    Returns:
        List of tab numbers, or None if parsing fails or invalid for US electrical system

    Examples:
        "tabs [28]" -> [28] (120V)
        "tabs [30:32]" -> [30, 32] (240V)

    """
    if not tabs_attr or not tabs_attr.startswith("tabs ["):
        return None

    try:
        # Extract content between brackets
        content = tabs_attr[6:-1]  # Remove "tabs [" and "]"

        if ":" in content:
            # Range format: "30:32" (240V)
            start, end = map(int, content.split(":"))
            return [start, end]
        else:
            # Single tab: "28" (120V)
            return [int(content)]

    except (ValueError, IndexError) as e:
        _LOGGER.warning("Failed to parse tabs attribute '%s': %s", tabs_attr, e)
        return None


def get_circuit_voltage_type(circuit: SpanPanelCircuit) -> str:
    """Determine the voltage type of a circuit based on its tabs.

    For US electrical systems, circuits can only be 120V (1 tab) or 240V (2 tabs).

    Args:
        circuit: SpanPanelCircuit object

    Returns:
        Voltage type: "120V" for single tab, "240V" for two tabs, "unknown" otherwise

    """
    if not circuit.tabs:
        return "unknown"

    if len(circuit.tabs) == 1:
        return "120V"
    elif len(circuit.tabs) == 2:
        return "240V"
    else:
        # More than 2 tabs is not valid for US electrical system
        _LOGGER.warning(
            "Circuit %s has %d tabs, which is not valid for US electrical system (expected 1 or 2)",
            circuit.circuit_id,
            len(circuit.tabs),
        )
        return "unknown"


def get_panel_voltage_attribute() -> int:
    """Get voltage attribute for panel-level sensors.

    US residential electrical panels are standardized as 240V split-phase systems.
    Panel-level sensors (like main meter energy) represent aggregate measurements
    at the full panel voltage.

    Returns:
        Panel voltage in volts (always 240 for US residential panels)

    """
    return 240


def construct_voltage_attribute(circuit: SpanPanelCircuit) -> int | None:
    """Construct voltage attribute for a circuit based on tab count.

    For US electrical systems, circuits can only have 1 tab (120V) or 2 tabs (240V).

    Args:
        circuit: SpanPanelCircuit object with tabs information

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
    elif len(circuit.tabs) == 2:
        return 240
    else:
        # More than 2 tabs is not valid for US electrical system
        _LOGGER.warning(
            "Circuit %s has %d tabs, which is not valid for US electrical system (expected 1 or 2)",
            circuit.circuit_id,
            len(circuit.tabs),
        )
        return None


# Rename the dispatcher and update docstring


def construct_multi_tab_entity_id_from_key(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    platform: str,
    sensor_key: str,
    sensor_config: dict[str, Any],
    unique_id: str | None = None,
) -> str | None:
    """Construct entity ID for multi-tab (e.g., 240V) or synthetic sensor using sensor key.

    This is a convenience helper that determines the appropriate entity ID construction
    method based on the sensor key and calls the right helper function.

    Args:
        coordinator: The coordinator instance
        span_panel: The span panel data
        platform: Platform name ("sensor", "switch", "select")
        sensor_key: Sensor key like "span_abc123_solar_inverter_instant_power"
        sensor_config: Sensor configuration dictionary
        unique_id: The unique ID for this entity (None to skip registry lookup)

    Returns:
        Constructed entity ID string or None if unable to construct

    """
    # Extract suffix from sensor key
    suffix = get_suffix_from_sensor_key(sensor_key)

    # Check if sensor has tabs attribute that we can use for circuit-based naming
    tabs_attr = sensor_config.get("attributes", {}).get("tabs")
    if tabs_attr:
        tab_numbers = parse_tabs_attribute(tabs_attr)
        if tab_numbers:
            _LOGGER.debug(
                "Using tabs attribute '%s' for entity ID construction: %s",
                tabs_attr,
                tab_numbers,
            )
            # Use multi-circuit helper with tab numbers from attribute
            return construct_multi_circuit_entity_id(
                coordinator=coordinator,
                span_panel=span_panel,
                platform=platform,
                suffix=suffix,
                circuit_numbers=tab_numbers,
                friendly_name=sensor_config.get("name"),
                unique_id=unique_id,
            )

    # Determine sensor type and use appropriate helper
    if is_solar_sensor_key(sensor_key):
        # Multi-tab (e.g., 240V) synthetic sensor
        solar_info = extract_solar_info_from_sensor_key(sensor_key, sensor_config)
        if solar_info:
            friendly_name = solar_info.get("friendly_name", "Solar")
            if not isinstance(friendly_name, str):
                friendly_name = "Solar"
            return construct_240v_synthetic_entity_id(
                coordinator=coordinator,
                span_panel=span_panel,
                platform=platform,
                suffix=suffix,
                friendly_name=friendly_name,
                unique_id=unique_id,
                tab1=solar_info.get("leg1", 0),
                tab2=solar_info.get("leg2", 0),
            )

    elif is_panel_level_sensor_key(sensor_key):
        # Panel-level sensor
        device_name = coordinator.config_entry.title
        return construct_panel_entity_id(
            coordinator=coordinator,
            span_panel=span_panel,
            platform=platform,
            suffix=suffix,
            device_name=device_name,
            unique_id=unique_id,
        )

    else:
        # Other synthetic sensors - use multi-circuit helper with friendly name from config
        name = sensor_config.get("name", "")
        if name:
            # Remove suffix from name to get base friendly name
            for suffix_pattern in [" Power", " Energy", " Consumption", " Production"]:
                if name.endswith(suffix_pattern):
                    name = name[: -len(suffix_pattern)]
                    break

            return construct_multi_circuit_entity_id(
                coordinator=coordinator,
                span_panel=span_panel,
                platform=platform,
                suffix=suffix,
                circuit_numbers=[],  # Non-solar synthetics don't necessarily map to specific circuits
                friendly_name=name,
                unique_id=unique_id,
            )

    return None
