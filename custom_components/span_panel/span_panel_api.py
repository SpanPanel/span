"""Span Panel API"""

import logging
import uuid
from typing import Any, Dict

from homeassistant.helpers.httpx_client import httpx

from .const import (API_TIMEOUT, PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE,
                    SPAN_CIRCUITS, SPAN_SOE, URL_CIRCUITS, URL_PANEL,
                    URL_REGISTER, URL_STATUS, URL_STORAGE_BATTERY,
                    CircuitPriority, CircuitRelayState)
from .exceptions import SpanPanelReturnedEmptyData
from .options import Options
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery

_LOGGER = logging.getLogger(__name__)


class SpanPanelApi:
    """Span Panel API"""

    def __init__(
        self,
        host: str,
        access_token: str | None = None,
        options: Options | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.host: str = host.lower()
        self.access_token: str | None = access_token
        self.options: Options | None = options
        self._async_client = async_client

    @property
    def async_client(self):
        """Return the httpx.AsyncClient"""

        return self._async_client or httpx.AsyncClient(verify=True)

    async def ping(self) -> bool:
        """Ping the Span Panel API"""

        # status endpoint doesn't require auth.
        try:
            await self.get_status_data()
            return True
        except httpx.HTTPError:
            return False

    async def get_access_token(self) -> str:
        """Get the access token"""
        register_results = await self.post_data(
            URL_REGISTER,
            {
                "name": f"home-assistant-{uuid.uuid4()}",
                "description": "Home Assistant Local Span Integration",
            },
        )
        return register_results.json()["accessToken"]

    async def get_status_data(self) -> SpanPanelHardwareStatus:
        """Get the status data"""
        response = await self.get_data(URL_STATUS)
        status_data = SpanPanelHardwareStatus.from_dict(response.json())
        return status_data

    async def get_panel_data(self) -> SpanPanelData:
        """Get the panel data"""
        response = await self.get_data(URL_PANEL)
        panel_data = SpanPanelData.from_dict(response.json(), self.options)

        # Span Panel API might return empty result.
        # We use relay state == UNKNOWN as an indication of that scenario.
        if panel_data.main_relay_state == PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE:
            raise SpanPanelReturnedEmptyData()

        return panel_data

    async def get_circuits_data(self) -> Dict[str, SpanPanelCircuit]:
        """Get the circuits data"""
        response = await self.get_data(URL_CIRCUITS)
        raw_circuits_data = response.json()[SPAN_CIRCUITS]

        if not raw_circuits_data:
            raise SpanPanelReturnedEmptyData()

        circuits_data: Dict[str, SpanPanelCircuit] = {}
        for circuit_id, raw_circuit_data in raw_circuits_data.items():
            circuits_data[circuit_id] = SpanPanelCircuit.from_dict(raw_circuit_data)
        return circuits_data

    async def get_storage_battery_data(self) -> SpanPanelStorageBattery:
        """Get the storage battery data"""
        response = await self.get_data(URL_STORAGE_BATTERY)
        storage_battery_data = response.json()[SPAN_SOE]

        # Span Panel API might return empty result.
        # We use relay state == UNKNOWN as an indication of that scenario.
        if not storage_battery_data:
            raise SpanPanelReturnedEmptyData()

        return SpanPanelStorageBattery.from_dic(storage_battery_data)

    async def set_relay(self, circuit: SpanPanelCircuit, state: CircuitRelayState):
        """Set the relay state"""
        await self.post_data(
            f"{URL_CIRCUITS}/{circuit.circuit_id}",
            {"relayStateIn": {"relayState": state.name}},
        )

    async def set_priority(self, circuit: SpanPanelCircuit, priority: CircuitPriority):
        """Set the priority"""
        await self.post_data(
            f"{URL_CIRCUITS}/{circuit.circuit_id}",
            {"priorityIn": {"priority": priority.name}},
        )

    async def get_data(self, url) -> httpx.Response:
        """
        Fetch data from the endpoint and if inverters selected default
        to fetching inverter data.
        Update from PC endpoint.
        """
        formatted_url = url.format(self.host)
        response = await self._async_fetch_with_retry(
            formatted_url, follow_redirects=False
        )
        return response

    async def post_data(self, url: str, payload: dict) -> httpx.Response:
        """Post data to the endpoint"""
        formatted_url = url.format(self.host)
        response = await self._async_post(formatted_url, payload)
        return response

    async def _async_fetch_with_retry(self, url, **kwargs) -> httpx.Response:
        """
        Retry 3 times to fetch the url if there is a transport error.
        """
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        for attempt in range(3):
            _LOGGER.debug("HTTP GET Attempt #%s: %s", attempt + 1, url)
            try:
                async with self.async_client as client:
                    resp = await client.get(
                        url, timeout=API_TIMEOUT, headers=headers, **kwargs
                    )
                    resp.raise_for_status()
                    _LOGGER.debug("Fetched from %s: %s: %s", url, resp, resp.text)
                    return resp
            except httpx.TransportError:
                if attempt == 2:
                    raise
        raise httpx.TransportError("Too many attempts")

    async def _async_post(
        self, url: str, json: dict[str, Any] | None = None, **kwargs
    ) -> httpx.Response:
        """
        POST to the url
        """
        headers = {"accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        _LOGGER.debug("HTTP POST Attempt: %s", url)
        async with self.async_client as client:
            resp = await client.post(
                url, json=json, headers=headers, timeout=API_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            _LOGGER.debug("HTTP POST %s: %s: %s", url, resp, resp.text)
            return resp
