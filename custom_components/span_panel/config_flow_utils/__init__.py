"""Config flow package for Span Panel integration."""

from homeassistant.const import CONF_SCAN_INTERVAL
import voluptuous as vol

from custom_components.span_panel.const import (
    ENTITY_NAMING_PATTERN,
    EntityNamingPattern,
)
from custom_components.span_panel.options import (
    BATTERY_ENABLE,
    ENERGY_REPORTING_GRACE_PERIOD,
)

from .options import (
    build_general_options_schema,
    entities_have_device_prefix,
    get_current_naming_pattern,
    get_entity_naming_schema,
    get_general_options_defaults,
    get_simulation_offline_minutes_defaults,
    get_simulation_offline_minutes_schema,
    get_simulation_start_time_defaults,
    get_simulation_start_time_schema,
    pattern_to_flags,
    process_general_options_input,
)
from .simulation import (
    extract_serial_from_config,
    get_available_simulation_configs,
    get_simulation_config_path,
    validate_yaml_config,
)
from .validation import (
    validate_auth_token,
    validate_host,
    validate_ipv4_address,
    validate_simulation_time,
    validate_v2_passphrase,
)

# Export commonly used items for backward compatibility
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL): vol.All(int, vol.Range(min=5)),
        vol.Optional(BATTERY_ENABLE): bool,
        vol.Optional(ENTITY_NAMING_PATTERN): vol.In([e.value for e in EntityNamingPattern]),
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
    }
)

__all__ = [
    # Validation
    "validate_auth_token",
    "validate_host",
    "validate_ipv4_address",
    "validate_simulation_time",
    "validate_v2_passphrase",
    # Simulation
    "extract_serial_from_config",
    "get_available_simulation_configs",
    "get_simulation_config_path",
    "validate_yaml_config",
    # Options
    "build_general_options_schema",
    "entities_have_device_prefix",
    "get_current_naming_pattern",
    "get_entity_naming_schema",
    "get_general_options_defaults",
    "get_simulation_offline_minutes_defaults",
    "get_simulation_offline_minutes_schema",
    "get_simulation_start_time_defaults",
    "get_simulation_start_time_schema",
    "pattern_to_flags",
    "process_general_options_input",
    # Backward compatibility
    "OPTIONS_SCHEMA",
]
