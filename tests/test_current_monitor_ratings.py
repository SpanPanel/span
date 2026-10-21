"""Current monitoring judges circuits against their monitoring rating.

A user may set the rating a circuit is judged against (a tandem breaker is published
at the rating of one half). The monitor never stores it: it asks the panel's
`MonitoringRatingManager` for each circuit's rating on every snapshot, so the
monitoring rating applies to alerts and to the status, and survives anything that
resets the monitor's own state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.alert_dispatcher import dispatch_test_alert
from custom_components.span_panel.const import DOMAIN, EVENT_CURRENT_ALERT
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.monitoring_rating_manager import MonitoringRatingManager
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.services import (
    _async_register_monitoring_rating_services,
    _async_register_monitoring_services,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for

SERVICE_A: Final = 100
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}


def _circuit(circuit_id: str, rating: float | None, current_a: float) -> SpanCircuitSnapshot:
    return SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=f"Name of {circuit_id}",
        breaker_rating_a=rating,
        breaker_rating_declared=True,
        current_a=current_a,
    )


def _panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        circuits={c.circuit_id: c for c in circuits}, main_breaker_rating_a=SERVICE_A
    )


def _shared(circuit: SpanCircuitSnapshot, peer: str, tab: int) -> SpanCircuitSnapshot:
    return replace(circuit, tabs=[tab], meter_shared_with=(peer,), relay_shared_with=(peer,))


async def _begun(
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot
) -> MonitoringRatingManager:
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    return manager


def _entry(hass: HomeAssistant, **options: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        unique_id="sp3-ratings-001",
        options={
            CONTINUOUS_THRESHOLD_PCT: 80,
            SPIKE_THRESHOLD_PCT: 500,
            WINDOW_DURATION_M: 15,
            NOTIFY_TARGETS: "event_bus",
            **options,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _monitor(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, **options: object
) -> tuple[CurrentMonitor, MonitoringRatingManager]:
    entry = _entry(hass, **options)
    manager = await _begun(hass, entry, snapshot)
    return CurrentMonitor(hass, entry, manager), manager


def _run_past_the_window(monitor: CurrentMonitor, snapshot: SpanPanelSnapshot, cid: str) -> None:
    """Feed the snapshot twice, the window having elapsed between the two."""
    monitor.process_snapshot(snapshot)
    state = monitor.get_circuit_state(cid)
    assert state is not None
    state.over_threshold_since = datetime.now(UTC) - timedelta(minutes=16)
    monitor.process_snapshot(snapshot)


class TestAlerts:
    """A circuit is judged against the monitoring rating the user set for it."""

    async def test_a_20a_circuit_at_30a_alerts_continuously_without_a_monitoring_rating(
        self, hass: HomeAssistant
    ) -> None:
        """Without a monitoring rating the panel's rating applies, 30 A on 20 A."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, _manager = await _monitor(hass, snapshot)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-2")
        await hass.async_block_till_done()

        assert [a.data["alert_type"] for a in alerts] == ["continuous_overload"]
        assert _ratings_in(alerts[0].data) == (20.0, 20.0, False)

    async def test_the_same_circuit_does_not_alert_with_a_monitoring_rating_of_50a(
        self, hass: HomeAssistant
    ) -> None:
        """30 A is 60% of a 50 A breaker, under the 80% continuous threshold."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_rating("circuit-2", snapshot, 50)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-2")
        await hass.async_block_till_done()

        assert alerts == []
        status = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert status["utilization_pct"] == 60.0

    async def test_a_circuit_with_a_monitoring_rating_is_skipped_while_the_service_rating_is_pending(
        self, hass: HomeAssistant
    ) -> None:
        """Declared with no value yet: not judged against the 20 A the panel published."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_rating("circuit-2", snapshot, 50)
        pending = replace(snapshot, main_breaker_rating_a=None)

        monitor.process_snapshot(pending)

        # Skipped as an unrated circuit is: no state is kept, so nothing can alert.
        assert monitor.get_circuit_state("circuit-2") is None
        assert manager.resolve("circuit-2", pending).judged_a is None
        status = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert status["breaker_rating_a"] == 20.0
        assert (status["monitoring_rating_a"], status["monitoring_rating_user_set"]) == (
            None,
            False,
        )

    async def test_the_alert_names_the_monitoring_rating(self, hass: HomeAssistant) -> None:
        """At 45 A a 50 A monitoring rating is over 80%; the text says 50 A, the payload both.

        The payload's `breaker_rating_a` stays the panel's 20 A: a structured reader
        must not be handed the user's judgement as panel data. The text placeholder
        `{breaker_rating_a}` carries what the alert judged against, so the message and its threshold agree.
        """
        snapshot = _panel(_circuit("circuit-2", 20.0, 45.0))
        monitor, manager = await _monitor(hass, snapshot, **{NOTIFY_TARGETS: "event_bus,notify.x"})
        manager.set_rating("circuit-2", snapshot, 50)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        with patch(
            "custom_components.span_panel.alert_dispatcher.dispatch_to_target"
        ) as dispatched:
            _run_past_the_window(monitor, snapshot, "circuit-2")
            await hass.async_block_till_done()

        assert [_ratings_in(a.data) for a in alerts] == [(20.0, 50.0, True)]
        assert alerts[0].data["utilization_pct"] == 90.0
        message = dispatched.call_args.args[3]
        assert "90.0% of 50A rating" in message

    async def test_a_shared_meter_alert_carries_the_panel_sum_and_the_judged_sum(
        self, hass: HomeAssistant
    ) -> None:
        """15 + 15 A, one member set to 20 A: breaker 30 A, judged against 35 A, user set."""
        snapshot = _panel(
            _shared(_circuit("circuit-52", 15.0, 30.0), "circuit-53", 44),
            _shared(_circuit("circuit-53", 15.0, 30.0), "circuit-52", 44),
        )
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_rating("circuit-53", snapshot, 20)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-52")
        await hass.async_block_till_done()

        assert [_ratings_in(a.data) for a in alerts] == [(30.0, 35.0, True)]

    async def test_a_mains_alert_carries_the_service_rating_twice_never_user_set(
        self, hass: HomeAssistant
    ) -> None:
        """The mains has no monitoring rating: both ratings are the service rating."""
        snapshot = replace(_panel(_circuit("circuit-2", 20.0, 1.0)), upstream_l1_current_a=95.0)
        monitor, _manager = await _monitor(hass, snapshot)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        monitor.process_snapshot(snapshot)
        state = monitor.get_mains_state("upstream_l1")
        assert state is not None
        state.over_threshold_since = datetime.now(UTC) - timedelta(minutes=16)
        monitor.process_snapshot(snapshot)
        await hass.async_block_till_done()

        mains = [a.data for a in alerts if a.data["alert_source"] == "mains"]
        assert [_ratings_in(data) for data in mains] == [
            (float(SERVICE_A), float(SERVICE_A), False)
        ]

    async def test_spike_alerts_carry_the_same_ratings(self, hass: HomeAssistant) -> None:
        """A spike is dispatched apart from a continuous overload; its payload reads the same way."""
        snapshot = replace(_panel(_circuit("circuit-2", 20.0, 45.0)), upstream_l1_current_a=95.0)
        monitor, manager = await _monitor(hass, snapshot, **{SPIKE_THRESHOLD_PCT: 80})
        manager.set_rating("circuit-2", snapshot, 50)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        monitor.process_snapshot(snapshot)
        await hass.async_block_till_done()

        assert len(alerts) == 2
        assert {
            a.data["alert_source"]: (a.data["alert_type"], *_ratings_in(a.data)) for a in alerts
        } == {
            "circuit": ("spike", 20.0, 50.0, True),
            "mains": ("spike", float(SERVICE_A), float(SERVICE_A), False),
        }

    async def test_the_test_notification_has_the_same_payload_shape(
        self, hass: HomeAssistant
    ) -> None:
        """An automation written against the test event reads the same three keys."""
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        dispatch_test_alert(hass, {NOTIFY_TARGETS: "event_bus"})
        await hass.async_block_till_done()

        assert [_ratings_in(a.data) for a in alerts] == [(20.0, 20.0, False)]


def _ratings_in(payload: Mapping[str, object]) -> tuple[object, object, object]:
    """Return an alert payload's panel rating, the rating it judged against, and whether the user set that."""
    return (
        payload["breaker_rating_a"],
        payload["monitoring_rating_a"],
        payload["monitoring_rating_user_set"],
    )


class TestStatus:
    """Status entries carry the panel's rating, the one judged against, and whether the user set it."""

    async def test_a_circuit_without_a_monitoring_rating_reports_the_panels_rating(
        self, hass: HomeAssistant
    ) -> None:
        """Judged against is the panel's rating, and the user did not set it."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        monitor, _manager = await _monitor(hass, snapshot)
        monitor.process_snapshot(snapshot)

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]

        assert entry["breaker_rating_a"] == 20.0
        assert entry["monitoring_rating_a"] == 20.0
        assert entry["monitoring_rating_user_set"] is False
        assert "panel_breaker_rating_a" not in entry
        assert "breaker_rating_user_set" not in entry

    async def test_a_circuit_with_a_monitoring_rating_reports_both_ratings(
        self, hass: HomeAssistant
    ) -> None:
        """Judged against is the monitoring rating; the breaker rating stays the panel's."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_rating("circuit-2", snapshot, 50)
        monitor.process_snapshot(snapshot)

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]

        assert entry["breaker_rating_a"] == 20.0
        assert entry["monitoring_rating_a"] == 50.0
        assert entry["monitoring_rating_user_set"] is True
        assert entry["utilization_pct"] == 20.0

    async def test_the_mains_entry_has_the_same_shape(self, hass: HomeAssistant) -> None:
        """The mains has no monitoring rating: it is judged against its panel rating, never user set."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        monitor, _manager = await _monitor(hass, snapshot)
        monitor.process_snapshot(snapshot)

        (entry,) = monitor.get_monitoring_status()["mains"].values()

        assert entry["breaker_rating_a"] == float(SERVICE_A)
        assert entry["monitoring_rating_a"] == float(SERVICE_A)
        assert entry["monitoring_rating_user_set"] is False
        assert "panel_breaker_rating_a" not in entry
        assert "breaker_rating_user_set" not in entry


class TestSharedMeters:
    """A shared meter is judged against its members' summed ratings."""

    async def test_one_member_with_a_monitoring_rating_changes_the_sum_and_marks_the_group(
        self, hass: HomeAssistant
    ) -> None:
        """15 + 15 A with one member given a 20 A monitoring rating is judged against 35 A, breaker 30 A, user set."""
        snapshot = _panel(
            _shared(_circuit("circuit-52", 15.0, 12.0), "circuit-53", 44),
            _shared(_circuit("circuit-53", 15.0, 12.0), "circuit-52", 44),
        )
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_rating("circuit-53", snapshot, 20)
        monitor.process_snapshot(snapshot)

        status = monitor.get_monitoring_status()["circuits"]

        for member in ("circuit-52", "circuit-53"):
            assert status[member]["breaker_rating_a"] == 30.0
            assert status[member]["monitoring_rating_a"] == 35.0
            assert status[member]["monitoring_rating_user_set"] is True

    async def test_a_group_with_an_unrated_member_is_unratable_yet_still_user_set(
        self, hass: HomeAssistant
    ) -> None:
        """One member with a monitoring rating, another with no panel rating: no sum, but the flag is true."""
        rated = _shared(_circuit("circuit-52", 15.0, 12.0), "circuit-53", 44)
        unrated = _shared(_circuit("circuit-53", None, 12.0), "circuit-52", 44)
        snapshot = _panel(rated, unrated)
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_rating("circuit-52", snapshot, 20)
        monitor.process_snapshot(snapshot)

        status = monitor.get_monitoring_status()["circuits"]

        for member in ("circuit-52", "circuit-53"):
            assert status[member]["breaker_rating_a"] is None
            assert status[member]["monitoring_rating_a"] is None
            assert status[member]["monitoring_rating_user_set"] is True


class TestMonitoringRatingSurvivesTheMonitorsOwnReset:
    """The monitoring rating is the manager's, so the monitor's resets cannot lose it."""

    async def _panel_with_services(
        self, hass: HomeAssistant, snapshot: SpanPanelSnapshot
    ) -> tuple[MockConfigEntry, MonitoringRatingManager]:
        entry = _entry(hass)
        manager = await _begun(hass, entry, snapshot)
        coordinator = MagicMock()
        coordinator.data = snapshot
        coordinator.monitoring_rating_manager = manager
        coordinator.current_monitor = CurrentMonitor(hass, entry, manager)
        entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator,
            panel_device_id="sp3-ratings-001-device",
            curation=CurationOverlay.empty(),
            pv_binding=pv_binding_for(snapshot),
            setup_snapshot=snapshot,
        )
        entry.mock_state(hass, ConfigEntryState.LOADED)
        _async_register_monitoring_rating_services(hass)
        _async_register_monitoring_services(hass)
        return entry, manager

    async def _rating_in_status(self, hass: HomeAssistant) -> tuple[object, object]:
        response = await hass.services.async_call(
            DOMAIN, "get_monitoring_status", {}, blocking=True, return_response=True
        )
        assert response is not None
        circuits = response["circuits"]
        assert isinstance(circuits, dict)
        entry = circuits["circuit-2"]
        assert isinstance(entry, dict)
        return entry["monitoring_rating_a"], entry["monitoring_rating_user_set"]

    async def test_clear_circuit_threshold_leaves_the_rating_alone(
        self, hass: HomeAssistant
    ) -> None:
        """Clearing a circuit's thresholds resets its monitor state, not its monitoring rating."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        entry, manager = await self._panel_with_services(hass, snapshot)
        manager.set_rating("circuit-2", snapshot, 50)
        entry.runtime_data.coordinator.current_monitor.process_snapshot(snapshot)
        await hass.services.async_call(
            DOMAIN,
            "set_circuit_threshold",
            {"circuit_id": "circuit-2", CONTINUOUS_THRESHOLD_PCT: 70},
            blocking=True,
        )

        await hass.services.async_call(
            DOMAIN, "clear_circuit_threshold", {"circuit_id": "circuit-2"}, blocking=True
        )

        assert await self._rating_in_status(hass) == (50.0, True)

    async def test_monitoring_off_and_on_leaves_the_rating_alone(self, hass: HomeAssistant) -> None:
        """A new monitor, built when monitoring is turned back on, reads the same monitoring rating."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        entry, manager = await self._panel_with_services(hass, snapshot)
        manager.set_rating("circuit-2", snapshot, 50)
        original = entry.runtime_data.coordinator.current_monitor

        await hass.services.async_call(
            DOMAIN, "set_global_monitoring", {"enabled": False}, blocking=True
        )
        assert entry.runtime_data.coordinator.current_monitor is None
        await hass.services.async_call(
            DOMAIN, "set_global_monitoring", {"enabled": True}, blocking=True
        )

        assert entry.runtime_data.coordinator.current_monitor is not original
        assert await self._rating_in_status(hass) == (50.0, True)


@pytest.mark.parametrize("stored", [None, 50])
async def test_a_monitor_is_judged_by_the_manager_it_was_given(
    hass: HomeAssistant, stored: int | None
) -> None:
    """The rating a monitor uses is the manager's answer, whichever manager that is."""
    snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
    monitor, manager = await _monitor(hass, snapshot)
    if stored is not None:
        manager.set_rating("circuit-2", snapshot, stored)
    monitor.process_snapshot(snapshot)

    entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]

    assert entry["breaker_rating_a"] == 20.0
    assert entry["monitoring_rating_a"] == float(stored or 20)
