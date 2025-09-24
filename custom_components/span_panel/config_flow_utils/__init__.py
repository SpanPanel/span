"""Config flow package for Span Panel integration."""

from typing import Any

from homeassistant.const import CONF_SCAN_INTERVAL
from span_panel_api import SpanPanelClient
import voluptuous as vol

from custom_components.span_panel.const import (
    CONF_API_RETRIES,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRY_TIMEOUT,
    CONFIG_API_RETRIES,
    CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    CONFIG_API_RETRY_TIMEOUT,
    CONFIG_TIMEOUT,
    ENTITY_NAMING_PATTERN,
    EntityNamingPattern,
)
from custom_components.span_panel.options import (
    BATTERY_ENABLE,
    ENERGY_REPORTING_GRACE_PERIOD,
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
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
    get_available_unmapped_tabs,
    get_filtered_tab_options,
    validate_auth_token,
    validate_host,
    validate_ipv4_address,
    validate_simulation_time,
    validate_solar_configuration,
    validate_solar_tab_selection,
)

# Export commonly used items for backward compatibility
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL): vol.All(int, vol.Range(min=5)),
        vol.Optional(BATTERY_ENABLE): bool,
        vol.Optional(INVERTER_ENABLE): bool,
        vol.Optional(INVERTER_LEG1): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(INVERTER_LEG2): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ENTITY_NAMING_PATTERN): vol.In([e.value for e in EntityNamingPattern]),
        vol.Optional(CONF_API_RETRIES): vol.All(int, vol.Range(min=0, max=10)),
        vol.Optional(CONF_API_RETRY_TIMEOUT): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=10.0)
        ),
        vol.Optional(CONF_API_RETRY_BACKOFF_MULTIPLIER): vol.All(
            vol.Coerce(float), vol.Range(min=1.0, max=5.0)
        ),
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
    }
)

__all__ = [
    # Validation
    "get_available_unmapped_tabs",
    "get_filtered_tab_options",
    "validate_auth_token",
    "validate_host",
    "validate_ipv4_address",
    "validate_simulation_time",
    "validate_solar_configuration",
    "validate_solar_tab_selection",
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


# Define backward compatibility items to avoid circular imports
def create_config_client(host: str, use_ssl: bool = False) -> Any:
    """Create a SpanPanelClient with config settings for quick feedback."""

    return SpanPanelClient(
        host=host,
        timeout=CONFIG_TIMEOUT,
        use_ssl=use_ssl,
        retries=CONFIG_API_RETRIES,
        retry_timeout=CONFIG_API_RETRY_TIMEOUT,
        retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    )


# Export create_config_client function
__all__.extend(["create_config_client"])
