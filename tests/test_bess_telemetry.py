"""What the BESS reports about itself, surfaced as two sensors on its own device.

`meter/active-power` and `status/communication-state` were declared, published
and read by nobody. They are the battery's own view of its power and its own view
of its link, as opposed to the enclosure's view of both.

Every assertion runs against a real snapshot built by the real schema_1 adapter
over the vendored capture, and every expected value is read out of that capture
rather than written as a literal — a test that pins the same constant the code
pins passes whether or not the wire is ever read. Each reading is proved by
republishing it, deleting it, or dropping the node that carries it.

**The sign is the hard part, and it is what most of this module is about.** The
capture is of a *charging* battery, published as a negative `meter/active-power`
because the enclosure meters the BESS the way it meters a circuit it feeds. The
snapshot negates that into its charge-positive frame, and `battery_power` — the
sensor already on this device, reading the enclosure's discharge-positive
`power-flows/battery` — negates too. Two negations of two opposite wire
conventions, landing on one convention in the UI. A sensor whose sign
contradicted the one beside it would be worse than no sensor, so the agreement is
asserted directly rather than inferred from the two definitions.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    Producibility,
)
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
from .factories import SpanPanelSnapshotFactory

from pytest_homeassistant_custom_component.common import MockConfigEntry

BESS = "bess"

POWER_TOPIC = "meter/active-power"
COMMS_TOPIC = "status/communication-state"
ENCLOSURE_FLOW_TOPIC = "power-flows/battery"

POWER_KEY = "meter_power"
COMMS_KEY = "communication_state"
ENCLOSURE_FLOW_KEY = BATTERY_POWER_SENSOR.key


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
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
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
    and the enclosure publishes that as a *negative* number.

    Were the capture ever retaken with the battery discharging, this fails first
    and says so, rather than the sign tests failing and reading as a wiring bug.
    """
    flows = {
        name: float(_published(SCHEMA_ONE_PANEL, f"power-flows/{name}"))
        for name in ("pv", "battery", "grid", "site")
    }

    assert flows["pv"] + flows["battery"] + flows["grid"] == pytest.approx(flows["site"])
    # PV alone exceeds the site load, so the surplus has nowhere to go but the
    # battery and the grid — and the grid term is an export.
    assert flows["pv"] > flows["site"]
    assert flows["grid"] < 0
    assert flows["battery"] < 0


def test_the_bess_meter_agrees_with_the_enclosure_about_direction() -> None:
    """The two properties describing this battery must not disagree on the wire.

    `battery_power` negates the enclosure's flow and `bess_meter_power` negates
    the BESS's own meter; that is only coherent because the two are published in
    the same frame. Pinned here rather than assumed, because "negate exactly one
    of them" would be the wrong rule if a firmware ever published them opposed.
    """
    bess_meter = float(_published(BESS, POWER_TOPIC))
    enclosure_flow = float(_published(SCHEMA_ONE_PANEL, ENCLOSURE_FLOW_TOPIC))

    assert (bess_meter < 0) == (enclosure_flow < 0)


# ---------------------------------------------------------------------------
# The sign, and the agreement with the sensor beside it
# ---------------------------------------------------------------------------


def test_charging_reads_positive() -> None:
    """The convention, asserted on the state a user sees rather than on the field.

    The wire is charge-negative and the snapshot is charge-positive, so the
    sensor's state is the negation of the published value. Magnitude and sign are
    asserted separately on purpose: losing the negation keeps the magnitude, so
    only the sign check catches it.
    """
    published = float(_published(BESS, POWER_TOPIC))
    state = _state(schema_one_snapshot(), POWER_KEY)

    assert state == -published
    assert isinstance(state, float) and state > 0


def test_it_agrees_with_the_battery_power_sensor_beside_it() -> None:
    """The two battery-power sensors on this device must not contradict each other.

    `battery_power` reads the enclosure's arbitrated `power-flows/battery`, which
    the capability catalog defines as discharge-positive; this one reads the
    BESS's own meter, which the enclosure publishes charge-negative. Two opposite
    wire conventions, and the UI shows one — so the check is on the states, not
    on either definition. A flip on either side fails here even if the side that
    flipped still looks self-consistent.
    """
    snapshot = schema_one_snapshot()

    own_meter = _state(snapshot, POWER_KEY)
    enclosure_flow = _state(snapshot, ENCLOSURE_FLOW_KEY)

    assert isinstance(own_meter, float) and isinstance(enclosure_flow, float)
    assert (own_meter > 0) == (enclosure_flow > 0)


def test_they_agree_when_the_battery_discharges_too() -> None:
    """Agreement at one operating point could be coincidence; this is the other.

    Both properties are republished with the battery discharging — the capture's
    two values negated — and both sensors must go negative together.
    """
    snapshot = _republishing_both(
        power=-float(_published(BESS, POWER_TOPIC)),
        enclosure_flow=-float(_published(SCHEMA_ONE_PANEL, ENCLOSURE_FLOW_TOPIC)),
    )

    own_meter = _state(snapshot, POWER_KEY)
    enclosure_flow = _state(snapshot, ENCLOSURE_FLOW_KEY)

    assert isinstance(own_meter, float) and own_meter < 0
    assert isinstance(enclosure_flow, float) and enclosure_flow < 0


def _republishing_both(*, power: float, enclosure_flow: float) -> SpanPanelSnapshot:
    """A snapshot with the BESS meter and the enclosure's flow both rewritten."""
    tree = schema_one_tree()
    tree[BESS][POWER_TOPIC] = str(power)
    tree[SCHEMA_ONE_PANEL][ENCLOSURE_FLOW_TOPIC] = str(enclosure_flow)
    return schema_one_snapshot(tree)


# ---------------------------------------------------------------------------
# States follow the wire
# ---------------------------------------------------------------------------


def test_republishing_the_meter_moves_the_sensor() -> None:
    """The mutation proof. The republished value differs in magnitude and in sign
    from what the capture carries, so a sensor pinned to a constant — or wired to
    the enclosure's flow instead — cannot report it."""
    published = float(_published(BESS, POWER_TOPIC))
    discharging = -published / 2

    snapshot = _republishing(meter__active_power=str(discharging))

    assert _state(snapshot, POWER_KEY) == -discharging
    assert _state(snapshot, POWER_KEY) != -published


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


def test_both_paths_are_exempt_as_schema_1_only() -> None:
    """Pinned here as well as in the conformance suite, because the reason is
    specific to these properties: flat's BESS device class declares neither, so
    the producible gate cannot be satisfied and the descriptions must stay
    derived. schema_1 does map both, which is what makes the annotation
    SCHEMA_1_ONLY rather than NEITHER."""
    assert RESIDUAL_EXEMPT_PATHS["battery.power_w"] is Producibility.SCHEMA_1_ONLY
    assert RESIDUAL_EXEMPT_PATHS["battery.communication_state"] is Producibility.SCHEMA_1_ONLY


@pytest.mark.parametrize("description", BESS_TELEMETRY_SENSORS, ids=lambda d: d.key)
def test_each_description_names_its_field_as_well_as_its_reason(description: Any) -> None:
    """`field_path` says what the entity's value is and `derived` says why that
    path is outside the both-adapters gate. Leaving the first unset excuses the
    entity from its Repair mention and from going unavailable when the panel stops
    resolving the property."""
    assert description.derived is DerivedReason.SCHEMA_CONDITIONAL_FIELD
    assert description.field_path in RESIDUAL_EXEMPT_PATHS
