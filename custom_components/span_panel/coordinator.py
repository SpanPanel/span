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
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

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
        """Initialize the coordinator."""
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

            # Schedule reload outside of the coordinator's update cycle to avoid conflicts
            async def schedule_reload() -> None:
                """Schedule the reload after the current update cycle completes."""
                try:
                    # Wait for current operations to complete
                    await self.hass.async_block_till_done()

                    if self.config_entry is None:
                        _LOGGER.error(
                            "Cannot reload: config_entry is None - integration incorrectly initialized"
                        )
                        return

                    _LOGGER.info("Auto-sync performing scheduled integration reload")
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    _LOGGER.info("Auto-sync integration reload completed")

                except (ConfigEntryNotReady, HomeAssistantError) as e:
                    _LOGGER.error("Auto-sync failed to reload integration: %s", e)
                except Exception as e:
                    _LOGGER.error("Unexpected error during auto-sync reload: %s", e, exc_info=True)

            # Schedule the reload to run outside the current update cycle
            self.hass.async_create_task(schedule_reload())

            # Return current data and continue with normal operation until reload completes
            return self.span_panel_api

        try:
            _LOGGER.debug("Starting coordinator update")
            await asyncio.wait_for(self.span_panel_api.update(), timeout=API_TIMEOUT)
            return self.span_panel_api
        except SpanPanelAuthError as err:
            _LOGGER.error("Authentication failed while updating Span data: %s", str(err))
            raise ConfigEntryAuthFailed from err
        except (SpanPanelConnectionError, SpanPanelTimeoutError) as err:
            _LOGGER.error("Connection/timeout error while updating Span data: %s", str(err))
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except SpanPanelRetriableError as err:
            _LOGGER.warning(
                "Retriable error occurred while updating Span data (will retry): %s",
                str(err),
            )
            raise UpdateFailed(f"Temporary SPAN Panel error: {err}") from err
        except SpanPanelServerError as err:
            _LOGGER.error("SPAN Panel server error (will not retry): %s", str(err))
            raise UpdateFailed(f"SPAN Panel server error: {err}") from err
        except SpanPanelAPIError as err:
            _LOGGER.error("API error while updating Span data: %s", str(err))
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except TimeoutError as err:
            _LOGGER.error(
                "An asyncio.TimeoutError occurred while updating Span data: %s",
                str(err),
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err
