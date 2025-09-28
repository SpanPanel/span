"""Span Panel Coordinator for managing data updates and entity migrations."""

from __future__ import annotations

from datetime import timedelta
import logging
from time import time as _epoch_time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from .entity_id_naming_patterns import EntityIdMigrationManager
from .exceptions import SpanPanelSimulationOfflineError
from .options import ENERGY_REPORTING_GRACE_PERIOD
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
        # Flag to track if a reload was requested
        self._reload_requested = False
        # Flag to track if panel is offline/unreachable
        self._panel_offline = False
        # Track last grace period value for comparison
        self._last_grace_period = config_entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15)

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

    @property
    def panel_offline(self) -> bool:
        """Return True if the panel is currently offline/unreachable."""
        return self._panel_offline

    def get_synthetic_sensor_set_id(self) -> str | None:
        """Return the synthetic sensor set id if set during synthetic setup."""
        return self.synthetic_sensor_set_id

    def request_reload(self) -> None:
        """Request a reload of the integration."""
        self._reload_requested = True

    async def _async_update_data(self) -> SpanPanel:
        """Fetch data from API endpoint."""
        try:
            # Reset offline flag on successful update
            self._panel_offline = False

            # Performance timing - start of update cycle
            cycle_start = _epoch_time()

            # Track cadence locally for debugging purposes
            self._last_tick_epoch = cycle_start

            # Time the panel data fetch
            panel_fetch_start = _epoch_time()
            await self.span_panel.update()
            panel_fetch_duration = _epoch_time() - panel_fetch_start

            cycle_total_duration = _epoch_time() - cycle_start

            # INFO level performance logging
            _LOGGER.info(
                "SPAN Panel update cycle completed - Total: %.3fs | Panel fetch: %.3fs",
                cycle_total_duration,
                panel_fetch_duration,
            )

            # Handle reload request if one was made
            if self._reload_requested:
                self._reload_requested = False
                self.hass.async_create_task(self._async_reload_task())

            # Check for pending legacy migration
            # Only attempt migration if integration data is properly set up in hass.data
            if self.config_entry.entry_id in self.hass.data.get(
                DOMAIN, {}
            ) and self.config_entry.options.get("pending_legacy_migration", False):
                _LOGGER.info(
                    "Found pending legacy migration flag in coordinator update, performing migration"
                )
                await self._handle_pending_legacy_migration()

            # Check for pending naming pattern migration
            # Only attempt migration if integration data is properly set up in hass.data
            if self.config_entry.entry_id in self.hass.data.get(DOMAIN, {}):
                _LOGGER.debug(
                    "Checking for pending_naming_migration flag: %s",
                    self.config_entry.options.get("pending_naming_migration", False),
                )
                if self.config_entry.options.get("pending_naming_migration", False):
                    _LOGGER.info(
                        "Found pending naming migration flag in coordinator update, performing migration"
                    )
                    _LOGGER.debug("Config entry options: %s", self.config_entry.options)
                    # Only proceed if we have the old flags stored (indicating a real user change)
                    if (
                        "old_use_circuit_numbers" in self.config_entry.options
                        and "old_use_device_prefix" in self.config_entry.options
                    ):
                        await self._handle_pending_naming_migration()
                    else:
                        _LOGGER.warning(
                            "Found pending_naming_migration flag but no old flags stored - skipping migration"
                        )
                        # Clean up the invalid flag
                        current_options = dict(self.config_entry.options)
                        current_options.pop("pending_naming_migration", None)
                        self.hass.config_entries.async_update_entry(
                            self.config_entry, options=current_options
                        )
            else:
                _LOGGER.debug(
                    "Integration data not yet set up in hass.data - skipping migration checks"
                )

            return self.span_panel

        except ConfigEntryAuthFailed:
            # Re-raise auth errors - these should trigger reauth
            raise

        except Exception as err:
            # Set offline flag for any error
            self._panel_offline = True

            # Performance timing for error path
            error_cycle_start = _epoch_time()

            # Log specific error types for debugging
            if isinstance(err, SpanPanelSimulationOfflineError):
                _LOGGER.debug("Span Panel simulation offline mode: %s", err)
            elif isinstance(err, SpanPanelConnectionError):
                _LOGGER.warning("Span Panel connection error: %s", err)
            elif isinstance(err, SpanPanelTimeoutError):
                _LOGGER.warning("Span Panel timeout: %s", err)
            elif isinstance(err, SpanPanelServerError):
                _LOGGER.warning("Span Panel server error: %s", err)
            elif isinstance(err, SpanPanelRetriableError):
                _LOGGER.warning("Span Panel retriable error: %s", err)
            elif isinstance(err, SpanPanelAPIError):
                _LOGGER.warning("Span Panel API error: %s", err)
            else:
                _LOGGER.warning("Unexpected Span Panel error: %s", err)

            error_cycle_duration = _epoch_time() - error_cycle_start

            # INFO level performance logging for error path
            _LOGGER.info(
                "SPAN Panel update cycle (ERROR PATH) completed - Total: %.3fs",
                error_cycle_duration,
            )

            # Return the last known data instead of raising UpdateFailed
            # This keeps the coordinator updating so grace period logic can work
            return self.span_panel

    async def _async_reload_task(self) -> None:
        """Task to handle integration reload with proper error handling."""
        try:
            _LOGGER.info("Reloading SPAN Panel integration")
            await self.hass.async_block_till_done()
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            _LOGGER.info("SPAN Panel integration reload completed successfully")

        except ConfigEntryNotReady as err:
            _LOGGER.warning("Config entry not ready during reload: %s", err)
        except HomeAssistantError as err:
            _LOGGER.error("Home Assistant error during reload: %s", err)
        except Exception as err:
            _LOGGER.exception("Unexpected error during reload: %s", err)

    async def migrate_entity_ids(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate entity IDs based on old and new configuration flags.

        This method delegates to the EntityIdMigrationManager to handle the actual migration logic.

        Args:
            old_flags: Configuration flags before the change
            new_flags: Configuration flags after the change

        Returns:
            bool: True if migration succeeded, False otherwise

        """
        return await self._migration_manager.migrate_entity_ids(old_flags, new_flags)

    async def _handle_pending_legacy_migration(self) -> None:
        """Handle pending legacy migration after integration startup.

        This function is called when a pending_legacy_migration flag is found in the
        config entry data. It performs the migration and then cleans up the flag.
        The migration happens during the coordinator update cycle.
        """
        # Always remove the flag first to prevent infinite loops
        _LOGGER.info("Removing pending_legacy_migration flag to prevent loops")
        current_options = dict(self.config_entry.options)
        current_options.pop("pending_legacy_migration", None)
        self.hass.config_entries.async_update_entry(self.config_entry, options=current_options)

        try:
            _LOGGER.info("Starting pending legacy migration")

            # Capture the old flags (legacy state)
            old_flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: False}

            # Get the new flags from the current config entry options
            new_flags = {
                USE_CIRCUIT_NUMBERS: self.config_entry.options.get(USE_CIRCUIT_NUMBERS, False),
                USE_DEVICE_PREFIX: self.config_entry.options.get(USE_DEVICE_PREFIX, True),
            }

            success = await self.migrate_entity_ids(old_flags, new_flags)

            if success:
                _LOGGER.info("Pending legacy migration completed successfully")
                _LOGGER.info("Scheduling final reload to display new entity IDs in UI")
                # Schedule reload to pick up new entity IDs
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
            else:
                _LOGGER.error("Pending legacy migration failed")

        except Exception as e:
            _LOGGER.error("Pending legacy migration task failed: %s", e, exc_info=True)

    async def _handle_pending_naming_migration(self) -> None:
        """Handle pending naming pattern migration after integration startup.

        This function is called when a pending_naming_migration flag is found in the
        config entry data. It performs the migration and then cleans up the flag.
        The migration happens during the coordinator update cycle.
        """
        # Always remove the flag first to prevent infinite loops
        _LOGGER.info("Removing pending_naming_migration flag to prevent loops")
        current_options = dict(self.config_entry.options)
        current_options.pop("pending_naming_migration", None)
        self.hass.config_entries.async_update_entry(self.config_entry, options=current_options)

        try:
            _LOGGER.info("Starting pending naming pattern migration")

            # Get the old flags that were stored during config flow processing
            old_flags = {
                USE_CIRCUIT_NUMBERS: self.config_entry.options.get(
                    "old_use_circuit_numbers", False
                ),
                USE_DEVICE_PREFIX: self.config_entry.options.get("old_use_device_prefix", False),
            }

            # Get the new flags from the current config entry options
            new_flags = {
                USE_CIRCUIT_NUMBERS: self.config_entry.options.get(USE_CIRCUIT_NUMBERS, False),
                USE_DEVICE_PREFIX: self.config_entry.options.get(USE_DEVICE_PREFIX, True),
            }

            # Use the generalized migration method that handles old/new flag comparison
            success = await self._migration_manager.migrate_entity_ids(old_flags, new_flags)

            if success:
                _LOGGER.info("Pending naming pattern migration completed successfully")
                # Clean up the old flags that were stored for migration
                current_options = dict(self.config_entry.options)
                current_options.pop("old_use_circuit_numbers", None)
                current_options.pop("old_use_device_prefix", None)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, options=current_options
                )
                # Schedule reload to pick up new entity IDs
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
            else:
                _LOGGER.error("Pending naming pattern migration failed")

        except Exception as e:
            _LOGGER.error("Pending naming pattern migration task failed: %s", e, exc_info=True)
