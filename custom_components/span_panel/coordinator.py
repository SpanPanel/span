"""Coordinator for Span Panel."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.httpx_client import httpx
from homeassistant.helpers.update_coordinator import (DataUpdateCoordinator,
                                                      UpdateFailed)

from .const import API_TIMEOUT
from .span_panel import SpanPanel

_LOGGER = logging.getLogger(__name__)


class SpanPanelCoordinator(DataUpdateCoordinator[SpanPanel]):
    """Coordinator for Span Panel."""

    def __init__(
        self,
        hass: HomeAssistant,
        span_panel: SpanPanel,
        name: str,
        update_interval: int,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=f"span panel {name}",
            update_interval=timedelta(seconds=update_interval),
            always_update=True,
        )
        self.span_panel = span_panel

    async def _async_update_data(self) -> SpanPanel:
        """Fetch data from API endpoint."""
        try:
            _LOGGER.debug("Starting coordinator update")
            await asyncio.wait_for(self.span_panel.update(), timeout=API_TIMEOUT)
            _LOGGER.debug("Coordinator update successful - data: %s", self.span_panel)
        except httpx.HTTPStatusError as err:
            if err.response.status_code == httpx.codes.UNAUTHORIZED:
                raise ConfigEntryAuthFailed from err
            else:
                _LOGGER.error(
                    "httpx.StatusError occurred while updating Span data: %s",
                    str(err),
                )
                raise UpdateFailed(f"Error communicating with API: {err}") from err
        except httpx.HTTPError as err:
            _LOGGER.error(
                "An httpx.HTTPError occurred while updating Span data: %s", str(err)
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except asyncio.TimeoutError as err:
            _LOGGER.error(
                "An asyncio.TimeoutError occurred while updating Span data: %s",
                str(err),
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        return self.span_panel
