"""Options flow utilities for Span Panel config flow."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
import voluptuous as vol

from custom_components.span_panel.const import (
    CONF_SIMULATION_OFFLINE_MINUTES,
    CONF_SIMULATION_START_TIME,
    DEFAULT_SNAPSHOT_INTERVAL,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from custom_components.span_panel.options import (
    ENERGY_REPORTING_GRACE_PERIOD,
    SNAPSHOT_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def build_general_options_schema(
    config_entry: ConfigEntry,
    available_tabs: list[int] | None = None,
    current_leg1: int = 0,
    current_leg2: int = 0,
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for general options form.

    Args:
        config_entry: The config entry
        available_tabs: Unused (kept for backward compatibility)
        current_leg1: Unused (kept for backward compatibility)
        current_leg2: Unused (kept for backward compatibility)
        user_input: Current user input for dynamic updates

    Returns:
        Voluptuous schema for the form

    """
    schema_fields = {
        vol.Optional(SNAPSHOT_UPDATE_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=15)
        ),
        vol.Optional(ENABLE_PANEL_NET_ENERGY_SENSORS): bool,
        vol.Optional(ENABLE_CIRCUIT_NET_ENERGY_SENSORS): bool,
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
    }

    return vol.Schema(schema_fields)


def get_general_options_defaults(
    config_entry: ConfigEntry, current_leg1: int = 0, current_leg2: int = 0
) -> dict[str, Any]:
    """Get default values for general options form.

    Args:
        config_entry: The config entry
        current_leg1: Unused (kept for backward compatibility)
        current_leg2: Unused (kept for backward compatibility)

    Returns:
        Dictionary of default values

    """
    defaults = {
        SNAPSHOT_UPDATE_INTERVAL: config_entry.options.get(
            SNAPSHOT_UPDATE_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL
        ),
        ENABLE_PANEL_NET_ENERGY_SENSORS: config_entry.options.get(
            ENABLE_PANEL_NET_ENERGY_SENSORS, True
        ),
        ENABLE_CIRCUIT_NET_ENERGY_SENSORS: config_entry.options.get(
            ENABLE_CIRCUIT_NET_ENERGY_SENSORS, True
        ),
        ENERGY_REPORTING_GRACE_PERIOD: config_entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15),
    }

    # Add legacy upgrade default if applicable
    is_legacy_install = not config_entry.options.get(USE_DEVICE_PREFIX, False)
    if is_legacy_install:
        defaults["legacy_upgrade_to_friendly"] = False

    return defaults


def process_general_options_input(
    config_entry: ConfigEntry, user_input: dict[str, Any], available_tabs: list[int] | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Process user input for general options.

    Args:
        config_entry: The config entry
        user_input: User input from the form
        available_tabs: Unused (kept for backward compatibility)

    Returns:
        Tuple of (processed_options, errors)

    """
    errors: dict[str, str] = {}

    # Filter out separator fields from user input
    filtered_input = {k: v for k, v in user_input.items() if not k.startswith("_separator")}

    # Handle legacy upgrade flag if present
    legacy_upgrade_requested: bool = bool(user_input.get("legacy_upgrade_to_friendly", False))
    filtered_input.pop("legacy_upgrade_to_friendly", None)

    # Merge with existing options to preserve unchanged values
    merged_options = dict(config_entry.options)
    merged_options.update(filtered_input)
    filtered_input = merged_options

    # Handle legacy upgrade if requested
    if legacy_upgrade_requested:
        filtered_input[USE_DEVICE_PREFIX] = True
        filtered_input[USE_CIRCUIT_NUMBERS] = False
        filtered_input["pending_legacy_migration"] = True
    else:
        # Preserve existing naming flags by default.
        use_prefix: Any | bool = config_entry.options.get(USE_DEVICE_PREFIX, True)
        use_circuit_numbers: Any | bool = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        filtered_input[USE_DEVICE_PREFIX] = use_prefix
        filtered_input[USE_CIRCUIT_NUMBERS] = use_circuit_numbers

    # Remove any entity naming pattern from input (shouldn't be there anyway)
    filtered_input.pop(ENTITY_NAMING_PATTERN, None)

    # Clean up any simulation-only change flag since this will trigger a reload
    filtered_input.pop("_simulation_only_change", None)

    return filtered_input, errors


def get_entity_naming_schema() -> vol.Schema:
    """Get the entity naming options schema."""
    pattern_options = {
        EntityNamingPattern.FRIENDLY_NAMES.value: "Friendly Names (e.g., span_panel_kitchen_outlets_power)",
        EntityNamingPattern.CIRCUIT_NUMBERS.value: "Circuit Numbers (e.g., span_panel_circuit_15_power)",
    }

    return vol.Schema(
        {
            vol.Optional(ENTITY_NAMING_PATTERN): vol.In(pattern_options),
        }
    )


def get_current_naming_pattern(config_entry: ConfigEntry) -> str:
    """Determine the current entity naming pattern from configuration flags."""
    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

    if use_circuit_numbers:
        return EntityNamingPattern.CIRCUIT_NUMBERS.value
    elif use_device_prefix:
        return EntityNamingPattern.FRIENDLY_NAMES.value
    else:
        return EntityNamingPattern.LEGACY_NAMES.value


def entities_have_device_prefix(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Best-effort detection if entities already use the device prefix."""
    registry = er.async_get(hass)

    device_name = config_entry.data.get("device_name", config_entry.title)
    if not device_name:
        return False

    sanitized_device_name = slugify(device_name)
    for entry in registry.entities.values():
        try:
            if entry.config_entry_id != config_entry.entry_id:
                continue
            object_id = entry.entity_id.split(".", 1)[1]
            if object_id.startswith(f"{sanitized_device_name}_"):
                return True
        except (IndexError, AttributeError):
            continue
    return False


def pattern_to_flags(pattern: str) -> dict[str, bool]:
    """Convert entity naming pattern to configuration flags."""
    if pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value:
        return {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True}
    elif pattern == EntityNamingPattern.FRIENDLY_NAMES.value:
        return {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}
    else:  # LEGACY_NAMES
        return {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: False}


def get_simulation_start_time_schema() -> vol.Schema:
    """Get the simulation start time schema."""
    return vol.Schema(
        {
            vol.Optional(CONF_SIMULATION_START_TIME): str,
        }
    )


def get_simulation_start_time_defaults(config_entry: ConfigEntry) -> dict[str, Any]:
    """Get the simulation start time defaults."""
    return {
        CONF_SIMULATION_START_TIME: config_entry.options.get(CONF_SIMULATION_START_TIME, ""),
    }


def get_simulation_offline_minutes_schema() -> vol.Schema:
    """Get the simulation offline minutes schema."""
    return vol.Schema(
        {
            vol.Optional(CONF_SIMULATION_OFFLINE_MINUTES): int,
        }
    )


def get_simulation_offline_minutes_defaults(config_entry: ConfigEntry) -> dict[str, Any]:
    """Get the simulation offline minutes defaults."""
    return {
        CONF_SIMULATION_OFFLINE_MINUTES: config_entry.options.get(
            CONF_SIMULATION_OFFLINE_MINUTES, 0
        ),
    }


def build_entity_naming_options_schema(config_entry: ConfigEntry) -> vol.Schema:
    """Build the schema for entity naming options form."""
    is_legacy_install = not config_entry.options.get(USE_DEVICE_PREFIX, False)

    naming_pattern_options = {
        "friendly_names": "Friendly Entity ID Pattern",
        "circuit_numbers": "Circuit Tab Naming Pattern",
    }

    current_pattern = get_current_naming_pattern(config_entry)

    if current_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value:
        default_pattern = "circuit_numbers"
    else:
        default_pattern = "friendly_names"

    if is_legacy_install:
        schema_fields = {
            vol.Optional("entity_naming_pattern", default=default_pattern): vol.In(
                naming_pattern_options
            ),
            vol.Optional("legacy_upgrade_to_friendly", default=False): bool,
        }
    else:
        schema_fields = {
            vol.Optional("entity_naming_pattern", default=default_pattern): vol.In(
                naming_pattern_options
            )
        }

    return vol.Schema(schema_fields)


def get_entity_naming_options_defaults(config_entry: ConfigEntry) -> dict[str, Any]:
    """Get default values for entity naming options form."""
    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

    if use_circuit_numbers and use_device_prefix:
        current_pattern = "circuit_numbers"
    else:
        current_pattern = "friendly_names"

    return {
        "entity_naming_pattern": current_pattern,
        "legacy_upgrade_to_friendly": False,
    }


def process_entity_naming_options_input(
    config_entry: ConfigEntry, user_input: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Process entity naming options input and return filtered data."""
    filtered_input: dict[str, Any] = {}
    errors: dict[str, str] = {}

    legacy_upgrade_requested = user_input.get("legacy_upgrade_to_friendly", False)
    if legacy_upgrade_requested:
        filtered_input[USE_DEVICE_PREFIX] = True
        filtered_input[USE_CIRCUIT_NUMBERS] = False
        filtered_input["pending_legacy_migration"] = True
    else:
        selected_pattern = user_input.get("entity_naming_pattern")
        if selected_pattern is None:
            filtered_input[USE_CIRCUIT_NUMBERS] = config_entry.options.get(
                USE_CIRCUIT_NUMBERS, False
            )
            filtered_input[USE_DEVICE_PREFIX] = config_entry.options.get(USE_DEVICE_PREFIX, False)
            return filtered_input, errors

        if selected_pattern == "circuit_numbers":
            new_use_circuit_numbers = True
            new_use_device_prefix = True
        else:  # "friendly_names"
            new_use_circuit_numbers = False
            new_use_device_prefix = True

        current_use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        current_use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

        if current_use_circuit_numbers != new_use_circuit_numbers:
            filtered_input["old_use_circuit_numbers"] = current_use_circuit_numbers
            filtered_input["old_use_device_prefix"] = current_use_device_prefix
            filtered_input[USE_CIRCUIT_NUMBERS] = new_use_circuit_numbers
            filtered_input[USE_DEVICE_PREFIX] = new_use_device_prefix
            filtered_input["pending_naming_migration"] = True
        else:
            filtered_input[USE_CIRCUIT_NUMBERS] = current_use_circuit_numbers
            filtered_input[USE_DEVICE_PREFIX] = current_use_device_prefix

    return filtered_input, errors
