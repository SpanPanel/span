"""Options flow helpers for Span Panel config flow."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
import voluptuous as vol

from .const import (
    DEFAULT_SNAPSHOT_INTERVAL,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_ENERGY_DIP_COMPENSATION,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    ENABLE_UNMAPPED_CIRCUIT_SENSORS,
    PANEL_ADMIN_ONLY,
    PANEL_SHOW_SIDEBAR,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .options import (
    ENERGY_REPORTING_GRACE_PERIOD,
    SNAPSHOT_UPDATE_INTERVAL,
)

GENERAL_OPTIONS_SCHEMA: vol.Schema = vol.Schema(
    {
        vol.Optional(PANEL_SHOW_SIDEBAR): bool,
        vol.Optional(PANEL_ADMIN_ONLY): bool,
        vol.Optional(SNAPSHOT_UPDATE_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=15)
        ),
        vol.Optional(ENABLE_PANEL_NET_ENERGY_SENSORS): bool,
        vol.Optional(ENABLE_CIRCUIT_NET_ENERGY_SENSORS): bool,
        vol.Optional(ENABLE_UNMAPPED_CIRCUIT_SENSORS): bool,
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
        vol.Optional(ENABLE_ENERGY_DIP_COMPENSATION): bool,
    }
)


def get_general_options_defaults(
    config_entry: ConfigEntry,
    panel_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Get default values for general options form.

    Args:
        config_entry: The config entry
        panel_settings: Domain-level panel sidebar settings from storage

    Returns:
        Dictionary of default values

    """
    ps = panel_settings or {}
    return {
        PANEL_SHOW_SIDEBAR: ps.get(PANEL_SHOW_SIDEBAR, True),
        PANEL_ADMIN_ONLY: ps.get(PANEL_ADMIN_ONLY, False),
        SNAPSHOT_UPDATE_INTERVAL: config_entry.options.get(
            SNAPSHOT_UPDATE_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL
        ),
        ENABLE_PANEL_NET_ENERGY_SENSORS: config_entry.options.get(
            ENABLE_PANEL_NET_ENERGY_SENSORS, True
        ),
        ENABLE_CIRCUIT_NET_ENERGY_SENSORS: config_entry.options.get(
            ENABLE_CIRCUIT_NET_ENERGY_SENSORS, True
        ),
        ENABLE_UNMAPPED_CIRCUIT_SENSORS: config_entry.options.get(
            ENABLE_UNMAPPED_CIRCUIT_SENSORS, False
        ),
        ENERGY_REPORTING_GRACE_PERIOD: config_entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15),
        ENABLE_ENERGY_DIP_COMPENSATION: config_entry.options.get(
            ENABLE_ENERGY_DIP_COMPENSATION, False
        ),
    }


_PANEL_SETTING_KEYS = {PANEL_SHOW_SIDEBAR, PANEL_ADMIN_ONLY}


def process_general_options_input(
    config_entry: ConfigEntry,
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """Process user input for general options.

    Args:
        config_entry: The config entry
        user_input: User input from the form

    Returns:
        Tuple of (processed_options, errors, panel_settings)

    """
    errors: dict[str, str] = {}

    # Extract panel settings (domain-level, not per-entry)
    panel_settings = {k: v for k, v in user_input.items() if k in _PANEL_SETTING_KEYS}

    # Filter out separator fields and panel settings from entry options
    filtered_input = {
        k: v
        for k, v in user_input.items()
        if not k.startswith("_separator") and k not in _PANEL_SETTING_KEYS
    }

    # Merge with existing options to preserve unchanged values
    merged_options = dict(config_entry.options)
    merged_options.update(filtered_input)
    filtered_input = merged_options

    # Preserve existing naming flags
    use_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)
    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
    filtered_input[USE_DEVICE_PREFIX] = use_prefix
    filtered_input[USE_CIRCUIT_NUMBERS] = use_circuit_numbers

    return filtered_input, errors, panel_settings


def get_current_naming_pattern(config_entry: ConfigEntry) -> str:
    """Determine the current entity naming pattern from configuration flags."""
    use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
    use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, False)

    if use_circuit_numbers:
        return EntityNamingPattern.CIRCUIT_NUMBERS.value
    if use_device_prefix:
        return EntityNamingPattern.FRIENDLY_NAMES.value
    return EntityNamingPattern.LEGACY_NAMES.value
