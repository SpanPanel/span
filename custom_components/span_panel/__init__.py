"""The Span Panel integration."""
from __future__ import annotations

import logging

import async_timeout
import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import COORDINATOR, DOMAIN, NAME, SCAN_INTERVAL
from .span_panel import SpanPanel

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Span Panel from a config entry.
    """
    config = entry.data
    host = config[CONF_HOST]

    _LOGGER.debug("ASYNC_SETUP_ENTRY %s", host)

    span_panel = SpanPanel(
        host=config[CONF_HOST],
        access_token=config[CONF_ACCESS_TOKEN],
        async_client=get_async_client(hass),
    )

    _LOGGER.debug("ASYNC_SETUP_ENTRY panel %s", span_panel)

    async def async_update_data():
        """Fetch data from API endpoint."""
        async with async_timeout.timeout(30):
            try:
                await span_panel.update()
            except httpx.HTTPStatusError as err:
                raise ConfigEntryAuthFailed from err
            except httpx.HTTPError as err:
                raise UpdateFailed(f"Error communicating with API: {err}") from err

            return span_panel

    name = "SN-TODO"

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"span panel {name}",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
        NAME: name,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry.
    """
    _LOGGER.debug("ASYNC_UNLOAD")
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
