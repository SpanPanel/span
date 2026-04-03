"""Tests for the CurrentMonitor class."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.span_panel.const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    ENABLE_CURRENT_MONITORING,
    EVENT_CURRENT_ALERT,
)
from homeassistant.core import CoreState

from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
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
        NOTIFY_TARGETS: "event_bus",
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


def _run_coro(coro):
    """Run a coroutine synchronously so async_create_task records inner calls.

    When an event loop is already running (async tests) the coroutine is
    scheduled as an eager task so it is consumed immediately and never
    triggers an "unawaited coroutine" warning.
    """
    if not asyncio.iscoroutine(coro):
        return coro
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return loop.create_task(coro)
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()


def _make_hass():
    """Create a minimal mock hass object."""
    hass = MagicMock()
    hass.state = CoreState.running
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.async_create_task = MagicMock(side_effect=_run_coro)
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
        old_alert_time = datetime.now(UTC) - timedelta(minutes=16)
        state.last_spike_alert = old_alert_time

        hass.bus.async_fire.reset_mock()
        monitor.process_snapshot(snapshot)
        # Should have fired again — new timestamp is strictly later than the backdated one
        assert monitor.get_circuit_state("1").last_spike_alert > old_alert_time


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


class TestNotificationDispatch:
    """Tests for alert notification through all channels."""

    def test_spike_fires_event_bus(self):
        """Spike alert fires event on the HA event bus."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(**{NOTIFY_TARGETS: "event_bus"}))
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
        """No event fired when event_bus target is not in notify_targets."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options(**{NOTIFY_TARGETS: ""}))
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
            **{NOTIFY_TARGETS: "notify.mobile_app_phone"}
        ))
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)

        # Should have called the notify service for the target
        notify_calls = [
            c for c in hass.services.async_call.call_args_list
            if c[0][0] == "notify"
        ]
        assert len(notify_calls) == 1

    def test_no_notifications_during_startup(self):
        """Service calls are suppressed when HA is still starting."""
        hass = _make_hass()
        hass.state = CoreState.starting
        monitor = _make_monitor(hass, _make_options())
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)

        hass.services.async_call.assert_not_called()
        # Event bus should still fire
        hass.bus.async_fire.assert_called()

    def test_notification_message_format_spike(self):
        """Spike notification message includes current and rating."""
        title, message = CurrentMonitor._format_notification(
            alert_type="spike",
            alert_name="Kitchen",
            alert_id="sensor.kitchen_current",
            current_a=22.1,
            breaker_rating_a=20.0,
            threshold_pct=100,
            utilization_pct=110.5,
            window_duration_s=None,
            title_template="SPAN: {name} {alert_type}",
            message_template="{name} at {current_a}A ({utilization_pct}% of {breaker_rating_a}A rating)",
        )
        assert title == "SPAN: Kitchen spike"
        assert "22.1A" in message
        assert "110.5%" in message
        assert "20A" in message

    def test_notification_message_format_continuous(self):
        """Continuous notification message includes window duration."""
        title, message = CurrentMonitor._format_notification(
            alert_type="continuous_overload",
            alert_name="Kitchen",
            alert_id="sensor.kitchen_current",
            current_a=18.4,
            breaker_rating_a=20.0,
            threshold_pct=80,
            utilization_pct=92.0,
            window_duration_s=900,
            title_template="SPAN: {name} {alert_type}",
            message_template=(
                "{name} drawing {current_a}A ({utilization_pct}% of {breaker_rating_a}A rating) "
                "— continuous threshold of {threshold_pct}% exceeded over {window_m} min"
            ),
        )
        assert title == "SPAN: Kitchen continuous_overload"
        assert "18.4A" in message
        assert "92.0%" in message
        assert "80%" in message
        assert "15 min" in message

    def test_notification_custom_template_with_entity_id(self):
        """Custom templates produce expected output with entity_id placeholder."""
        title, message = CurrentMonitor._format_notification(
            alert_type="spike",
            alert_name="Kitchen",
            alert_id="sensor.kitchen_current",
            current_a=22.1,
            breaker_rating_a=20.0,
            threshold_pct=100,
            utilization_pct=110.5,
            window_duration_s=None,
            title_template="Alert: {entity_id}",
            message_template="{entity_id} is at {current_a}A",
        )
        assert title == "Alert: sensor.kitchen_current"
        assert message == "sensor.kitchen_current is at 22.1A"

    def test_notification_invalid_template_falls_back(self):
        """Invalid template placeholders fall back to defaults."""
        title, message = CurrentMonitor._format_notification(
            alert_type="spike",
            alert_name="Kitchen",
            alert_id="sensor.kitchen_current",
            current_a=22.1,
            breaker_rating_a=20.0,
            threshold_pct=100,
            utilization_pct=110.5,
            window_duration_s=None,
            title_template="{nonexistent_var}",
            message_template="{also_bad}",
        )
        assert "Kitchen" in title
        assert "22.1A" in message


class TestGlobalSettingsStorage:
    """Tests for global monitoring settings in storage."""

    def test_get_global_settings_returns_defaults_from_entry_options(self):
        """Returns values from entry.options when no stored globals exist."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        settings = monitor.get_global_settings()
        assert settings[CONTINUOUS_THRESHOLD_PCT] == DEFAULT_CONTINUOUS_THRESHOLD_PCT
        assert settings[SPIKE_THRESHOLD_PCT] == DEFAULT_SPIKE_THRESHOLD_PCT
        assert settings[WINDOW_DURATION_M] == DEFAULT_WINDOW_DURATION_M
        assert settings[COOLDOWN_DURATION_M] == DEFAULT_COOLDOWN_DURATION_M

    def test_get_global_settings_from_storage(self):
        """Returns stored values when available, ignoring entry.options."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor._global_settings = {
            CONTINUOUS_THRESHOLD_PCT: 70,
            SPIKE_THRESHOLD_PCT: 90,
        }
        settings = monitor.get_global_settings()
        assert settings[CONTINUOUS_THRESHOLD_PCT] == 70
        assert settings[SPIKE_THRESHOLD_PCT] == 90

    def test_get_global_settings_storage_defaults_missing_keys(self):
        """Stored globals with missing keys fall back to defaults."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor._global_settings = {
            CONTINUOUS_THRESHOLD_PCT: 70,
        }
        settings = monitor.get_global_settings()
        assert settings[CONTINUOUS_THRESHOLD_PCT] == 70
        assert settings[SPIKE_THRESHOLD_PCT] == DEFAULT_SPIKE_THRESHOLD_PCT

    def test_set_global_settings_persists(self):
        """set_global_settings updates internal state."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor.set_global_settings({
            CONTINUOUS_THRESHOLD_PCT: 75,
            SPIKE_THRESHOLD_PCT: 95,
        })
        assert monitor._global_settings[CONTINUOUS_THRESHOLD_PCT] == 75
        assert monitor._global_settings[SPIKE_THRESHOLD_PCT] == 95

    def test_set_global_settings_ignores_unknown_keys(self):
        """Unknown keys are not stored."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor.set_global_settings({"bogus_key": 42})
        assert "bogus_key" not in monitor._global_settings

    def test_set_global_settings_triggers_save(self):
        """set_global_settings schedules a storage save."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor.set_global_settings({CONTINUOUS_THRESHOLD_PCT: 75})
        hass.async_create_task.assert_called()

    def test_circuit_thresholds_use_global_settings(self):
        """Circuit thresholds read from global settings, not entry.options."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor._global_settings = {
            CONTINUOUS_THRESHOLD_PCT: 70,
            SPIKE_THRESHOLD_PCT: 85,
            WINDOW_DURATION_M: 10,
            COOLDOWN_DURATION_M: 20,
        }
        thresholds = monitor._resolve_circuit_thresholds("test_circuit")
        assert thresholds == (70, 85, 10, 20)

    def test_mains_thresholds_use_global_settings(self):
        """Mains thresholds read from global settings, not entry.options."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor._global_settings = {
            CONTINUOUS_THRESHOLD_PCT: 65,
            SPIKE_THRESHOLD_PCT: 88,
            WINDOW_DURATION_M: 12,
            COOLDOWN_DURATION_M: 25,
        }
        thresholds = monitor._resolve_mains_thresholds("upstream_l1")
        assert thresholds == (65, 88, 12, 25)

    def test_dispatch_alert_uses_global_settings(self):
        """Alert dispatch reads notification settings from global settings."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor._global_settings = {
            NOTIFY_TARGETS: "notify.test_target",
        }
        circuit = SpanCircuitSnapshotFactory.create(
            circuit_id="1", name="Kitchen",
            current_a=20.0, breaker_rating_a=20.0,
        )
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"1": circuit}, main_breaker_rating_a=200,
        )
        monitor.process_snapshot(snapshot)
        # Event bus should NOT fire because event_bus is not in notify_targets
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_and_load_global_settings(self):
        """Global settings survive save/load cycle."""
        hass = _make_hass()
        store_data: dict[str, Any] = {}

        async def mock_save(data: dict[str, Any]) -> None:
            store_data["saved"] = data

        async def mock_load() -> dict[str, Any] | None:
            return store_data.get("saved")

        monitor = _make_monitor(hass)
        monitor._store = MagicMock()
        monitor._store.async_save = AsyncMock(side_effect=mock_save)
        monitor._store.async_load = AsyncMock(side_effect=mock_load)

        monitor.set_global_settings({
            CONTINUOUS_THRESHOLD_PCT: 70,
            SPIKE_THRESHOLD_PCT: 90,
        })
        await monitor.async_save_overrides()

        # Create a new monitor and load
        monitor2 = _make_monitor(hass)
        monitor2._store = MagicMock()
        monitor2._store.async_load = AsyncMock(side_effect=mock_load)
        await monitor2.async_load_overrides()

        assert monitor2._global_settings[CONTINUOUS_THRESHOLD_PCT] == 70
        assert monitor2._global_settings[SPIKE_THRESHOLD_PCT] == 90

    @pytest.mark.asyncio
    async def test_load_without_global_key_leaves_empty(self):
        """Loading storage without a 'global' key leaves global_settings empty."""
        hass = _make_hass()
        monitor = _make_monitor(hass)
        monitor._store = MagicMock()
        monitor._store.async_load = AsyncMock(return_value={
            "circuit_overrides": {},
            "mains_overrides": {},
        })
        await monitor.async_load_overrides()
        assert monitor._global_settings == {}
