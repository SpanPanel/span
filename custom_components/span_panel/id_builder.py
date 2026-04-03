"""Pure ID construction and suffix mapping functions for Span Panel integration.

This module contains functions that build unique IDs and map suffixes.
It has NO dependency on Home Assistant, coordinator, or entity registry --
only logging, re, and span_panel_api types.
"""

from __future__ import annotations

import logging
import re

from span_panel_api import SpanPanelSnapshot

_LOGGER = logging.getLogger(__name__)

# Global suffix mappings for API description keys to user-friendly/entity suffixes
# These mappings drive consistent unique_id/entity_id suffixes across all sensors,
# including Net Energy and import/export flows, and are used for reverse lookups.

# Circuit sensor API field mappings (used by get_user_friendly_suffix)
# Includes power, produced/consumed, net energy, and import/export energy
CIRCUIT_SUFFIX_MAPPING = {
    "instantPowerW": "power",
    "producedEnergyWh": "energy_produced",
    "consumedEnergyWh": "energy_consumed",
    "netEnergyWh": "energy_net",
    "importedEnergyWh": "energy_imported",
    "exportedEnergyWh": "energy_exported",
    "circuit_priority": "priority",
    "current": "current",
    "breaker_rating": "breaker_rating",
}

# Panel sensor API field mappings (used by get_user_friendly_suffix)
# Includes main meter/feedthrough produced, consumed, and net energy
PANEL_SUFFIX_MAPPING = {
    "instantGridPowerW": "grid_power",  # Descriptive to differentiate from other power types
    "feedthroughPowerW": "feed_through_power",
    "batteryPowerW": "battery_power",
    "pvPowerW": "pv_power",
    "gridPowerFlowW": "grid_power_flow",
    "sitePowerW": "site_power",
    "mainMeterEnergyProducedWh": "main_meter_energy_produced",  # Consistent naming
    "mainMeterEnergyConsumedWh": "main_meter_energy_consumed",  # Consistent naming
    "mainMeterNetEnergyWh": "main_meter_energy_net",  # Consistent naming
    "feedthroughEnergyProducedWh": "feed_through_energy_produced",  # Consistent naming
    "feedthroughEnergyConsumedWh": "feed_through_energy_consumed",  # Consistent naming
    "feedthroughNetEnergyWh": "feed_through_energy_net",  # Consistent naming
    "batteryPercentage": "battery_percentage",
}

# Panel entity suffix mappings (used by get_panel_entity_suffix)
# These are the actual entity_id/unique_id suffixes used for panel sensors
# (e.g., "main_meter_net_energy" / "feed_through_net_energy").
PANEL_ENTITY_SUFFIX_MAPPING = {
    "instantGridPowerW": "current_power",
    "feedthroughPowerW": "feed_through_power",
    "batteryPowerW": "battery_power",
    "pvPowerW": "pv_power",
    "gridPowerFlowW": "grid_power_flow",
    "sitePowerW": "site_power",
    "mainMeterEnergyProducedWh": "main_meter_produced_energy",
    "mainMeterEnergyConsumedWh": "main_meter_consumed_energy",
    "mainMeterNetEnergyWh": "main_meter_net_energy",
    "feedthroughEnergyProducedWh": "feed_through_produced_energy",
    "feedthroughEnergyConsumedWh": "feed_through_consumed_energy",
    "feedthroughNetEnergyWh": "feed_through_net_energy",
    "batteryPercentage": "battery_level",
}

# Combined mapping for general suffix lookup
ALL_SUFFIX_MAPPINGS = {**CIRCUIT_SUFFIX_MAPPING, **PANEL_SUFFIX_MAPPING}


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
        "energy_net",
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


def is_panel_level_sensor_key(sensor_key: str) -> bool:
    """Check if a sensor key represents a panel-level sensor.

    Panel-level sensors have the form: span_{device_identifier}_{sensor_type}
    Circuit sensors have the form: span_{device_identifier}_{circuit_id}_{sensor_type}

    Args:
        sensor_key: Sensor key to check (e.g., "span_span12345678_current_power" or
            "span_span12345678_12ce227695cd44338864b0ef2ec4168b_instantPowerW").

    Returns:
        True if this is a panel-level sensor (no circuit ID)

    Examples:
        is_panel_level_sensor_key("span_span12345678_current_power") → True
        is_panel_level_sensor_key(
            "span_span12345678_12ce227695cd44338864b0ef2ec4168b_instantPowerW"
        ) → False

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
    # No UUID pattern found, this is a panel-level sensor
    return True


def get_user_friendly_suffix(description_key: str) -> str:
    """Convert API description keys to user-friendly suffixes for consistent naming."""
    # If we have a direct mapping, use it
    if description_key in ALL_SUFFIX_MAPPINGS:
        return ALL_SUFFIX_MAPPINGS[description_key]

    # Otherwise, sanitize by converting dots to underscores and making lowercase
    return description_key.replace(".", "_").lower()


def get_panel_entity_suffix(description_key: str) -> str:
    """Convert panel API description keys to entity ID suffixes for unique ID consistency.

    This ensures panel unique IDs use the same suffix as entity IDs for consistency.
    """
    # If we have a direct mapping, use it
    if description_key in PANEL_ENTITY_SUFFIX_MAPPING:
        return PANEL_ENTITY_SUFFIX_MAPPING[description_key]

    # Otherwise, fall back to the general suffix mapping
    return get_user_friendly_suffix(description_key)


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


def build_bess_unique_id(serial: str, description_key: str) -> str:
    """Build unique ID for BESS sensor entities (pure function).

    Returns: "span_{serial}_bess_{description_key}"
    """
    return f"span_{serial}_bess_{description_key}"


def build_evse_unique_id(serial: str, evse_id: str, description_key: str) -> str:
    """Build unique ID for EVSE sensor/binary_sensor entities (pure function).

    Returns: "span_{serial}_evse_{evse_id}_{description_key}"
    """
    return f"span_{serial}_evse_{evse_id}_{description_key}"


def construct_synthetic_unique_id(serial: str, sensor_name: str) -> str:
    """Build unique ID for synthetic sensors using consistent pattern (pure function).

    Args:
        serial: Panel serial number
        sensor_name: Complete sensor name with suffix (e.g., "solar_inverter_power")

    Returns:
        Unique ID like "span_{serial}_{sensor_name}"

    """
    return f"span_{serial.lower()}_{sensor_name}"


def construct_circuit_unique_id(
    snapshot: SpanPanelSnapshot, circuit_id: str, description_key: str
) -> str:
    """Construct unique ID for circuit sensors using consistent pattern.

    Args:
        snapshot: The panel snapshot data
        circuit_id: Circuit ID from panel API (UUID or tab number)
        description_key: Sensor description key (e.g., "instantPowerW")

    Returns:
        Unique ID like "span_{serial}_{circuit_id}_{consistent_suffix}"

    Examples:
        span_abc123_0dad2f16cd514812ae1807b0457d473e_power
        span_abc123_circuit_15_energy_produced

    """
    return build_circuit_unique_id(snapshot.serial_number, circuit_id, description_key)


def construct_panel_unique_id(snapshot: SpanPanelSnapshot, description_key: str) -> str:
    """Construct unique ID for panel-level sensors using consistent pattern.

    Args:
        snapshot: The panel snapshot data
        description_key: Sensor description key (e.g., "instantGridPowerW")

    Returns:
        Unique ID like "span_{serial}_{consistent_suffix}" (uses descriptive consistent names)

    Examples:
        span_abc123_grid_power
        span_abc123_feed_through_power
        span_abc123_dsm_state

    """
    return build_panel_unique_id(snapshot.serial_number, description_key)


def construct_switch_unique_id(snapshot: SpanPanelSnapshot, circuit_id: str) -> str:
    """Construct unique ID for switch entities using consistent pattern.

    Args:
        snapshot: The panel snapshot data
        circuit_id: Circuit ID from panel API

    Returns:
        Unique ID like "span_{serial}_relay_{circuit_id}"

    Examples:
        span_abc123_relay_0dad2f16cd514812ae1807b0457d473e

    """
    return build_switch_unique_id(snapshot.serial_number, circuit_id)


def construct_binary_sensor_unique_id(snapshot: SpanPanelSnapshot, description_key: str) -> str:
    """Construct unique ID for binary sensor entities using consistent pattern.

    Args:
        snapshot: The panel snapshot data
        description_key: Sensor description key (e.g., "doorState")

    Returns:
        Unique ID like "span_{serial}_{description_key}"

    Examples:
        span_abc123_doorState
        span_abc123_eth0Link

    """
    return build_binary_sensor_unique_id(snapshot.serial_number, description_key)


def construct_select_unique_id(snapshot: SpanPanelSnapshot, select_id: str) -> str:
    """Construct unique ID for select entities using consistent pattern.

    Args:
        snapshot: The panel snapshot data
        select_id: Select entity identifier

    Returns:
        Unique ID like "span_{serial}_select_{select_id}"

    Examples:
        span_abc123_select_priority_mode

    """
    return build_select_unique_id(snapshot.serial_number, select_id)
