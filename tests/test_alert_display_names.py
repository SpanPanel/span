"""A current alert's text names the circuit as Home Assistant shows it; its event keeps the panel's name.

A user who renames a circuit's device in Home Assistant finds it under that name
everywhere Home Assistant shows it, so a notification naming the circuit by the
panel's name names one they cannot find. The notification's title and message,
through `{name}`, carry the device's name as Home Assistant shows it
(`util.circuit_display_name`), read when the alert fires, so a rename made since
setup reaches the next alert. Their `{entity_id}` is the entity ID of the
point's Current sensor, as the documentation has always said.

The `span_panel_current_alert` event is read by automations, so its
`alert_name` stays the panel's name and its `alert_id` the circuit's id, and
`alert_display_name` carries the name the text uses beside them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events, async_mock_service
from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.alert_dispatcher import dispatch_test_alert
from custom_components.span_panel.const import DOMAIN, EVENT_CURRENT_ALERT
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.id_builder import build_circuit_unique_id, build_panel_unique_id
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    NOTIFICATION_MESSAGE_TEMPLATE,
    NOTIFICATION_TITLE_TEMPLATE,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from custom_components.span_panel.package_rating_manager import PackageRatingManager

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .helpers import register_circuit_device, register_panel_device

SERIAL: Final = "sp3-display-001"
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}
_NOTIFY_SERVICE: Final = "synthetic_phone"
_TITLE: Final = "SPAN: {name} {alert_type}"
_MESSAGE: Final = "{name} at {current_a}A"


def _circuit(
    circuit_id: str,
    name: str,
    tab: int,
    *,
    current_a: float = 25.0,
    device_type: str = "circuit",
) -> SpanCircuitSnapshot:
    """Return a 20 A circuit drawing `current_a`, over its rating by default, so it spikes."""
    return SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=name,
        tabs=[tab],
        breaker_rating_a=20.0,
        current_a=current_a,
        device_type=device_type,
    )


def _panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    """Return a panel whose mains draw too little to alert, so every alert is a circuit's."""
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL,
        circuits={c.circuit_id: c for c in circuits},
        main_breaker_rating_a=200,
        upstream_l1_current_a=1.0,
        upstream_l2_current_a=1.0,
        downstream_l1_current_a=1.0,
        downstream_l2_current_a=1.0,
    )


class _Panel:
    """One panel's entry, its registered panel device and its monitor."""

    def __init__(
        self, hass: HomeAssistant, entry: MockConfigEntry, monitor: CurrentMonitor
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.monitor = monitor
        self.panel_device_id = register_panel_device(hass, entry, SERIAL)

    def register(self, circuit: SpanCircuitSnapshot) -> dr.ChildDeviceEntry:
        """Register the circuit's own device, as its first entity would."""
        return register_circuit_device(
            self.hass, self.entry, SERIAL, circuit, panel_device_id=self.panel_device_id
        )

    def rename(self, device: dr.ChildDeviceEntry, name: str) -> None:
        """Rename the device in Home Assistant, as a user does on its page."""
        dr.async_get(self.hass).async_update_child_device(device.id, name_by_user=name)

    def sensor(self, unique_id: str, object_id: str) -> str:
        """Register one of the panel's sensors under `unique_id`, as its platform would, and return its entity ID."""
        return (
            er.async_get(self.hass)
            .async_get_or_create(
                "sensor", DOMAIN, unique_id, config_entry=self.entry, suggested_object_id=object_id
            )
            .entity_id
        )


async def _set_up(hass: HomeAssistant, snapshot: SpanPanelSnapshot, **options: object) -> _Panel:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        unique_id=SERIAL,
        options={
            SPIKE_THRESHOLD_PCT: 100,
            NOTIFY_TARGETS: f"event_bus, notify.{_NOTIFY_SERVICE}",
            NOTIFICATION_TITLE_TEMPLATE: _TITLE,
            NOTIFICATION_MESSAGE_TEMPLATE: _MESSAGE,
            **options,
        },
    )
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    return _Panel(hass, entry, CurrentMonitor(hass, entry, manager, snapshot))


async def _judge(panel: _Panel, snapshot: SpanPanelSnapshot) -> None:
    panel.monitor.process_snapshot(snapshot)
    await panel.hass.async_block_till_done()


def _texts(calls: list[ServiceCall]) -> list[tuple[object, object]]:
    return [(call.data["title"], call.data["message"]) for call in calls]


def _names(events: list[Event]) -> list[tuple[object, object]]:
    return [(event.data["alert_name"], event.data["alert_display_name"]) for event in events]


@pytest.fixture
def notified(hass: HomeAssistant) -> list[ServiceCall]:
    """Every call to the notify target the panel's alerts are sent to."""
    return async_mock_service(hass, "notify", _NOTIFY_SERVICE)


@pytest.fixture
def alerts(hass: HomeAssistant) -> list[Event]:
    """Every `span_panel_current_alert` event fired."""
    return async_capture_events(hass, EVENT_CURRENT_ALERT)


async def test_a_renamed_circuits_notification_carries_the_rename(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """The title and message name the circuit by the user's name, and the event's `alert_name` stays the panel's."""
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot)
    panel.rename(panel.register(circuit), "Synthetic Workshop")

    await _judge(panel, snapshot)

    assert _texts(notified) == [("SPAN: Synthetic Workshop spike", "Synthetic Workshop at 25.0A")]
    assert _names(alerts) == [("Panel Name Seven", "Synthetic Workshop")]


async def test_a_rename_after_setup_reaches_the_next_alert(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """The name is read when the alert fires, not when the monitor is built."""
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot)
    device = panel.register(circuit)
    await _judge(panel, snapshot)

    panel.rename(device, "Synthetic Workshop")
    state = panel.monitor.get_circuit_state("circuit-7")
    assert state is not None
    # Past the cooldown, so the same spike alerts again.
    state.last_spike_alert = datetime.now(UTC) - timedelta(hours=1)
    await _judge(panel, snapshot)

    assert _texts(notified) == [
        ("SPAN: Panel Name Seven spike", "Panel Name Seven at 25.0A"),
        ("SPAN: Synthetic Workshop spike", "Synthetic Workshop at 25.0A"),
    ]
    assert _names(alerts) == [
        ("Panel Name Seven", "Panel Name Seven"),
        ("Panel Name Seven", "Synthetic Workshop"),
    ]


async def test_an_unrenamed_circuit_is_named_as_before(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """A circuit nobody renamed is named by the panel's name, in its text and in both event fields."""
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot)
    panel.register(circuit)

    await _judge(panel, snapshot)

    assert _texts(notified) == [("SPAN: Panel Name Seven spike", "Panel Name Seven at 25.0A")]
    assert _names(alerts) == [("Panel Name Seven", "Panel Name Seven")]


async def test_a_circuit_with_no_device_is_named_as_its_device_would_be(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """A circuit with no device takes the name `circuit_device_info` would give it (`circuit_device_name`).

    A charger's feed circuit has none where it has neither a breaker switch nor
    a priority select, and on the first setup after an upgrade from a release
    before circuits had devices, the monitor judges pushes before they are
    registered.
    """
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot)

    await _judge(panel, snapshot)

    assert _texts(notified) == [("SPAN: Panel Name Seven spike", "Panel Name Seven at 25.0A")]
    assert _names(alerts) == [("Panel Name Seven", "Panel Name Seven")]


async def test_a_shared_meter_is_named_by_each_members_name_as_home_assistant_shows_it(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """A shared meter's alert joins its members' names, each the user's where they renamed it.

    Two 20 A circuits at one space carry 40 A together, so both report 45 A.
    """
    first = replace(
        _circuit("circuit-52", "Panel Name A", 44, current_a=45.0),
        meter_shared_with=("circuit-53",),
        relay_shared_with=("circuit-53",),
    )
    second = replace(
        _circuit("circuit-53", "Panel Name B", 44, current_a=45.0),
        meter_shared_with=("circuit-52",),
        relay_shared_with=("circuit-52",),
    )
    snapshot = replace(_panel(first, second), publishes_shared_meters=True)
    panel = await _set_up(hass, snapshot)
    panel.rename(panel.register(first), "Synthetic Fan")
    panel.register(second)

    await _judge(panel, snapshot)

    assert _texts(notified) == [
        ("SPAN: Synthetic Fan + Panel Name B spike", "Synthetic Fan + Panel Name B at 45.0A")
    ]
    assert _names(alerts) == [("Panel Name A + Panel Name B", "Synthetic Fan + Panel Name B")]


async def test_the_mains_is_named_by_its_own_name(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """The mains has no device of its own: its text and both event fields carry the leg's name."""
    snapshot = replace(_panel(), upstream_l1_current_a=250.0)
    panel = await _set_up(hass, snapshot)

    await _judge(panel, snapshot)

    assert _texts(notified) == [("SPAN: Mains Upstream L1 spike", "Mains Upstream L1 at 250.0A")]
    assert _names(alerts) == [("Mains Upstream L1", "Mains Upstream L1")]


async def test_the_test_event_carries_the_display_name_too(
    hass: HomeAssistant, alerts: list[Event]
) -> None:
    """An automation written against the test event finds `alert_display_name` beside `alert_name`."""
    dispatch_test_alert(hass, {NOTIFY_TARGETS: "event_bus"})
    await hass.async_block_till_done()

    assert _names(alerts) == [("Kitchen Oven", "Kitchen Oven")]


async def test_an_unnamed_solar_circuit_with_no_device_is_named_solar(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """Its device would be named "Solar", so its text is, while `alert_name` keeps its positions.

    The name its device would be given, not the label a message used before:
    once the device is registered, Home Assistant shows "Solar".
    """
    circuit = _circuit("circuit-7", "", 7, device_type="pv")
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot)

    await _judge(panel, snapshot)

    assert _texts(notified) == [("SPAN: Solar spike", "Solar at 25.0A")]
    assert _names(alerts) == [("Circuit 7", "Solar")]


async def test_a_continuous_overload_names_the_circuit_as_home_assistant_shows_it(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """The sustained alert's text carries the rename, as the spike's does."""
    circuit = _circuit("circuit-7", "Panel Name Seven", 7, current_a=18.0)
    snapshot = _panel(circuit)
    panel = await _set_up(
        hass,
        snapshot,
        **{SPIKE_THRESHOLD_PCT: 500, CONTINUOUS_THRESHOLD_PCT: 80, WINDOW_DURATION_M: 15},
    )
    panel.rename(panel.register(circuit), "Synthetic Workshop")
    await _judge(panel, snapshot)
    state = panel.monitor.get_circuit_state("circuit-7")
    assert state is not None
    # Over the threshold for longer than the window.
    state.over_threshold_since = datetime.now(UTC) - timedelta(minutes=16)

    await _judge(panel, snapshot)

    assert _texts(notified) == [
        ("SPAN: Synthetic Workshop continuous_overload", "Synthetic Workshop at 18.0A")
    ]
    assert _names(alerts) == [("Panel Name Seven", "Synthetic Workshop")]


async def test_a_circuit_renamed_on_the_panel_keeps_the_name_home_assistant_shows_until_its_reload(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """Between a rename on the panel and the reload that renames its device, Home Assistant shows the old name.

    So the text carries the name the device is registered under, not the one it
    is about to be given, which `effective_circuit_device_name` answers for an
    entity id. The event's `alert_name` is the panel's, so it already carries the
    new one.
    """
    registered = _circuit("circuit-7", "Old Panel Name", 7)
    snapshot = _panel(replace(registered, name="New Panel Name"))
    panel = await _set_up(hass, snapshot)
    panel.register(registered)

    await _judge(panel, snapshot)

    assert _texts(notified) == [("SPAN: Old Panel Name spike", "Old Panel Name at 25.0A")]
    assert _names(alerts) == [("New Panel Name", "Old Panel Name")]


@pytest.mark.usefixtures("notified")
async def test_the_monitoring_status_keeps_the_panels_name_after_a_rename(
    hass: HomeAssistant,
) -> None:
    """Scripts read the status's `name`, so a rename in Home Assistant leaves it the panel's."""
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot)
    panel.rename(panel.register(circuit), "Synthetic Workshop")

    await _judge(panel, snapshot)

    assert [c["name"] for c in panel.monitor.get_monitoring_status()["circuits"].values()] == [
        "Panel Name Seven"
    ]


_ENTITY_TITLE: Final = {NOTIFICATION_TITLE_TEMPLATE: "{entity_id}"}


def _titles(calls: list[ServiceCall]) -> list[object]:
    return [call.data["title"] for call in calls]


def _alert_ids(events: list[Event]) -> list[object]:
    return [event.data["alert_id"] for event in events]


async def test_entity_id_is_the_circuits_current_sensor(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """`{entity_id}` carries the circuit's Current sensor, while the event's `alert_id` stays the circuit's id."""
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot, **_ENTITY_TITLE)
    panel.sensor(build_circuit_unique_id(SERIAL, "circuit-7", "instantPowerW"), "seven_power")
    current = panel.sensor(build_circuit_unique_id(SERIAL, "circuit-7", "current"), "seven_current")

    await _judge(panel, snapshot)

    assert _titles(notified) == [current]
    assert current == "sensor.seven_current"
    assert _alert_ids(alerts) == ["circuit-7"]


async def test_entity_id_is_the_power_sensor_of_a_circuit_without_a_current_sensor(
    hass: HomeAssistant, notified: list[ServiceCall]
) -> None:
    """A circuit whose current arrived after setup has no Current sensor yet, so its Power sensor stands in.

    The Monitoring tab shows the circuit under the same entity.
    """
    circuit = _circuit("circuit-7", "Panel Name Seven", 7)
    snapshot = _panel(circuit)
    panel = await _set_up(hass, snapshot, **_ENTITY_TITLE)
    power = panel.sensor(
        build_circuit_unique_id(SERIAL, "circuit-7", "instantPowerW"), "seven_power"
    )

    await _judge(panel, snapshot)

    assert _titles(notified) == [power]


async def test_entity_id_of_a_shared_meter_is_the_sensor_of_the_circuit_alert_id_names(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """A shared meter is judged under its first member's id, and `{entity_id}` is that member's sensor."""
    first = replace(
        _circuit("circuit-52", "Panel Name A", 44, current_a=45.0),
        meter_shared_with=("circuit-53",),
        relay_shared_with=("circuit-53",),
    )
    second = replace(
        _circuit("circuit-53", "Panel Name B", 44, current_a=45.0),
        meter_shared_with=("circuit-52",),
        relay_shared_with=("circuit-52",),
    )
    snapshot = replace(_panel(first, second), publishes_shared_meters=True)
    panel = await _set_up(hass, snapshot, **_ENTITY_TITLE)
    current = panel.sensor(build_circuit_unique_id(SERIAL, "circuit-52", "current"), "a_current")
    panel.sensor(build_circuit_unique_id(SERIAL, "circuit-53", "current"), "b_current")

    await _judge(panel, snapshot)

    assert _titles(notified) == [current]
    assert _alert_ids(alerts) == ["circuit-52"]


async def test_entity_id_of_the_mains_is_the_legs_current_sensor(
    hass: HomeAssistant, notified: list[ServiceCall], alerts: list[Event]
) -> None:
    """The mains leg's Current sensor, while the event's `alert_id` stays the leg."""
    snapshot = replace(_panel(), upstream_l1_current_a=250.0)
    panel = await _set_up(hass, snapshot, **_ENTITY_TITLE)
    leg = panel.sensor(build_panel_unique_id(SERIAL, "upstream_l1_current"), "upstream_l1_current")

    await _judge(panel, snapshot)

    assert _titles(notified) == [leg]
    assert _alert_ids(alerts) == ["upstream_l1"]
