"""A circuit whose relay is locked carries no policy that acts on that relay.

`load-shed` and `pcs` are both policies on a circuit's relay, and the eBus
catalogs say each is "meaningful only on a circuit that also publishes a
controllable `switch`" (`capabilities/load-shed.md`, `capabilities/pcs.md`,
`capabilities/switch.md` "Relationship to load-shed and PCS"). A relay whose
`switch/relay-controllable` is false is locked: nothing opens it, a shed
included.

The panel still publishes a stored shed priority on such circuits, and a PCS
participation, and acts on neither. SpanPanel/span#351 is that difference
reaching a user: five circuits commissioned "always on", publishing four
different shed priorities between them, every one of which stayed powered
through an off-grid test. The circuit power sensor republished the priority as
`shed_priority`, so a circuit that stays on in an outage claimed it turned off.

The rule is the declaration's and is read on every update, so a circuit
re-commissioned in place gains or loses the attributes with its relay.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.sensor import create_circuit_sensors
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for

#: The capture's one circuit whose relay is locked: `switch/relay-controllable`
#: is `false` and `switch/relay` carries no `$settable`. It still publishes a
#: shed priority and a PCS participation, which is the issue in miniature.
LOCKED_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"
#: A circuit whose relay the panel can open, publishing both policies too.
CONTROLLABLE_CIRCUIT = "0ab966b95f92a6a51ec548485aa85f54"

#: The attributes that state a policy acting on the relay.
RELAY_POLICIES = frozenset({"shed_priority", "pcs_managed", "pcs_priority"})
#: The attributes that state facts about the relay itself, which a locked
#: relay still has: whether it is locked, whether it can be shed, its state,
#: and who last moved it.
RELAY_FACTS = frozenset({"always_on", "is_sheddable", "relay_state", "relay_requester"})


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    """Return a coordinator-like mock carrying one snapshot."""
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


def _power_sensor(snapshot: SpanPanelSnapshot, circuit_id: str) -> SpanCircuitPowerSensor:
    """Return the power sensor the platform creates for one circuit, which carries these attributes."""
    coordinator = _coordinator(snapshot)
    for sensor in create_circuit_sensors(coordinator, snapshot, coordinator.config_entry):
        if isinstance(sensor, SpanCircuitPowerSensor) and sensor.circuit_id == circuit_id:
            return sensor
    raise AssertionError(f"no power sensor created for circuit {circuit_id}")


def _attributes(snapshot: SpanPanelSnapshot, circuit_id: str) -> dict[str, Any]:
    return _power_sensor(snapshot, circuit_id).extra_state_attributes or {}


def _republishing(device_id: str, **topics: str) -> SpanPanelSnapshot:
    """Return the capture with some of one device's topics republished, keyed `node/property`."""
    tree = schema_one_tree()
    tree[device_id].update(topics)
    return schema_one_snapshot(tree)


def _without_a_relay(device_id: str) -> SpanPanelSnapshot:
    """Return the capture with one circuit's `switch` node gone, declaration and topics both.

    A circuit that still occupies its breaker spaces and declares no relay at
    all: neither controllable nor locked.
    """
    tree = schema_one_tree()
    for topic in [topic for topic in tree[device_id] if topic.startswith("switch/")]:
        del tree[device_id][topic]
    description = json.loads(tree[device_id]["$description"])
    del description["nodes"]["switch"]
    tree[device_id]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def test_the_capture_publishes_both_policies_on_a_locked_relay() -> None:
    """Guard the premise of the omission tests.

    They would be vacuous on a capture that published neither policy on the
    locked circuit.
    """
    tree = schema_one_tree()

    assert tree[LOCKED_CIRCUIT]["switch/relay-controllable"] == "false"
    assert tree[LOCKED_CIRCUIT]["load-shed/priority"]
    assert tree[LOCKED_CIRCUIT]["pcs/managed"]
    assert tree[LOCKED_CIRCUIT]["pcs/priority"]
    assert tree[CONTROLLABLE_CIRCUIT]["switch/relay-controllable"] == "true"


@pytest.mark.parametrize("stored", ["UNKNOWN", "NEVER", "SOC_THRESHOLD", "OFF_GRID"])
def test_a_locked_relay_reports_no_policy_whatever_priority_is_stored(stored: str) -> None:
    """Every value the reporter's five locked circuits published, and none of them is shown.

    `OFF_GRID` is the one that misled: it reads as "turns off in an outage" on a
    circuit the panel never turns off.
    """
    attributes = _attributes(
        _republishing(LOCKED_CIRCUIT, **{"load-shed/priority": stored}), LOCKED_CIRCUIT
    )

    assert not RELAY_POLICIES & attributes.keys(), attributes


def test_a_locked_relay_still_reports_what_it_is() -> None:
    """The facts about the relay stay, and they are the ones that are true of it."""
    attributes = _attributes(schema_one_snapshot(), LOCKED_CIRCUIT)

    assert attributes.keys() >= RELAY_FACTS, attributes
    assert attributes["always_on"] is True
    assert attributes["is_sheddable"] is False


def test_a_controllable_relay_still_reports_both_policies() -> None:
    """The omission is the locked relay's alone."""
    tree = schema_one_tree()
    attributes = _attributes(schema_one_snapshot(), CONTROLLABLE_CIRCUIT)

    assert attributes["shed_priority"] == tree[CONTROLLABLE_CIRCUIT]["load-shed/priority"]
    assert attributes["pcs_managed"] is True
    assert attributes["pcs_priority"] == int(tree[CONTROLLABLE_CIRCUIT]["pcs/priority"])
    assert attributes["always_on"] is False


def test_the_rule_follows_the_relay_on_every_update() -> None:
    """Re-commissioning a circuit in place moves its relay, and the attributes go with it.

    One sensor across updates, in both directions, so a rule decided once at
    creation -- or a value latched from the first read -- would show here.
    """
    sensor = _power_sensor(schema_one_snapshot(), LOCKED_CIRCUIT)
    assert not RELAY_POLICIES & (sensor.extra_state_attributes or {}).keys()

    sensor.coordinator.data = _republishing(LOCKED_CIRCUIT, **{"switch/relay-controllable": "true"})
    unlocked = sensor.extra_state_attributes or {}
    assert unlocked.keys() >= RELAY_POLICIES, unlocked
    assert unlocked["always_on"] is False

    sensor.coordinator.data = schema_one_snapshot()
    assert not RELAY_POLICIES & (sensor.extra_state_attributes or {}).keys()

    controllable = _power_sensor(schema_one_snapshot(), CONTROLLABLE_CIRCUIT)
    controllable.coordinator.data = _republishing(
        CONTROLLABLE_CIRCUIT, **{"switch/relay-controllable": "false"}
    )
    locked = controllable.extra_state_attributes or {}
    assert not RELAY_POLICIES & locked.keys(), locked
    assert locked["always_on"] is True


@pytest.mark.spec_only
def test_a_circuit_that_declares_no_relay_reports_no_policy() -> None:
    """Load-shed and PCS need a relay to act on, and this circuit declares none.

    Pins the field the omission reads. With no relay there is nothing to be
    locked, so `always_on` is false here, and a rule reading it would show
    both policies. The circuit still publishes them: its `load-shed` and `pcs`
    nodes are untouched.
    """
    snapshot = _without_a_relay(CONTROLLABLE_CIRCUIT)
    circuit = snapshot.circuits[CONTROLLABLE_CIRCUIT]
    assert circuit.always_on is False
    assert circuit.is_user_controllable is False
    assert circuit.measures_outside_panel is False
    assert circuit.pcs_managed is not None

    attributes = _attributes(snapshot, CONTROLLABLE_CIRCUIT)

    assert not RELAY_POLICIES & attributes.keys(), attributes


@pytest.mark.spec_only
def test_a_circuit_that_declares_no_relay_reports_no_relay_facts() -> None:
    """Nor does it report a relay it does not have.

    `always_on`, `relay_state`, `relay_requester` and `is_sheddable` are facts
    about a relay. With none declared they could only be the adapter's defaults
    -- not locked, state UNKNOWN, requester UNKNOWN, never shed -- which read as
    a relay whose state is unknown. The circuit's other attributes stay.
    """
    attributes = _attributes(_without_a_relay(CONTROLLABLE_CIRCUIT), CONTROLLABLE_CIRCUIT)

    assert not RELAY_FACTS & attributes.keys(), attributes
    assert "tabs" in attributes


@pytest.mark.spec_only
@pytest.mark.parametrize("circuit_id", [CONTROLLABLE_CIRCUIT, LOCKED_CIRCUIT])
def test_a_relay_that_has_not_reported_is_still_a_relay(circuit_id: str) -> None:
    """Whether a circuit has a relay is the declaration's to say, not the reading's.

    The `switch` node is declared and `switch/relay` carries no value, as on a
    panel that has described the circuit and not yet published its state. The
    relay is there and its state is UNKNOWN, which is what the facts say; a
    rule reading the state instead would drop them as though no relay existed.
    """
    tree = schema_one_tree()
    del tree[circuit_id]["switch/relay"]
    snapshot = schema_one_snapshot(tree)
    assert snapshot.circuits[circuit_id].relay_state == "UNKNOWN"

    attributes = _attributes(snapshot, circuit_id)

    assert attributes.keys() >= RELAY_FACTS, attributes
    assert attributes["relay_state"] == "UNKNOWN"


def test_a_flat_always_on_circuit_reports_no_shed_priority() -> None:
    """Flat firmware says the same thing through `circuit/always-on`.

    The flat adapter derives `is_user_controllable` as `not always-on`, so the
    snapshot here is the one it builds for a circuit commissioned always on.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="48",
        name="Sump",
        always_on=True,
        is_user_controllable=False,
        is_sheddable=False,
        priority="OFF_GRID",
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={circuit.circuit_id: circuit})

    attributes = _attributes(snapshot, circuit.circuit_id)

    assert "shed_priority" not in attributes
    assert attributes["always_on"] is True
    assert attributes["is_sheddable"] is False
