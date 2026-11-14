"""What the BESS reports about itself, surfaced as two sensors on its own device.

`meter/active-power` and `status/communication-state` were declared, published
and read by nobody. They are the battery's own view of its power and its own view
of its link, as opposed to the enclosure's view of both.

Every assertion runs against a real snapshot built by the real schema_1 adapter
over the reference capture, and every expected value is read out of that capture
rather than written as a literal — a test that pins the same constant the code
pins passes whether or not the wire is ever read. Each reading is proved by
republishing it, deleting it, or dropping the node that carries it.

**The sign is the hard part, and it is what most of this module is about.** The
capture is of a *charging* battery, and the BESS's own `meter/active-power` has
been published in two frames. Firmware through r202633 publishes it
charge-positive, the way the enclosure meters a circuit it feeds, positive when
power flows *into* the thing being metered; from r202639 it is discharge-positive,
the negation of `power-flows/battery`. The library brings either into the
snapshot discharge-positive, negating the old frame and passing the new one
through, and `battery_power` negates the enclosure's `power-flows/battery`, which
kept its sign across the change. So both sensors land on the same convention in
the UI, reading negative for this charging battery. A sensor whose sign
contradicted the one beside it would be worse than no sensor, so the agreement is
asserted directly rather than inferred from the definitions.

**The agreement is asserted in both frames; the direction is inherited from the
fixture.** The capture is in r202639's frame: `span-panel-api` recaptured the
reference tree with `ebus-panel-sim` 0.9.0, which publishes the BESS meter as the
flow's negation. The tests that read the capture unmodified are scoped to that
frame. The old frame is no longer in any capture the library ships, so the tests
about it republish the capture the way r202633 publishes it: a version string the
library reads as r202633, and the meter in the old frame (`_in_the_old_frame`),
named as such. Neither negation the integration owns changed:
`BATTERY_POWER_SENSOR` is the single negation it has been since 2.0.8, and what a
real panel displays depends on what that panel publishes, not on this fixture.

Which way *is* charging is read off the reference capture, and the capture now
carries the post-`distribution-enclosure-simulator#39` frame: `power-flows` 0.3
defines the node balance as every term positive when power flows into the thing
it names, so `pv` is negative while producing and the four terms sum to zero.
The capture this module was first written against predated that fix and satisfied
`grid + pv + battery == site` instead, which is why these assertions read the
other way until span-panel-api re-captured the reference tree. The library pins
the same four values and states the same frame (`test_schema_one_panel.py`,
`test_power_flows`). Refresh the fixture and revisit these assertions together;
do not change one without the other.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.field_paths import RESIDUAL_EXEMPT_PATHS, declared_field_paths
from custom_components.span_panel.helpers import detect_capabilities, has_bess_telemetry
from custom_components.span_panel.sensor import create_battery_sensors
from custom_components.span_panel.sensor_definitions import (
    BATTERY_POWER_SENSOR,
    BESS_TELEMETRY_SENSORS,
)
from custom_components.span_panel.sensor_panel import (
    SpanBessMetadataSensor,
    SpanPanelBattery,
    SpanPanelPowerSensor,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, UnitOfPower
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.typing import StateType

from .adapter_fixtures import SCHEMA_ONE_PANEL, schema_one_snapshot, schema_one_tree
from .factories import SpanPanelSnapshotFactory, pv_binding_for

from pytest_homeassistant_custom_component.common import MockConfigEntry

BESS = "bess"

POWER_TOPIC = "meter/active-power"
COMMS_TOPIC = "status/communication-state"
ENCLOSURE_FLOW_TOPIC = "power-flows/battery"

POWER_KEY = "meter_power"
COMMS_KEY = "communication_state"
ENCLOSURE_FLOW_KEY = BATTERY_POWER_SENSOR.key

R202633_FIRMWARE = "spanos2/r202633/01"
"""A release that publishes the BESS meter charge-positive, the old frame."""

R202639_FIRMWARE = "spanos2/r202639/03"
"""The first release that publishes the BESS meter discharge-positive, the capture's frame."""


def _in_the_old_frame(raw: float) -> float:
    """A BESS meter reading as a release before r202639 publishes the same instant.

    The capture is in r202639's frame, which negated the BESS meter and left
    `power-flows/battery` alone, so the old frame's reading of the same battery
    is the negation. Named rather than written as a bare minus sign so a test
    that builds the old frame says that it does.
    """
    return -raw


@pytest.fixture(autouse=True)
def _mock_entity_registry() -> Any:
    """Patch entity registry lookups used during sensor construction."""
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch(
        "custom_components.span_panel.sensor_base.er.async_get",
        return_value=registry,
    ):
        yield registry


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    """A coordinator-like mock carrying one snapshot."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = MagicMock()
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    return coordinator


def _published(device_id: str, topic: str) -> str:
    """What the capture publishes on one topic, or fail saying it does not."""
    value = schema_one_tree()[device_id].get(topic)
    assert value is not None, f"{device_id} publishes no {topic} in the capture"
    return value


def _republishing(**topics: str) -> SpanPanelSnapshot:
    """A snapshot from the capture with some BESS topics rewritten."""
    tree = schema_one_tree()
    for name, value in topics.items():
        tree[BESS][name.replace("__", "/").replace("_", "-")] = value
    return schema_one_snapshot(tree)


def _without(topic: str) -> SpanPanelSnapshot:
    """A snapshot from a BESS that stopped publishing (and declaring) a property."""
    tree = schema_one_tree()
    node, _, property_id = topic.partition("/")
    description = json.loads(tree[BESS]["$description"])
    del tree[BESS][topic]
    del description["nodes"][node]["properties"][property_id]
    tree[BESS]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def _without_node(node: str) -> SpanPanelSnapshot:
    """A snapshot from a BESS with no such capability node at all."""
    tree = schema_one_tree()
    for topic in [t for t in tree[BESS] if t.startswith(f"{node}/")]:
        del tree[BESS][topic]
    description = json.loads(tree[BESS]["$description"])
    del description["nodes"][node]
    tree[BESS]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def _without_bess() -> SpanPanelSnapshot:
    """A snapshot from a capture with no BESS device in the tree at all."""
    tree = {device: topics for device, topics in schema_one_tree().items() if device != BESS}
    return schema_one_snapshot(tree)


BessSensor = SpanPanelBattery | SpanPanelPowerSensor | SpanBessMetadataSensor
"""What `create_battery_sensors` returns: everything on the BESS sub-device."""


def _sensors(snapshot: SpanPanelSnapshot) -> dict[str, BessSensor]:
    """Whatever the platform creates for this snapshot, keyed by description key."""
    created = create_battery_sensors(_coordinator(snapshot), snapshot)
    return {sensor.entity_description.key: sensor for sensor in created}


def _state(snapshot: SpanPanelSnapshot, key: str) -> StateType | date | datetime | Decimal:
    """The state one BESS sensor reports for a snapshot.

    Typed as `SensorEntity.native_value` is, rather than narrowed to what these
    two sensors happen to report: narrowing here would be the test asserting its
    own expectation twice, once in the annotation and once in the body.
    """
    sensor = _sensors(snapshot)[key]
    sensor._update_native_value()
    return sensor.native_value


# ---------------------------------------------------------------------------
# The premise: the capture publishes both properties, on a charging battery
# ---------------------------------------------------------------------------


def test_the_capture_publishes_both_properties() -> None:
    """Guard the premise for every test below, all of which read the capture for
    their expected value: a capture that stopped publishing one would make them
    vacuously true rather than failing."""
    assert _published(BESS, POWER_TOPIC)
    assert _published(BESS, COMMS_TOPIC)


def test_the_capture_is_a_charging_battery() -> None:
    """The premise of every sign assertion below, derived rather than assumed.

    A sign convention can only be tested against a known physical state, and
    "negative means charging" is the very claim under test, so reading the state
    off the sign would be circular. The enclosure's four power flows balance
    instead — `pv + battery + grid == site`, with `grid` positive when importing —
    and solving that identity says which way the battery is going without
    appealing to any convention this codebase chose.

    The capture: 8500 W of PV meets 2653 W of site load and exports 2347 W, and
    the 3500 W left over is going into the battery. So this battery is charging,
    and the enclosure publishes that as a *positive* number.

    `power-flows` 0.3 defines the balance as a node balance — every term positive
    when power flows into the thing it names — so the four sum to zero rather than
    three of them summing to the fourth, and PV reads negative while producing.
    Asserting the sum against zero rather than against `site` is what makes this a
    statement about the frame and not just about four numbers.

    Were the capture ever retaken with the battery discharging, this fails first
    and says so, rather than the sign tests failing and reading as a wiring bug.
    """
    flows = {
        name: float(_published(SCHEMA_ONE_PANEL, f"power-flows/{name}"))
        for name in ("pv", "battery", "grid", "site")
    }

    assert sum(flows.values()) == pytest.approx(0.0, abs=1e-6)
    # PV alone exceeds the site load, so the surplus has nowhere to go but the
    # battery and the grid — and the grid term is an export, which in this frame
    # is power flowing into the grid and therefore positive.
    assert -flows["pv"] > flows["site"]
    assert flows["grid"] > 0
    assert flows["battery"] > 0


def test_the_capture_publishes_the_bess_meter_opposite_the_enclosure_flow() -> None:
    """The frame every test reading the capture unmodified is scoped to.

    r202639 publishes the BESS meter as the negation of `power-flows/battery`,
    and the capture carries no version string the library reads a release
    from, so the opposed signs are what select that frame: the meter passes
    through and only `battery_power` negates. Pinned here rather than assumed,
    because a capture retaken in the old frame would turn the capture-reading
    sign tests into old-frame tests without any of them failing for that
    reason.

    The old frame built from the capture is checked against the same flow,
    because the tests about that frame are only about it if the built meter
    carries the flow's sign, which is what r202633 publishes.
    """
    bess_meter = float(_published(BESS, POWER_TOPIC))
    enclosure_flow = float(_published(SCHEMA_ONE_PANEL, ENCLOSURE_FLOW_TOPIC))

    assert bess_meter != 0 and enclosure_flow != 0
    assert (bess_meter > 0) != (enclosure_flow > 0)
    assert (_in_the_old_frame(bess_meter) > 0) == (enclosure_flow > 0)


# ---------------------------------------------------------------------------
# The sign, and the agreement with the sensor beside it
# ---------------------------------------------------------------------------


def test_charging_reads_negative() -> None:
    """The convention, asserted on the state a user sees rather than on the field.

    From r202639 the wire is discharge-positive, so the capture publishes this
    charging battery negative and the snapshot passes it through: the sensor is
    discharge-positive and a charging battery reads negative. The library pins
    the same relation on the same capture in `test_schema_one_devices.py`:
    `test_the_captured_tree_resolves_to_the_new_frame_through_the_snapshot`
    (`battery.power_w == raw`, and `< 0`).

    The value and the sign are asserted separately on purpose: the value pins
    that nothing negates a meter already in the sensor's frame, the sign pins
    the convention a user reads whichever frame the wire was in.
    """
    published = float(_published(BESS, POWER_TOPIC))
    state = _state(schema_one_snapshot(), POWER_KEY)

    assert state == published
    assert isinstance(state, float) and state < 0


def test_charging_reads_negative_in_the_old_frame() -> None:
    """The same charging battery, published the way firmware through r202633 does.

    That wire is charge-positive, the enclosure metering the BESS the way it
    meters anything else it feeds, positive into the device, and the snapshot
    negates it, so the sensor reads negative for the same battery it reads
    negative from the capture. The library pins the same relation in
    `test_schema_one_devices.py`:
    `test_an_r202633_tree_reports_the_same_charging_battery_through_the_snapshot`.

    Magnitude and sign are asserted separately on purpose: losing the negation
    keeps the magnitude, so only the sign check catches it.
    """
    old_frame = _in_the_old_frame(float(_published(BESS, POWER_TOPIC)))
    tree = schema_one_tree()
    tree[SCHEMA_ONE_PANEL]["info/firmware-version"] = R202633_FIRMWARE
    tree[BESS][POWER_TOPIC] = str(old_frame)

    state = _state(schema_one_snapshot(tree), POWER_KEY)

    assert old_frame > 0
    assert state == -old_frame
    assert isinstance(state, float) and state < 0


def test_it_agrees_with_the_battery_power_sensor_beside_it() -> None:
    """The two battery-power sensors on this device must not contradict each other.

    `battery_power` reads the enclosure's arbitrated `power-flows/battery`; this
    one reads the BESS's own meter. The specification defines the first as the
    negation of the second, which is how the capture publishes them (r202639's
    frame); the old frame, which publishes them with the *same* sign, has its own
    section below. Exactly one path negates in each frame, so the UI shows one
    convention either way, and the check is on the states rather than on either
    definition. A flip on either side fails here even if the side that flipped
    still looks self-consistent.
    """
    snapshot = schema_one_snapshot()

    own_meter = _state(snapshot, POWER_KEY)
    enclosure_flow = _state(snapshot, ENCLOSURE_FLOW_KEY)

    assert isinstance(own_meter, float) and isinstance(enclosure_flow, float)
    assert (own_meter > 0) == (enclosure_flow > 0)


def test_they_agree_when_the_battery_discharges_too() -> None:
    """Agreement at one operating point could be coincidence; this is the other.

    Both properties are republished with the battery discharging — the capture's
    two values negated, so still opposed, still the capture's frame — and both
    sensors must go positive together, the sensors being discharge-positive.
    """
    snapshot = _republishing_both(
        power=-float(_published(BESS, POWER_TOPIC)),
        enclosure_flow=-float(_published(SCHEMA_ONE_PANEL, ENCLOSURE_FLOW_TOPIC)),
    )

    own_meter = _state(snapshot, POWER_KEY)
    enclosure_flow = _state(snapshot, ENCLOSURE_FLOW_KEY)

    assert isinstance(own_meter, float) and own_meter > 0
    assert isinstance(enclosure_flow, float) and enclosure_flow > 0


def _republishing_both(*, power: float, enclosure_flow: float) -> SpanPanelSnapshot:
    """A snapshot with the BESS meter and the enclosure's flow both rewritten."""
    tree = schema_one_tree()
    tree[BESS][POWER_TOPIC] = str(power)
    tree[SCHEMA_ONE_PANEL][ENCLOSURE_FLOW_TOPIC] = str(enclosure_flow)
    return schema_one_snapshot(tree)


# ---------------------------------------------------------------------------
# Firmware through r202633: the BESS meter is published in the flow's sign
# ---------------------------------------------------------------------------


def _r202633(*, enclosure_flow: float) -> SpanPanelSnapshot:
    """Republish the capture the way firmware through r202633 publishes it.

    Before r202639 the BESS meter is charge-positive, equal to
    `power-flows/battery` rather than its negation, and the enclosure reports a
    version string the library reads that release from.
    """
    tree = schema_one_tree()
    tree[SCHEMA_ONE_PANEL]["info/firmware-version"] = R202633_FIRMWARE
    tree[SCHEMA_ONE_PANEL][ENCLOSURE_FLOW_TOPIC] = str(enclosure_flow)
    tree[BESS][POWER_TOPIC] = str(enclosure_flow)
    return schema_one_snapshot(tree)


@pytest.mark.parametrize("direction", ["charging", "discharging"])
def test_r202633_the_two_sensors_agree(direction: str) -> None:
    """In the old frame the wire properties share a sign, and the sensors still agree.

    The agreement the capture itself pinned until it was retaken in r202639's
    frame: here the library negates the BESS meter and `battery_power` negates
    the flow, so each path negates exactly once. The capture's enclosure flow is
    a charging battery; negating it gives the discharging case.
    """
    captured = float(_published(SCHEMA_ONE_PANEL, ENCLOSURE_FLOW_TOPIC))
    enclosure_flow = captured if direction == "charging" else -captured
    snapshot = _r202633(enclosure_flow=enclosure_flow)

    own_meter = _state(snapshot, POWER_KEY)
    flow_sensor = _state(snapshot, ENCLOSURE_FLOW_KEY)

    assert isinstance(own_meter, float) and isinstance(flow_sensor, float)
    assert own_meter == flow_sensor
    assert (own_meter > 0) == (direction == "discharging")


# ---------------------------------------------------------------------------
# Firmware r202639: the BESS meter is published as the flow's negation
# ---------------------------------------------------------------------------


def _r202639(*, enclosure_flow: float, power: float | None = None) -> SpanPanelSnapshot:
    """Republish the capture under an r202639 version string.

    From r202639 the BESS meter is the negation of `power-flows/battery` rather
    than equal to it, which is the frame the capture already publishes; what this
    adds is a version string the library reads the release from, so the frame no
    longer rests on the two signs. `power` overrides the meter for the one test
    that needs the two properties to disagree.
    """
    tree = schema_one_tree()
    tree[SCHEMA_ONE_PANEL]["info/firmware-version"] = R202639_FIRMWARE
    tree[SCHEMA_ONE_PANEL][ENCLOSURE_FLOW_TOPIC] = str(enclosure_flow)
    tree[BESS][POWER_TOPIC] = str(-enclosure_flow if power is None else power)
    return schema_one_snapshot(tree)


@pytest.mark.parametrize("direction", ["charging", "discharging"])
def test_r202639_the_two_sensors_agree(direction: str) -> None:
    """On r202639 the wire properties are opposed, and the sensors still agree.

    The capture's enclosure flow is a charging battery; negating it gives the
    discharging case. Both sensors must report the same value, positive exactly
    when the battery discharges, so the library's pass-through on this firmware
    lands on the convention `battery_power` already had.
    """
    captured = float(_published(SCHEMA_ONE_PANEL, ENCLOSURE_FLOW_TOPIC))
    enclosure_flow = captured if direction == "charging" else -captured
    snapshot = _r202639(enclosure_flow=enclosure_flow)

    own_meter = _state(snapshot, POWER_KEY)
    flow_sensor = _state(snapshot, ENCLOSURE_FLOW_KEY)

    assert isinstance(own_meter, float) and isinstance(flow_sensor, float)
    assert own_meter == flow_sensor
    assert (own_meter > 0) == (direction == "discharging")


def test_r202639_the_firmware_version_decides_when_the_flow_is_idle() -> None:
    """With the flow idle, only the firmware version says the meter's sign.

    With `power-flows/battery` at zero the signs cannot be compared, so only the
    release build in the firmware version can say the meter is already
    discharge-positive. A value that passes through unchanged proves it was read.
    """
    snapshot = _r202639(enclosure_flow=0.0, power=1200.0)

    assert _state(snapshot, POWER_KEY) == 1200.0


# ---------------------------------------------------------------------------
# States follow the wire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("firmware", "on_the_wire"),
    [
        (R202639_FIRMWARE, lambda discharging: discharging),
        (R202633_FIRMWARE, _in_the_old_frame),
    ],
    ids=["r202639", "r202633"],
)
def test_republishing_the_meter_moves_the_sensor(
    firmware: str, on_the_wire: Callable[[float], float]
) -> None:
    """The mutation proof, in each frame. The battery is republished discharging
    at half the captured rate, so the state differs in magnitude and in sign from
    what the capture reads, and a sensor pinned to a constant — or wired to the
    enclosure's flow instead — cannot report it.

    The panel is pinned to each release's version string. The capture's own does
    not parse, and without one the library picks the frame from the meter's sign
    against the flow's, which a one-sided republish that flips the meter flips
    too.

    The capture's meter is already discharge-positive, which is the sensor's
    frame, so half its negation is the discharging battery as the sensor reads
    it; `on_the_wire` is how each release publishes that."""
    published = float(_published(BESS, POWER_TOPIC))
    discharging = -published / 2

    tree = schema_one_tree()
    tree[BESS][POWER_TOPIC] = str(on_the_wire(discharging))
    tree[SCHEMA_ONE_PANEL]["info/firmware-version"] = firmware
    snapshot = schema_one_snapshot(tree)

    assert discharging > 0
    assert _state(snapshot, POWER_KEY) == discharging
    assert _state(snapshot, POWER_KEY) != published


def test_a_battery_at_rest_reports_zero_and_not_negative_zero() -> None:
    """`-0.0` compares equal to `0.0` and renders as "-0.0" beside it, so a
    negation added without a guard produces a reading that looks broken exactly
    when nothing is happening."""
    snapshot = _republishing(meter__active_power="0.0")

    assert _state(snapshot, POWER_KEY) == 0.0
    assert str(_state(snapshot, POWER_KEY)) == "0.0"


def test_zero_watts_is_a_state_and_not_an_absence() -> None:
    """An idle battery is a reading. A gate that treated zero as absence would
    delete the entity whenever the battery stopped moving power."""
    snapshot = _republishing(meter__active_power="0.0")

    assert POWER_KEY in _sensors(snapshot)


def test_the_communication_state_is_the_published_enum_lowercased() -> None:
    """Lowercase because HA looks the state up as a translation key, which its own
    contract restricts to `[a-z0-9-_]+`."""
    published = _published(BESS, COMMS_TOPIC)

    assert _state(schema_one_snapshot(), COMMS_KEY) == published.lower()


@pytest.mark.parametrize("republished", ["DEGRADED", "LOST", "UNKNOWN"])
def test_republishing_the_communication_state_moves_the_sensor(republished: str) -> None:
    """Every other member of the enum the BESS's own `$description` declares, so
    a sensor pinned to the captured OK cannot report any of them."""
    snapshot = _republishing(status__communication_state=republished)

    assert _state(snapshot, COMMS_KEY) == republished.lower()
    assert _state(snapshot, COMMS_KEY) != _published(BESS, COMMS_TOPIC).lower()


def test_the_declared_options_are_the_enum_the_bess_declares() -> None:
    """The sensor's "Possible states" against the wire's `format`, so a firmware
    that widens the enum is caught here rather than by the runtime append."""
    description = json.loads(schema_one_tree()[BESS]["$description"])
    declared = description["nodes"]["status"]["properties"]["communication-state"]["format"]

    options = next(d for d in BESS_TELEMETRY_SENSORS if d.key == COMMS_KEY).options

    assert options is not None
    assert set(options) == {value.lower() for value in declared.split(",")}


def test_communication_state_is_not_the_connected_binary_sensor() -> None:
    """The two link facts this task deliberately keeps apart.

    `bess_connected` is the enclosure's `connection/fed-by-device-status` view;
    this sensor is the BESS's report about itself. A BESS can report its own link
    LOST while the enclosure still claims it as OK, and a mapping that conflated
    them could not express that.
    """
    snapshot = _republishing(status__communication_state="LOST")

    assert _state(snapshot, COMMS_KEY) == "lost"
    assert snapshot.battery.connected is True


# ---------------------------------------------------------------------------
# Absence: deleted property, dropped node, no BESS, flat panel
# ---------------------------------------------------------------------------


def test_the_capture_creates_both_sensors() -> None:
    created = _sensors(schema_one_snapshot())

    assert POWER_KEY in created
    assert COMMS_KEY in created


def test_a_bess_with_no_meter_node_gets_no_power_sensor() -> None:
    """A dead entity stuck at unknown is worse than no entity: it occupies the
    entity list, breaks a dashboard card, and cannot be told apart from a battery
    whose meter has failed."""
    snapshot = _without_node("meter")

    assert POWER_KEY not in _sensors(snapshot)
    # The other half of the pair is unaffected — a partial BESS is legal firmware.
    assert COMMS_KEY in _sensors(snapshot)


def test_a_bess_with_no_status_node_gets_no_communication_sensor() -> None:
    snapshot = _without_node("status")

    assert COMMS_KEY not in _sensors(snapshot)
    assert POWER_KEY in _sensors(snapshot)


@pytest.mark.parametrize(
    ("key", "topic", "unknown"),
    [(POWER_KEY, POWER_TOPIC, None), (COMMS_KEY, COMMS_TOPIC, "unknown")],
)
def test_a_reading_that_stops_arriving_goes_unknown_rather_than_stale(
    key: str, topic: str, unknown: str | None
) -> None:
    """Absence after setup is a different event from absence at setup.

    Creation is decided once, from what the panel was publishing when the entry
    loaded; a property that stops arriving afterwards cannot delete an entity a
    user already has on a dashboard, so it has to degrade instead. The last value
    persisting would be the worse outcome — a battery reading 3500 W forever is
    indistinguishable from one that is actually charging.

    Driven through the coordinator rather than by rebuilding the entity, because
    that is the path a live update takes.
    """
    sensor = _sensors(schema_one_snapshot())[key]

    sensor.coordinator.data = _without(topic)
    sensor._update_native_value()

    assert sensor.native_value == unknown


@pytest.mark.parametrize(("key", "topic"), [(POWER_KEY, POWER_TOPIC), (COMMS_KEY, COMMS_TOPIC)])
def test_a_property_declared_and_never_published_creates_no_entity(key: str, topic: str) -> None:
    """The gate is the value, not the declaration.

    A BESS may declare a property in its `$description` and publish nothing on it
    — 19 instances in this capture do. An entity created from a declaration alone
    would be permanently unknown, which is the outcome the per-description gate
    exists to prevent, so this is the same answer as a missing node reached by a
    different route.
    """
    tree = schema_one_tree()
    del tree[BESS][topic]

    created = _sensors(schema_one_snapshot(tree))

    assert key not in created


def test_a_bess_publishing_neither_gets_neither_sensor() -> None:
    tree = schema_one_tree()
    description = json.loads(tree[BESS]["$description"])
    for node in ("meter", "status"):
        for topic in [t for t in tree[BESS] if t.startswith(f"{node}/")]:
            del tree[BESS][topic]
        del description["nodes"][node]
    tree[BESS]["$description"] = json.dumps(description)
    snapshot = schema_one_snapshot(tree)

    assert has_bess_telemetry(snapshot) is False
    created = _sensors(snapshot)
    assert POWER_KEY not in created
    assert COMMS_KEY not in created
    # The BESS itself is still commissioned, so its metadata sensors survive.
    assert created


def test_no_bess_device_creates_no_battery_sensors_at_all() -> None:
    snapshot = _without_bess()

    assert create_battery_sensors(_coordinator(snapshot), snapshot) == []


def test_a_flat_panel_gets_neither_sensor() -> None:
    """The same absence by the other route: flat's BESS device class declares
    neither property, so the factory's default snapshot carries neither field."""
    snapshot = SpanPanelSnapshotFactory.create()

    assert has_bess_telemetry(snapshot) is False
    created = _sensors(snapshot)
    assert POWER_KEY not in created
    assert COMMS_KEY not in created


def test_the_telemetry_appearing_is_a_capability_change() -> None:
    """Which is how a BESS that gains these nodes mid-life gets the sensors: the
    coordinator reloads on a new capability."""
    assert "bess_telemetry" not in detect_capabilities(SpanPanelSnapshotFactory.create())
    assert "bess_telemetry" in detect_capabilities(schema_one_snapshot())
    assert "bess_telemetry" in detect_capabilities(_without_node("meter"))
    assert "bess_telemetry" not in detect_capabilities(_without_bess())


# ---------------------------------------------------------------------------
# Shape of the entities
# ---------------------------------------------------------------------------


def test_the_power_sensor_is_a_watt_measurement_enabled_by_default() -> None:
    """The battery's own charge/discharge figure is a reading a user graphs and
    automates on, so it belongs beside the other power sensors rather than under
    the diagnostics fold."""
    description = next(d for d in BESS_TELEMETRY_SENSORS if d.key == POWER_KEY)

    assert description.device_class is SensorDeviceClass.POWER
    assert description.state_class is SensorStateClass.MEASUREMENT
    assert description.native_unit_of_measurement == UnitOfPower.WATT
    assert description.entity_registry_enabled_default is True
    assert description.entity_category is not EntityCategory.DIAGNOSTIC


def test_the_communication_sensor_is_a_diagnostic_off_by_default() -> None:
    """A fault signal: interesting when something is wrong, noise on a device card
    the rest of the time."""
    description = next(d for d in BESS_TELEMETRY_SENSORS if d.key == COMMS_KEY)

    assert description.device_class is SensorDeviceClass.ENUM
    assert description.entity_category is EntityCategory.DIAGNOSTIC
    assert description.entity_registry_enabled_default is False


def test_the_declared_unit_matches_what_the_bess_declares() -> None:
    """HA's unit against the tree's, for the path schema_1 carries metadata for. A
    disagreement here is what the unit-mismatch Repair reports at runtime."""
    from .adapter_fixtures import schema_one_metadata

    description = next(d for d in BESS_TELEMETRY_SENSORS if d.key == POWER_KEY)

    assert schema_one_metadata()["battery.power_w"].unit == (description.native_unit_of_measurement)


def test_both_sensors_live_on_the_bess_sub_device() -> None:
    """Beside the metadata sensors and the battery level, not on the panel."""
    created = _sensors(schema_one_snapshot())
    bess_device = created["vendor"].device_info

    assert created[POWER_KEY].device_info == bess_device
    assert created[COMMS_KEY].device_info == bess_device


def test_every_bess_sensor_gets_a_distinct_unique_id() -> None:
    """They live on one device and differ only by description key, so a key reused
    from the metadata group would silently collide."""
    created = _sensors(schema_one_snapshot())
    unique_ids = {sensor.unique_id for sensor in created.values()}

    assert len(unique_ids) == len(created)


# ---------------------------------------------------------------------------
# Conformance annotations
# ---------------------------------------------------------------------------


def test_both_paths_are_declarations() -> None:
    """Pinned here as well as in the conformance suite: the adapter maps both
    properties from the BESS's own `$description`, so the producible gate covers
    them and neither is an exemption."""
    for path in ("battery.power_w", "battery.communication_state"):
        assert path in declared_field_paths()
        assert path not in RESIDUAL_EXEMPT_PATHS


@pytest.mark.parametrize("description", BESS_TELEMETRY_SENSORS, ids=lambda d: d.key)
def test_each_description_is_a_plain_declaration(description: Any) -> None:
    """`field_path` says what the entity's value is, and with no `derived` the
    gate covers it: the entity gets its Repair mention and goes unavailable when
    the panel stops resolving the property."""
    assert description.derived is None
    assert description.field_path in declared_field_paths()
