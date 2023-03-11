"""Constants for the Span Panel integration."""

from datetime import timedelta

DOMAIN = "span_panel"
COORDINATOR = "coordinator"
NAME = "name"

URL_STATUS = "http://{}/api/v1/status"
URL_SPACES = "http://{}/api/v1/spaces"
URL_CIRCUITS = "http://{}/api/v1/circuits"
URL_PANEL = "http://{}/api/v1/panel"
URL_REGISTER = "http://{}/api/v1/auth/register"

CIRCUITS_NAME = "name"
CIRCUITS_RELAY = "relayState"
CIRCUITS_RELAY_OPEN = "OPEN"
CIRCUITS_RELAY_CLOSED = "CLOSED"
CIRCUITS_POWER = "instantPowerW"
CIRCUITS_ENERGY_PRODUCED = "producedEnergyWh"
CIRCUITS_ENERGY_CONSUMED = "consumedEnergyWh"
CIRCUITS_BREAKER_POSITIONS = "tabs"
CIRCUITS_PRIORITY = "priority"
CIRCUITS_IS_USER_CONTROLLABLE = "is_user_controllable"
CIRCUITS_IS_SHEDDABLE = "is_sheddable"
CIRCUITS_IS_NEVER_BACKUP = "is_never_backup"

SPAN_CIRCUITS = "circuits"
SPAN_SYSTEM = "system"
PANEL_POWER = "instantGridPowerW"
SYSTEM_DOOR_STATE = "doorState"
SYSTEM_DOOR_STATE_CLOSED = "CLOSED"
SYSTEM_DOOR_STATE_OPEN = "OPEN"
SYSTEM_ETHERNET_LINK = "eth0Link"
SYSTEM_CELLULAR_LINK = "wwanLink"
SYSTEM_WIFI_LINK = "wlanLink"

PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE = "UNKNOWN"

SCAN_INTERVAL = timedelta(seconds=15)
API_TIMEOUT = 30
