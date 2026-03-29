"""Current monitoring for SPAN Panel circuits and mains legs.

Detects spike (instantaneous) and continuous overload conditions by comparing
current readings against breaker ratings with configurable thresholds.
Dispatches notifications via event bus, notify services, and persistent
notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from span_panel_api import SpanPanelSnapshot

from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    EVENT_CURRENT_ALERT,
)
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    ENABLE_EVENT_BUS,
    ENABLE_PERSISTENT_NOTIFICATIONS,
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
        self._store: Store = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )

    # --- Public API ---

    def process_snapshot(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all circuits and mains legs."""
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
        self._mains_overrides[leg] = existing
        self._hass.async_create_task(self.async_save_overrides())

    def clear_mains_override(self, leg: str) -> None:
        """Remove per-mains-leg threshold overrides."""
        self._mains_overrides.pop(leg, None)
        self._mains_states.pop(leg, None)
        self._hass.async_create_task(self.async_save_overrides())

    def get_monitoring_status(self) -> dict[str, Any]:
        """Return current monitoring state for all tracked points."""
        return {
            "circuits": {
                cid: {
                    "last_current_a": s.last_current_a,
                    "over_threshold_since": s.over_threshold_since.isoformat()
                    if s.over_threshold_since
                    else None,
                    "last_spike_alert": s.last_spike_alert.isoformat()
                    if s.last_spike_alert
                    else None,
                    "last_continuous_alert": s.last_continuous_alert.isoformat()
                    if s.last_continuous_alert
                    else None,
                }
                for cid, s in self._circuit_states.items()
            },
            "mains": {
                leg: {
                    "last_current_a": s.last_current_a,
                    "over_threshold_since": s.over_threshold_since.isoformat()
                    if s.over_threshold_since
                    else None,
                    "last_spike_alert": s.last_spike_alert.isoformat()
                    if s.last_spike_alert
                    else None,
                    "last_continuous_alert": s.last_continuous_alert.isoformat()
                    if s.last_continuous_alert
                    else None,
                }
                for leg, s in self._mains_states.items()
            },
        }

    async def async_start(self) -> None:
        """Start the monitor — load persisted overrides."""
        await self.async_load_overrides()
        _LOGGER.info("Current monitor started")

    def async_stop(self) -> None:
        """Stop the monitor — clear all tracking state."""
        self._circuit_states.clear()
        self._mains_states.clear()
        _LOGGER.info("Current monitor stopped")

    async def async_save_overrides(self) -> None:
        """Persist circuit and mains overrides to storage."""
        await self._store.async_save(
            {
                "circuit_overrides": self._circuit_overrides,
                "mains_overrides": self._mains_overrides,
            }
        )

    async def async_load_overrides(self) -> None:
        """Load circuit and mains overrides from storage."""
        data = await self._store.async_load()
        if data is None:
            return
        self._circuit_overrides = data.get("circuit_overrides", {})
        self._mains_overrides = data.get("mains_overrides", {})

    # --- Threshold resolution ---

    def _resolve_circuit_thresholds(self, circuit_id: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a circuit."""
        override = self._circuit_overrides.get(circuit_id, {})
        opts = self._entry.options
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
            opts.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
        )

    def _resolve_mains_thresholds(self, leg: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a mains leg."""
        override = self._mains_overrides.get(leg, {})
        opts = self._entry.options
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
            opts.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
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

        opts = self._entry.options

        if opts.get(ENABLE_EVENT_BUS, True):
            self._hass.bus.async_fire(EVENT_CURRENT_ALERT, event_data)

        title, message = self._format_notification(
            alert_type,
            alert_name,
            current_a,
            breaker_rating_a,
            threshold_pct,
            utilization_pct,
            window_duration_s,
        )

        raw_targets = opts.get(NOTIFY_TARGETS, "notify.notify")
        if isinstance(raw_targets, str):
            notify_targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
        else:
            notify_targets = raw_targets
        for target in notify_targets:
            self._hass.async_create_task(
                self._hass.services.async_call(
                    target.split(".")[0] if "." in target else "notify",
                    target.split(".")[1] if "." in target else "notify",
                    {"title": title, "message": message},
                )
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
        alert_type: str,
        alert_name: str,
        current_a: float,
        breaker_rating_a: float,
        threshold_pct: int,
        utilization_pct: float,
        window_duration_s: int | None,
    ) -> tuple[str, str]:
        """Format notification title and message."""
        if alert_type == "spike":
            title = f"SPAN: {alert_name} spike"
            message = (
                f"{alert_name} spike at {current_a:.1f}A "
                f"({utilization_pct}% of {breaker_rating_a:.0f}A rating)"
            )
        else:
            window_m = (window_duration_s or 0) // 60
            title = f"SPAN: {alert_name} overload"
            message = (
                f"{alert_name} drawing {current_a:.1f}A "
                f"({utilization_pct}% of {breaker_rating_a:.0f}A rating) "
                f"— continuous threshold of {threshold_pct}% "
                f"exceeded over {window_m} min"
            )
        return title, message
