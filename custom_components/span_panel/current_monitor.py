"""Current monitoring for SPAN Panel circuits and mains legs.

Detects spike (instantaneous) and continuous overload conditions by comparing
current readings against breaker ratings with configurable thresholds.
Dispatches notifications via event bus, notify services, and persistent
notifications.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from span_panel_api import SpanPanelSnapshot

from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE,
    DEFAULT_NOTIFICATION_PRIORITY,
    DEFAULT_NOTIFICATION_TITLE_TEMPLATE,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    DOMAIN,
    EVENT_CURRENT_ALERT,
)
from .helpers import build_circuit_unique_id, build_panel_unique_id
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    ENABLE_EVENT_BUS,
    ENABLE_PERSISTENT_NOTIFICATIONS,
    NOTIFICATION_MESSAGE_TEMPLATE,
    NOTIFICATION_PRIORITY,
    NOTIFICATION_TITLE_TEMPLATE,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Maps mains leg identifiers to SpanPanelSnapshot attribute names
_MAINS_CURRENT_ATTRS: dict[str, str] = {
    "upstream_l1": "upstream_l1_current_a",
    "upstream_l2": "upstream_l2_current_a",
    "downstream_l1": "downstream_l1_current_a",
    "downstream_l2": "downstream_l2_current_a",
}

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "span_panel_current_monitor"


@dataclass
class MonitoredPointState:
    """Tracking state for a single monitored point (circuit or mains leg)."""

    last_current_a: float = 0.0
    over_threshold_since: datetime | None = None
    last_spike_alert: datetime | None = None
    last_continuous_alert: datetime | None = None


class CurrentMonitor:
    """Monitors current draw against breaker ratings for overload detection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the CurrentMonitor."""
        self._hass = hass
        self._entry = entry
        self._circuit_states: dict[str, MonitoredPointState] = {}
        self._mains_states: dict[str, MonitoredPointState] = {}
        self._circuit_overrides: dict[str, dict[str, Any]] = {}
        self._mains_overrides: dict[str, dict[str, Any]] = {}
        self._global_settings: dict[str, Any] = {}
        self._last_snapshot: SpanPanelSnapshot | None = None
        self._store: Store = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )

    # --- Public API ---

    def process_snapshot(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all circuits and mains legs."""
        self._last_snapshot = snapshot
        self._evaluate_circuits(snapshot)
        self._evaluate_mains(snapshot)

    def get_circuit_state(self, circuit_id: str) -> MonitoredPointState | None:
        """Return tracking state for a circuit, or None if not monitored."""
        return self._circuit_states.get(circuit_id)

    def get_mains_state(self, leg: str) -> MonitoredPointState | None:
        """Return tracking state for a mains leg, or None if not monitored."""
        return self._mains_states.get(leg)

    def set_circuit_override(self, circuit_id: str, overrides: dict[str, Any]) -> None:
        """Set per-circuit threshold overrides."""
        existing = self._circuit_overrides.get(circuit_id, {})
        existing.update(overrides)
        if self._is_redundant_override(existing):
            self._circuit_overrides.pop(circuit_id, None)
        else:
            self._circuit_overrides[circuit_id] = existing
        self._hass.async_create_task(self.async_save_overrides())

    def clear_circuit_override(self, circuit_id: str) -> None:
        """Remove per-circuit threshold overrides."""
        self._circuit_overrides.pop(circuit_id, None)
        self._circuit_states.pop(circuit_id, None)
        self._hass.async_create_task(self.async_save_overrides())

    def set_mains_override(self, leg: str, overrides: dict[str, Any]) -> None:
        """Set per-mains-leg threshold overrides."""
        existing = self._mains_overrides.get(leg, {})
        existing.update(overrides)
        if self._is_redundant_override(existing):
            self._mains_overrides.pop(leg, None)
        else:
            self._mains_overrides[leg] = existing
        self._hass.async_create_task(self.async_save_overrides())

    def _is_redundant_override(self, override: dict[str, Any]) -> bool:
        """Check if an override matches global defaults (and can be removed).

        An override is redundant if monitoring is enabled (or not set, defaulting
        to True) and all threshold values match the global settings.
        """
        if override.get("monitoring_enabled") is False:
            return False
        g = self.get_global_settings()
        threshold_keys = (
            CONTINUOUS_THRESHOLD_PCT,
            SPIKE_THRESHOLD_PCT,
            WINDOW_DURATION_M,
            COOLDOWN_DURATION_M,
        )
        for key in threshold_keys:
            if key in override and override[key] != g[key]:
                return False
        # All present keys match globals (or aren't set), and monitoring is enabled
        return True

    def clear_mains_override(self, leg: str) -> None:
        """Remove per-mains-leg threshold overrides."""
        self._mains_overrides.pop(leg, None)
        self._mains_states.pop(leg, None)
        self._hass.async_create_task(self.async_save_overrides())

    def get_global_settings(self) -> dict[str, Any]:
        """Get the effective global monitoring settings.

        Returns stored global settings if available, otherwise falls back
        to config entry options for backward compatibility during migration.
        """
        opts: Mapping[str, Any]
        if self._global_settings:
            opts = self._global_settings
        else:
            opts = self._entry.options
        return {
            CONTINUOUS_THRESHOLD_PCT: opts.get(
                CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT
            ),
            SPIKE_THRESHOLD_PCT: opts.get(SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT),
            WINDOW_DURATION_M: opts.get(WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M),
            COOLDOWN_DURATION_M: opts.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
            NOTIFY_TARGETS: opts.get(NOTIFY_TARGETS, "notify.notify"),
            ENABLE_PERSISTENT_NOTIFICATIONS: opts.get(ENABLE_PERSISTENT_NOTIFICATIONS, True),
            ENABLE_EVENT_BUS: opts.get(ENABLE_EVENT_BUS, True),
            NOTIFICATION_TITLE_TEMPLATE: opts.get(
                NOTIFICATION_TITLE_TEMPLATE, DEFAULT_NOTIFICATION_TITLE_TEMPLATE
            ),
            NOTIFICATION_MESSAGE_TEMPLATE: opts.get(
                NOTIFICATION_MESSAGE_TEMPLATE, DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE
            ),
            NOTIFICATION_PRIORITY: opts.get(NOTIFICATION_PRIORITY, DEFAULT_NOTIFICATION_PRIORITY),
        }

    def set_global_settings(self, settings: dict[str, Any]) -> None:
        """Update global monitoring settings in storage."""
        valid_keys = {
            CONTINUOUS_THRESHOLD_PCT,
            SPIKE_THRESHOLD_PCT,
            WINDOW_DURATION_M,
            COOLDOWN_DURATION_M,
            NOTIFY_TARGETS,
            ENABLE_PERSISTENT_NOTIFICATIONS,
            ENABLE_EVENT_BUS,
            NOTIFICATION_TITLE_TEMPLATE,
            NOTIFICATION_MESSAGE_TEMPLATE,
            NOTIFICATION_PRIORITY,
        }
        for key, value in settings.items():
            if key in valid_keys:
                self._global_settings[key] = value

        self._hass.async_create_task(self.async_save_overrides())

    def _resolve_circuit_entity_id(self, circuit_id: str) -> str:
        """Resolve circuit_id to the power sensor entity_id, or fall back to circuit_id."""
        snapshot = self._last_snapshot
        if snapshot is None:
            return circuit_id
        serial = snapshot.serial_number
        unique_id = build_circuit_unique_id(serial, circuit_id, "instantPowerW")
        entity_reg = er.async_get(self._hass)
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        return entity_id if entity_id is not None else circuit_id

    def resolve_entity_to_circuit_id(self, entity_id: str) -> str:
        """Resolve a power sensor entity_id to its internal circuit_id.

        Accepts either an entity_id (sensor.span_panel_kitchen_power) or
        a raw circuit_id (UUID) for backwards compatibility.
        """
        entity_reg = er.async_get(self._hass)
        entry = entity_reg.async_get(entity_id)
        if entry is not None and entry.unique_id:
            # unique_id format: span_{serial}_{circuit_id}_{suffix}
            parts = entry.unique_id.split("_")
            # Find the circuit_id — it's the UUID segment between serial and suffix
            # Serial is parts[1], suffix is last part(s). The circuit UUID is a 32-char hex.
            for part in parts:
                if len(part) == 32 and all(c in "0123456789abcdef" for c in part):
                    return part
        # Fall through: assume it's already a circuit_id
        return entity_id

    def resolve_entity_to_mains_leg(self, entity_id: str) -> str:
        """Resolve a current sensor entity_id to its internal mains leg name.

        Accepts either an entity_id (sensor.span_panel_upstream_l1_current)
        or a raw leg name (upstream_l1) for backwards compatibility.
        """
        # Check if it's already a known leg name
        if entity_id in _MAINS_CURRENT_ATTRS:
            return entity_id
        entity_reg = er.async_get(self._hass)
        entry = entity_reg.async_get(entity_id)
        if entry is not None and entry.unique_id:
            # unique_id format: span_{serial}_{leg}_current
            # e.g., span_sp3-242424-001_upstream_l1_current
            for leg in _MAINS_CURRENT_ATTRS:
                if f"_{leg}_current" in entry.unique_id:
                    return leg
        return entity_id

    def _resolve_mains_entity_id(self, leg: str) -> str:
        """Resolve mains leg to its current sensor entity_id, or fall back to leg name."""
        snapshot = self._last_snapshot
        if snapshot is None:
            return leg
        serial = snapshot.serial_number
        # The sensor key matches the leg name + "_current" (e.g., "upstream_l1_current")
        unique_id = build_panel_unique_id(serial, f"{leg}_current")
        entity_reg = er.async_get(self._hass)
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        return entity_id if entity_id is not None else leg

    def get_monitoring_status(self) -> dict[str, Any]:
        """Return current monitoring state for all tracked points."""
        snapshot = self._last_snapshot
        main_rating = (
            float(snapshot.main_breaker_rating_a)
            if snapshot and snapshot.main_breaker_rating_a
            else None
        )

        circuits: dict[str, dict[str, Any]] = {}
        # Include all circuits from the snapshot, not just those with active state.
        # Circuits with relays off may have no state yet but should still appear.
        all_circuit_ids = set(self._circuit_states.keys())
        if snapshot:
            all_circuit_ids |= {
                cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")
            }

        for cid in all_circuit_ids:
            state = self._circuit_states.get(cid)
            circuit = snapshot.circuits.get(cid) if snapshot else None
            rating = (
                float(circuit.breaker_rating_a) if circuit and circuit.breaker_rating_a else None
            )
            last_current = state.last_current_a if state else 0.0
            utilization = round(last_current / rating * 100, 1) if rating else None
            cont_pct, spike_pct, window_m, cooldown_m = self._resolve_circuit_thresholds(cid)
            entity_id = self._resolve_circuit_entity_id(cid)
            override = self._circuit_overrides.get(cid, {})
            has_override = bool(override)
            monitoring_enabled = override.get("monitoring_enabled", True)

            circuits[entity_id] = {
                "name": circuit.name if circuit else cid,
                "last_current_a": last_current,
                "breaker_rating_a": rating,
                "utilization_pct": utilization,
                "continuous_threshold_pct": cont_pct,
                "spike_threshold_pct": spike_pct,
                "window_duration_m": window_m,
                "cooldown_duration_m": cooldown_m,
                "has_override": has_override,
                "monitoring_enabled": monitoring_enabled,
                "over_threshold_since": state.over_threshold_since.isoformat()
                if state and state.over_threshold_since
                else None,
                "last_spike_alert": state.last_spike_alert.isoformat()
                if state and state.last_spike_alert
                else None,
                "last_continuous_alert": state.last_continuous_alert.isoformat()
                if state and state.last_continuous_alert
                else None,
            }

        # Present mains as a single entry using the higher of the two upstream legs.
        # The main breaker is a single 240V breaker; internally we still evaluate
        # per-leg, but the UI shows one combined "Mains Breaker" point.
        mains: dict[str, dict[str, Any]] = {}
        l1_state = self._mains_states.get("upstream_l1")
        l2_state = self._mains_states.get("upstream_l2")
        l1_current = l1_state.last_current_a if l1_state else 0.0
        l2_current = l2_state.last_current_a if l2_state else 0.0
        peak_current = max(l1_current, l2_current)
        utilization = round(peak_current / main_rating * 100, 1) if main_rating else None

        # Use upstream_l1 thresholds as the representative (they're the same unless overridden)
        cont_pct, spike_pct, window_m, cooldown_m = self._resolve_mains_thresholds("upstream_l1")
        override = self._mains_overrides.get("upstream_l1", {})
        has_override = bool(override)
        monitoring_enabled = override.get("monitoring_enabled", True)

        # Resolve to the current_power entity for the mains entry key
        mains_entity_id = self._resolve_mains_entity_id("upstream_l1")
        mains[mains_entity_id] = {
            "name": "Mains Breaker",
            "last_current_a": peak_current,
            "breaker_rating_a": main_rating,
            "utilization_pct": utilization,
            "continuous_threshold_pct": cont_pct,
            "spike_threshold_pct": spike_pct,
            "window_duration_m": window_m,
            "cooldown_duration_m": cooldown_m,
            "has_override": has_override,
            "monitoring_enabled": monitoring_enabled,
            "over_threshold_since": None,
            "last_spike_alert": None,
            "last_continuous_alert": None,
        }

        return {"circuits": circuits, "mains": mains}

    async def async_start(self) -> None:
        """Start the monitor — load persisted overrides."""
        await self.async_load_overrides()
        _LOGGER.info("Current monitor started")

    def async_stop(self) -> None:
        """Stop the monitor — clear all tracking state."""
        self._circuit_states.clear()
        self._mains_states.clear()
        _LOGGER.info("Current monitor stopped")

    async def async_save_disabled(self) -> None:
        """Mark monitoring as disabled in storage, preserving settings."""
        data = await self._store.async_load() or {}
        data["enabled"] = False
        await self._store.async_save(data)

    async def async_save_overrides(self) -> None:
        """Persist circuit overrides, mains overrides, and global settings to storage."""
        data: dict[str, Any] = {
            "enabled": True,
            "circuit_overrides": self._circuit_overrides,
            "mains_overrides": self._mains_overrides,
        }
        if self._global_settings:
            data["global"] = self._global_settings
        await self._store.async_save(data)

    async def async_load_overrides(self) -> None:
        """Load circuit overrides, mains overrides, and global settings from storage."""
        data = await self._store.async_load()
        if data is None:
            return
        self._circuit_overrides = data.get("circuit_overrides", {})
        self._mains_overrides = data.get("mains_overrides", {})
        self._global_settings = data.get("global", {})

    @staticmethod
    async def async_is_enabled(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Check if monitoring was previously enabled by reading storage."""
        store: Store = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
        data = await store.async_load()
        return bool(data and data.get("enabled", False))

    # --- Threshold resolution ---

    def _resolve_circuit_thresholds(self, circuit_id: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a circuit."""
        override = self._circuit_overrides.get(circuit_id, {})
        opts = self.get_global_settings()
        return (
            override.get(
                CONTINUOUS_THRESHOLD_PCT,
                opts.get(CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT),
            ),
            override.get(
                SPIKE_THRESHOLD_PCT,
                opts.get(SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT),
            ),
            override.get(
                WINDOW_DURATION_M,
                opts.get(WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M),
            ),
            override.get(
                COOLDOWN_DURATION_M,
                opts.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
            ),
        )

    def _resolve_mains_thresholds(self, leg: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a mains leg."""
        override = self._mains_overrides.get(leg, {})
        opts = self.get_global_settings()
        return (
            override.get(
                CONTINUOUS_THRESHOLD_PCT,
                opts.get(CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT),
            ),
            override.get(
                SPIKE_THRESHOLD_PCT,
                opts.get(SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT),
            ),
            override.get(
                WINDOW_DURATION_M,
                opts.get(WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M),
            ),
            override.get(
                COOLDOWN_DURATION_M,
                opts.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
            ),
        )

    def _is_circuit_monitoring_disabled(self, circuit_id: str) -> bool:
        """Check if monitoring is disabled for a specific circuit."""
        override = self._circuit_overrides.get(circuit_id, {})
        return override.get("monitoring_enabled") is False

    def _is_mains_monitoring_disabled(self, leg: str) -> bool:
        """Check if monitoring is disabled for a specific mains leg."""
        override = self._mains_overrides.get(leg, {})
        return override.get("monitoring_enabled") is False

    # --- Circuit evaluation ---

    def _evaluate_circuits(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all circuits in the snapshot."""
        for circuit_id, circuit in snapshot.circuits.items():
            if circuit.current_a is None or circuit.breaker_rating_a is None:
                continue
            if self._is_circuit_monitoring_disabled(circuit_id):
                continue

            state = self._circuit_states.setdefault(circuit_id, MonitoredPointState())
            current = abs(circuit.current_a)
            rating = circuit.breaker_rating_a
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = self._resolve_circuit_thresholds(circuit_id)

            self._check_spike(
                state,
                current,
                rating,
                spike_pct,
                cooldown_m,
                alert_name=circuit.name,
                alert_id=circuit_id,
                alert_source="circuit",
                snapshot=snapshot,
            )
            self._check_continuous(
                state,
                current,
                rating,
                cont_pct,
                window_m,
                cooldown_m,
                alert_name=circuit.name,
                alert_id=circuit_id,
                alert_source="circuit",
                snapshot=snapshot,
            )

    # --- Mains evaluation ---

    def _evaluate_mains(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all mains legs."""
        if snapshot.main_breaker_rating_a is None:
            return

        rating = float(snapshot.main_breaker_rating_a)

        for leg, attr in _MAINS_CURRENT_ATTRS.items():
            current_val = getattr(snapshot, attr, None)
            if current_val is None:
                continue
            if self._is_mains_monitoring_disabled(leg):
                continue

            state = self._mains_states.setdefault(leg, MonitoredPointState())
            current = abs(current_val)
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = self._resolve_mains_thresholds(leg)

            leg_label = leg.replace("_", " ").title()

            self._check_spike(
                state,
                current,
                rating,
                spike_pct,
                cooldown_m,
                alert_name=f"Mains {leg_label}",
                alert_id=leg,
                alert_source="mains",
                snapshot=snapshot,
            )
            self._check_continuous(
                state,
                current,
                rating,
                cont_pct,
                window_m,
                cooldown_m,
                alert_name=f"Mains {leg_label}",
                alert_id=leg,
                alert_source="mains",
                snapshot=snapshot,
            )

    # --- Threshold checks ---

    def _check_spike(
        self,
        state: MonitoredPointState,
        current: float,
        rating: float,
        threshold_pct: int,
        cooldown_m: int,
        *,
        alert_name: str,
        alert_id: str,
        alert_source: str,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Check for instantaneous spike condition."""
        limit = rating * threshold_pct / 100.0
        if current < limit:
            return

        now = datetime.now(UTC)
        if state.last_spike_alert is not None and now - state.last_spike_alert < timedelta(
            minutes=cooldown_m
        ):
            return

        state.last_spike_alert = now
        utilization = round(current / rating * 100, 1)

        self._dispatch_alert(
            alert_type="spike",
            alert_name=alert_name,
            alert_id=alert_id,
            alert_source=alert_source,
            current_a=current,
            breaker_rating_a=rating,
            threshold_pct=threshold_pct,
            utilization_pct=utilization,
            snapshot=snapshot,
        )

    def _check_continuous(
        self,
        state: MonitoredPointState,
        current: float,
        rating: float,
        threshold_pct: int,
        window_m: int,
        cooldown_m: int,
        *,
        alert_name: str,
        alert_id: str,
        alert_source: str,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Check for sustained continuous overload condition."""
        limit = rating * threshold_pct / 100.0
        now = datetime.now(UTC)

        if current < limit:
            state.over_threshold_since = None
            return

        if state.over_threshold_since is None:
            state.over_threshold_since = now

        elapsed = now - state.over_threshold_since
        if elapsed < timedelta(minutes=window_m):
            return

        if (
            state.last_continuous_alert is not None
            and now - state.last_continuous_alert < timedelta(minutes=cooldown_m)
        ):
            return

        state.last_continuous_alert = now
        utilization = round(current / rating * 100, 1)

        self._dispatch_alert(
            alert_type="continuous_overload",
            alert_name=alert_name,
            alert_id=alert_id,
            alert_source=alert_source,
            current_a=current,
            breaker_rating_a=rating,
            threshold_pct=threshold_pct,
            utilization_pct=utilization,
            snapshot=snapshot,
            window_duration_s=int(elapsed.total_seconds()),
            over_threshold_since=state.over_threshold_since.isoformat(),
        )

    # --- Notification dispatch ---

    def _dispatch_alert(
        self,
        *,
        alert_type: str,
        alert_name: str,
        alert_id: str,
        alert_source: str,
        current_a: float,
        breaker_rating_a: float,
        threshold_pct: int,
        utilization_pct: float,
        snapshot: SpanPanelSnapshot,
        window_duration_s: int | None = None,
        over_threshold_since: str | None = None,
    ) -> None:
        """Dispatch alert through all enabled notification channels."""
        event_data: dict[str, Any] = {
            "alert_source": alert_source,
            "alert_id": alert_id,
            "alert_name": alert_name,
            "alert_type": alert_type,
            "current_a": round(current_a, 1),
            "breaker_rating_a": breaker_rating_a,
            "threshold_pct": threshold_pct,
            "utilization_pct": utilization_pct,
            "panel_serial": snapshot.serial_number,
        }
        if window_duration_s is not None:
            event_data["window_duration_s"] = window_duration_s
        if over_threshold_since is not None:
            event_data["over_threshold_since"] = over_threshold_since

        opts = self.get_global_settings()

        if opts.get(ENABLE_EVENT_BUS, True):
            self._hass.bus.async_fire(EVENT_CURRENT_ALERT, event_data)

        title, message = self._format_notification(
            alert_type=alert_type,
            alert_name=alert_name,
            alert_id=alert_id,
            current_a=current_a,
            breaker_rating_a=breaker_rating_a,
            threshold_pct=threshold_pct,
            utilization_pct=utilization_pct,
            window_duration_s=window_duration_s,
            title_template=opts.get(
                NOTIFICATION_TITLE_TEMPLATE, DEFAULT_NOTIFICATION_TITLE_TEMPLATE
            ),
            message_template=opts.get(
                NOTIFICATION_MESSAGE_TEMPLATE, DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE
            ),
        )

        if self._hass.state is not CoreState.running:
            _LOGGER.debug(
                "Skipping alert notifications during startup (state=%s)",
                self._hass.state,
            )
        else:
            raw_targets = opts.get(NOTIFY_TARGETS, "notify.notify")
            if isinstance(raw_targets, str):
                notify_targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
            else:
                notify_targets = raw_targets

            priority = opts.get(NOTIFICATION_PRIORITY, DEFAULT_NOTIFICATION_PRIORITY)
            push_data = self._build_push_data(priority)

            for target in notify_targets:
                self._hass.async_create_task(
                    self._dispatch_to_target(target, title, message, push_data)
                )

            if opts.get(ENABLE_PERSISTENT_NOTIFICATIONS, True):
                notification_id = f"span_panel_{alert_source}_{alert_id}_{alert_type}"
                self._hass.async_create_task(
                    self._hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": title,
                            "message": message,
                            "notification_id": notification_id,
                        },
                    )
                )

        _LOGGER.warning(
            "Current alert: %s — %s at %.1fA (%.1f%% of %.0fA rating)",
            alert_name,
            alert_type,
            current_a,
            utilization_pct,
            breaker_rating_a,
        )

    @staticmethod
    def _format_notification(
        *,
        alert_type: str,
        alert_name: str,
        alert_id: str,
        current_a: float,
        breaker_rating_a: float,
        threshold_pct: int,
        utilization_pct: float,
        window_duration_s: int | None,
        title_template: str,
        message_template: str,
    ) -> tuple[str, str]:
        """Format notification title and message using templates.

        Available placeholders:
            {name}            - Circuit/mains friendly name
            {entity_id}       - Entity ID (e.g. sensor.kitchen_current)
            {alert_type}      - "spike" or "continuous"
            {current_a}       - Current draw in amps (e.g. 18.3)
            {breaker_rating_a}- Breaker rating in amps (e.g. 20)
            {threshold_pct}   - Configured threshold percentage
            {utilization_pct} - Actual utilization percentage
            {window_m}        - Window duration in minutes (continuous only)
        """
        window_m = (window_duration_s or 0) // 60
        template_vars = {
            "name": alert_name,
            "entity_id": alert_id,
            "alert_type": alert_type,
            "current_a": f"{current_a:.1f}",
            "breaker_rating_a": f"{breaker_rating_a:.0f}",
            "threshold_pct": str(threshold_pct),
            "utilization_pct": str(utilization_pct),
            "window_m": str(window_m),
        }
        try:
            title = title_template.format_map(template_vars)
        except (KeyError, ValueError):
            title = f"SPAN: {alert_name} {alert_type}"
        try:
            message = message_template.format_map(template_vars)
        except (KeyError, ValueError):
            message = (
                f"{alert_name} at {current_a:.1f}A "
                f"({utilization_pct}% of {breaker_rating_a:.0f}A rating)"
            )
        return title, message

    @staticmethod
    def _build_push_data(priority: str) -> dict[str, Any]:
        """Build platform-specific push data for the given priority level.

        Returns a dict suitable for the ``data`` parameter of a notify service
        call.  Includes keys for both iOS (``push.interruption-level``) and
        Android (``priority``, ``channel``) so the correct one is picked up
        regardless of the receiving device platform.
        """
        if priority == "default":
            return {}

        android_priority_map = {
            "passive": "low",
            "active": "default",
            "time-sensitive": "high",
            "critical": "high",
        }
        data: dict[str, Any] = {
            "push": {"interruption-level": priority},
            "priority": android_priority_map.get(priority, "default"),
        }
        if priority == "critical":
            data["push"]["sound"] = {
                "name": "default",
                "critical": 1,
                "volume": 1.0,
            }
            data["channel"] = "alarm_stream"
        elif priority == "time-sensitive":
            data["channel"] = "alarm_stream_other"
        return data

    async def _dispatch_to_target(
        self,
        target: str,
        title: str,
        message: str,
        push_data: dict[str, Any],
    ) -> None:
        """Send a notification to a single target.

        Handles both entity-based targets (``notify.mobile_app_*``) which use
        ``notify.send_message`` with an ``entity_id``, and legacy service-based
        targets (``notify.notify``) which call the service directly.
        """
        service_data: dict[str, Any] = {"title": title, "message": message}
        if push_data:
            service_data["data"] = push_data

        is_entity = target.startswith("notify.") and self._hass.states.get(target)

        if is_entity:
            service_data["entity_id"] = target
            await self._hass.services.async_call("notify", "send_message", service_data)
        else:
            domain = target.split(".")[0] if "." in target else "notify"
            service = target.split(".")[1] if "." in target else "notify"
            await self._hass.services.async_call(domain, service, service_data)
