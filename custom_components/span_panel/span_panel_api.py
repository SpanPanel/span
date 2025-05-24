"""Span Panel API"""

import asyncio
from copy import deepcopy
import logging
from typing import Any
import uuid

import httpx

from .const import (
    API_TIMEOUT,
    PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE,
    SPAN_CIRCUITS,
    SPAN_SOE,
    URL_CIRCUITS,
    URL_PANEL,
    URL_REGISTER,
    URL_STATUS,
    URL_STORAGE_BATTERY,
    CircuitPriority,
    CircuitRelayState,
)
from .exceptions import SpanPanelReturnedEmptyData
from .options import Options
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelApi:
    """Span Panel API"""

    def __init__(
        self,
        host: str,
        access_token: str | None = None,  # nosec
        options: Options | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.host: str = host.lower()
        self.access_token: str | None = access_token
        self.options: Options | None = options
        self._async_client: Any | None = async_client

    @property
    def async_client(self) -> Any | Any:
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

    async def ping_with_auth(self) -> bool:
        """Test connection and authentication."""
        try:
            # Use get_panel_data() since it requires authentication
            await self.get_panel_data()
            return True
        except httpx.HTTPStatusError as err:
            if err.response.status_code == httpx.codes.UNAUTHORIZED:
                return False
            raise
        except (httpx.TransportError, SpanPanelReturnedEmptyData):
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
        response_data: dict[str, str] = register_results.json()
        if "accessToken" not in response_data:
            raise SpanPanelReturnedEmptyData("No access token in response")
        return response_data["accessToken"]

    async def get_status_data(self) -> SpanPanelHardwareStatus:
        """Get the status data"""
        response: httpx.Response = await self.get_data(URL_STATUS)
        status_data: SpanPanelHardwareStatus = SpanPanelHardwareStatus.from_dict(
            response.json()
        )
        return status_data

    async def get_panel_data(self) -> SpanPanelData:
        """Get the panel data"""
        response: httpx.Response = await self.get_data(URL_PANEL)
        # Deep copy the raw data before processing in case cached data cleaned up
        raw_data: Any = deepcopy(response.json())
        panel_data: SpanPanelData = SpanPanelData.from_dict(raw_data, self.options)

        # Span Panel API might return empty result.
        # We use relay state == UNKNOWN as an indication of that scenario.
        if panel_data.main_relay_state == PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE:
            raise SpanPanelReturnedEmptyData()

        return panel_data

    async def get_circuits_data(self) -> dict[str, SpanPanelCircuit]:
        """Get the circuits data"""
        response: httpx.Response = await self.get_data(URL_CIRCUITS)
        raw_circuits_data: Any = deepcopy(response.json()[SPAN_CIRCUITS])

        if not raw_circuits_data:
            raise SpanPanelReturnedEmptyData()

        circuits_data: dict[str, SpanPanelCircuit] = {}
        for circuit_id, raw_circuit_data in raw_circuits_data.items():
            circuits_data[circuit_id] = SpanPanelCircuit.from_dict(raw_circuit_data)
        return circuits_data

    async def get_storage_battery_data(self) -> SpanPanelStorageBattery:
        """Get the storage battery data"""
        response: httpx.Response = await self.get_data(URL_STORAGE_BATTERY)
        storage_battery_data: Any = response.json()[SPAN_SOE]

        # Span Panel API might return empty result.
        # We use relay state == UNKNOWN as an indication of that scenario.
        if not storage_battery_data:
            raise SpanPanelReturnedEmptyData()

        return SpanPanelStorageBattery.from_dic(storage_battery_data)

    async def set_relay(
        self, circuit: SpanPanelCircuit, state: CircuitRelayState
    ) -> None:
        """Set the relay state"""
        await self.post_data(
            f"{URL_CIRCUITS}/{circuit.circuit_id}",
            {"relayStateIn": {"relayState": state.name}},
        )

    async def set_priority(
        self, circuit: SpanPanelCircuit, priority: CircuitPriority
    ) -> None:
        """Set the priority"""
        await self.post_data(
            f"{URL_CIRCUITS}/{circuit.circuit_id}",
            {"priorityIn": {"priority": priority.name}},
        )

    async def get_data(self, url: str) -> httpx.Response:
        """
        Fetch data from the endpoint and if inverters selected default
        to fetching inverter data.
        Update from PC endpoint.
        """
        formatted_url: str = url.format(self.host)
        response: httpx.Response = await self._async_fetch_with_retry(
            formatted_url, follow_redirects=False
        )
        return response

    async def post_data(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        """Post data to the endpoint"""
        formatted_url: str = url.format(self.host)
        response: httpx.Response = await self._async_post(formatted_url, payload)
        return response

    async def _async_fetch_with_retry(self, url: str, **kwargs: Any) -> httpx.Response:
        """
        Retry 3 times if there is a transport error or certain HTTP errors.
        """
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        # HTTP status codes that are worth retrying (typically transient issues)
        retry_status_codes = {502, 503, 504, 429}

        last_exception: Exception | None = None
        for attempt in range(3):
            _LOGGER.debug("HTTP GET Attempt #%s: %s", attempt + 1, url)
            try:
                async with self.async_client as client:
                    resp: httpx.Response = await client.get(
                        url, timeout=API_TIMEOUT, headers=headers, **kwargs
                    )

                    # Only retry specific HTTP status codes that are typically transient
                    if resp.status_code in retry_status_codes and attempt < 2:
                        _LOGGER.debug(
                            "Received status %s for %s, retrying (attempt %s of 3)",
                            resp.status_code,
                            url,
                            attempt + 1,
                        )
                        # Add exponential backoff delay between retries (0.5s, then 1s)
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue

                    # For all other status codes, raise immediately
                    resp.raise_for_status()
                    _LOGGER.debug("Fetched from %s: %s: %s", url, resp, resp.text)
                    return resp

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in retry_status_codes and attempt < 2:
                    _LOGGER.debug(
                        "HTTP error %s for %s, retrying (attempt %s of 3)",
                        exc.response.status_code,
                        url,
                        attempt + 1,
                    )
                    # Add exponential backoff delay between retries
                    await asyncio.sleep(0.5 * (2**attempt))
                    last_exception = exc
                    continue
                raise

            except httpx.TransportError as exc:
                if attempt < 2:
                    _LOGGER.debug(
                        "Transport error for %s, retrying (attempt %s of 3): %s",
                        url,
                        attempt + 1,
                        str(exc),
                    )
                    # Add exponential backoff delay between retries
                    await asyncio.sleep(0.5 * (2**attempt))
                    last_exception = exc
                    continue
                raise

        # If we get here, we've exhausted all retries
        if last_exception:
            raise last_exception
        raise httpx.TransportError("Too many attempts")

    async def _async_post(
        self, url: str, json: dict[str, Any] | None = None, **kwargs: Any
    ) -> httpx.Response:
        """
        POST to the url
        """
        headers: dict[str, str] = {"accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        _LOGGER.debug("HTTP POST Attempt: %s", url)
        async with self.async_client as client:
            resp: httpx.Response = await client.post(
                url, json=json, headers=headers, timeout=API_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            _LOGGER.debug("HTTP POST %s: %s: %s", url, resp, resp.text)
            return resp
