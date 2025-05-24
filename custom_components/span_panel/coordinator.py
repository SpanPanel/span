"""Coordinator for Span Panel."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import httpx

from .const import API_TIMEOUT
from .span_panel import SpanPanel

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelCoordinator(DataUpdateCoordinator[SpanPanel]):
    """Coordinator for Span Panel."""

    def __init__(
        self,
        hass: HomeAssistant,
        span_panel: SpanPanel,
        name: str,
        update_interval: int,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"span panel {name}",
            update_interval=timedelta(seconds=update_interval),
            always_update=True,
        )
        self.span_panel_api = span_panel
        self.config_entry: ConfigEntry | None = config_entry

        # Flag for panel name auto-sync integration reload
        self._needs_reload = False

    def request_reload(self) -> None:
        """Request an integration reload for the next update cycle."""
        self._needs_reload = True
        _LOGGER.debug("Integration reload requested for next update cycle")

    async def _async_update_data(self) -> SpanPanel:
        """Fetch data from API endpoint."""
        # Check if reload is needed before updating (auto-sync)
        if self._needs_reload:
            self._needs_reload = False
            _LOGGER.info("Auto-sync triggering integration reload")
            try:
                if self.config_entry is None:
                    _LOGGER.error(
                        "Cannot reload: config_entry is None - integration incorrectly initialized"
                    )
                    raise ConfigEntryNotReady(
                        "Config entry is None - integration incorrectly initialized"
                    )
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                # After successful reload, this coordinator instance is destroyed
                # so we never reach this point - no need to return anything
                return (
                    self.span_panel_api
                )  # Return current data in case reload is delayed
            except (ConfigEntryNotReady, HomeAssistantError) as e:
                _LOGGER.error("auto-sync failed to reload integration: %s", e)
                # Continue with normal update if reload fails

        try:
            _LOGGER.debug("Starting coordinator update")
            await asyncio.wait_for(self.span_panel_api.update(), timeout=API_TIMEOUT)
            return self.span_panel_api
        except httpx.HTTPStatusError as err:
            if err.response.status_code == httpx.codes.UNAUTHORIZED:
                raise ConfigEntryAuthFailed from err
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
        except TimeoutError as err:
            _LOGGER.error(
                "An asyncio.TimeoutError occurred while updating Span data: %s",
                str(err),
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err
