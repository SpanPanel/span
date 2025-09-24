"""Options flow utilities for Span Panel config flow."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector
from homeassistant.util import slugify
import voluptuous as vol

from custom_components.span_panel.const import (
    CONF_API_RETRIES,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRY_TIMEOUT,
    CONF_SIMULATION_OFFLINE_MINUTES,
    CONF_SIMULATION_START_TIME,
    DEFAULT_API_RETRIES,
    DEFAULT_API_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_API_RETRY_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from custom_components.span_panel.options import (
    BATTERY_ENABLE,
    ENERGY_REPORTING_GRACE_PERIOD,
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)

from .validation import get_filtered_tab_options, validate_solar_configuration

_LOGGER = logging.getLogger(__name__)


def build_general_options_schema(
    config_entry: ConfigEntry,
    available_tabs: list[int],
    current_leg1: int = 0,
    current_leg2: int = 0,
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for general options form.

    Args:
        config_entry: The config entry
        available_tabs: List of available unmapped tabs
        current_leg1: Current leg 1 selection
        current_leg2: Current leg 2 selection
        user_input: Current user input for dynamic updates

    Returns:
        Voluptuous schema for the form

    """
    # If user_input exists, use those values for filtering (for dynamic updates)
    if user_input is not None:
        leg1_raw_dyn = user_input.get(INVERTER_LEG1, current_leg1)
        leg2_raw_dyn = user_input.get(INVERTER_LEG2, current_leg2)
        try:
            current_leg1 = int(leg1_raw_dyn)
        except (TypeError, ValueError):
            current_leg1 = 0
        try:
            current_leg2 = int(leg2_raw_dyn)
        except (TypeError, ValueError):
            current_leg2 = 0

    # Create filtered tab options for each dropdown
    leg1_options = get_filtered_tab_options(current_leg2, available_tabs)
    leg2_options = get_filtered_tab_options(current_leg1, available_tabs)
    # Convert to selector options lists (value/label) to force dropdowns
    leg1_select_options = [{"value": str(k), "label": v} for k, v in leg1_options.items()]
    leg2_select_options = [{"value": str(k), "label": v} for k, v in leg2_options.items()]

    schema_fields = {
        vol.Optional(CONF_SCAN_INTERVAL): vol.All(int, vol.Range(min=5)),
        vol.Optional(BATTERY_ENABLE): bool,
        vol.Optional(INVERTER_ENABLE): bool,
        vol.Optional(INVERTER_LEG1, default=str(current_leg1)): selector(
            {"select": {"options": leg1_select_options, "mode": "dropdown"}}
        ),
        vol.Optional(INVERTER_LEG2, default=str(current_leg2)): selector(
            {"select": {"options": leg2_select_options, "mode": "dropdown"}}
        ),
        vol.Optional(CONF_API_RETRIES): vol.All(int, vol.Range(min=0, max=10)),
        vol.Optional(CONF_API_RETRY_TIMEOUT): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=10.0)
        ),
        vol.Optional(CONF_API_RETRY_BACKOFF_MULTIPLIER): vol.All(
            vol.Coerce(float), vol.Range(min=1.0, max=5.0)
        ),
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
    }

    # If legacy (no device prefix), show upgrade toggle in general options.
    # Check USE_DEVICE_PREFIX flag to determine if this is a legacy installation.
    is_legacy_install = not config_entry.options.get(USE_DEVICE_PREFIX, False)
    if is_legacy_install:
        schema_fields[vol.Optional("legacy_upgrade_to_friendly", default=False)] = bool

    return vol.Schema(schema_fields)


def get_general_options_defaults(
    config_entry: ConfigEntry, current_leg1: int, current_leg2: int
) -> dict[str, Any]:
    """Get default values for general options form.

    Args:
        config_entry: The config entry
        current_leg1: Current leg 1 selection
        current_leg2: Current leg 2 selection

    Returns:
        Dictionary of default values

    """
    defaults = {
        CONF_SCAN_INTERVAL: config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds
        ),
        BATTERY_ENABLE: config_entry.options.get("enable_battery_percentage", False),
        INVERTER_ENABLE: config_entry.options.get("enable_solar_circuit", False),
        # Defaults for selector values must be strings
        INVERTER_LEG1: str(current_leg1),
        INVERTER_LEG2: str(current_leg2),
        CONF_API_RETRIES: config_entry.options.get(CONF_API_RETRIES, DEFAULT_API_RETRIES),
        CONF_API_RETRY_TIMEOUT: config_entry.options.get(
            CONF_API_RETRY_TIMEOUT, DEFAULT_API_RETRY_TIMEOUT
        ),
        CONF_API_RETRY_BACKOFF_MULTIPLIER: config_entry.options.get(
            CONF_API_RETRY_BACKOFF_MULTIPLIER, DEFAULT_API_RETRY_BACKOFF_MULTIPLIER
        ),
        ENERGY_REPORTING_GRACE_PERIOD: config_entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15),
    }

    # Add legacy upgrade default if applicable
    is_legacy_install = not config_entry.options.get(USE_DEVICE_PREFIX, False)
    if is_legacy_install:
        defaults["legacy_upgrade_to_friendly"] = False

    return defaults


def process_general_options_input(
    config_entry: ConfigEntry, user_input: dict[str, Any], available_tabs: list[int]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Process user input for general options.

    Args:
        config_entry: The config entry
        user_input: User input from the form
        available_tabs: List of available unmapped tabs

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

    # Validate solar tab selection if solar is enabled
    if filtered_input.get(INVERTER_ENABLE, False):
        # Coerce selector values (strings) back to integers
        leg1_raw = filtered_input.get(INVERTER_LEG1, 0)
        leg2_raw = filtered_input.get(INVERTER_LEG2, 0)
        try:
            leg1 = int(leg1_raw)
        except (TypeError, ValueError):
            leg1 = 0
        try:
            leg2 = int(leg2_raw)
        except (TypeError, ValueError):
            leg2 = 0

        # Validate solar configuration
        is_valid, error_message = validate_solar_configuration(True, leg1, leg2, available_tabs)
        if not is_valid:
            errors["base"] = error_message
            _LOGGER.warning("Solar tab validation failed: %s", error_message)

        # Persist coerced integer values
        filtered_input[INVERTER_LEG1] = leg1
        filtered_input[INVERTER_LEG2] = leg2

    # Handle legacy upgrade if requested
    if legacy_upgrade_requested:
        # Mark this config entry for legacy prefix upgrade after reload
        # The migration code will check which entities actually need renaming
        filtered_input[USE_DEVICE_PREFIX] = True
        filtered_input[USE_CIRCUIT_NUMBERS] = False
        filtered_input["pending_legacy_migration"] = True
    else:
        # Preserve existing naming flags by default.
        # Important: default use_device_prefix to True for new installs
        # so we do not accidentally treat them as legacy when the option
        # was not yet persisted.
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
        # Pre-1.0.4 installation - no device prefix
        return EntityNamingPattern.LEGACY_NAMES.value


def entities_have_device_prefix(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Best-effort detection if entities already use the device prefix.

    Checks the entity registry for any entity belonging to this config entry where
    the object_id starts with the device name prefix. Both FRIENDLY_NAMES and CIRCUIT_NUMBERS
    patterns include the device name prefix; only LEGACY lacks it.
    """
    registry = er.async_get(hass)

    # Get the device name from config entry and sanitize it
    device_name = config_entry.data.get("device_name", config_entry.title)
    if not device_name:
        return False

    sanitized_device_name = slugify(device_name)
    for entry in registry.entities.values():
        try:
            if entry.config_entry_id != config_entry.entry_id:
                continue
            object_id = entry.entity_id.split(".", 1)[1]
            # Check if the object_id starts with the device name followed by underscore
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
