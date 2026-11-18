"""Options flow helpers for Span Panel config flow."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.field_visibility import ConditionOperator, FieldCondition
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import probatio as vol

from .const import (
    DEFAULT_ENERGY_DIP_COMPENSATION,
    DEFAULT_SNAPSHOT_INTERVAL,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_ENERGY_DIP_COMPENSATION,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    PANEL_ADMIN_ONLY,
    PANEL_SHOW_SIDEBAR,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .control_gate import (
    DEFAULT_ALLOW_CONTEXTLESS_CONTROL,
    DEFAULT_CONTROL_MODE,
    DEFAULT_RELAY_DEBOUNCE_SECONDS,
    ControlMode,
)
from .options import (
    ALLOW_CONTEXTLESS_CONTROL,
    CONTROL_LOCK_TIMEOUT,
    CONTROL_MODE,
    ENERGY_REPORTING_GRACE_PERIOD,
    RELAY_DEBOUNCE_SECONDS,
    SNAPSHOT_UPDATE_INTERVAL,
)

# Each condition hides a field only when the form submits the value that turns
# its feature off. Core decides visibility from the submitted values, never from
# the stored options, so a client that leaves the controlling field out gets its
# dependent fields treated as shown, and saves what it sends for them as it
# always could. A hidden field is left out of the submission, and the stored
# value survives because saving merges into what is already stored.

_SIDEBAR_PANEL_SHOWN: FieldCondition = {
    "field": PANEL_SHOW_SIDEBAR,
    "operator": ConditionOperator.NOT_EQ,
    "value": False,
}
"""Shows a field while the sidebar panel is on.

The admin-only choice is read only when the panel is registered, and the panel
is registered only while it is on.
"""

_CONTROL_ENABLED: FieldCondition = {
    "field": CONTROL_MODE,
    "operator": ConditionOperator.NOT_EQ,
    "value": ControlMode.DISABLED.value,
}
"""Shows a field while control is on.

With control disabled the gate refuses every command before it consults
anything else, so neither contextless control nor the relay debounce decides
anything, and no control-lock switch is created for the lock timeout to
configure.
"""

GENERAL_OPTIONS_SCHEMA: vol.Schema = vol.Schema(
    {
        vol.Optional(PANEL_SHOW_SIDEBAR): bool,
        data_entry_flow.Optional(PANEL_ADMIN_ONLY, visible=_SIDEBAR_PANEL_SHOWN): bool,
        vol.Optional(SNAPSHOT_UPDATE_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=15)
        ),
        vol.Optional(ENABLE_PANEL_NET_ENERGY_SENSORS): bool,
        vol.Optional(ENABLE_CIRCUIT_NET_ENERGY_SENSORS): bool,
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
        vol.Optional(ENABLE_ENERGY_DIP_COMPENSATION): bool,
        # A SelectSelector rather than `vol.In`, so the three choices are
        # translated from `selector.control_mode` rather than shown as raw
        # option values.
        vol.Optional(CONTROL_MODE): SelectSelector(
            SelectSelectorConfig(
                options=[mode.value for mode in ControlMode],
                translation_key=CONTROL_MODE,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        data_entry_flow.Optional(ALLOW_CONTEXTLESS_CONTROL, visible=_CONTROL_ENABLED): bool,
        # Minutes. `0` means the lock stays armed until somebody disarms it; a
        # negative value turns the feature off, which is how "no lock entity"
        # is expressed without a second option.
        data_entry_flow.Optional(CONTROL_LOCK_TIMEOUT, visible=_CONTROL_ENABLED): vol.All(
            vol.Coerce(float), vol.Range(min=-1, max=1440)
        ),
        data_entry_flow.Optional(RELAY_DEBOUNCE_SECONDS, visible=_CONTROL_ENABLED): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=60)
        ),
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
        ENERGY_REPORTING_GRACE_PERIOD: config_entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15),
        ENABLE_ENERGY_DIP_COMPENSATION: config_entry.options.get(
            ENABLE_ENERGY_DIP_COMPENSATION, DEFAULT_ENERGY_DIP_COMPENSATION
        ),
        # Every default below reproduces what an entry already does. A silent
        # tightening on upgrade would break a household's automations with an
        # error the user cannot diagnose from the entity. Contextless control
        # can default to off, and the mode to administrators, only because
        # migrations 7.2 and 7.4 stored the old values in every entry that
        # predates them.
        CONTROL_MODE: config_entry.options.get(CONTROL_MODE, DEFAULT_CONTROL_MODE.value),
        ALLOW_CONTEXTLESS_CONTROL: config_entry.options.get(
            ALLOW_CONTEXTLESS_CONTROL, DEFAULT_ALLOW_CONTEXTLESS_CONTROL
        ),
        CONTROL_LOCK_TIMEOUT: config_entry.options.get(CONTROL_LOCK_TIMEOUT, -1),
        RELAY_DEBOUNCE_SECONDS: config_entry.options.get(
            RELAY_DEBOUNCE_SECONDS, DEFAULT_RELAY_DEBOUNCE_SECONDS
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

    # Merge with existing options, so a field the form left out keeps its stored
    # value. That includes a field its visibility condition hid.
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
