"""Current monitoring judges a tandem against both breakers once its package is copied.

A user may copy a tandem's ratings from the SPAN app (the panel publishes only the
first), so the space is judged against both breakers together. The monitor never
stores the copy: it asks the panel's `PackageRatingManager` for each circuit's
rating on every snapshot, so the copy applies to alerts and to the status, and
survives anything that resets the monitor's own state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.alert_dispatcher import dispatch_test_alert
from custom_components.span_panel.const import DOMAIN, EVENT_CURRENT_ALERT
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.services import (
    _async_register_monitoring_services,
    _async_register_package_rating_services,
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


def _in_spaces(circuit: SpanCircuitSnapshot, *spaces: int) -> SpanCircuitSnapshot:
    return replace(circuit, tabs=list(spaces))


def _panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        circuits={c.circuit_id: c for c in circuits}, main_breaker_rating_a=SERVICE_A
    )


def _shared(circuit: SpanCircuitSnapshot, peer: str, tab: int) -> SpanCircuitSnapshot:
    return replace(circuit, tabs=[tab], meter_shared_with=(peer,), relay_shared_with=(peer,))


def _sharing_panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    """A panel naming its shared meters, which only a panel that describes its breakers does."""
    return replace(_panel(*circuits), publishes_shared_meters=True)


async def _begun(
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot
) -> PackageRatingManager:
    manager = PackageRatingManager(hass, entry)
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
) -> tuple[CurrentMonitor, PackageRatingManager]:
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
    """A circuit is judged against both breakers of the package the user copied for it."""

    async def test_a_20a_circuit_at_30a_alerts_continuously_without_a_copied_package(
        self, hass: HomeAssistant
    ) -> None:
        """Without a copied package the panel's rating applies, 30 A on 20 A."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, _manager = await _monitor(hass, snapshot)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-2")
        await hass.async_block_till_done()

        assert [a.data["alert_type"] for a in alerts] == ["continuous_overload"]
        assert _ratings_in(alerts[0].data) == (20.0, 20.0, False, None, None)

    async def test_the_same_circuit_does_not_alert_with_a_20_30_package_copied(
        self, hass: HomeAssistant
    ) -> None:
        """30 A is 60% of a 20-30 tandem's 50 A, under the 80% continuous threshold."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-2")
        await hass.async_block_till_done()

        assert alerts == []
        status = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert status["utilization_pct"] == 60.0

    async def test_a_circuit_with_a_copied_package_is_skipped_while_the_service_rating_is_pending(
        self, hass: HomeAssistant
    ) -> None:
        """Declared with no value yet: not judged against the 20 A the panel published."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
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

    async def test_the_alert_names_the_rating_the_package_gives(self, hass: HomeAssistant) -> None:
        """At 45 A a 20-30 package's 50 A is over 80%; the text says 50 A, the payload both.

        The payload's `breaker_rating_a` stays the panel's 20 A: a structured reader
        must not be handed the user's judgement as panel data. The text placeholder
        `{breaker_rating_a}` carries what the alert judged against, so the message and its threshold agree.
        """
        snapshot = _panel(_circuit("circuit-2", 20.0, 45.0))
        monitor, manager = await _monitor(hass, snapshot, **{NOTIFY_TARGETS: "event_bus,notify.x"})
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        with patch(
            "custom_components.span_panel.alert_dispatcher.dispatch_to_target"
        ) as dispatched:
            _run_past_the_window(monitor, snapshot, "circuit-2")
            await hass.async_block_till_done()

        assert [_ratings_in(a.data) for a in alerts] == [(20.0, 50.0, True, [20, 30], "applies")]
        assert alerts[0].data["utilization_pct"] == 90.0
        message = dispatched.call_args.args[3]
        assert "90.0% of 50A rating" in message

    async def test_a_shared_meter_alert_carries_its_capacity_and_never_a_package(
        self, hass: HomeAssistant
    ) -> None:
        """15 + 15 A on one space: breaker and judged-against are both its 30 A capacity, not user set."""
        snapshot = _sharing_panel(
            _shared(_circuit("circuit-52", 15.0, 30.0), "circuit-53", 44),
            _shared(_circuit("circuit-53", 15.0, 30.0), "circuit-52", 44),
        )
        monitor, manager = await _monitor(hass, snapshot)
        with pytest.raises(ServiceValidationError) as refused:
            manager.set_package_ratings("circuit-53", snapshot, [15, 5])
        assert refused.value.translation_key == "package_ratings_panel_describes_breakers"
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-52")
        await hass.async_block_till_done()

        assert [_ratings_in(a.data) for a in alerts] == [(30.0, 30.0, False, None, None)]

    async def test_a_mains_alert_carries_the_service_rating_twice_never_user_set(
        self, hass: HomeAssistant
    ) -> None:
        """The mains takes no package ratings: both ratings are the service rating."""
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
            (float(SERVICE_A), float(SERVICE_A), False, None, None)
        ]

    async def test_spike_alerts_carry_the_same_ratings(self, hass: HomeAssistant) -> None:
        """A spike is dispatched apart from a continuous overload; its payload reads the same way."""
        snapshot = replace(_panel(_circuit("circuit-2", 20.0, 45.0)), upstream_l1_current_a=95.0)
        monitor, manager = await _monitor(hass, snapshot, **{SPIKE_THRESHOLD_PCT: 80})
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        monitor.process_snapshot(snapshot)
        await hass.async_block_till_done()

        assert len(alerts) == 2
        assert {
            a.data["alert_source"]: (a.data["alert_type"], *_ratings_in(a.data)) for a in alerts
        } == {
            "circuit": ("spike", 20.0, 50.0, True, [20, 30], "applies"),
            "mains": ("spike", float(SERVICE_A), float(SERVICE_A), False, None, None),
        }

    async def test_an_alert_on_a_package_across_two_spaces_says_it_is_on_averaged_legs(
        self, hass: HomeAssistant
    ) -> None:
        """30 A on a 20 A two-space circuit holding a 20-30 copy: judged against 20 A, the copy kept beside it."""
        snapshot = _panel(_in_spaces(_circuit("circuit-18", 20.0, 30.0), 18, 20))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_package_ratings("circuit-18", snapshot, [20, 30])
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        _run_past_the_window(monitor, snapshot, "circuit-18")
        await hass.async_block_till_done()

        assert [a.data["alert_type"] for a in alerts] == ["continuous_overload"]
        assert _ratings_in(alerts[0].data) == (20.0, 20.0, False, [20, 30], "averaged_legs")

    async def test_the_test_notification_has_the_same_payload_shape(
        self, hass: HomeAssistant
    ) -> None:
        """An automation written against the test event reads the same five keys."""
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)

        dispatch_test_alert(hass, {NOTIFY_TARGETS: "event_bus"})
        await hass.async_block_till_done()

        assert [_ratings_in(a.data) for a in alerts] == [(20.0, 20.0, False, None, None)]


def _ratings_in(payload: Mapping[str, object]) -> tuple[object, object, object, object, object]:
    """Return an alert payload's panel rating, the rating it judged against, whether the user set that, the copy and its state."""
    return (
        payload["breaker_rating_a"],
        payload["monitoring_rating_a"],
        payload["monitoring_rating_user_set"],
        payload["package_ratings_a"],
        payload["package_ratings_state"],
    )


class TestStatus:
    """Status entries carry the panel's rating, the one judged against, and whether the user set it."""

    async def test_a_circuit_without_a_copied_package_reports_the_panels_rating(
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
        assert entry["package_ratings_a"] is None
        assert "panel_breaker_rating_a" not in entry
        assert "breaker_rating_user_set" not in entry

    async def test_a_circuit_with_a_copied_package_reports_both_ratings(
        self, hass: HomeAssistant
    ) -> None:
        """Judged against is the package's 50 A; the breaker rating stays the panel's."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
        monitor.process_snapshot(snapshot)

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]

        assert entry["breaker_rating_a"] == 20.0
        assert entry["monitoring_rating_a"] == 50.0
        assert entry["monitoring_rating_user_set"] is True
        assert entry["package_ratings_a"] == [20, 30]
        assert entry["utilization_pct"] == 20.0

    async def test_the_mains_entry_has_the_same_shape(self, hass: HomeAssistant) -> None:
        """The mains takes no package ratings: it is judged against its panel rating, never user set."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        monitor, _manager = await _monitor(hass, snapshot)
        monitor.process_snapshot(snapshot)

        (entry,) = monitor.get_monitoring_status()["mains"].values()

        assert entry["breaker_rating_a"] == float(SERVICE_A)
        assert entry["monitoring_rating_a"] == float(SERVICE_A)
        assert entry["monitoring_rating_user_set"] is False
        assert entry["package_ratings_a"] is None
        assert entry["package_ratings_form"] is None
        assert entry["package_ratings_state"] is None
        assert "panel_breaker_rating_a" not in entry
        assert "breaker_rating_user_set" not in entry

    async def test_each_circuit_entry_says_which_form_its_copy_takes_and_what_it_is(
        self, hass: HomeAssistant
    ) -> None:
        """As the topology says: a 20-20 tandem applies, a 20-30 package across two spaces is on averaged legs.

        A circuit with no copy still says its form, so the Monitoring tab can draw
        the field, and has no state.
        """
        snapshot = _panel(
            _in_spaces(_circuit("circuit-31", 20.0, 10.0), 31),
            _in_spaces(_circuit("circuit-18", 20.0, 10.0), 18, 20),
            _in_spaces(_circuit("circuit-5", 15.0, 10.0), 5),
        )
        monitor, manager = await _monitor(hass, snapshot)
        manager.set_package_ratings("circuit-31", snapshot, [20, 20])
        manager.set_package_ratings("circuit-18", snapshot, [20, 30])
        monitor.process_snapshot(snapshot)

        circuits = monitor.get_monitoring_status()["circuits"]

        assert {
            circuit_id: (entry["package_ratings_form"], entry["package_ratings_state"])
            for circuit_id, entry in circuits.items()
        } == {
            "circuit-31": ("one_space", "applies"),
            "circuit-18": ("two_spaces", "averaged_legs"),
            "circuit-5": ("one_space", None),
        }
        assert circuits["circuit-18"]["monitoring_rating_a"] == 20.0
        assert circuits["circuit-18"]["package_ratings_a"] == [20, 30]


class TestSharedMeters:
    """A shared meter is judged against its capacity, and never against a package a user copied."""

    async def test_a_package_copied_before_the_meter_was_shared_is_removed_and_the_group_judged_by_its_capacity(
        self, hass: HomeAssistant
    ) -> None:
        """A copy made while the circuit stood alone goes once the panel names the meter's sharers."""
        alone = _panel(_circuit("circuit-52", 15.0, 12.0), _circuit("circuit-53", 15.0, 12.0))
        shared = _sharing_panel(
            _shared(_circuit("circuit-52", 15.0, 12.0), "circuit-53", 44),
            _shared(_circuit("circuit-53", 15.0, 12.0), "circuit-52", 44),
        )
        monitor, manager = await _monitor(hass, alone)
        manager.set_package_ratings("circuit-53", alone, [15, 5])
        assert manager.resolve("circuit-53", alone).user_set

        manager.reconcile(shared, lambda: _DECLARED)
        monitor.process_snapshot(shared)

        status = monitor.get_monitoring_status()["circuits"]
        for member in ("circuit-52", "circuit-53"):
            assert status[member]["breaker_rating_a"] == 30.0
            assert status[member]["monitoring_rating_a"] == 30.0
            assert status[member]["monitoring_rating_user_set"] is False
            assert status[member]["package_ratings_a"] is None
            # A group's entry is the shared meter's judgement, which takes no copy.
            assert status[member]["package_ratings_form"] is None
            assert status[member]["package_ratings_state"] is None

    async def test_a_group_with_an_unrated_member_has_no_capacity_and_is_not_user_set(
        self, hass: HomeAssistant
    ) -> None:
        """One member with no panel rating: no capacity can be given, and nothing the user copied stands in for it."""
        rated = _shared(_circuit("circuit-52", 15.0, 12.0), "circuit-53", 44)
        unrated = _shared(_circuit("circuit-53", None, 12.0), "circuit-52", 44)
        snapshot = _sharing_panel(rated, unrated)
        monitor, _manager = await _monitor(hass, snapshot)
        monitor.process_snapshot(snapshot)

        status = monitor.get_monitoring_status()["circuits"]

        for member in ("circuit-52", "circuit-53"):
            assert status[member]["breaker_rating_a"] is None
            assert status[member]["monitoring_rating_a"] is None
            assert status[member]["monitoring_rating_user_set"] is False


class TestACopySurvivesTheMonitorsOwnReset:
    """The copied package is the manager's, so the monitor's resets cannot lose it."""

    async def _panel_with_services(
        self, hass: HomeAssistant, snapshot: SpanPanelSnapshot
    ) -> tuple[MockConfigEntry, PackageRatingManager]:
        entry = _entry(hass)
        manager = await _begun(hass, entry, snapshot)
        coordinator = MagicMock()
        coordinator.data = snapshot
        coordinator.package_rating_manager = manager
        coordinator.current_monitor = CurrentMonitor(hass, entry, manager)
        entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator,
            panel_device_id="sp3-ratings-001-device",
            curation=CurationOverlay.empty(),
            pv_binding=pv_binding_for(snapshot),
            setup_snapshot=snapshot,
        )
        entry.mock_state(hass, ConfigEntryState.LOADED)
        _async_register_package_rating_services(hass)
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
        """Clearing a circuit's thresholds resets its monitor state, not its copied package."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        entry, manager = await self._panel_with_services(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
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
        """A new monitor, built when monitoring is turned back on, reads the same copied package."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 10.0))
        entry, manager = await self._panel_with_services(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20, 30])
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
        manager.set_package_ratings("circuit-2", snapshot, [20, stored - 20])
    monitor.process_snapshot(snapshot)

    entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]

    assert entry["breaker_rating_a"] == 20.0
    assert entry["monitoring_rating_a"] == float(stored or 20)


class TestStatusWhileMonitoringIsOff:
    """A point whose monitoring is off reports no judgement; turning it back on resumes it."""

    async def _services(
        self, hass: HomeAssistant, snapshot: SpanPanelSnapshot
    ) -> tuple[CurrentMonitor, PackageRatingManager]:
        helper = TestACopySurvivesTheMonitorsOwnReset()
        entry, manager = await helper._panel_with_services(hass, snapshot)
        return entry.runtime_data.coordinator.current_monitor, manager

    async def _set(
        self, hass: HomeAssistant, service: str, target: dict[str, str], **fields: object
    ) -> None:
        await hass.services.async_call(DOMAIN, service, {**target, **fields}, blocking=True)

    async def test_a_circuit_turned_off_reports_no_utilization_current_or_window(
        self, hass: HomeAssistant
    ) -> None:
        """Over threshold, then off: the frozen figures are gone, the alert history is kept."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, manager = await self._services(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20, 15])
        _run_past_the_window(monitor, snapshot, "circuit-2")
        before = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert before["utilization_pct"] == 85.7
        assert before["over_threshold_since"] is not None

        await self._set(
            hass, "set_circuit_threshold", {"circuit_id": "circuit-2"}, monitoring_enabled=False
        )
        monitor.process_snapshot(snapshot)

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert entry["monitoring_enabled"] is False
        assert entry["utilization_pct"] is None
        assert entry["last_current_a"] is None
        assert entry["over_threshold_since"] is None
        assert entry["monitoring_rating_a"] == 35.0

    async def test_turning_it_back_on_resumes_with_no_stale_window(
        self, hass: HomeAssistant
    ) -> None:
        """A window opened before it was turned off does not make the first sample after an alert."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, manager = await self._services(hass, snapshot)
        manager.set_package_ratings("circuit-2", snapshot, [20])
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)
        monitor.process_snapshot(snapshot)
        state = monitor.get_circuit_state("circuit-2")
        assert state is not None
        state.over_threshold_since = datetime.now(UTC) - timedelta(minutes=16)

        await self._set(
            hass, "set_circuit_threshold", {"circuit_id": "circuit-2"}, monitoring_enabled=False
        )
        await self._set(
            hass, "set_circuit_threshold", {"circuit_id": "circuit-2"}, monitoring_enabled=True
        )
        monitor.process_snapshot(snapshot)
        await hass.async_block_till_done()

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert entry["last_current_a"] == 30.0
        assert entry["utilization_pct"] == 150.0
        assert entry["over_threshold_since"] is not None
        assert datetime.fromisoformat(entry["over_threshold_since"]) > datetime.now(
            UTC
        ) - timedelta(minutes=1)
        assert [a.data["alert_type"] for a in alerts] == []

    async def test_a_disabled_circuit_keeps_its_alert_history(self, hass: HomeAssistant) -> None:
        """Last alerts are history, not a judgement: turning monitoring off leaves them."""
        snapshot = _panel(_circuit("circuit-2", 20.0, 30.0))
        monitor, _manager = await self._services(hass, snapshot)
        _run_past_the_window(monitor, snapshot, "circuit-2")
        await hass.async_block_till_done()
        shown = monitor.get_monitoring_status()["circuits"]["circuit-2"]["last_continuous_alert"]
        assert shown is not None

        await self._set(
            hass, "set_circuit_threshold", {"circuit_id": "circuit-2"}, monitoring_enabled=False
        )

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert entry["last_continuous_alert"] == shown

    async def test_a_shared_group_turned_off_reports_no_judgement(
        self, hass: HomeAssistant
    ) -> None:
        """Turning off either member turns off the group, and both rows read null."""
        snapshot = _sharing_panel(
            _shared(_circuit("circuit-52", 15.0, 40.0), "circuit-53", 44),
            _shared(_circuit("circuit-53", 15.0, 40.0), "circuit-52", 44),
        )
        monitor, _manager = await self._services(hass, snapshot)
        _run_past_the_window(monitor, snapshot, "circuit-52")

        await self._set(
            hass, "set_circuit_threshold", {"circuit_id": "circuit-53"}, monitoring_enabled=False
        )
        monitor.process_snapshot(snapshot)

        status = monitor.get_monitoring_status()["circuits"]
        for member in ("circuit-52", "circuit-53"):
            assert status[member]["monitoring_enabled"] is False
            assert status[member]["utilization_pct"] is None
            assert status[member]["last_current_a"] is None
            assert status[member]["over_threshold_since"] is None

        await self._set(
            hass, "set_circuit_threshold", {"circuit_id": "circuit-53"}, monitoring_enabled=True
        )
        monitor.process_snapshot(snapshot)
        status = monitor.get_monitoring_status()["circuits"]
        for member in ("circuit-52", "circuit-53"):
            assert status[member]["last_current_a"] == 40.0
            assert status[member]["utilization_pct"] is not None

    async def test_the_mains_turned_off_reports_no_utilization_or_current(
        self, hass: HomeAssistant
    ) -> None:
        """The mains entry nulls its figures while off and resumes when back on."""
        snapshot = replace(_panel(_circuit("circuit-2", 20.0, 10.0)), upstream_l1_current_a=60.0)
        monitor, _manager = await self._services(hass, snapshot)
        monitor.process_snapshot(snapshot)
        (before,) = monitor.get_monitoring_status()["mains"].values()
        assert before["utilization_pct"] == 60.0

        await self._set(
            hass, "set_mains_threshold", {"leg": "upstream_l1"}, monitoring_enabled=False
        )
        monitor.process_snapshot(snapshot)

        (entry,) = monitor.get_monitoring_status()["mains"].values()
        assert entry["monitoring_enabled"] is False
        assert entry["utilization_pct"] is None
        assert entry["last_current_a"] is None
        assert entry["over_threshold_since"] is None

        await self._set(
            hass, "set_mains_threshold", {"leg": "upstream_l1"}, monitoring_enabled=True
        )
        monitor.process_snapshot(snapshot)
        (entry,) = monitor.get_monitoring_status()["mains"].values()
        assert entry["last_current_a"] == 60.0
        assert entry["utilization_pct"] == 60.0

    async def test_the_report_does_not_depend_on_the_state_having_been_reset(
        self, hass: HomeAssistant
    ) -> None:
        """An override that arrives without passing the set service, with a figure already held, still reports null."""
        snapshot = replace(_panel(_circuit("circuit-2", 20.0, 30.0)), upstream_l2_current_a=60.0)
        monitor, _manager = await self._services(hass, snapshot)
        _run_past_the_window(monitor, snapshot, "circuit-2")
        await monitor._store.async_save(
            {
                "enabled": True,
                "circuit_overrides": {"circuit-2": {"monitoring_enabled": False}},
                "mains_overrides": {"upstream_l1": {"monitoring_enabled": False}},
            }
        )

        await monitor.async_load_overrides()

        entry = monitor.get_monitoring_status()["circuits"]["circuit-2"]
        assert entry["utilization_pct"] is None
        assert entry["last_current_a"] is None
        assert entry["over_threshold_since"] is None
        (mains,) = monitor.get_monitoring_status()["mains"].values()
        assert mains["utilization_pct"] is None
        assert mains["last_current_a"] is None

    async def test_the_mains_back_on_resumes_with_no_stale_window(
        self, hass: HomeAssistant
    ) -> None:
        """A mains window opened before monitoring was turned off does not alert on the first sample after."""
        snapshot = replace(_panel(_circuit("circuit-2", 20.0, 10.0)), upstream_l1_current_a=90.0)
        monitor, _manager = await self._services(hass, snapshot)
        alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)
        monitor.process_snapshot(snapshot)
        leg = monitor.get_mains_state("upstream_l1")
        assert leg is not None and leg.over_threshold_since is not None
        leg.over_threshold_since = datetime.now(UTC) - timedelta(minutes=16)

        await self._set(
            hass, "set_mains_threshold", {"leg": "upstream_l1"}, monitoring_enabled=False
        )
        await self._set(
            hass, "set_mains_threshold", {"leg": "upstream_l1"}, monitoring_enabled=True
        )
        monitor.process_snapshot(snapshot)
        await hass.async_block_till_done()

        assert [a.data["alert_type"] for a in alerts] == []
