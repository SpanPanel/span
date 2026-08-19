"""Span Panel Coordinator for managing data updates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
from time import time as _epoch_time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from . import SpanPanelConfigEntry
    from .current_monitor import CurrentMonitor
    from .graph_horizon import GraphHorizonManager

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from span_panel_api import SpanMqttClient, SpanPanelClientProtocol, SpanPanelSnapshot
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelStaleDataError

from .const import DOMAIN
from .field_paths import iter_all_field_path_declarations
from .id_builder import build_circuit_unique_id, get_user_friendly_suffix
from .schema_repairs import async_sync_schema_issues
from .schema_validation import SchemaFindings, evaluate_field_metadata
from .sensor_definitions import sensor_descriptions_by_field_path


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


class SpanPanelCoordinator(DataUpdateCoordinator[SpanPanelSnapshot]):
    """Coordinator for managing Span Panel data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SpanMqttClient,
        config_entry: SpanPanelConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self._client = client
        self.config_entry: SpanPanelConfigEntry = config_entry
        # Track last tick for visibility into cadence
        self._last_tick_epoch: float | None = None
        # Flag to track if a reload was requested
        self._reload_requested = False
        # Flag to track if panel is offline/unreachable
        self._panel_offline = False

        # Streaming state
        self._unregister_streaming: Callable[[], None] | None = None
        self._unregister_connection: Callable[[], None] | None = None
        self._unregister_schema_change: Callable[[], None] | None = None

        # Hardware capability tracking — detect when BESS/PV are commissioned
        # and trigger a reload so the factory creates the appropriate sensors.
        self._known_capabilities: frozenset[str] | None = None

        # Schema validation — run once after first successful refresh
        self._schema_validated = False
        self._findings: SchemaFindings | None = None

        # Energy dip compensation — sensors append events here during updates;
        # drained and surfaced as a persistent notification after each cycle.
        self._pending_dip_events: list[tuple[str, float, float]] = []

        # Circuit energy sensor registry — consumed/produced sensors register
        # here so net energy sensors can read their dip offsets directly.
        self._circuit_energy_sensors: dict[tuple[str, str], SpanCircuitEnergySensorProtocol] = {}

        # Current monitor — set by async_setup_entry when monitoring is enabled
        self.current_monitor: CurrentMonitor | None = None

        # Graph horizon manager — set by async_setup_entry
        self.graph_horizon_manager: GraphHorizonManager | None = None

        update_interval = _STREAMING_FALLBACK_INTERVAL

        _LOGGER.debug(
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
    def client(self) -> SpanMqttClient:
        """Return the underlying panel client for entity control."""
        return self._client

    @property
    def panel_offline(self) -> bool:
        """Return True if the panel is currently offline/unreachable."""
        return self._panel_offline

    def request_reload(self) -> None:
        """Request a reload of the integration."""
        self._reload_requested = True

    def _mark_panel_online(self) -> None:
        """Mark the panel online and log a recovery transition once."""
        if self._panel_offline:
            _LOGGER.info("%s is back online", self.config_entry.title or "SPAN Panel")
        self._panel_offline = False

    def _mark_panel_offline(self, reason: Exception | str) -> None:
        """Mark the panel offline and log the transition once.

        `reason` is rendered with %s — both Exception and str format
        correctly. The broker-disconnect path (from the MQTT client
        connection callback) passes a short string; the snapshot-poll
        path passes a SpanPanelStaleDataError or unexpected Exception.
        """
        if not self._panel_offline:
            _LOGGER.info(
                "%s is unavailable: %s",
                self.config_entry.title or "SPAN Panel",
                reason,
            )
        self._panel_offline = True

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
        """Set up push streaming and broker-connection state listening."""
        self._unregister_connection = self._client.register_connection_callback(
            self._on_connection_change
        )
        self._unregister_schema_change = self._client.register_schema_change_callback(
            self._on_schema_generation_change
        )
        self._unregister_streaming = self._client.register_snapshot_callback(self._on_snapshot_push)
        await self._client.start_streaming()
        _LOGGER.info("MQTT push streaming started")

    def _on_schema_generation_change(self, previous: str | None, current: str | None) -> None:
        """Reload the entry when the panel changes schema generation underneath us.

        The library rebuilds its parser on its own, which restores *reading* — values
        resolve again straight away. It cannot restore *topology*: devices and
        entities are created in `async_setup_entry` from the tree as it looked then.
        v1.0 introduces a MID the flat tree has no equivalent for and re-keys the
        EVSEs, so without a reload the panel reads correctly and still shows the old
        device set. Observed exactly that on a live upgrade — data flowed, entities
        did not appear, and a manual reload was needed.

        Scheduled rather than awaited: this is called from the client's own callback
        fan-out, and reloading the entry tears down that client. `async_schedule_reload`
        defers to the loop so the teardown does not run inside the object being torn
        down.
        """
        _LOGGER.warning(
            "SPAN panel firmware upgraded its eBus schema generation: data-model-version "
            "%s -> %s. Reloading the integration so devices and entities match the new "
            "tree; the MID and any re-keyed chargers appear after the reload.",
            previous or "absent (flat)",
            current or "absent (flat)",
        )
        async_create(
            self.hass,
            (
                f"Your SPAN Panel reported a new eBus data model "
                f"(**{previous or 'flat'} → {current or 'flat'}**), which happens after a "
                "firmware upgrade.\n\n"
                "The integration is reloading so its devices and entities match what the "
                "panel now publishes. New devices — such as the Microgrid Interconnect "
                "Device — appear once that finishes.\n\n"
                "Entities that were renamed or replaced by the upgrade may need to be "
                "removed manually if they remain unavailable."
            ),
            title="SPAN Panel firmware upgraded",
            notification_id=f"span_schema_upgrade_{self.config_entry.entry_id}",
        )
        self._explain_the_upgrade(current)
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    def _explain_the_upgrade(self, current: str | None) -> None:
        """Raise a repair describing what the new schema changed for the user.

        Nothing they depend on goes away, which is worth saying plainly because a
        firmware upgrade invites the opposite assumption.

        `sensor.*_dsm_grid_state` keeps its entity id and its history and gets *more*
        trustworthy. Under flat, `schema_0` derived it: the battery's `grid-state` when
        one was commissioned, otherwise an inference from `dominant-power-source` and
        whether any power was crossing the grid connection. Under v1.0 it reads the
        islanding state the MID actually senses -- the heuristic v1.0 exists to retire,
        retired.

        `binary_sensor.*_grid_islandable` also survives. v1.0 publishes no panel-level
        `grid-islandable`, on purpose: `devices/bess.md` reads backup capability from
        the capability set, "a MID `grid` child means premises-segment backup", and
        "there is no single 'islanded?' bit to reconcile". So it now reflects MID
        presence, the classifier the spec nominates.

        What is genuinely new is the MID device itself and its `grid-state`, the health
        of the utility supply, which flat did not report at all.

        A repair rather than only a notification because notifications are dismissed
        and gone: a user who was away when the panel upgraded should still find out
        that a device appeared and why a sensor changed provenance. It asks for no
        action, which is why it is the mildest severity available.
        """
        # Absent means flat, present means parent/child -- the migration guide's own
        # detection rule.
        #
        # Guarding on the direction even though panel firmware does not roll back:
        # once a panel is on v1.0 it stays there, so in the field this only ever fires
        # one way. The reverse happens solely in the upgrade rehearsal, where the two
        # simulators are swapped under a live client, and raising a retirement repair
        # there would be noise about a transition no user experiences.
        if current is None:
            return
        async_create_issue(
            self.hass,
            DOMAIN,
            f"panel_upgraded_to_ebus_v1_{self.config_entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="panel_upgraded_to_ebus_v1",
        )

    def _on_connection_change(self, connected: bool) -> None:
        """Handle a broker connection state edge from the MQTT client.

        Called on the event loop when the bridge transitions between
        connected and disconnected. Flips the panel-offline flag and
        pushes an immediate listener update so sensors enter or exit
        grace-period logic without waiting for the 60 s fallback poll.

        Listener fan-out is guarded by a real state change so a misbehaving
        or future-version library that re-emits the same edge does not
        trigger spurious entity re-renders.
        """
        was_offline = self._panel_offline
        if connected:
            self._mark_panel_online()
        else:
            self._mark_panel_offline("MQTT broker disconnected")
        if self._panel_offline != was_offline:
            self.async_update_listeners()

    async def _on_snapshot_push(self, snapshot: SpanPanelSnapshot) -> None:
        """Handle a pushed snapshot from MQTT streaming."""
        self._mark_panel_online()
        self._check_capability_change(snapshot)
        self.async_set_updated_data(snapshot)
        await self._run_post_update_tasks(snapshot)

    async def async_shutdown(self) -> None:
        """Shut down the coordinator and release resources."""
        if self._unregister_connection is not None:
            self._unregister_connection()
            self._unregister_connection = None

        if self._unregister_schema_change is not None:
            self._unregister_schema_change()
            self._unregister_schema_change = None

        if self._unregister_streaming is not None:
            self._unregister_streaming()
            self._unregister_streaming = None

        await self._client.stop_streaming()
        await self._client.close()

        _LOGGER.info("Coordinator shutdown complete")

    # --- Schema validation ---

    def _run_schema_validation(self) -> None:
        """Classify the adapter's field metadata once at startup.

        Reads the metadata through ``SpanPanelClientProtocol`` so this module
        never names a transport class, and stores the result for the platforms
        and the Repairs reconciler to read.
        """
        field_metadata = (
            self._client.field_metadata
            if isinstance(self._client, SpanPanelClientProtocol)
            else None
        )

        if field_metadata is None:
            # "Unknown", NOT "nothing is wrong". `field_metadata` is None for the
            # whole _on_pre_rebuild -> retained-message window, and that fires on
            # an ORDINARY reconnect (after MQTT_FULL_REBUILD_AFTER_FAILURES), not
            # only on a generation change. Reconciling against empty findings here
            # would delete every schema issue — and with it every dismissal the
            # user has made. Keep the previous findings and skip this pass.
            _LOGGER.debug("Schema validation skipped: metadata not available yet")
            return

        self._findings = evaluate_field_metadata(
            field_metadata, sensor_descriptions_by_field_path()
        )
        async_sync_schema_issues(
            self.hass,
            self.config_entry,
            self._findings,
            self._affected_entity_ids(self._findings.unresolved),
        )

    def _affected_entity_ids(self, field_paths: frozenset[str]) -> dict[str, list[str]]:
        """Entity ids this entry owns that read each of `field_paths`.

        Matched through `get_user_friendly_suffix(description.key)`, not the
        snapshot field name: a unique_id ends in the suffix ("_power"), never in
        the field ("instant_power_w"), so matching on the field would silently
        find nothing and report every dead field as affecting zero entities.
        """
        entity_registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(entity_registry, self.config_entry.entry_id)

        suffixes_by_path: dict[str, set[str]] = {path: set() for path in field_paths}
        for field_path, description in iter_all_field_path_declarations():
            if field_path in suffixes_by_path:
                suffixes_by_path[field_path].add(get_user_friendly_suffix(description.key))

        return {
            path: [
                entry.entity_id
                for entry in entries
                if any(entry.unique_id.endswith(suffix) for suffix in suffixes)
            ]
            for path, suffixes in suffixes_by_path.items()
        }

    @property
    def unresolved_paths(self) -> frozenset[str]:
        """Field paths the adapter could not resolve. Empty when healthy."""
        return self._findings.unresolved if self._findings is not None else frozenset()

    @property
    def schema_findings(self) -> SchemaFindings | None:
        """Findings from the last completed validation pass, if any."""
        return self._findings

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

    # --- Solar entity migration (v1 → v2) ---

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

    # --- Post-update maintenance ---

    async def _run_post_update_tasks(self, snapshot: SpanPanelSnapshot) -> None:
        """Run maintenance tasks after a snapshot update.

        Called from both the polling path (_async_update_data) and the streaming
        path (_on_snapshot_push). The HA DataUpdateCoordinator resets its fallback
        poll timer on every async_set_updated_data() call, so during active MQTT
        streaming the polling path effectively never fires. This shared method
        ensures reload requests are processed regardless of transport mode.
        """
        # One-shot schema validation after first successful refresh
        if not self._schema_validated:
            self._schema_validated = True
            self._run_schema_validation()

        # Check for pending solar entity migration (v1 solar → v2 PV circuit)
        if self.config_entry.data.get("solar_migration_pending", False):
            await self._handle_solar_migration(snapshot)

        # Fire persistent notification for any energy dips detected this cycle
        await self._fire_dip_notification()

        # Delegate snapshot to current monitor if enabled
        if self.current_monitor is not None:
            self.current_monitor.process_snapshot(snapshot)

        # Handle reload request if one was made (e.g., name sync, capability change)
        if self._reload_requested:
            self._reload_requested = False
            self.hass.async_create_task(self._async_reload_task())

    # --- Data update ---

    async def _async_update_data(self) -> SpanPanelSnapshot:
        """Fetch data from the panel client."""
        try:
            # Performance timing
            cycle_start = _epoch_time()
            self._last_tick_epoch = cycle_start

            fetch_start = _epoch_time()
            snapshot = await self._client.get_snapshot()
            fetch_duration = _epoch_time() - fetch_start

            cycle_total = _epoch_time() - cycle_start
            _LOGGER.debug(
                "SPAN Panel update cycle completed - Total: %.3fs | Fetch: %.3fs",
                cycle_total,
                fetch_duration,
            )

            self._mark_panel_online()

            # Check for new hardware capabilities (BESS, PV, power-flows)
            self._check_capability_change(snapshot)

            await self._run_post_update_tasks(snapshot)

        except SpanPanelAuthError as err:
            raise ConfigEntryAuthFailed from err

        except ConfigEntryAuthFailed:
            raise

        except SpanPanelStaleDataError as err:
            # Expected offline path — the library signals the client
            # isn't live. Same handling as other offline errors.
            self._mark_panel_offline(err)
            if self.data is not None:
                return self.data
            raise

        except Exception as err:
            # Unexpected error — log the transition but keep the
            # coordinator ticking on last-known data for grace-period logic.
            # On first refresh (self.data is None), re-raise so
            # async_config_entry_first_refresh surfaces the error properly.
            self._mark_panel_offline(err)
            if self.data is not None:
                return self.data
            raise
        else:
            return snapshot

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
        except Exception:
            _LOGGER.exception("Unexpected error during reload")
