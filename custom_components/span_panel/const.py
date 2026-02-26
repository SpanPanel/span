"""Constants for the Span Panel integration."""

from datetime import timedelta
import enum
from typing import Final

DOMAIN: Final = "span_panel"
COORDINATOR = "coordinator"
NAME = "name"

CONF_SERIAL_NUMBER = "serial_number"
CONF_USE_SSL = "use_ssl"
CONF_DEVICE_NAME = "device_name"

# v2 API / MQTT configuration (stored in config entry data)
CONF_API_VERSION = "api_version"
CONF_EBUS_BROKER_HOST = "ebus_broker_host"
CONF_EBUS_BROKER_USERNAME = "ebus_broker_username"
CONF_EBUS_BROKER_PASSWORD = "ebus_broker_password"
CONF_EBUS_BROKER_PORT = "ebus_broker_mqtts_port"
CONF_HOP_PASSPHRASE = "hop_passphrase"
CONF_PANEL_SERIAL = "panel_serial"

# Simulation configuration
CONF_SIMULATION_CONFIG = "simulation_config"
CONF_SIMULATION_START_TIME = "simulation_start_time"
CONF_SIMULATION_OFFLINE_MINUTES = "simulation_offline_minutes"

# Time format constants for simulation
TIME_ONLY_FORMATS = ["HH:MM", "H:MM"]  # 24-hour and 12-hour formats
ISO_DATETIME_FORMAT = "YYYY-MM-DDTHH:MM:SS"  # Full ISO datetime format

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
DSM_GRID_UP = "DSM_GRID_UP"
DSM_GRID_DOWN = "DSM_GRID_DOWN"
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

DEFAULT_SCAN_INTERVAL = timedelta(seconds=15)


class CircuitRelayState(enum.Enum):
    """Enumeration representing the possible relay states for a circuit."""

    OPEN = "Open"
    CLOSED = "Closed"
    UNKNOWN = "Unknown"


class CircuitPriority(enum.Enum):
    """Enumeration representing the possible circuit priority levels."""

    NEVER = "Never"
    SOC_THRESHOLD = "SOC Threshold"
    OFF_GRID = "Off-Grid"
    UNKNOWN = "Unknown"


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
