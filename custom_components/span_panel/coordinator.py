"""Span Panel Coordinator for managing data updates and entity migrations."""

from __future__ import annotations

from datetime import timedelta
import logging
from time import time as _epoch_time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SIGNAL_STAGE_NATIVE_SENSORS,
    SIGNAL_STAGE_SELECTS,
    SIGNAL_STAGE_SWITCHES,
    SIGNAL_STAGE_SYNTHETIC_SENSORS,
)
from .entity_id_naming_patterns import EntityIdMigrationManager
from .span_panel import SpanPanel

_LOGGER = logging.getLogger(__name__)


class SpanPanelCoordinator(DataUpdateCoordinator[SpanPanel]):
    """Coordinator for managing Span Panel data updates and entity migrations."""

    def __init__(
        self,
        hass: HomeAssistant,
        span_panel: SpanPanel,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.span_panel = span_panel
        self.config_entry = config_entry
        self._migration_manager = EntityIdMigrationManager(hass, config_entry.entry_id)
        # Deterministic synthetic sensor set identifier, filled by synthetic setup
        self.synthetic_sensor_set_id: str | None = None
        # Track last tick for visibility into cadence
        self._last_tick_epoch: float | None = None

        # Get scan interval from options, with fallback to default
        raw_scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds())
        )

        # Coerce scan interval to integer seconds, clamp to minimum of 5
        try:
            # Accept strings, floats, and ints; e.g., "15", 15.0, 15
            scan_interval_seconds = int(float(raw_scan_interval))
        except (TypeError, ValueError):
            scan_interval_seconds = int(DEFAULT_SCAN_INTERVAL.total_seconds())

        if scan_interval_seconds < 5:
            _LOGGER.debug(
                "Configured scan interval %s is below minimum; clamping to 5 seconds",
                scan_interval_seconds,
            )
            scan_interval_seconds = 5

        if str(raw_scan_interval) != str(scan_interval_seconds):
            _LOGGER.debug(
                "Coerced scan interval option from raw=%s to %s seconds",
                raw_scan_interval,
                scan_interval_seconds,
            )

        # Log at INFO so it is visible without debug logging
        _LOGGER.info(
            "Span Panel coordinator: update interval set to %s seconds",
            scan_interval_seconds,
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval_seconds),
        )

        # Ensure config_entry is properly set after super().__init__
        self.config_entry = config_entry

    def get_synthetic_sensor_set_id(self) -> str | None:
        """Return the synthetic sensor set id if set during synthetic setup."""
        return self.synthetic_sensor_set_id

    async def _async_update_data(self) -> SpanPanel:
        """Fetch data from API endpoint."""
        try:
            # INFO log to make cadence visible without debug logging
            now_epoch = _epoch_time()
            # Track cadence locally for debugging purposes
            self._last_tick_epoch = now_epoch

            await self.span_panel.update()

            # Emit staged update signals in deterministic order so platforms
            # update sequentially instead of all entities at once. Entities
            # subscribe to their stage via dispatcher and perform their normal
            # state update when their stage signal fires.
            async_dispatcher_send(self.hass, SIGNAL_STAGE_SWITCHES)
            async_dispatcher_send(self.hass, SIGNAL_STAGE_SELECTS)
            async_dispatcher_send(self.hass, SIGNAL_STAGE_NATIVE_SENSORS)
            async_dispatcher_send(self.hass, SIGNAL_STAGE_SYNTHETIC_SENSORS)
            return self.span_panel

        except Exception as err:
            _LOGGER.error("Error communicating with Span Panel API: %s", err)
            if "401" in str(err) or "Unauthorized" in str(err):
                raise ConfigEntryAuthFailed("Authentication failed") from err
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def migrate_synthetic_entities(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate synthetic sensor entity IDs based on old and new configuration flags.

        This method delegates to the EntityIdMigrationManager to handle the actual migration logic.

        Args:
            old_flags: Configuration flags before the change
            new_flags: Configuration flags after the change

        Returns:
            bool: True if migration succeeded, False otherwise

        """
        return await self._migration_manager.migrate_synthetic_entities(old_flags, new_flags)
