import logging
import uuid

import httpx

from .const import (
    API_TIMEOUT,
    PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE,
    URL_CIRCUITS,
    URL_PANEL,
    URL_REGISTER,
    URL_STATUS,
)
from .exceptions import SpanPanelReturnedEmptyData
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_status import SpanPanelStatus

_LOGGER = logging.getLogger(__name__)


class SpanPanelApi:
    def __init__(
        self,
        host: str,
        access_token: str | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.host: str = host.lower()
        self.access_token: str = access_token
        self._async_client = async_client

        self.status_data: SpanPanelStatus
        self.panel_data: SpanPanelData
        self.circuits_data: dict[str, SpanPanelCircuit]

    @property
    def async_client(self):
        return self._async_client or httpx.AsyncClient(verify=False)

    async def ping(self) -> bool:
        # status endpoint doesn't require auth.
        try:
            await self.get_status_data()
            return True
        except httpx.HTTPError:
            return False

    async def get_access_token(self) -> str:
        register_results = await self.post_data(
            URL_REGISTER,
            {
                "name": f"home-assistant-{uuid.uuid4()}",
                "description": "Home Assistant Local Span Integration",
            },
        )
        return register_results.json()["accessToken"]

    async def update(self) -> None:
        self.status_data = await self.get_status_data()
        self.panel_data = await self.get_panel_data()
        self.circuits_data = await self.get_circuits_data()

    async def get_status_data(self) -> SpanPanelStatus:
        response = await self.get_data(URL_STATUS)
        status_data = SpanPanelStatus.from_dict(response.json())
        return status_data

    async def get_panel_data(self) -> SpanPanelData:
        response = await self.get_data(URL_PANEL)
        panel_data = SpanPanelData.from_dict(response.json())

        # Span Panel API might return empty result.
        # We use relay state == UNKNOWN as an indication of that scenario.
        if panel_data.main_relay_state == PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE:
            raise SpanPanelReturnedEmptyData()

        return panel_data

    async def get_circuits_data(self) -> SpanPanelCircuit:
        response = await self.get_data(URL_CIRCUITS)
        raw_curcuits_data = response.json()["circuits"]

        # Span Panel API might return empty result.
        # We use an empty curcuits dictionary as an indication of that scenario.
        if not raw_curcuits_data:
            raise SpanPanelReturnedEmptyData()

        circuits_data = {}
        for id, raw_curcuit_data in raw_curcuits_data.items():
            circuits_data[id] = SpanPanelCircuit.from_dict(raw_curcuit_data)

        return circuits_data

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
        formatted_url = url.format(self.host)
        response = await self._async_post(formatted_url, payload)
        return response

    # async def set_json_data(self, url, identity, json):
    #     formatted_url = url.format(self.host, identity)
    #     response = await self._async_post(formatted_url, json)
    #     return response

    async def _async_fetch_with_retry(self, url, **kwargs) -> httpx.Response:
        """
        Retry 3 times to fetch the url if there is a transport error.
        """

        if self.access_token:
            headers = {"accessToken": self.access_token}
        else:
            headers = {}

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

    async def _async_post(self, url, json=None, **kwargs) -> httpx.Response:
        """
        POST to the url
        """
        if self.access_token:
            headers = {"accessToken": self.access_token}
        else:
            headers = {}

        _LOGGER.debug("HTTP POST Attempt: %s", url)
        async with self.async_client as client:
            headers = {"accessToken": self.option_access_token}
            resp = await client.post(
                url, json=json, headers=headers, timeout=API_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            _LOGGER.debug("HTTP POST %s: %s: %s", url, resp, resp.text)
            return resp
