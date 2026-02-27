"""Config flow package for Span Panel integration."""

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
]
