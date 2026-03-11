"""Span Panel Coordinator for managing data updates and entity migrations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
from time import time as _epoch_time
from typing import Protocol

from homeassistant.components.persistent_notification import async_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from span_panel_api import (
    DynamicSimulationEngine,
    SpanMqttClient,
    SpanPanelSnapshot,
)
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

from .const import DOMAIN
from .helpers import build_circuit_unique_id
from .options import ENERGY_REPORTING_GRACE_PERIOD


class SpanCircuitEnergySensorProtocol(Protocol):
    """Protocol for circuit energy sensors that expose their dip offset."""

    @property
    def energy_offset(self) -> float:
        """Cumulative dip compensation offset."""
        ...


_LOGGER = logging.getLogger(__name__)

# Suppress the noisy "Manually updated span_panel data" DEBUG message that
# HA's DataUpdateCoordinator emits on every async_set_updated_data() call.
# In push/streaming mode this fires every ~1s and drowns out useful debug logs.


class _SuppressManualUpdateFilter(logging.Filter):
    """Filter out the HA DataUpdateCoordinator 'Manually updated' noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "Manually updated" not in record.getMessage()


_LOGGER.addFilter(_SuppressManualUpdateFilter())

# Fallback poll interval for MQTT streaming mode (push is the primary update path)
_STREAMING_FALLBACK_INTERVAL = timedelta(seconds=60)

# Poll interval for simulation mode (no streaming, coordinator polls get_snapshot)
_SIMULATION_POLL_INTERVAL = timedelta(seconds=5)


class SpanPanelCoordinator(DataUpdateCoordinator[SpanPanelSnapshot]):
    """Coordinator for managing Span Panel data updates and entity migrations."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SpanMqttClient | DynamicSimulationEngine,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self._client = client
        self.config_entry = config_entry
        # Track last tick for visibility into cadence
        self._last_tick_epoch: float | None = None
        # Flag to track if a reload was requested
        self._reload_requested = False
        # Flag to track if panel is offline/unreachable
        self._panel_offline = False
        # Track last grace period value for comparison
        self._last_grace_period = config_entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15)

        # Streaming state
        self._unregister_streaming: Callable[[], None] | None = None

        # Simulation offline mode
        self._simulation_offline_minutes: int = 0
        self._offline_start_time: float | None = None

        # Hardware capability tracking — detect when BESS/PV are commissioned
        # and trigger a reload so the factory creates the appropriate sensors.
        self._known_capabilities: frozenset[str] | None = None

        # Energy dip compensation — sensors append events here during updates;
        # drained and surfaced as a persistent notification after each cycle.
        self._pending_dip_events: list[tuple[str, float, float]] = []

        # Circuit energy sensor registry — consumed/produced sensors register
        # here so net energy sensors can read their dip offsets directly.
        self._circuit_energy_sensors: dict[tuple[str, str], SpanCircuitEnergySensorProtocol] = {}

        # MQTT streaming: push is the primary update path; poll is a safety net.
        # Simulation: poll is the only update path; use the snapshot interval.
        if isinstance(client, SpanMqttClient):
            update_interval = _STREAMING_FALLBACK_INTERVAL
        else:
            update_interval = _SIMULATION_POLL_INTERVAL

        _LOGGER.info(
            "Span Panel coordinator: poll interval %s seconds",
            update_interval.total_seconds(),
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
        )

        # Ensure config_entry is properly set after super().__init__
        self.config_entry = config_entry

    @property
    def client(self) -> SpanMqttClient | DynamicSimulationEngine:
        """Return the underlying panel client for entity control."""
        return self._client

    @property
    def panel_offline(self) -> bool:
        """Return True if the panel is currently offline/unreachable."""
        return self._panel_offline

    def request_reload(self) -> None:
        """Request a reload of the integration."""
        self._reload_requested = True

    # --- Energy dip compensation ---

    def report_energy_dip(self, entity_id: str, delta: float, cumulative_offset: float) -> None:
        """Record an energy dip detected by a sensor during this update cycle.

        Called synchronously by sensors from _process_raw_value. No I/O —
        just a list append. Events are drained in _run_post_update_tasks.
        """
        self._pending_dip_events.append((entity_id, delta, cumulative_offset))

    def register_circuit_energy_sensor(
        self, circuit_id: str, energy_type: str, sensor: SpanCircuitEnergySensorProtocol
    ) -> None:
        """Register a consumed/produced energy sensor so net energy can read its dip offset."""
        self._circuit_energy_sensors[(circuit_id, energy_type)] = sensor

    def get_circuit_dip_offset(self, circuit_id: str, energy_type: str) -> float:
        """Return the cumulative dip offset from the registered sensor, or 0."""
        sensor = self._circuit_energy_sensors.get((circuit_id, energy_type))
        if sensor is None:
            return 0.0
        return sensor.energy_offset

    async def _fire_dip_notification(self) -> None:
        """Create a persistent notification summarising energy dips this cycle."""
        if not self._pending_dip_events:
            return

        events = self._pending_dip_events
        self._pending_dip_events = []

        title = "SPAN Panel: Energy Dip Detected"
        preamble = (
            "The following energy sensors reported a decrease in their "
            "counter value. Dip compensation has automatically applied "
            "offsets — no action is required for new data."
        )

        lines: list[str] = []
        for entity_id, delta, offset in events:
            lines.append(
                f"- **{entity_id}**: dip {delta:.1f} Wh (cumulative offset {offset:.1f} Wh)"
            )

        body = preamble + "\n\n" + "\n".join(lines)

        entry_id = self.config_entry.entry_id
        async_create(
            self.hass,
            body,
            title=title,
            notification_id=f"span_energy_dip_{entry_id}",
        )

    # --- Streaming ---

    async def async_setup_streaming(self) -> None:
        """Set up push streaming if the client supports it."""
        if not isinstance(self._client, SpanMqttClient):
            return

        self._unregister_streaming = self._client.register_snapshot_callback(self._on_snapshot_push)
        await self._client.start_streaming()
        _LOGGER.info("MQTT push streaming started")

    async def _on_snapshot_push(self, snapshot: SpanPanelSnapshot) -> None:
        """Handle a pushed snapshot from MQTT streaming."""
        self._panel_offline = False
        self._check_capability_change(snapshot)
        self.async_set_updated_data(snapshot)
        await self._run_post_update_tasks(snapshot)

    async def async_shutdown(self) -> None:
        """Shut down the coordinator and release resources."""
        if self._unregister_streaming is not None:
            self._unregister_streaming()
            self._unregister_streaming = None

        if isinstance(self._client, SpanMqttClient):
            await self._client.stop_streaming()
            await self._client.close()

        _LOGGER.info("Coordinator shutdown complete")

    # --- Simulation offline mode ---

    def set_simulation_offline_mode(self, minutes: int) -> None:
        """Configure simulation offline mode duration.

        When minutes > 0, the coordinator will raise SpanPanelConnectionError
        during data updates for the specified duration, triggering the energy
        grace period path in entity base classes.
        """
        self._simulation_offline_minutes = minutes
        if minutes > 0:
            self._offline_start_time = _epoch_time()
        else:
            self._offline_start_time = None

    def _is_simulation_offline(self) -> bool:
        """Check if the simulation is currently in offline mode."""
        if self._simulation_offline_minutes <= 0 or self._offline_start_time is None:
            return False

        elapsed = _epoch_time() - self._offline_start_time
        if elapsed >= self._simulation_offline_minutes * 60:
            # Window expired — resume normal operation
            self._simulation_offline_minutes = 0
            self._offline_start_time = None
            return False

        return True

    # --- Hardware capability detection ---

    @staticmethod
    def _detect_capabilities(snapshot: SpanPanelSnapshot) -> frozenset[str]:
        """Derive optional hardware capabilities present in the snapshot."""
        caps: set[str] = set()
        if snapshot.battery.soe_percentage is not None:
            caps.add("bess")
        if snapshot.power_flow_pv is not None or any(
            c.device_type == "pv" for c in snapshot.circuits.values()
        ):
            caps.add("pv")
        if snapshot.power_flow_site is not None:
            caps.add("power_flows")
        if (
            any(c.device_type == "evse" for c in snapshot.circuits.values())
            or len(snapshot.evse) > 0
        ):
            caps.add("evse")
        return frozenset(caps)

    def _check_capability_change(self, snapshot: SpanPanelSnapshot) -> None:
        """Check if hardware capabilities changed and request reload if expanded."""
        current = self._detect_capabilities(snapshot)
        if self._known_capabilities is None:
            # First snapshot — record baseline
            self._known_capabilities = current
            return

        new_caps = current - self._known_capabilities
        if new_caps:
            _LOGGER.info(
                "New hardware capabilities detected: %s — requesting reload",
                ", ".join(sorted(new_caps)),
            )
            self._known_capabilities = current
            self.request_reload()

    # --- Post-update maintenance ---

    async def _run_post_update_tasks(self, snapshot: SpanPanelSnapshot) -> None:
        """Run maintenance tasks after a snapshot update.

        Called from both the polling path (_async_update_data) and the streaming
        path (_on_snapshot_push). The HA DataUpdateCoordinator resets its fallback
        poll timer on every async_set_updated_data() call, so during active MQTT
        streaming the polling path effectively never fires. This shared method
        ensures reload requests and pending migrations are processed regardless
        of transport mode.
        """
        # Fire persistent notification for any energy dips detected this cycle
        await self._fire_dip_notification()

        # Handle reload request if one was made (e.g., name sync, capability change)
        if self._reload_requested:
            self._reload_requested = False
            self.hass.async_create_task(self._async_reload_task())

        # Check for pending solar entity migration (v1 solar → v2 PV circuit)
        if self.config_entry.data.get("solar_migration_pending", False):
            await self._handle_solar_migration(snapshot)

    # --- Data update ---

    async def _async_update_data(self) -> SpanPanelSnapshot:
        """Fetch data from the panel client."""
        try:
            # Reset offline flag on successful update
            self._panel_offline = False

            # Performance timing
            cycle_start = _epoch_time()
            self._last_tick_epoch = cycle_start

            # Simulation offline mode: raise before fetching to trigger grace period
            if self._is_simulation_offline():
                raise SpanPanelConnectionError("Panel is offline in simulation mode")

            fetch_start = _epoch_time()
            snapshot = await self._client.get_snapshot()
            fetch_duration = _epoch_time() - fetch_start

            cycle_total = _epoch_time() - cycle_start
            _LOGGER.info(
                "SPAN Panel update cycle completed - Total: %.3fs | Fetch: %.3fs",
                cycle_total,
                fetch_duration,
            )

            # Check for new hardware capabilities (BESS, PV, power-flows)
            self._check_capability_change(snapshot)

            await self._run_post_update_tasks(snapshot)

            return snapshot

        except SpanPanelAuthError as err:
            raise ConfigEntryAuthFailed from err

        except ConfigEntryAuthFailed:
            raise

        except Exception as err:
            self._panel_offline = True

            if isinstance(err, SpanPanelConnectionError):
                _LOGGER.warning("Span Panel connection error: %s", err)
            elif isinstance(err, SpanPanelTimeoutError):
                _LOGGER.warning("Span Panel timeout: %s", err)
            elif isinstance(err, SpanPanelServerError):
                _LOGGER.warning("Span Panel server error: %s", err)
            elif isinstance(err, SpanPanelAPIError):
                _LOGGER.warning("Span Panel API error: %s", err)
            else:
                _LOGGER.warning("Unexpected Span Panel error: %s", err)

            # Return last known data to keep coordinator updating for grace period logic.
            # On first refresh (self.data is None), re-raise so async_config_entry_first_refresh
            # surfaces the error properly.
            if self.data is not None:
                return self.data
            raise

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

    # --- Solar entity migration (v1 virtual sensors → v2 PV circuit sensors) ---

    # Old solar unique_id suffixes → circuit sensor description keys.
    # These map the v1 virtual solar sensor unique_ids to the v2 circuit sensor
    # description keys used by build_circuit_unique_id.
    _SOLAR_SUFFIX_TO_DESCRIPTION_KEY: dict[str, str] = {
        "_solar_current_power": "instantPowerW",
        "_solar_produced_energy": "producedEnergyWh",
        "_solar_consumed_energy": "consumedEnergyWh",
        "_solar_net_energy": "netEnergyWh",
    }

    async def _handle_solar_migration(self, snapshot: SpanPanelSnapshot) -> None:
        """Migrate v1 virtual solar entities to v2 PV circuit entities.

        When solar_migration_pending is set in config entry data (by v3→v4
        config migration), this method finds the PV circuit in the MQTT
        snapshot and rewrites entity registry unique_ids in-place so that
        history and statistics are preserved.

        Old pattern: span_{serial}_solar_current_power
        New pattern: span_{serial}_{pv_uuid}_power
        """
        # TODO(post-2.0.0): Remove solar_migration_pending handling once all
        # users have been forced through the 2.0.x upgrade path.

        pv_circuits = [c for c in snapshot.circuits.values() if c.device_type == "pv"]

        if len(pv_circuits) == 0:
            _LOGGER.info("No PV circuits found — removing stale solar entities")
            self._remove_stale_solar_entities()
            self._clear_solar_migration_flag()
            return

        if len(pv_circuits) > 1:
            _LOGGER.warning(
                "Found %d PV circuits — cannot auto-migrate solar entities. "
                "Please reconfigure solar manually.",
                len(pv_circuits),
            )
            async_create(
                self.hass,
                "Multiple PV circuits detected on your SPAN Panel. "
                "Automatic solar entity migration cannot proceed. "
                "Please reconfigure solar settings in the integration options.",
                title="SPAN Panel: Solar Migration Required",
                notification_id=f"span_solar_migration_{self.config_entry.entry_id}",
            )
            return

        # Single PV circuit — proceed with unique_id rewrite
        pv_circuit = pv_circuits[0]
        pv_uuid = pv_circuit.circuit_id
        serial = snapshot.serial_number
        _LOGGER.info(
            "Found single PV circuit %s — migrating solar entity unique IDs",
            pv_uuid,
        )

        entity_registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(entity_registry, self.config_entry.entry_id)
        migrated_count = 0

        for entry in entries:
            if not entry.unique_id:
                continue
            for old_suffix, desc_key in self._SOLAR_SUFFIX_TO_DESCRIPTION_KEY.items():
                if entry.unique_id.endswith(old_suffix):
                    new_unique_id = build_circuit_unique_id(serial, pv_uuid, desc_key)
                    _LOGGER.info(
                        "Migrating solar entity: %s → %s (entity_id=%s)",
                        entry.unique_id,
                        new_unique_id,
                        entry.entity_id,
                    )
                    entity_registry.async_update_entity(
                        entry.entity_id, new_unique_id=new_unique_id
                    )
                    migrated_count += 1
                    break

        _LOGGER.info("Solar migration complete: %d entities migrated", migrated_count)
        self._clear_solar_migration_flag()

        if migrated_count > 0:
            # Reload so platform re-registers entities with updated unique IDs
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

    def _remove_stale_solar_entities(self) -> None:
        """Remove v1 virtual solar entities that have no v2 PV equivalent."""
        entity_registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(entity_registry, self.config_entry.entry_id)
        for entry in entries:
            if not entry.unique_id:
                continue
            if any(
                entry.unique_id.endswith(suffix) for suffix in self._SOLAR_SUFFIX_TO_DESCRIPTION_KEY
            ):
                _LOGGER.info(
                    "Removing stale solar entity: %s (unique_id=%s)",
                    entry.entity_id,
                    entry.unique_id,
                )
                entity_registry.async_remove(entry.entity_id)

    def _clear_solar_migration_flag(self) -> None:
        """Clear the solar_migration_pending flag from config entry data."""
        updated_data = dict(self.config_entry.data)
        updated_data.pop("solar_migration_pending", None)
        self.hass.config_entries.async_update_entry(self.config_entry, data=updated_data)
