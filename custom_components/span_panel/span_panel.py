"""Module to read production and consumption values from a Span panel."""

import logging
from typing import Dict

from homeassistant.helpers.httpx_client import httpx

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

_LOGGER: logging.Logger = logging.getLogger(__name__)

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
    """Class to manage the Span Panel."""

    def __init__(
        self,
        host: str,
        access_token: str | None = None,  # nosec
        options: Options | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the Span Panel."""
        self._options = options
        self.api = SpanPanelApi(host, access_token, options, async_client)
        self._status: SpanPanelHardwareStatus | None = None
        self._panel: SpanPanelData | None = None
        self._circuits: Dict[str, SpanPanelCircuit] = {}
        self._storage_battery: SpanPanelStorageBattery | None = None

    def _get_hardware_status(self) -> SpanPanelHardwareStatus:
        """Get hardware status with type checking."""
        if self._status is None:
            raise RuntimeError("Hardware status not available")
        return self._status

    def _get_data(self) -> SpanPanelData:
        """Get data with type checking."""
        if self._panel is None:
            raise RuntimeError("Panel data not available")
        return self._panel

    def _get_storage_battery(self) -> SpanPanelStorageBattery:
        """Get storage battery with type checking."""
        if self._storage_battery is None:
            raise RuntimeError("Storage battery not available")
        return self._storage_battery

    @property
    def host(self) -> str:
        """Return the host of the panel."""
        return self.api.host

    @property
    def options(self) -> Options | None:
        """Get options data atomically"""
        return self._options

    def _update_status(self, new_status: SpanPanelHardwareStatus) -> None:
        """Atomic update of status data"""
        self._status = new_status

    def _update_panel(self, new_panel: SpanPanelData) -> None:
        """Atomic update of panel data"""
        self._panel = new_panel

    def _update_circuits(self, new_circuits: Dict[str, SpanPanelCircuit]) -> None:
        """Atomic update of circuits data"""
        self._circuits = new_circuits

    def _update_storage_battery(self, new_battery: SpanPanelStorageBattery) -> None:
        """Atomic update of storage battery data"""
        self._storage_battery = new_battery

    async def update(self) -> None:
        """Update all panel data atomically"""
        try:
            _LOGGER.debug("Starting panel update")
            # Get new data
            new_status = await self.api.get_status_data()
            _LOGGER.debug("Got status data: %s", new_status)
            new_panel = await self.api.get_panel_data()
            _LOGGER.debug("Got panel data: %s", new_panel)
            new_circuits = await self.api.get_circuits_data()
            _LOGGER.debug("Got circuits data: %s", new_circuits)

            # Atomic updates
            self._update_status(new_status)
            self._update_panel(new_panel)
            self._update_circuits(new_circuits)

            if self._options and self._options.enable_battery_percentage:
                new_battery = await self.api.get_storage_battery_data()
                _LOGGER.debug("Got battery data: %s", new_battery)
                self._update_storage_battery(new_battery)

            _LOGGER.debug("Panel update completed successfully")
        except SpanPanelReturnedEmptyData:
            _LOGGER.warning("Span Panel returned empty data")
        except Exception as err:
            _LOGGER.error("Error updating panel: %s", err, exc_info=True)
            raise

    @property
    def status(self) -> SpanPanelHardwareStatus:
        """Get status data atomically"""
        return self._get_hardware_status()

    @property
    def panel(self) -> SpanPanelData:
        """Get panel data atomically"""
        return self._get_data()

    @property
    def circuits(self) -> Dict[str, SpanPanelCircuit]:
        """Get circuits data atomically"""
        return self._circuits

    @property
    def storage_battery(self) -> SpanPanelStorageBattery:
        """Get storage battery data atomically"""
        return self._get_storage_battery()
