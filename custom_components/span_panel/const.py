"""Constants for the Span Panel integration."""

import enum
from datetime import timedelta
from typing import Final

DOMAIN: Final = "span_panel"
COORDINATOR = "coordinator"
NAME = "name"

CONF_SERIAL_NUMBER = "serial_number"

URL_STATUS = "http://{}/api/v1/status"
URL_SPACES = "http://{}/api/v1/spaces"
URL_CIRCUITS = "http://{}/api/v1/circuits"
URL_PANEL = "http://{}/api/v1/panel"
URL_REGISTER = "http://{}/api/v1/auth/register"
URL_STORAGE_BATTERY = "http://{}/api/v1/storage/soe"

STORAGE_BATTERY_PERCENTAGE = "batteryPercentage"
CIRCUITS_NAME = "name"
CIRCUITS_RELAY = "relayState"
CIRCUITS_POWER = "instantPowerW"
CIRCUITS_ENERGY_PRODUCED = "producedEnergyWh"
CIRCUITS_ENERGY_CONSUMED = "consumedEnergyWh"
CIRCUITS_BREAKER_POSITIONS = "tabs"
CIRCUITS_PRIORITY = "priority"
CIRCUITS_IS_USER_CONTROLLABLE = "is_user_controllable"
CIRCUITS_IS_SHEDDABLE = "is_sheddable"
CIRCUITS_IS_NEVER_BACKUP = "is_never_backup"

SPAN_CIRCUITS = "circuits"
SPAN_SOE = "soe"
SPAN_SYSTEM = "system"
PANEL_POWER = "instantGridPowerW"
SYSTEM_DOOR_STATE = "doorState"
SYSTEM_DOOR_STATE_CLOSED = "CLOSED"
SYSTEM_DOOR_STATE_UNKNOWN = "UNKNOWN"
SYSTEM_DOOR_STATE_OPEN = "OPEN"
SYSTEM_ETHERNET_LINK = "eth0Link"
SYSTEM_CELLULAR_LINK = "wwanLink"
SYSTEM_WIFI_LINK = "wlanLink"

STATUS_SOFTWARE_VER = "softwareVer"
DSM_GRID_STATE = "dsmGridState"
DSM_STATE = "dsmState"
CURRENT_RUN_CONFIG = "currentRunConfig"
MAIN_RELAY_STATE = "mainRelayState"

PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE = "UNKNOWN"
USE_DEVICE_PREFIX = "use_device_prefix"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=15)
API_TIMEOUT = 30


class CircuitRelayState(enum.Enum):
    """Enumeration representing the possible relay states for a circuit."""

    OPEN = "Open"
    CLOSED = "Closed"
    UNKNOWN = "Unknown"


class CircuitPriority(enum.Enum):
    """Enumeration representing the possible circuit priority levels."""

    MUST_HAVE = "Must Have"
    NICE_TO_HAVE = "Nice To Have"
    NON_ESSENTIAL = "Non-Essential"
    UNKNOWN = "Unknown"
