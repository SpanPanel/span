"""Constants for the Span Panel integration."""

import enum
from typing import Final

DOMAIN: Final = "span_panel"

CONF_SERIAL_NUMBER = "serial_number"
CONF_USE_SSL = "use_ssl"
CONF_DEVICE_NAME = "device_name"

# v2 API / MQTT configuration (stored in config entry data)
CONF_API_VERSION = "api_version"
CONF_EBUS_BROKER_HOST = "ebus_broker_host"
CONF_EBUS_BROKER_USERNAME = "ebus_broker_username"
CONF_EBUS_BROKER_PASSWORD = "ebus_broker_password"  # nosec B105
CONF_EBUS_BROKER_PORT = "ebus_broker_mqtts_port"
CONF_HOP_PASSPHRASE = "hop_passphrase"  # nosec B105
CONF_HTTP_PORT = "http_port"
CONF_PANEL_SERIAL = "panel_serial"
CONF_REGISTERED_FQDN = "registered_fqdn"

# Binary sensor / status field keys (used in entity definitions)
SYSTEM_DOOR_STATE = "doorState"
SYSTEM_DOOR_STATE_CLOSED = "CLOSED"
SYSTEM_DOOR_STATE_UNKNOWN = "UNKNOWN"
SYSTEM_DOOR_STATE_OPEN = "OPEN"
SYSTEM_ETHERNET_LINK = "eth0Link"
SYSTEM_CELLULAR_LINK = "wwanLink"
SYSTEM_WIFI_LINK = "wlanLink"
PANEL_STATUS = "panel_status"

USE_DEVICE_PREFIX = "use_device_prefix"
USE_CIRCUIT_NUMBERS = "use_circuit_numbers"

# Migration constants
MIGRATION_COMPLETED = "migration_completed"
GENERATED_YAML = "generated_yaml"
MIGRATION_VERSION = "migration_version"

# SPAN Panel State Constants
# DSM (Demand Side Management) States
DSM_ON_GRID = "DSM_ON_GRID"
DSM_OFF_GRID = "DSM_OFF_GRID"

# Panel Run Configuration States
PANEL_ON_GRID = "PANEL_ON_GRID"
PANEL_OFF_GRID = "PANEL_OFF_GRID"
PANEL_BACKUP = "PANEL_BACKUP"


# Entity naming pattern options
ENTITY_NAMING_PATTERN = "entity_naming_pattern"

# Net energy sensor configuration
ENABLE_PANEL_NET_ENERGY_SENSORS = "enable_panel_net_energy_sensors"
ENABLE_CIRCUIT_NET_ENERGY_SENSORS = "enable_circuit_net_energy_sensors"
ENABLE_ENERGY_DIP_COMPENSATION = "enable_energy_dip_compensation"

# Unmapped circuit sensor configuration
ENABLE_UNMAPPED_CIRCUIT_SENSORS = "enable_unmapped_circuit_sensors"

# Current monitoring configuration
ENABLE_CURRENT_MONITORING = "enable_current_monitoring"
DEFAULT_CONTINUOUS_THRESHOLD_PCT = 80
DEFAULT_SPIKE_THRESHOLD_PCT = 100
DEFAULT_WINDOW_DURATION_M = 15
DEFAULT_COOLDOWN_DURATION_M = 15
DEFAULT_NOTIFICATION_TITLE_TEMPLATE = "SPAN: {name} {alert_type}"
DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE = (
    "{name} at {current_a}A ({utilization_pct}% of {breaker_rating_a}A rating)"
)
DEFAULT_NOTIFICATION_PRIORITY = "default"
NOTIFICATION_PRIORITIES: Final[tuple[str, ...]] = (
    "default",
    "passive",
    "active",
    "time-sensitive",
    "critical",
)
EVENT_CURRENT_ALERT = "span_panel_current_alert"

# Graph time horizon configuration
VALID_GRAPH_HORIZONS: Final[tuple[str, ...]] = ("5m", "1h", "1d", "1M")
DEFAULT_GRAPH_HORIZON = "5m"

# Mains leg identifiers
MAINS_LEGS: Final[tuple[str, ...]] = (
    "upstream_l1",
    "upstream_l2",
    "downstream_l1",
    "downstream_l2",
)

# Panel sidebar settings (domain-level, shared across config entries)
PANEL_SHOW_SIDEBAR = "show_panel"
PANEL_ADMIN_ONLY = "panel_admin_only"

DEFAULT_SNAPSHOT_INTERVAL: Final[float] = 5.0


class CircuitRelayState(enum.Enum):
    """Enumeration representing the possible relay states for a circuit."""

    OPEN = "Open"
    CLOSED = "Closed"
    UNKNOWN = "Unknown"


class CircuitPriority(enum.Enum):
    """Enumeration representing the possible circuit priority levels."""

    NEVER = "never"
    SOC_THRESHOLD = "soc_threshold"
    OFF_GRID = "off_grid"
    UNKNOWN = "unknown"


class EntityNamingPattern(enum.Enum):
    """Entity naming pattern options for user selection."""

    FRIENDLY_NAMES = (
        "friendly_names"  # Device + Friendly Name (e.g., span_panel_kitchen_outlets_power)
    )
    CIRCUIT_NUMBERS = (
        "circuit_numbers"  # Device + Circuit Numbers (e.g., span_panel_circuit_1_power)
    )
    LEGACY_NAMES = (
        "legacy_names"  # No Device Prefix (e.g., kitchen_outlets_power) - Read-only for pre-1.0.4
    )
