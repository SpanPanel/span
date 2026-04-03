"""Current monitoring for SPAN Panel circuits and mains legs.

Detects spike (instantaneous) and continuous overload conditions by comparing
current readings against breaker ratings with configurable thresholds.
Dispatches notifications via event bus, notify services, and persistent
notifications.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from span_panel_api import SpanPanelSnapshot

from .alert_dispatcher import dispatch_alert, format_notification
from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE,
    DEFAULT_NOTIFICATION_PRIORITY,
    DEFAULT_NOTIFICATION_TITLE_TEMPLATE,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    DOMAIN,
)
from .helpers import build_circuit_unique_id, build_panel_unique_id
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    NOTIFICATION_MESSAGE_TEMPLATE,
    NOTIFICATION_PRIORITY,
    NOTIFICATION_TITLE_TEMPLATE,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from .threshold_evaluator import (
    check_continuous,
    check_spike,
    is_monitoring_disabled,
    resolve_thresholds,
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
            NOTIFY_TARGETS: opts.get(NOTIFY_TARGETS, ""),
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
            NOTIFICATION_TITLE_TEMPLATE,
            NOTIFICATION_MESSAGE_TEMPLATE,
            NOTIFICATION_PRIORITY,
        }
        for key, value in settings.items():
            if key in valid_keys:
                self._global_settings[key] = value

        self._hass.async_create_task(self.async_save_overrides())

    def _resolve_circuit_entity_id(self, circuit_id: str) -> str:
        """Resolve circuit_id to a sensor entity_id for monitoring status keys.

        Tries the current sensor first, then falls back to power, matching
        the frontend lookup order (circuit.entities.current ?? .power).
        """
        snapshot = self._last_snapshot
        if snapshot is None:
            return circuit_id
        serial = snapshot.serial_number
        entity_reg = er.async_get(self._hass)
        # Try current sensor first (matches frontend preference)
        current_uid = build_circuit_unique_id(serial, circuit_id, "current")
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, current_uid)
        if entity_id is not None:
            return entity_id
        # Fall back to power sensor
        power_uid = build_circuit_unique_id(serial, circuit_id, "instantPowerW")
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, power_uid)
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
            cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
                self._circuit_overrides.get(cid, {}), self.get_global_settings()
            )
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
        cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
            self._mains_overrides.get("upstream_l1", {}), self.get_global_settings()
        )
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

    # --- Threshold resolution (delegated to threshold_evaluator) ---

    def _resolve_circuit_thresholds(self, circuit_id: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a circuit."""
        return resolve_thresholds(
            self._circuit_overrides.get(circuit_id, {}), self.get_global_settings()
        )

    def _resolve_mains_thresholds(self, leg: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a mains leg."""
        return resolve_thresholds(self._mains_overrides.get(leg, {}), self.get_global_settings())

    # --- Circuit evaluation ---

    def _evaluate_circuits(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all circuits in the snapshot."""
        for circuit_id, circuit in snapshot.circuits.items():
            if circuit.current_a is None or circuit.breaker_rating_a is None:
                continue
            if is_monitoring_disabled(self._circuit_overrides.get(circuit_id, {})):
                continue

            state = self._circuit_states.setdefault(circuit_id, MonitoredPointState())
            current = abs(circuit.current_a)
            rating = circuit.breaker_rating_a
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
                self._circuit_overrides.get(circuit_id, {}), self.get_global_settings()
            )

            alert = check_spike(state, current, rating, spike_pct, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=circuit.name or circuit_id,
                    alert_id=circuit_id,
                    alert_source="circuit",
                    current_a=alert.current_a,
                    breaker_rating_a=alert.breaker_rating_a,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                )

            alert = check_continuous(state, current, rating, cont_pct, window_m, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=circuit.name or circuit_id,
                    alert_id=circuit_id,
                    alert_source="circuit",
                    current_a=alert.current_a,
                    breaker_rating_a=alert.breaker_rating_a,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                    window_duration_s=alert.window_duration_s,
                    over_threshold_since=alert.over_threshold_since,
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
            if is_monitoring_disabled(self._mains_overrides.get(leg, {})):
                continue

            state = self._mains_states.setdefault(leg, MonitoredPointState())
            current = abs(current_val)
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
                self._mains_overrides.get(leg, {}), self.get_global_settings()
            )

            leg_label = leg.replace("_", " ").title()

            alert = check_spike(state, current, rating, spike_pct, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=f"Mains {leg_label}",
                    alert_id=leg,
                    alert_source="mains",
                    current_a=alert.current_a,
                    breaker_rating_a=alert.breaker_rating_a,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                )

            alert = check_continuous(state, current, rating, cont_pct, window_m, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=f"Mains {leg_label}",
                    alert_id=leg,
                    alert_source="mains",
                    current_a=alert.current_a,
                    breaker_rating_a=alert.breaker_rating_a,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                    window_duration_s=alert.window_duration_s,
                    over_threshold_since=alert.over_threshold_since,
                )

    # Backward-compatible static alias kept for existing callers/tests.
    _format_notification = staticmethod(format_notification)  # type: ignore[assignment]
