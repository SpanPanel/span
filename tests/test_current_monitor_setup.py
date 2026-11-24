"""Current monitoring across setup and the first update, as Home Assistant runs them.

Setup builds the monitor holding the coordinator's snapshot. Holding is not
judging: building the monitor raises no alert, sends no notification and opens
no over-threshold window. The first judgement is the first update's, which in
a setup that goes well is the refresh the sensor platform asks for before the
entry is loaded, and it alerts exactly as it does for a monitor that held
nothing. What the held snapshot changes is what the status reports until that
judgement: the panel's circuits, each with nothing judged yet, where it listed
none.

The alerting test reads nothing of how the monitor is built, only when, so it
runs as written against a monitor built holding nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Final
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import async_capture_events, async_mock_service
from span_panel_api import SpanPanelSnapshot
from span_panel_api.exceptions import SpanPanelStaleDataError

from custom_components.span_panel.const import EVENT_CURRENT_ALERT
from custom_components.span_panel.current_monitor import CurrentMonitor, MonitoredPointState
from custom_components.span_panel.options import NOTIFY_TARGETS

from .captures_replay import ReplayClient, RetainedTree, capture, snapshot
from .replayed_panel import MONITORED_OPTIONS, Panel, replayed_panels

_NOTIFY_SERVICE: Final = "synthetic_phone"
"""A notify service standing in for a user's phone."""

_OPTIONS: Final = {**MONITORED_OPTIONS, NOTIFY_TARGETS: f"event_bus, notify.{_NOTIFY_SERVICE}"}
"""Monitoring on, alerting through both channels: the event bus and a notify service."""

_BETWEEN: Final = timedelta(seconds=30)
"""Time let pass after the monitor is built, and again before the first push."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


def _overloaded() -> tuple[RetainedTree, str, float]:
    """Return a capture whose one circuit draws twice its rating, that circuit, and the rating.

    Twice the rating is over the default spike threshold, which alerts at once,
    and over the continuous threshold, which opens the window it alerts after.
    """
    tree = capture("main32_r202639").tree()
    circuit_id, circuit = next(
        (cid, c)
        for cid, c in sorted(snapshot(tree).circuits.items())
        if c.tabs
        and c.breaker_rating_a
        and not c.measures_outside_panel
        and not c.meter_shared_with
    )
    rating = float(circuit.breaker_rating_a or 0)
    tree[circuit_id]["meter/current"] = str(2 * rating)
    return tree, circuit_id, rating


def _monitor(panel: Panel) -> CurrentMonitor:
    monitor = panel.runtime.coordinator.current_monitor
    assert monitor is not None, "monitoring is not running"
    return monitor


async def _settled(panels: Callable[..., Panel], tree: RetainedTree) -> Panel:
    """Return a panel installed, settled and unloaded, so its next setup is a restart's.

    A fresh install's first update renames the circuit devices and reloads the
    entry, which builds a second monitor; a restart's first update reloads
    nothing, so the monitor it judges with is the one setup built.
    """
    panel = panels(tree, options=_OPTIONS)
    await panel.set_up(tree)
    await panel.unload()
    return panel


async def test_building_the_monitor_alerts_nothing_and_the_first_update_alerts_as_before(
    hass: HomeAssistant, panels: Callable[..., Panel], freezer: FrozenDateTimeFactory
) -> None:
    """Nothing once the monitor is built; one spike, notification and window at the first update.

    The sample is read once the monitor is built and started, and the clock
    then moves on, so a judgement made in building or starting it would show in
    the sample and carry the earlier time. The first push then lands inside the
    spike's cooldown and the open window, and adds nothing.
    """
    tree, circuit_id, _rating = _overloaded()
    notifications: list[ServiceCall] = async_mock_service(hass, "notify", _NOTIFY_SERVICE)
    panel = await _settled(panels, tree)
    notifications.clear()
    alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)
    built: list[tuple[datetime, MonitoredPointState | None, int, int]] = []
    start = CurrentMonitor.async_start

    async def started(monitor: CurrentMonitor) -> None:
        await start(monitor)
        built.append(
            (
                dt_util.utcnow(),
                monitor.get_circuit_state(circuit_id),
                len(alerts),
                len(notifications),
            )
        )
        freezer.tick(_BETWEEN)

    with patch.object(CurrentMonitor, "async_start", started):
        assert await hass.config_entries.async_setup(panel.entry.entry_id) is True
        await hass.async_block_till_done()

    ((built_at, state_when_built, alerts_when_built, notifications_when_built),) = built
    assert (state_when_built, alerts_when_built, notifications_when_built) == (None, 0, 0)
    first_judged = built_at + _BETWEEN
    assert [(event.data["alert_id"], event.data["alert_type"]) for event in alerts] == [
        (circuit_id, "spike")
    ]
    assert len(notifications) == 1
    monitor = _monitor(panel)
    state = monitor.get_circuit_state(circuit_id)
    assert state is not None
    assert state.last_spike_alert == first_judged
    assert state.over_threshold_since == first_judged

    freezer.tick(_BETWEEN)
    await panel.push(tree)

    assert _monitor(panel) is monitor
    assert len(alerts) == 1
    assert len(notifications) == 1
    assert state.over_threshold_since == first_judged
    assert state.last_continuous_alert is None


async def test_until_the_first_judgement_the_status_lists_the_circuits_unjudged(
    hass: HomeAssistant, panels: Callable[..., Panel]
) -> None:
    """A setup whose own refresh finds the panel not live leaves the monitor holding, unjudged.

    The status then lists every circuit the panel publishes, each with its
    rating and nothing judged, and the mains with its rating and no reading,
    until the first push judges them.
    """
    tree, circuit_id, rating = _overloaded()
    async_mock_service(hass, "notify", _NOTIFY_SERVICE)
    panel = await _settled(panels, tree)
    alerts = async_capture_events(hass, EVENT_CURRENT_ALERT)
    start = CurrentMonitor.async_start
    read = ReplayClient.get_snapshot
    built = False

    async def start_once_built(monitor: CurrentMonitor) -> None:
        nonlocal built
        await start(monitor)
        built = True

    async def live_until_built(client: ReplayClient) -> SpanPanelSnapshot:
        if built:
            raise SpanPanelStaleDataError("MQTT broker disconnected")
        return await read(client)

    with (
        patch.object(CurrentMonitor, "async_start", start_once_built),
        patch.object(ReplayClient, "get_snapshot", live_until_built),
    ):
        assert await hass.config_entries.async_setup(panel.entry.entry_id) is True
        await hass.async_block_till_done()

    monitor = _monitor(panel)
    status = monitor.get_monitoring_status()
    assert alerts == []
    assert {row["point_id"] for row in status["circuits"].values()} == {
        cid
        for cid, circuit in panel.snapshot.circuits.items()
        if not circuit.measures_outside_panel
    }
    before = panel.monitoring(circuit_id)
    assert before["monitoring_rating_a"] == rating
    assert before["monitoring_enabled"] is True
    assert before["last_current_a"] is None
    assert before["utilization_pct"] is None
    assert before["over_threshold_since"] is None
    assert before["last_spike_alert"] is None
    (mains,) = status["mains"].values()
    assert mains["breaker_rating_a"] is not None
    assert mains["last_current_a"] is None
    assert mains["utilization_pct"] is None

    await panel.push(tree)

    assert _monitor(panel) is monitor
    after = panel.monitoring(circuit_id)
    assert after["last_current_a"] == 2 * rating
    assert after["utilization_pct"] == 200.0
    assert [(event.data["alert_id"], event.data["alert_type"]) for event in alerts] == [
        (circuit_id, "spike")
    ]
