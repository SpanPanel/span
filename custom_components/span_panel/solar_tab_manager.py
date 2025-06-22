"""Solar tab manager for SPAN Panel integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class SolarTabManager:
    """Manages enabling/disabling tab circuits for solar configuration."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Initialize the solar tab manager."""
        self._hass = hass
        self._config_entry = config_entry

    async def enable_solar_tabs(self, leg1: int, leg2: int) -> None:
        """Enable specific tab circuits for solar use (but keep them hidden).

        Note: With the simplified approach, unmapped tab sensors are always enabled
        but hidden. This method is kept for compatibility but doesn't need to do anything.
        """
        _LOGGER.debug("Solar tabs %d and %d configured (sensors already enabled)", leg1, leg2)

    async def disable_solar_tabs(self) -> None:
        """Disable all solar tab circuits when solar is disabled.

        Note: With the simplified approach, we leave unmapped tab sensors enabled
        but hidden so users can access them if needed. This method is kept for
        compatibility but doesn't need to do anything.
        """
        _LOGGER.debug("Solar disabled (leaving unmapped tab sensors enabled but hidden)")
