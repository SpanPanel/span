"""Module to read production and consumption values from a Span panel."""

import logging

from .exceptions import SpanPanelReturnedEmptyData
from .options import Options
from .span_panel_api import SpanPanelApi
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery

STATUS_URL = "http://{}/api/v1/status"
SPACES_URL = "http://{}/api/v1/spaces"
CIRCUITS_URL = "http://{}/api/v1/circuits"
PANEL_URL = "http://{}/api/v1/panel"
REGISTER_URL = "http://{}/api/v1/auth/register"
STORAGE_BATTERY_URL = "http://{}/api/v1/storage/soe"

_LOGGER = logging.getLogger(__name__)

SPAN_CIRCUITS = "circuits"
SPAN_SYSTEM = "system"
PANEL_POWER = "instantGridPowerW"
SYSTEM_DOOR_STATE = "doorState"
SYSTEM_DOOR_STATE_CLOSED = "CLOSED"
SYSTEM_DOOR_STATE_OPEN = "OPEN"
SYSTEM_ETHERNET_LINK = "eth0Link"
SYSTEM_CELLULAR_LINK = "wwanLink"
SYSTEM_WIFI_LINK = "wlanLink"


class SpanPanel:
    """Instance of a Span panel"""

    def __init__(
        self, host: str, access_token: str, options: Options, async_client=None
    ) -> None:
        self.api = SpanPanelApi(host, access_token, options, async_client)
        self.updated_at: int = 0
        self.status: SpanPanelHardwareStatus
        self.panel: SpanPanelData
        self.circuits: dict[str, SpanPanelCircuit]
        self.storage_battery: SpanPanelStorageBattery

    @property
    def host(self) -> str:
        return self.api.host

    async def update(self) -> None:
        try:
            self.status = await self.api.get_status_data()
        except SpanPanelReturnedEmptyData:
            _LOGGER.warning("Span Panel API returned empty result. Ignoring...")

        try:
            self.panel = await self.api.get_panel_data()
        except SpanPanelReturnedEmptyData:
            _LOGGER.warning("Span Panel API returned empty result. Ignoring...")

        try:
            self.circuits = await self.api.get_circuits_data()
        except SpanPanelReturnedEmptyData:
            _LOGGER.warning("Span Panel API returned empty result. Ignoring...")

        try:
            self.storage_battery = await self.api.get_storage_battery_data()

        except SpanPanelReturnedEmptyData:
            _LOGGER.warning(
                "Span Panel API returned empty result for battery storage. "
                "Ignoring..."
            )
