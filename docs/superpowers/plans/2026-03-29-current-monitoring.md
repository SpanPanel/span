# Current Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a current monitoring system that detects spike and continuous overload conditions on circuits and mains legs, with configurable thresholds and
multi-channel notifications.

**Architecture:** A `CurrentMonitor` class in `current_monitor.py` is instantiated by `async_setup_entry` when enabled, receives snapshots from the coordinator
via a single delegation call, and independently evaluates thresholds, tracks overload windows, and dispatches notifications. Per-circuit/mains overrides are
persisted via `hass.helpers.storage.Store`. Services provide runtime configuration.

**Tech Stack:** Python, Home Assistant core APIs (storage, services, event bus, persistent notifications, notify platform), voluptuous for schema validation.

**Spec:** `docs/superpowers/specs/2026-03-29-current-monitoring-design.md`

---

## File Map

| File                                                  | Action | Responsibility                                                                                |
| ----------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------- |
| `custom_components/span_panel/const.py`               | Modify | New constants for monitoring config keys, defaults, event type                                |
| `custom_components/span_panel/options.py`             | Modify | New option key constants                                                                      |
| `custom_components/span_panel/current_monitor.py`     | Create | `CurrentMonitor` class — threshold evaluation, state tracking, notification dispatch, storage |
| `custom_components/span_panel/config_flow_options.py` | Modify | Add monitoring toggle + global threshold fields to options schema                             |
| `custom_components/span_panel/strings.json`           | Modify | Translation strings for new options and services                                              |
| `custom_components/span_panel/services.yaml`          | Modify | Service definitions for threshold management                                                  |
| `custom_components/span_panel/__init__.py`            | Modify | Instantiate/teardown monitor, register monitoring services                                    |
| `custom_components/span_panel/coordinator.py`         | Modify | Single line to delegate snapshot to monitor                                                   |
| `tests/test_current_monitor.py`                       | Create | Unit tests for `CurrentMonitor`                                                               |
| `tests/test_current_monitor_services.py`              | Create | Integration tests for monitoring services                                                     |
| `tests/test_current_monitor_options.py`               | Create | Tests for options flow additions                                                              |

---

## Task 1: Add Constants and Option Keys

**Files:**

- Modify: `custom_components/span_panel/const.py:58` (after existing feature toggles)
- Modify: `custom_components/span_panel/options.py:7` (after existing option keys)

- [ ] **Step 1: Add monitoring constants to `const.py`**

Add after the `ENABLE_UNMAPPED_CIRCUIT_SENSORS` line (line 61):

```python
# Current monitoring configuration
ENABLE_CURRENT_MONITORING = "enable_current_monitoring"
DEFAULT_CONTINUOUS_THRESHOLD_PCT = 80
DEFAULT_SPIKE_THRESHOLD_PCT = 100
DEFAULT_WINDOW_DURATION_M = 15
DEFAULT_COOLDOWN_DURATION_M = 15
EVENT_CURRENT_ALERT = "span_panel_current_alert"

# Mains leg identifiers
MAINS_LEGS: Final[tuple[str, ...]] = (
    "upstream_l1",
    "upstream_l2",
    "downstream_l1",
    "downstream_l2",
)
```

- [ ] **Step 2: Add option keys to `options.py`**

Add after `SNAPSHOT_UPDATE_INTERVAL` (line 7):

```python
CONTINUOUS_THRESHOLD_PCT = "continuous_threshold_pct"
SPIKE_THRESHOLD_PCT = "spike_threshold_pct"
WINDOW_DURATION_M = "window_duration_m"
COOLDOWN_DURATION_M = "cooldown_duration_m"
NOTIFY_TARGETS = "notify_targets"
ENABLE_PERSISTENT_NOTIFICATIONS = "enable_persistent_notifications"
ENABLE_EVENT_BUS = "enable_event_bus"
```

- [ ] **Step 3: Commit**

```bash
git add custom_components/span_panel/const.py custom_components/span_panel/options.py
git commit -m "feat: add current monitoring constants and option keys"
```

---

## Task 2: CurrentMonitor Core — State Tracking and Threshold Evaluation

**Files:**

- Create: `custom_components/span_panel/current_monitor.py`
- Create: `tests/test_current_monitor.py`

- [ ] **Step 1: Write failing tests for monitored point state tracking**

Create `tests/test_current_monitor.py`:

```python
"""Tests for the CurrentMonitor class."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.span_panel.const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    ENABLE_CURRENT_MONITORING,
    EVENT_CURRENT_ALERT,
    MAINS_LEGS,
)
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    ENABLE_EVENT_BUS,
    ENABLE_PERSISTENT_NOTIFICATIONS,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from tests.factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory


def _make_options(**overrides):
    """Create options dict with monitoring enabled and defaults."""
    opts = {
        ENABLE_CURRENT_MONITORING: True,
        CONTINUOUS_THRESHOLD_PCT: DEFAULT_CONTINUOUS_THRESHOLD_PCT,
        SPIKE_THRESHOLD_PCT: DEFAULT_SPIKE_THRESHOLD_PCT,
        WINDOW_DURATION_M: DEFAULT_WINDOW_DURATION_M,
        COOLDOWN_DURATION_M: DEFAULT_COOLDOWN_DURATION_M,
        NOTIFY_TARGETS: ["notify.notify"],
        ENABLE_PERSISTENT_NOTIFICATIONS: True,
        ENABLE_EVENT_BUS: True,
    }
    opts.update(overrides)
    return opts


def _make_monitor(hass, options=None, entry_id="test_entry"):
    """Create a CurrentMonitor with mocked hass and entry."""
    if options is None:
        options = _make_options()
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.options = options
    return CurrentMonitor(hass, entry)


def _make_hass():
    """Create a minimal mock hass object."""
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro)
    return hass


class TestSpikeDetection:
    """Tests for instantaneous spike threshold detection."""

    def test_spike_fires_when_current_meets_threshold(self):
        """A reading at exactly the breaker rating triggers a spike."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1",
            name="Kitchen",
            current_a=20.0,
            breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit},
            main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1").last_spike_alert is not None

    def test_spike_does_not_fire_below_threshold(self):
        """A reading below the threshold does not trigger a spike."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1",
            name="Kitchen",
            current_a=19.9,
            breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit},
            main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1").last_spike_alert is None

    def test_spike_uses_absolute_value_for_pv(self):
        """Negative current (PV backfeed) is checked by absolute value."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="15",
            name="Solar",
            current_a=-30.0,
            breaker_rating_a=30.0,
            device_type="pv",
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"15": circuit},
            main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("15").last_spike_alert is not None

    def test_spike_skips_circuit_with_none_current(self):
        """Circuits without current_a (non-V2) are skipped."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1",
            name="Kitchen",
            current_a=None,
            breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit},
            main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1") is None

    def test_spike_skips_circuit_with_none_rating(self):
        """Circuits without breaker_rating_a are skipped."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1",
            name="Kitchen",
            current_a=20.0,
            breaker_rating_a=None,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit},
            main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1") is None


class TestContinuousOverloadDetection:
    """Tests for sustained overload window tracking."""

    def test_continuous_window_starts_when_over_threshold(self):
        """Over-threshold reading starts the window timer."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{CONTINUOUS_THRESHOLD_PCT: 80}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1",
            name="Kitchen",
            current_a=16.1,  # 80.5% of 20A
            breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit},
            main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        state = monitor.get_circuit_state("1")
        assert state.over_threshold_since is not None

    def test_continuous_window_resets_when_below_threshold(self):
        """Dropping below threshold resets the window."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{CONTINUOUS_THRESHOLD_PCT: 80}
        ))
        # First reading: over threshold
        circuit_over = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=17.0, breaker_rating_a=20.0,
        )
        snapshot_over = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit_over}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot_over)
        assert monitor.get_circuit_state("1").over_threshold_since is not None

        # Second reading: below threshold
        circuit_under = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=15.0, breaker_rating_a=20.0,
        )
        snapshot_under = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit_under}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot_under)
        assert monitor.get_circuit_state("1").over_threshold_since is None

    def test_continuous_alert_fires_after_window_duration(self):
        """Alert fires when over threshold for the full window duration."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{CONTINUOUS_THRESHOLD_PCT: 80, WINDOW_DURATION_M: 15}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=17.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )

        # First snapshot sets the window start
        monitor.process_snapshot(snapshot)
        state = monitor.get_circuit_state("1")
        assert state.last_continuous_alert is None

        # Simulate time passing beyond window duration
        state.over_threshold_since = datetime.now(UTC) - timedelta(minutes=16)
        monitor.process_snapshot(snapshot)
        state = monitor.get_circuit_state("1")
        assert state.last_continuous_alert is not None

    def test_continuous_alert_does_not_fire_before_window(self):
        """Alert does not fire before the window duration elapses."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{CONTINUOUS_THRESHOLD_PCT: 80, WINDOW_DURATION_M: 15}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=17.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        # Immediately process again — window just started
        monitor.process_snapshot(snapshot)
        state = monitor.get_circuit_state("1")
        assert state.last_continuous_alert is None


class TestCooldown:
    """Tests for alert cooldown behavior."""

    def test_spike_suppressed_during_cooldown(self):
        """Second spike within cooldown is suppressed."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100, COOLDOWN_DURATION_M: 15}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )

        monitor.process_snapshot(snapshot)
        first_alert_time = monitor.get_circuit_state("1").last_spike_alert

        # Reset the hass.bus mock to track new calls
        hass.bus.async_fire.reset_mock()

        # Process again immediately — should be suppressed
        monitor.process_snapshot(snapshot)
        hass.bus.async_fire.assert_not_called()
        # Alert timestamp unchanged
        assert monitor.get_circuit_state("1").last_spike_alert == first_alert_time

    def test_spike_fires_again_after_cooldown(self):
        """Spike fires again after cooldown elapses."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100, COOLDOWN_DURATION_M: 15}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )

        monitor.process_snapshot(snapshot)
        # Push the alert timestamp back beyond cooldown
        state = monitor.get_circuit_state("1")
        state.last_spike_alert = datetime.now(UTC) - timedelta(minutes=16)

        hass.bus.async_fire.reset_mock()
        monitor.process_snapshot(snapshot)
        # Should have fired again
        assert monitor.get_circuit_state("1").last_spike_alert > state.last_spike_alert


class TestMainsMonitoring:
    """Tests for panel mains leg monitoring."""

    def test_mains_spike_detected_on_upstream_l1(self):
        """Upstream L1 current exceeding main breaker rating fires spike."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=200,
            upstream_l1_current_a=200.0,
            upstream_l2_current_a=100.0,
        )
        monitor.process_snapshot(snapshot)
        state = monitor.get_mains_state("upstream_l1")
        assert state is not None
        assert state.last_spike_alert is not None

    def test_mains_skipped_when_no_breaker_rating(self):
        """Mains monitoring skipped when main_breaker_rating_a is None."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=None,
            upstream_l1_current_a=200.0,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_mains_state("upstream_l1") is None

    def test_mains_skipped_when_leg_current_is_none(self):
        """Individual mains leg skipped when its current reading is None."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=200,
            upstream_l1_current_a=None,
            upstream_l2_current_a=100.0,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_mains_state("upstream_l1") is None
        assert monitor.get_mains_state("upstream_l2") is not None

    def test_mains_continuous_overload_on_single_leg(self):
        """Continuous overload fires on one leg while other stays normal."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{CONTINUOUS_THRESHOLD_PCT: 80, WINDOW_DURATION_M: 15}
        ))
        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=200,
            upstream_l1_current_a=170.0,  # 85% — over threshold
            upstream_l2_current_a=100.0,  # 50% — under threshold
        )
        monitor.process_snapshot(snapshot)

        l1_state = monitor.get_mains_state("upstream_l1")
        l2_state = monitor.get_mains_state("upstream_l2")
        assert l1_state.over_threshold_since is not None
        assert l2_state.over_threshold_since is None


class TestPerCircuitOverrides:
    """Tests for per-circuit threshold overrides."""

    def test_circuit_override_takes_precedence(self):
        """Per-circuit override threshold used instead of global."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        # Override circuit 1 to spike at 90%
        monitor.set_circuit_override("1", {SPIKE_THRESHOLD_PCT: 90})

        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=18.0,  # 90% of 20A — hits override threshold
            breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1").last_spike_alert is not None

    def test_circuit_override_disabled_skips_monitoring(self):
        """Per-circuit monitoring_enabled=False skips that circuit."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        monitor.set_circuit_override("1", {"monitoring_enabled": False})

        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Well Pump",
            current_a=25.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1") is None

    def test_clear_override_reverts_to_global(self):
        """Clearing an override reverts to global defaults."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        monitor.set_circuit_override("1", {SPIKE_THRESHOLD_PCT: 50})
        monitor.clear_circuit_override("1")

        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=19.0,  # 95% — under global 100% threshold
            breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_circuit_state("1").last_spike_alert is None


class TestPerMainsOverrides:
    """Tests for per-mains-leg threshold overrides."""

    def test_mains_override_takes_precedence(self):
        """Per-leg override threshold used instead of global."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        monitor.set_mains_override("upstream_l1", {SPIKE_THRESHOLD_PCT: 90})

        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=200,
            upstream_l1_current_a=180.0,  # 90% — hits override threshold
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_mains_state("upstream_l1").last_spike_alert is not None

    def test_mains_override_disabled_skips_leg(self):
        """Per-leg monitoring_enabled=False skips that leg."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        monitor.set_mains_override("upstream_l1", {"monitoring_enabled": False})

        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=200,
            upstream_l1_current_a=250.0,
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_mains_state("upstream_l1") is None

    def test_clear_mains_override_reverts_to_global(self):
        """Clearing a mains override reverts to global defaults."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{SPIKE_THRESHOLD_PCT: 100}
        ))
        monitor.set_mains_override("upstream_l1", {SPIKE_THRESHOLD_PCT: 50})
        monitor.clear_mains_override("upstream_l1")

        snapshot = SpanPanelSnapshotFactory.create(
            main_breaker_rating_a=200,
            upstream_l1_current_a=190.0,  # 95% — under global 100%
        )
        monitor.process_snapshot(snapshot)
        assert monitor.get_mains_state("upstream_l1").last_spike_alert is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor.py -v` Expected: FAIL —
`ModuleNotFoundError: No module named 'custom_components.span_panel.current_monitor'`

- [ ] **Step 3: Implement `CurrentMonitor` core**

Create `custom_components/span_panel/current_monitor.py`:

```python
"""Current monitoring for SPAN Panel circuits and mains legs.

Detects spike (instantaneous) and continuous overload conditions by comparing
current readings against breaker ratings with configurable thresholds.
Dispatches notifications via event bus, notify services, and persistent
notifications.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from span_panel_api import SpanPanelSnapshot

from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    DOMAIN,
    EVENT_CURRENT_ALERT,
    MAINS_LEGS,
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
        self._hass = hass
        self._entry = entry
        self._circuit_states: dict[str, MonitoredPointState] = {}
        self._mains_states: dict[str, MonitoredPointState] = {}
        self._circuit_overrides: dict[str, dict[str, Any]] = {}
        self._mains_overrides: dict[str, dict[str, Any]] = {}

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

    def set_circuit_override(
        self, circuit_id: str, overrides: dict[str, Any]
    ) -> None:
        """Set per-circuit threshold overrides."""
        existing = self._circuit_overrides.get(circuit_id, {})
        existing.update(overrides)
        self._circuit_overrides[circuit_id] = existing

    def clear_circuit_override(self, circuit_id: str) -> None:
        """Remove per-circuit threshold overrides."""
        self._circuit_overrides.pop(circuit_id, None)
        self._circuit_states.pop(circuit_id, None)

    def set_mains_override(self, leg: str, overrides: dict[str, Any]) -> None:
        """Set per-mains-leg threshold overrides."""
        existing = self._mains_overrides.get(leg, {})
        existing.update(overrides)
        self._mains_overrides[leg] = existing

    def clear_mains_override(self, leg: str) -> None:
        """Remove per-mains-leg threshold overrides."""
        self._mains_overrides.pop(leg, None)
        self._mains_states.pop(leg, None)

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

    # --- Threshold resolution ---

    def _resolve_circuit_thresholds(
        self, circuit_id: str
    ) -> tuple[int, int, int, int]:
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

    def _resolve_mains_thresholds(
        self, leg: str
    ) -> tuple[int, int, int, int]:
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

            state = self._circuit_states.setdefault(
                circuit_id, MonitoredPointState()
            )
            current = abs(circuit.current_a)
            rating = circuit.breaker_rating_a
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = (
                self._resolve_circuit_thresholds(circuit_id)
            )

            self._check_spike(
                state, current, rating, spike_pct, cooldown_m,
                alert_name=circuit.name,
                alert_id=circuit_id,
                alert_source="circuit",
                snapshot=snapshot,
            )
            self._check_continuous(
                state, current, rating, cont_pct, window_m, cooldown_m,
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

            cont_pct, spike_pct, window_m, cooldown_m = (
                self._resolve_mains_thresholds(leg)
            )

            leg_label = leg.replace("_", " ").title()

            self._check_spike(
                state, current, rating, spike_pct, cooldown_m,
                alert_name=f"Mains {leg_label}",
                alert_id=leg,
                alert_source="mains",
                snapshot=snapshot,
            )
            self._check_continuous(
                state, current, rating, cont_pct, window_m, cooldown_m,
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
        if (
            state.last_spike_alert is not None
            and now - state.last_spike_alert < timedelta(minutes=cooldown_m)
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
            alert_type, alert_name, current_a, breaker_rating_a,
            threshold_pct, utilization_pct, window_duration_s,
        )

        notify_targets: list[str] = opts.get(NOTIFY_TARGETS, ["notify.notify"])
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor.py -v` Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/span_panel/current_monitor.py tests/test_current_monitor.py
git commit -m "feat: add CurrentMonitor core with threshold evaluation and notifications"
```

---

## Task 3: Storage Persistence for Per-Circuit and Per-Mains Overrides

**Files:**

- Modify: `custom_components/span_panel/current_monitor.py`
- Add to: `tests/test_current_monitor.py`

- [ ] **Step 1: Write failing tests for storage persistence**

Add to `tests/test_current_monitor.py`:

```python
class TestStoragePersistence:
    """Tests for persisting overrides to HA storage."""

    @pytest.mark.asyncio
    async def test_save_and_load_circuit_overrides(self):
        """Circuit overrides survive save/load cycle."""
        hass = _make_hass()
        store_data = {}

        async def mock_save(data):
            store_data["saved"] = data

        async def mock_load():
            return store_data.get("saved")

        monitor = _make_monitor(hass)
        monitor._store = MagicMock()
        monitor._store.async_save = AsyncMock(side_effect=mock_save)
        monitor._store.async_load = AsyncMock(side_effect=mock_load)

        monitor.set_circuit_override("1", {SPIKE_THRESHOLD_PCT: 90})
        monitor.set_mains_override("upstream_l1", {SPIKE_THRESHOLD_PCT: 85})
        await monitor.async_save_overrides()

        # Create a new monitor and load
        monitor2 = _make_monitor(hass)
        monitor2._store = MagicMock()
        monitor2._store.async_load = AsyncMock(side_effect=mock_load)
        await monitor2.async_load_overrides()

        assert monitor2._circuit_overrides["1"][SPIKE_THRESHOLD_PCT] == 90
        assert monitor2._mains_overrides["upstream_l1"][SPIKE_THRESHOLD_PCT] == 85

    @pytest.mark.asyncio
    async def test_load_handles_no_existing_data(self):
        """Loading with no stored data leaves overrides empty."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        monitor._store = MagicMock()
        monitor._store.async_load = AsyncMock(return_value=None)
        await monitor.async_load_overrides()
        assert monitor._circuit_overrides == {}
        assert monitor._mains_overrides == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor.py::TestStoragePersistence -v` Expected: FAIL —
`AttributeError: 'CurrentMonitor' object has no attribute '_store'`

- [ ] **Step 3: Add storage methods to `CurrentMonitor`**

Add these imports at the top of `current_monitor.py`:

```python
from homeassistant.helpers.storage import Store
```

Add a constant after `_MAINS_CURRENT_ATTRS`:

```python
_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "span_panel_current_monitor"
```

Add to `__init__`:

```python
        self._store = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
```

Add methods to the class:

```python
    async def async_save_overrides(self) -> None:
        """Persist circuit and mains overrides to storage."""
        await self._store.async_save({
            "circuit_overrides": self._circuit_overrides,
            "mains_overrides": self._mains_overrides,
        })

    async def async_load_overrides(self) -> None:
        """Load circuit and mains overrides from storage."""
        data = await self._store.async_load()
        if data is None:
            return
        self._circuit_overrides = data.get("circuit_overrides", {})
        self._mains_overrides = data.get("mains_overrides", {})
```

Update `set_circuit_override`, `clear_circuit_override`, `set_mains_override`, `clear_mains_override` to call
`self._hass.async_create_task(self.async_save_overrides())` after mutation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor.py -v` Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/span_panel/current_monitor.py tests/test_current_monitor.py
git commit -m "feat: add storage persistence for current monitor overrides"
```

---

## Task 4: Options Flow — Monitoring Toggle and Global Defaults

**Files:**

- Modify: `custom_components/span_panel/config_flow_options.py:43-88`
- Modify: `custom_components/span_panel/strings.json:356-371`
- Create: `tests/test_current_monitor_options.py`

- [ ] **Step 1: Write failing tests for options flow**

Create `tests/test_current_monitor_options.py`:

```python
"""Tests for current monitoring options flow integration."""

from unittest.mock import MagicMock

from custom_components.span_panel.config_flow_options import (
    build_general_options_schema,
    get_general_options_defaults,
)
from custom_components.span_panel.const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    ENABLE_CURRENT_MONITORING,
)
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    ENABLE_EVENT_BUS,
    ENABLE_PERSISTENT_NOTIFICATIONS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)


def _make_entry(options=None):
    entry = MagicMock()
    entry.options = options or {}
    return entry


class TestMonitoringOptionsSchema:
    """Tests that monitoring fields appear in the options schema."""

    def test_schema_includes_monitoring_toggle(self):
        """Options schema includes enable_current_monitoring."""
        entry = _make_entry()
        schema = build_general_options_schema(entry)
        keys = [str(k) for k in schema.schema]
        assert ENABLE_CURRENT_MONITORING in keys

    def test_schema_includes_threshold_fields(self):
        """Options schema includes threshold configuration fields."""
        entry = _make_entry()
        schema = build_general_options_schema(entry)
        keys = [str(k) for k in schema.schema]
        assert CONTINUOUS_THRESHOLD_PCT in keys
        assert SPIKE_THRESHOLD_PCT in keys
        assert WINDOW_DURATION_M in keys
        assert COOLDOWN_DURATION_M in keys

    def test_schema_includes_notification_toggles(self):
        """Options schema includes notification channel toggles."""
        entry = _make_entry()
        schema = build_general_options_schema(entry)
        keys = [str(k) for k in schema.schema]
        assert ENABLE_PERSISTENT_NOTIFICATIONS in keys
        assert ENABLE_EVENT_BUS in keys


class TestMonitoringOptionsDefaults:
    """Tests that monitoring defaults are correct."""

    def test_defaults_monitoring_disabled(self):
        """Monitoring is disabled by default."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[ENABLE_CURRENT_MONITORING] is False

    def test_defaults_threshold_values(self):
        """Threshold defaults match NEC standards."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[CONTINUOUS_THRESHOLD_PCT] == DEFAULT_CONTINUOUS_THRESHOLD_PCT
        assert defaults[SPIKE_THRESHOLD_PCT] == DEFAULT_SPIKE_THRESHOLD_PCT
        assert defaults[WINDOW_DURATION_M] == DEFAULT_WINDOW_DURATION_M
        assert defaults[COOLDOWN_DURATION_M] == DEFAULT_COOLDOWN_DURATION_M

    def test_defaults_notification_channels_enabled(self):
        """Notification channels enabled by default."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[ENABLE_PERSISTENT_NOTIFICATIONS] is True
        assert defaults[ENABLE_EVENT_BUS] is True

    def test_existing_options_preserved(self):
        """Stored options override defaults."""
        entry = _make_entry({
            ENABLE_CURRENT_MONITORING: True,
            CONTINUOUS_THRESHOLD_PCT: 70,
        })
        defaults = get_general_options_defaults(entry)
        assert defaults[ENABLE_CURRENT_MONITORING] is True
        assert defaults[CONTINUOUS_THRESHOLD_PCT] == 70
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor_options.py -v` Expected: FAIL — monitoring fields not in schema

- [ ] **Step 3: Add monitoring fields to options schema**

In `config_flow_options.py`, add imports:

```python
from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_WINDOW_DURATION_M,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_CURRENT_MONITORING,
    ENABLE_ENERGY_DIP_COMPENSATION,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    ENABLE_UNMAPPED_CIRCUIT_SENSORS,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    ENABLE_EVENT_BUS,
    ENABLE_PERSISTENT_NOTIFICATIONS,
    ENERGY_REPORTING_GRACE_PERIOD,
    SNAPSHOT_UPDATE_INTERVAL,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
```

Add to `schema_fields` in `build_general_options_schema` (after line 51):

```python
        # Current monitoring
        vol.Optional(ENABLE_CURRENT_MONITORING): bool,
        vol.Optional(CONTINUOUS_THRESHOLD_PCT): vol.All(
            int, vol.Range(min=1, max=200)
        ),
        vol.Optional(SPIKE_THRESHOLD_PCT): vol.All(
            int, vol.Range(min=1, max=200)
        ),
        vol.Optional(WINDOW_DURATION_M): vol.All(
            int, vol.Range(min=1, max=180)
        ),
        vol.Optional(COOLDOWN_DURATION_M): vol.All(
            int, vol.Range(min=1, max=180)
        ),
        vol.Optional(ENABLE_PERSISTENT_NOTIFICATIONS): bool,
        vol.Optional(ENABLE_EVENT_BUS): bool,
```

Add to `get_general_options_defaults` return dict (after line 87):

```python
        ENABLE_CURRENT_MONITORING: config_entry.options.get(
            ENABLE_CURRENT_MONITORING, False
        ),
        CONTINUOUS_THRESHOLD_PCT: config_entry.options.get(
            CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT
        ),
        SPIKE_THRESHOLD_PCT: config_entry.options.get(
            SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT
        ),
        WINDOW_DURATION_M: config_entry.options.get(
            WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M
        ),
        COOLDOWN_DURATION_M: config_entry.options.get(
            COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M
        ),
        ENABLE_PERSISTENT_NOTIFICATIONS: config_entry.options.get(
            ENABLE_PERSISTENT_NOTIFICATIONS, True
        ),
        ENABLE_EVENT_BUS: config_entry.options.get(ENABLE_EVENT_BUS, True),
```

- [ ] **Step 4: Add translation strings to `strings.json`**

Add to `options.step.general_options.data` (inside the existing object):

```json
"enable_current_monitoring": "Current Monitoring",
"continuous_threshold_pct": "Continuous Load Threshold (%)",
"spike_threshold_pct": "Spike Threshold (%)",
"window_duration_m": "Continuous Load Window (minutes)",
"cooldown_duration_m": "Alert Cooldown (minutes)",
"enable_persistent_notifications": "Persistent Notifications",
"enable_event_bus": "Event Bus Alerts"
```

Add to `options.step.general_options.data_description`:

```json
"enable_current_monitoring": "Monitor current draw against breaker ratings. Alerts when thresholds exceeded. Requires V2 panel.",
"continuous_threshold_pct": "Percentage of breaker rating for continuous load alerts. NEC code specifies 80% for continuous loads. Range: 1-200%.",
"spike_threshold_pct": "Percentage of breaker rating for instantaneous spike alerts. Default 100% (full breaker rating). Range: 1-200%.",
"window_duration_m": "How long current must stay above the continuous threshold before alerting. Range: 1-180 minutes.",
"cooldown_duration_m": "Minimum time between repeated alerts for the same circuit and alert type. Range: 1-180 minutes.",
"enable_persistent_notifications": "Show alerts in the Home Assistant notification panel.",
"enable_event_bus": "Fire span_panel_current_alert events on the HA event bus for automations and Node-RED."
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor_options.py -v` Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/span_panel/config_flow_options.py custom_components/span_panel/strings.json tests/test_current_monitor_options.py
git commit -m "feat: add current monitoring options to config flow"
```

---

## Task 5: Services — set/clear/get for Circuits and Mains

**Files:**

- Modify: `custom_components/span_panel/services.yaml`
- Modify: `custom_components/span_panel/__init__.py:407-476`
- Create: `tests/test_current_monitor_services.py`

- [ ] **Step 1: Write failing tests for services**

Create `tests/test_current_monitor_services.py`:

```python
"""Tests for current monitoring service registration and handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.span_panel.const import DOMAIN, ENABLE_CURRENT_MONITORING
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.options import SPIKE_THRESHOLD_PCT


class TestServiceRegistration:
    """Tests for monitoring service registration."""

    def test_set_circuit_threshold_service_schema_validates(self):
        """Service schema accepts valid circuit threshold input."""
        from custom_components.span_panel.__init__ import (
            _build_set_circuit_threshold_schema,
        )

        schema = _build_set_circuit_threshold_schema()
        result = schema({"circuit_id": "1", "spike_threshold_pct": 90})
        assert result["circuit_id"] == "1"
        assert result["spike_threshold_pct"] == 90

    def test_set_circuit_threshold_schema_rejects_missing_circuit_id(self):
        """Service schema rejects input without circuit_id."""
        from custom_components.span_panel.__init__ import (
            _build_set_circuit_threshold_schema,
        )

        schema = _build_set_circuit_threshold_schema()
        with pytest.raises(vol.MultipleInvalid):
            schema({"spike_threshold_pct": 90})

    def test_set_mains_threshold_service_schema_validates(self):
        """Service schema accepts valid mains threshold input."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        result = schema({"leg": "upstream_l1", "spike_threshold_pct": 90})
        assert result["leg"] == "upstream_l1"

    def test_set_mains_threshold_schema_rejects_invalid_leg(self):
        """Service schema rejects invalid mains leg identifier."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        with pytest.raises(vol.MultipleInvalid):
            schema({"leg": "invalid_leg", "spike_threshold_pct": 90})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor_services.py -v` Expected: FAIL —
`ImportError: cannot import name '_build_set_circuit_threshold_schema'`

- [ ] **Step 3: Update `services.yaml`**

Replace the contents of `services.yaml`:

```yaml
export_circuit_manifest:

set_circuit_threshold:
  fields:
    circuit_id:
      required: true
      selector:
        text:
    continuous_threshold_pct:
      selector:
        number:
          min: 1
          max: 200
          unit_of_measurement: "%"
    spike_threshold_pct:
      selector:
        number:
          min: 1
          max: 200
          unit_of_measurement: "%"
    window_duration_m:
      selector:
        number:
          min: 1
          max: 180
          unit_of_measurement: min
    monitoring_enabled:
      selector:
        boolean:

clear_circuit_threshold:
  fields:
    circuit_id:
      required: true
      selector:
        text:

set_mains_threshold:
  fields:
    leg:
      required: true
      selector:
        select:
          options:
            - upstream_l1
            - upstream_l2
            - downstream_l1
            - downstream_l2
    continuous_threshold_pct:
      selector:
        number:
          min: 1
          max: 200
          unit_of_measurement: "%"
    spike_threshold_pct:
      selector:
        number:
          min: 1
          max: 200
          unit_of_measurement: "%"
    window_duration_m:
      selector:
        number:
          min: 1
          max: 180
          unit_of_measurement: min
    monitoring_enabled:
      selector:
        boolean:

clear_mains_threshold:
  fields:
    leg:
      required: true
      selector:
        select:
          options:
            - upstream_l1
            - upstream_l2
            - downstream_l1
            - downstream_l2

get_monitoring_status:
```

- [ ] **Step 4: Add service registration to `__init__.py`**

Add schema builder functions and service handlers. Add these after the existing `_async_register_services` function (after line 476):

```python
def _build_set_circuit_threshold_schema() -> vol.Schema:
    """Build schema for set_circuit_threshold service."""
    return vol.Schema(
        {
            vol.Required("circuit_id"): str,
            vol.Optional(CONTINUOUS_THRESHOLD_PCT): vol.All(
                int, vol.Range(min=1, max=200)
            ),
            vol.Optional(SPIKE_THRESHOLD_PCT): vol.All(
                int, vol.Range(min=1, max=200)
            ),
            vol.Optional(WINDOW_DURATION_M): vol.All(
                int, vol.Range(min=1, max=180)
            ),
            vol.Optional("monitoring_enabled"): bool,
        }
    )


def _build_set_mains_threshold_schema() -> vol.Schema:
    """Build schema for set_mains_threshold service."""
    return vol.Schema(
        {
            vol.Required("leg"): vol.In(MAINS_LEGS),
            vol.Optional(CONTINUOUS_THRESHOLD_PCT): vol.All(
                int, vol.Range(min=1, max=200)
            ),
            vol.Optional(SPIKE_THRESHOLD_PCT): vol.All(
                int, vol.Range(min=1, max=200)
            ),
            vol.Optional(WINDOW_DURATION_M): vol.All(
                int, vol.Range(min=1, max=180)
            ),
            vol.Optional("monitoring_enabled"): bool,
        }
    )


def _build_clear_circuit_threshold_schema() -> vol.Schema:
    """Build schema for clear_circuit_threshold service."""
    return vol.Schema({vol.Required("circuit_id"): str})


def _build_clear_mains_threshold_schema() -> vol.Schema:
    """Build schema for clear_mains_threshold service."""
    return vol.Schema({vol.Required("leg"): vol.In(MAINS_LEGS)})
```

Add a function to register monitoring services (called from `async_setup_entry`):

```python
def _async_register_monitoring_services(hass: HomeAssistant) -> None:
    """Register current monitoring services."""

    def _get_monitor(call: ServiceCall) -> CurrentMonitor:
        """Find the CurrentMonitor for the calling entry."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if (
                hasattr(entry, "runtime_data")
                and isinstance(entry.runtime_data, SpanPanelRuntimeData)
                and entry.runtime_data.coordinator.current_monitor is not None
            ):
                return entry.runtime_data.coordinator.current_monitor
        raise ServiceValidationError(
            "No SPAN panel with current monitoring enabled.",
            translation_domain=DOMAIN,
            translation_key="monitoring_not_enabled",
        )

    async def async_handle_set_circuit_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        data = dict(call.data)
        circuit_id = data.pop("circuit_id")
        monitor.set_circuit_override(circuit_id, data)

    async def async_handle_clear_circuit_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        monitor.clear_circuit_override(call.data["circuit_id"])

    async def async_handle_set_mains_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        data = dict(call.data)
        leg = data.pop("leg")
        monitor.set_mains_override(leg, data)

    async def async_handle_clear_mains_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        monitor.clear_mains_override(call.data["leg"])

    async def async_handle_get_monitoring_status(
        call: ServiceCall,
    ) -> ServiceResponse:
        monitor = _get_monitor(call)
        return cast(ServiceResponse, monitor.get_monitoring_status())

    hass.services.async_register(
        DOMAIN, "set_circuit_threshold",
        async_handle_set_circuit_threshold,
        schema=_build_set_circuit_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN, "clear_circuit_threshold",
        async_handle_clear_circuit_threshold,
        schema=_build_clear_circuit_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN, "set_mains_threshold",
        async_handle_set_mains_threshold,
        schema=_build_set_mains_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN, "clear_mains_threshold",
        async_handle_clear_mains_threshold,
        schema=_build_clear_mains_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN, "get_monitoring_status",
        async_handle_get_monitoring_status,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
```

Add required imports to `__init__.py`:

```python
from .const import ENABLE_CURRENT_MONITORING, MAINS_LEGS
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor_services.py -v` Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/span_panel/services.yaml custom_components/span_panel/__init__.py tests/test_current_monitor_services.py
git commit -m "feat: add current monitoring services for threshold management"
```

---

## Task 6: Integration Wiring — Setup, Teardown, Coordinator Hook

**Files:**

- Modify: `custom_components/span_panel/__init__.py:278-334`
- Modify: `custom_components/span_panel/coordinator.py:59-111, 287-307`

- [ ] **Step 1: Write failing test for monitor lifecycle**

Add to `tests/test_current_monitor.py`:

```python
class TestMonitorLifecycle:
    """Tests for monitor startup and shutdown."""

    @pytest.mark.asyncio
    async def test_async_start_loads_overrides(self):
        """async_start loads overrides from storage."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        monitor._store = MagicMock()
        monitor._store.async_load = AsyncMock(return_value={
            "circuit_overrides": {"1": {SPIKE_THRESHOLD_PCT: 90}},
            "mains_overrides": {"upstream_l1": {SPIKE_THRESHOLD_PCT: 85}},
        })
        await monitor.async_start()
        assert monitor._circuit_overrides["1"][SPIKE_THRESHOLD_PCT] == 90
        assert monitor._mains_overrides["upstream_l1"][SPIKE_THRESHOLD_PCT] == 85

    @pytest.mark.asyncio
    async def test_async_stop_clears_state(self):
        """async_stop clears all tracking state."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        assert len(monitor._circuit_states) > 0

        monitor.async_stop()
        assert len(monitor._circuit_states) == 0
        assert len(monitor._mains_states) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor.py::TestMonitorLifecycle -v` Expected: FAIL —
`AttributeError: 'CurrentMonitor' object has no attribute 'async_start'`

- [ ] **Step 3: Add lifecycle methods to `CurrentMonitor`**

Add to `CurrentMonitor`:

```python
    async def async_start(self) -> None:
        """Start the monitor — load persisted overrides."""
        await self.async_load_overrides()
        _LOGGER.info("Current monitor started")

    def async_stop(self) -> None:
        """Stop the monitor — clear all tracking state."""
        self._circuit_states.clear()
        self._mains_states.clear()
        _LOGGER.info("Current monitor stopped")
```

- [ ] **Step 4: Wire monitor into `__init__.py` setup/teardown**

In `async_setup_entry`, after line 280 (`await coordinator.async_setup_streaming()`), add:

```python
            if entry.options.get(ENABLE_CURRENT_MONITORING, False):
                from .current_monitor import CurrentMonitor

                monitor = CurrentMonitor(hass, entry)
                await monitor.async_start()
                coordinator.current_monitor = monitor
```

In `async_unload_entry`, before line 332 (`await entry.runtime_data.coordinator.async_shutdown()`), add:

```python
        if entry.runtime_data.coordinator.current_monitor is not None:
            entry.runtime_data.coordinator.current_monitor.async_stop()
```

- [ ] **Step 5: Add `current_monitor` attribute to coordinator**

In `coordinator.py` `__init__` (around line 100), add:

```python
        self.current_monitor: CurrentMonitor | None = None
```

Add the TYPE_CHECKING import at the top:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .current_monitor import CurrentMonitor
```

- [ ] **Step 6: Add coordinator snapshot delegation**

In `coordinator.py` `_run_post_update_tasks` (after line 302, after `await self._fire_dip_notification()`), add:

```python
        if self.current_monitor is not None:
            self.current_monitor.process_snapshot(snapshot)
```

- [ ] **Step 7: Register monitoring services in `async_setup`**

In `__init__.py`, within the existing `async_setup` function (around line 89), add monitoring service registration after `_async_register_services(hass)`:

```python
    _async_register_monitoring_services(hass)
```

- [ ] **Step 8: Run full test suite to verify nothing breaks**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -v` Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add custom_components/span_panel/__init__.py custom_components/span_panel/coordinator.py tests/test_current_monitor.py
git commit -m "feat: wire CurrentMonitor into integration lifecycle and coordinator"
```

---

## Task 7: Translation Strings for Services

**Files:**

- Modify: `custom_components/span_panel/strings.json`

- [ ] **Step 1: Add service translation strings**

Add a `"services"` section to `strings.json` (at the top level alongside `"config"`, `"options"`, etc.):

```json
"services": {
  "set_circuit_threshold": {
    "name": "Set circuit threshold",
    "description": "Set current monitoring thresholds for a specific circuit.",
    "fields": {
      "circuit_id": {
        "name": "Circuit ID",
        "description": "The circuit identifier to configure."
      },
      "continuous_threshold_pct": {
        "name": "Continuous threshold (%)",
        "description": "Percentage of breaker rating for continuous load alerts."
      },
      "spike_threshold_pct": {
        "name": "Spike threshold (%)",
        "description": "Percentage of breaker rating for instantaneous spike alerts."
      },
      "window_duration_m": {
        "name": "Window duration (minutes)",
        "description": "How long current must stay above threshold before alerting."
      },
      "monitoring_enabled": {
        "name": "Monitoring enabled",
        "description": "Enable or disable monitoring for this circuit."
      }
    }
  },
  "clear_circuit_threshold": {
    "name": "Clear circuit threshold",
    "description": "Remove per-circuit threshold overrides, reverting to global defaults.",
    "fields": {
      "circuit_id": {
        "name": "Circuit ID",
        "description": "The circuit identifier to clear."
      }
    }
  },
  "set_mains_threshold": {
    "name": "Set mains threshold",
    "description": "Set current monitoring thresholds for a specific mains leg.",
    "fields": {
      "leg": {
        "name": "Mains leg",
        "description": "The mains leg to configure."
      },
      "continuous_threshold_pct": {
        "name": "Continuous threshold (%)",
        "description": "Percentage of breaker rating for continuous load alerts."
      },
      "spike_threshold_pct": {
        "name": "Spike threshold (%)",
        "description": "Percentage of breaker rating for instantaneous spike alerts."
      },
      "window_duration_m": {
        "name": "Window duration (minutes)",
        "description": "How long current must stay above threshold before alerting."
      },
      "monitoring_enabled": {
        "name": "Monitoring enabled",
        "description": "Enable or disable monitoring for this mains leg."
      }
    }
  },
  "clear_mains_threshold": {
    "name": "Clear mains threshold",
    "description": "Remove per-mains-leg threshold overrides, reverting to global defaults.",
    "fields": {
      "leg": {
        "name": "Mains leg",
        "description": "The mains leg to clear."
      }
    }
  },
  "get_monitoring_status": {
    "name": "Get monitoring status",
    "description": "Returns current monitoring state for all tracked circuits and mains legs."
  },
  "export_circuit_manifest": {
    "name": "Export circuit manifest",
    "description": "Export circuit topology manifest for all configured SPAN panels."
  }
}
```

- [ ] **Step 2: Run linting to verify strings.json is valid**

Run: `cd /Users/bflood/projects/HA/span && python -c "import json; json.load(open('custom_components/span_panel/strings.json'))"` Expected: No error

- [ ] **Step 3: Commit**

```bash
git add custom_components/span_panel/strings.json
git commit -m "feat: add translation strings for current monitoring services"
```

---

## Task 8: Notify Targets Configuration

**Files:**

- Modify: `custom_components/span_panel/config_flow_options.py`
- Modify: `custom_components/span_panel/strings.json`
- Add to: `tests/test_current_monitor_options.py`

- [ ] **Step 1: Write failing test for notify targets in options**

Add to `tests/test_current_monitor_options.py`:

```python
from custom_components.span_panel.options import NOTIFY_TARGETS


class TestNotifyTargetsOptions:
    """Tests for notify targets configuration."""

    def test_defaults_include_notify_targets(self):
        """Default notify targets is notify.notify."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[NOTIFY_TARGETS] == "notify.notify"

    def test_stored_notify_targets_preserved(self):
        """Stored notify targets override defaults."""
        entry = _make_entry({
            NOTIFY_TARGETS: "notify.mobile_app_phone",
        })
        defaults = get_general_options_defaults(entry)
        assert defaults[NOTIFY_TARGETS] == "notify.mobile_app_phone"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor_options.py::TestNotifyTargetsOptions -v` Expected: FAIL

- [ ] **Step 3: Add notify targets to options schema and defaults**

In `config_flow_options.py` `build_general_options_schema`, add to `schema_fields`:

```python
        vol.Optional(NOTIFY_TARGETS): str,
```

Note: Stored as a comma-separated string in the options flow (HA text input). The `CurrentMonitor` splits on comma when reading. This keeps the options flow
simple — a single text field rather than a dynamic list.

In `get_general_options_defaults`, add:

```python
        NOTIFY_TARGETS: config_entry.options.get(NOTIFY_TARGETS, "notify.notify"),
```

In `strings.json` `options.step.general_options.data`, add:

```json
"notify_targets": "Notify Service Targets"
```

In `strings.json` `options.step.general_options.data_description`, add:

```json
"notify_targets": "Comma-separated list of notify service targets for current alerts (e.g., notify.notify, notify.mobile_app_phone). Default: notify.notify."
```

Update `CurrentMonitor._dispatch_alert` to handle comma-separated string:

```python
        raw_targets = opts.get(NOTIFY_TARGETS, "notify.notify")
        if isinstance(raw_targets, str):
            notify_targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
        else:
            notify_targets = raw_targets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor_options.py -v` Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/span_panel/config_flow_options.py custom_components/span_panel/strings.json custom_components/span_panel/current_monitor.py tests/test_current_monitor_options.py
git commit -m "feat: add notify targets configuration for current monitoring"
```

---

## Task 9: End-to-End Notification Dispatch Tests

**Files:**

- Add to: `tests/test_current_monitor.py`

- [ ] **Step 1: Write tests for notification dispatch**

Add to `tests/test_current_monitor.py`:

```python
class TestNotificationDispatch:
    """Tests for alert notification through all channels."""

    def test_spike_fires_event_bus(self):
        """Spike alert fires event on the HA event bus."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(**{ENABLE_EVENT_BUS: True}))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            serial_number="ABC123",
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)

        hass.bus.async_fire.assert_called_once()
        call_args = hass.bus.async_fire.call_args
        assert call_args[0][0] == EVENT_CURRENT_ALERT
        event_data = call_args[0][1]
        assert event_data["alert_type"] == "spike"
        assert event_data["alert_id"] == "1"
        assert event_data["alert_name"] == "Kitchen"
        assert event_data["current_a"] == 20.0
        assert event_data["breaker_rating_a"] == 20.0
        assert event_data["utilization_pct"] == 100.0
        assert event_data["panel_serial"] == "ABC123"

    def test_spike_does_not_fire_event_when_disabled(self):
        """No event fired when event bus is disabled."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(**{ENABLE_EVENT_BUS: False}))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        hass.bus.async_fire.assert_not_called()

    def test_spike_calls_notify_service(self):
        """Spike alert calls configured notify service targets."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{NOTIFY_TARGETS: ["notify.mobile_app_phone"]}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)

        # Should have called notify service
        notify_calls = [
            c for c in hass.services.async_call.call_args_list
            if c[0][0] == "notify"
        ]
        assert len(notify_calls) == 1
        assert notify_calls[0][0][1] == "mobile_app_phone"

    def test_spike_creates_persistent_notification(self):
        """Spike alert creates persistent notification when enabled."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{ENABLE_PERSISTENT_NOTIFICATIONS: True}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)

        pn_calls = [
            c for c in hass.services.async_call.call_args_list
            if c[0][0] == "persistent_notification"
        ]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][2]["notification_id"] == "span_panel_circuit_1_spike"

    def test_no_persistent_notification_when_disabled(self):
        """No persistent notification when disabled."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(
            **{ENABLE_PERSISTENT_NOTIFICATIONS: False}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)

        pn_calls = [
            c for c in hass.services.async_call.call_args_list
            if c[0][0] == "persistent_notification"
        ]
        assert len(pn_calls) == 0

    def test_notification_message_format_spike(self):
        """Spike notification message includes current and rating."""
        title, message = CurrentMonitor._format_notification(
            "spike", "Kitchen", 22.1, 20.0, 100, 110.5, None,
        )
        assert title == "SPAN: Kitchen spike"
        assert "22.1A" in message
        assert "110.5%" in message
        assert "20A" in message

    def test_notification_message_format_continuous(self):
        """Continuous notification message includes window duration."""
        title, message = CurrentMonitor._format_notification(
            "continuous_overload", "Kitchen", 18.4, 20.0, 80, 92.0, 900,
        )
        assert title == "SPAN: Kitchen overload"
        assert "18.4A" in message
        assert "92.0%" in message
        assert "80%" in message
        assert "15 min" in message
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_current_monitor.py::TestNotificationDispatch -v` Expected: All tests PASS (implementation
already in place from Task 2)

- [ ] **Step 3: Commit**

```bash
git add tests/test_current_monitor.py
git commit -m "test: add notification dispatch tests for current monitoring"
```

---

## Task 10: Full Integration Test and Linting

**Files:**

- All modified files

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -v` Expected: All tests PASS

- [ ] **Step 2: Run type checking**

Run: `cd /Users/bflood/projects/HA/span && python -m mypy custom_components/span_panel/current_monitor.py` Expected: No errors

- [ ] **Step 3: Run linting**

Run: `cd /Users/bflood/projects/HA/span && python -m ruff check custom_components/span_panel/current_monitor.py` Expected: No errors

- [ ] **Step 4: Run markdown linting on spec**

Run: `cd /Users/bflood/projects/HA/span && ./scripts/fix-markdown.sh /Users/bflood/projects/HA/span` Expected: No errors

- [ ] **Step 5: Fix any issues found**

Address any type errors, lint warnings, or test failures.

- [ ] **Step 6: Final commit if fixes were needed**

```bash
git add -u
git commit -m "fix: address linting and type checking issues in current monitor"
```
