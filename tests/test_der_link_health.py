"""The enclosure's link to each circuit-fed DER, as two diagnostic binary sensors.

`bess_connected` has shown the panel's view of the link to the battery since v1.0
landed, because the upstream lugs' `connection/fed-by-device-status` was already
read. The identical fact for a PV or a charger is published by the **circuit that
feeds it** — `connection/feeds-device-status`, the other half of the same
capability — and reached nothing, so one DER class had a link sensor and the
other two did not.

`pv_panel_link` and `evse_panel_link` close that.

**The naming is load-bearing.** `evse_ev_connected` already exists on the same
device and reads the charger's own `status/status`: *a vehicle is plugged in*.
The new one reads the feeding circuit's connection record: *the enclosure can
reach the charger*. Those are different questions with different answers, and
`test_the_charger_link_is_not_the_ev_plug` produces the state where they
disagree rather than asserting the distinction in prose.

**Every expectation is read out of the capture**, including the enum's members,
which come from the circuit's own `$description` `format` rather than from a list
written here. And the capture publishes `OK` on all three records, so no reading
is proved by the baseline alone: each is proved by republishing values that
differ per DER, and by swapping them, because two chargers fed by two circuits is
what makes cross-wiring falsifiable.

**Absence is a reading too.** Two of the capture's five circuits publish no
connection record at all — the enclosure data model calls that normal for a
mixed-load circuit, and the enum has no UNKNOWN member, so an unpublished
property is the only way a panel can say it does not know. The entity is gated on
the record existing, never on the circuit's type, and never treats silence as a
fault.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.binary_sensor import (
    EVSE_BINARY_SENSORS,
    EVSE_PANEL_LINK_SENSOR,
    PV_PANEL_LINK_SENSOR,
    SpanEvseBinarySensor,
    SpanPanelBinarySensor,
    SpanPanelBinarySensorEntityDescription,
    async_setup_entry,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    Producibility,
)
from custom_components.span_panel.helpers import detect_capabilities, has_der_link_health
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .factories import SpanPanelSnapshotFactory

from pytest_homeassistant_custom_component.common import MockConfigEntry

CONNECTION_NODE = "connection"
FEEDS_ID_TOPIC = f"{CONNECTION_NODE}/feeds-device-id"
FEEDS_STATUS_TOPIC = f"{CONNECTION_NODE}/feeds-device-status"

# The DER the capture commissions: one inverter and two chargers, each fed by its
# own circuit. Two chargers is what makes the wiring falsifiable, so a capture
# that lost one has to fail rather than quietly halve the evidence.
PV = "pv"
EVSE = "evse"
EVSE_2 = "evse-2"

STATUS_OK = "OK"

PanelBinarySensor = SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription]
LinkEntity = PanelBinarySensor | SpanEvseBinarySensor
"""Everything `binary_sensor.async_setup_entry` can add, and nothing wider.

Narrowed at the boundary rather than carried as `object`, so every lookup below
reads a real attribute instead of one the type checker had to be told to ignore.
"""


# ---------------------------------------------------------------------------
# Reading the capture
# ---------------------------------------------------------------------------


def _feeding_circuit(tree: dict[str, dict[str, str]], device_id: str) -> str:
    """The circuit the capture says feeds `device_id`, or fail saying none does."""
    feeders = [
        circuit for circuit, topics in tree.items() if topics.get(FEEDS_ID_TOPIC) == device_id
    ]
    assert len(feeders) == 1, f"{len(feeders)} circuits feed {device_id} in the capture, expected 1"
    return feeders[0]


def _status_options() -> list[str]:
    """The enum as the feeding circuit's own `$description` declares it.

    Read off the wire because the legal values are the panel's claim, not this
    module's — and because the absence of an `UNKNOWN` member is the premise
    that makes `None` the only way to report an unknown link.
    """
    tree = schema_one_tree()
    description = json.loads(tree[_feeding_circuit(tree, PV)]["$description"])
    declared = description["nodes"][CONNECTION_NODE]["properties"]["feeds-device-status"]
    assert declared["datatype"] == "enum"
    return str(declared["format"]).split(",")


def _not_ok() -> list[str]:
    return [option for option in _status_options() if option != STATUS_OK]


def _republishing(**statuses: str) -> SpanPanelSnapshot:
    """A snapshot from the capture with each named DER's link status rewritten.

    Keyed by DER rather than by circuit so a test says what it means — "the
    charger's link is down" — while the indirection through `_feeding_circuit`
    keeps it reading the capture's own topology rather than a circuit id copied
    into the test.
    """
    tree = schema_one_tree()
    for der, status in statuses.items():
        tree[_feeding_circuit(tree, der.replace("_", "-"))][FEEDS_STATUS_TOPIC] = status
    return schema_one_snapshot(tree)


def _without_status(der: str) -> SpanPanelSnapshot:
    """A snapshot whose feeding circuit stopped publishing the status half."""
    tree = schema_one_tree()
    del tree[_feeding_circuit(tree, der)][FEEDS_STATUS_TOPIC]
    return schema_one_snapshot(tree)


def _without_record(der: str) -> SpanPanelSnapshot:
    """A snapshot whose feeding circuit publishes no connection record at all.

    What a circuit feeding an ordinary load looks like, applied to a circuit
    that used to feed a DER.
    """
    tree = schema_one_tree()
    circuit = _feeding_circuit(tree, der)
    del tree[circuit][FEEDS_ID_TOPIC]
    del tree[circuit][FEEDS_STATUS_TOPIC]
    return schema_one_snapshot(tree)


# ---------------------------------------------------------------------------
# Building the entities
# ---------------------------------------------------------------------------


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
    )
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def _created(hass: HomeAssistant, snapshot: SpanPanelSnapshot) -> list[LinkEntity]:
    """Everything `binary_sensor.async_setup_entry` creates for one snapshot.

    Driven through the platform rather than by constructing descriptions,
    because the gate under test is a creation gate: an entity that must not
    exist cannot be observed by asking an entity for its state.
    """
    coordinator = _coordinator(snapshot)
    config_entry = coordinator.config_entry
    async_add_entities = MagicMock()

    await async_setup_entry(hass, config_entry, async_add_entities)

    added: Sequence[LinkEntity] = async_add_entities.call_args.args[0]
    for entity in added:
        assert isinstance(entity, SpanPanelBinarySensor | SpanEvseBinarySensor)
    return list(added)


def _keys(created: Sequence[LinkEntity]) -> list[str]:
    return [entity.entity_description.key for entity in created]


def _state(entity: LinkEntity) -> bool | None:
    entity.async_write_ha_state = MagicMock()
    entity._handle_coordinator_update()
    return entity.is_on


def _pv_link(created: Sequence[LinkEntity]) -> PanelBinarySensor:
    matches = [
        entity
        for entity in created
        if isinstance(entity, SpanPanelBinarySensor)
        and entity.entity_description.key == PV_PANEL_LINK_SENSOR.key
    ]
    assert len(matches) == 1, f"{len(matches)} PV link sensors created, expected 1"
    return matches[0]


def _evse_links_by_feed(
    created: Sequence[LinkEntity], snapshot: SpanPanelSnapshot
) -> dict[str, SpanEvseBinarySensor]:
    """The charger link sensors, keyed by the circuit that feeds each charger.

    Keyed by feed rather than by the snapshot's EVSE key, which is a harmonised
    serial: a test that named one would still pass if every record landed on the
    same charger, which is the failure this module exists to rule out.
    """
    links: dict[str, SpanEvseBinarySensor] = {}
    for entity in created:
        if (
            isinstance(entity, SpanEvseBinarySensor)
            and entity.entity_description.key == EVSE_PANEL_LINK_SENSOR.key
        ):
            links[snapshot.evse[entity._evse_id].feed_circuit_id] = entity
    return links


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_the_capture_publishes_a_link_record_for_three_ders_and_three_bare_circuits() -> None:
    """Guard every expectation below, and the negative case with them.

    Three DER-feeding circuits publish the record; the remaining circuits
    declare the node and publish neither half of it. That second group is the
    absence case, and it is in the capture rather than manufactured here.
    """
    tree = schema_one_tree()
    circuits = {
        device_id
        for device_id, topics in tree.items()
        if json.loads(topics["$description"])["type"].endswith(".circuit")
    }
    feeding = {_feeding_circuit(tree, der) for der in (PV, EVSE, EVSE_2)}

    assert feeding <= circuits
    for circuit in feeding:
        assert tree[circuit][FEEDS_STATUS_TOPIC] in _status_options()

    bare = circuits - feeding
    assert len(bare) == 3, f"expected three circuits feeding no DER, found {len(bare)}"
    for circuit in bare:
        declared = json.loads(tree[circuit]["$description"])["nodes"]
        assert CONNECTION_NODE in declared, (
            f"{circuit} does not declare the node, so its silence proves nothing about "
            "a circuit that declares the record and publishes none of it"
        )
        assert not [topic for topic in tree[circuit] if topic.startswith(f"{CONNECTION_NODE}/")]


def test_the_status_enum_has_no_unknown_member() -> None:
    """Why absence has to mean unknown: the enum cannot say it."""
    options = _status_options()

    assert STATUS_OK in options
    assert "UNKNOWN" not in options
    assert _not_ok(), "the enum offers no bad status, so nothing below can observe a broken link"


# ---------------------------------------------------------------------------
# The readings
# ---------------------------------------------------------------------------


async def test_each_der_reports_the_link_its_own_circuit_publishes(hass: HomeAssistant) -> None:
    """Baseline, with every expectation computed from the capture."""
    tree = schema_one_tree()
    snapshot = schema_one_snapshot(tree)
    created = await _created(hass, snapshot)

    assert _state(_pv_link(created)) is (
        tree[_feeding_circuit(tree, PV)][FEEDS_STATUS_TOPIC] == STATUS_OK
    )

    links = _evse_links_by_feed(created, snapshot)
    assert len(links) == 2, "the capture commissions two chargers; both should carry a link sensor"
    for der in (EVSE, EVSE_2):
        circuit = _feeding_circuit(tree, der)
        assert _state(links[circuit]) is (tree[circuit][FEEDS_STATUS_TOPIC] == STATUS_OK)


@pytest.mark.parametrize("status", _not_ok())
async def test_a_bad_status_flips_the_sensor(hass: HomeAssistant, status: str) -> None:
    """Both non-OK members, so a check written as `!= "LOST"` fails on DEGRADED."""
    snapshot = _republishing(pv=status, evse=status)
    created = await _created(hass, snapshot)

    assert _state(_pv_link(created)) is False
    links = _evse_links_by_feed(created, snapshot)
    assert _state(links[_feeding_circuit(schema_one_tree(), EVSE)]) is False


async def test_two_chargers_do_not_share_one_link(hass: HomeAssistant) -> None:
    """The cross-wiring case, and the reason the capture carries two chargers.

    Both read `OK` as captured, so the baseline test above is satisfied by an
    implementation that hands every charger the first record it finds. Here the
    two are republished differing and then swapped: getting one arrangement
    right by luck is possible, both is not.
    """
    down, degraded = _not_ok()[0], _not_ok()[-1]
    tree = schema_one_tree()
    first_circuit, second_circuit = _feeding_circuit(tree, EVSE), _feeding_circuit(tree, EVSE_2)

    for first, second in ((down, STATUS_OK), (STATUS_OK, down), (degraded, STATUS_OK)):
        snapshot = _republishing(evse=first, **{"evse_2": second})
        links = _evse_links_by_feed(await _created(hass, snapshot), snapshot)

        assert _state(links[first_circuit]) is (first == STATUS_OK)
        assert _state(links[second_circuit]) is (second == STATUS_OK)


async def test_the_inverters_link_is_not_a_chargers(hass: HomeAssistant) -> None:
    """The third DER, held apart from the two chargers the same way."""
    snapshot = _republishing(pv=_not_ok()[0])
    created = await _created(hass, snapshot)

    assert _state(_pv_link(created)) is False
    for link in _evse_links_by_feed(created, snapshot).values():
        assert _state(link) is True


# ---------------------------------------------------------------------------
# Absence is not a fault
# ---------------------------------------------------------------------------


async def test_a_der_whose_circuit_publishes_no_status_gets_no_entity(
    hass: HomeAssistant,
) -> None:
    """The gate is the record, and a retained topic can simply go away.

    Not "unavailable" and not `False`: the panel has said nothing, the enum has
    no way to say it, and an entity reporting a broken link on that basis would
    be inventing the one reading a user would act on.
    """
    snapshot = _without_status(PV)
    created = await _created(hass, snapshot)

    assert PV_PANEL_LINK_SENSOR.key not in _keys(created)
    assert len(_evse_links_by_feed(created, snapshot)) == 2, (
        "removing the inverter's status removed a charger's sensor too"
    )


async def test_a_charger_whose_circuit_publishes_no_record_gets_no_entity(
    hass: HomeAssistant,
) -> None:
    """Per charger, not per panel.

    One of two chargers losing its record must remove one of two sensors. A gate
    that asked "does any DER have a record" would keep both, and the surviving
    one would report a link nothing publishes.
    """
    snapshot = _without_record(EVSE)
    created = await _created(hass, snapshot)

    links = _evse_links_by_feed(created, snapshot)
    assert len(links) == 1
    assert _feeding_circuit(schema_one_tree(), EVSE_2) in links
    assert PV_PANEL_LINK_SENSOR.key in _keys(created), "the inverter's sensor went with it"


async def test_the_circuits_that_feed_no_der_create_nothing(hass: HomeAssistant) -> None:
    """The capture's three bare circuits, asserted as producing no entity.

    The `feeds-*` triple is absent from a mixed-load circuit by design, so the
    count of link sensors must equal the count of DER the capture claims — three
    — and not the count of circuits, five.
    """
    snapshot = schema_one_snapshot()
    created = await _created(hass, snapshot)

    link_keys = [key for key in _keys(created) if key.endswith("_panel_link")]
    assert len(link_keys) == 3


async def test_a_panel_with_no_connection_records_creates_neither_sensor(
    hass: HomeAssistant,
) -> None:
    """A flat panel, and any v1.0 panel whose circuits publish no record.

    The factory snapshot leaves both fields `None`, which is what every flat
    panel produces: flat publishes `connected` on the BESS and on no other
    device class.
    """
    snapshot = SpanPanelSnapshotFactory.create()
    created = await _created(hass, snapshot)

    assert [key for key in _keys(created) if key.endswith("_panel_link")] == []


# ---------------------------------------------------------------------------
# The fact this must not be confused with
# ---------------------------------------------------------------------------


async def test_the_charger_link_is_not_the_ev_plug(hass: HomeAssistant) -> None:
    """`evse_panel_link` and `evse_ev_connected` disagree, and both are right.

    The state that separates them: a charger mid-session behind a link the
    enclosure has lost. `status/status` still says a vehicle is plugged in —
    that is the last thing the panel heard — while the feeding circuit reports
    the link as down. One entity for both facts would have to pick, and would be
    wrong about one of them every time they diverge.

    The two sensors are also told apart at a glance: `CONNECTIVITY` against
    `PLUG`, diagnostic against primary, and neither key is a prefix of the
    other, so no automation can select one meaning to get the other.
    """
    tree = schema_one_tree()
    circuit = _feeding_circuit(tree, EVSE)
    plugged_in = tree[EVSE]["status/status"]
    snapshot = _republishing(evse=_not_ok()[0])
    created = await _created(hass, snapshot)

    link = _evse_links_by_feed(created, snapshot)[circuit]
    plug = next(
        entity
        for entity in created
        if isinstance(entity, SpanEvseBinarySensor)
        and entity.entity_description.key == "evse_ev_connected"
        and snapshot.evse[entity._evse_id].feed_circuit_id == circuit
    )

    assert snapshot.evse[link._evse_id].status == plugged_in
    assert _state(link) is False
    assert _state(plug) is True

    ev_connected = next(desc for desc in EVSE_BINARY_SENSORS if desc.key == "evse_ev_connected")
    assert EVSE_PANEL_LINK_SENSOR.device_class is BinarySensorDeviceClass.CONNECTIVITY
    assert ev_connected.device_class is BinarySensorDeviceClass.PLUG
    assert EVSE_PANEL_LINK_SENSOR.entity_category is EntityCategory.DIAGNOSTIC
    assert ev_connected.entity_category is None
    assert EVSE_PANEL_LINK_SENSOR.field_path != ev_connected.field_path
    assert not EVSE_PANEL_LINK_SENSOR.key.startswith(ev_connected.key)
    assert not ev_connected.key.startswith(EVSE_PANEL_LINK_SENSOR.key)


async def test_the_inverter_link_does_not_displace_the_batterys(hass: HomeAssistant) -> None:
    """`bess_connected` reads the lugs; these read a circuit. Both survive.

    Breaking every circuit-side record must leave the battery's sensor reporting
    what the upstream lugs say, or the new route has quietly taken over a field
    that was already right.
    """
    down = _not_ok()[0]
    snapshot = _republishing(pv=down, evse=down, **{"evse_2": down})
    created = await _created(hass, snapshot)

    bess = next(
        entity
        for entity in created
        if isinstance(entity, SpanPanelBinarySensor)
        and entity.entity_description.key == "bess_connected"
    )
    assert _state(bess) is True


# ---------------------------------------------------------------------------
# Declarations and gating
# ---------------------------------------------------------------------------


def test_both_descriptions_name_their_field_and_say_why_it_is_exempt() -> None:
    """`SCHEMA_CONDITIONAL_FIELD` *and* `field_path`, per the established rule.

    Flat firmware publishes `connected` on the BESS alone, so neither path can
    satisfy the both-adapters gate — while both entities still need their Repair
    mention and their unavailability when the panel stops resolving the property.
    """
    for description in (PV_PANEL_LINK_SENSOR, EVSE_PANEL_LINK_SENSOR):
        assert description.derived is DerivedReason.SCHEMA_CONDITIONAL_FIELD
        assert description.field_path is not None
        assert RESIDUAL_EXEMPT_PATHS[description.field_path] is Producibility.SCHEMA_1_ONLY

    assert PV_PANEL_LINK_SENSOR.field_path == "pv.connected"
    assert EVSE_PANEL_LINK_SENSOR.field_path == "evse.connected"


def test_the_capability_gate_follows_the_record_and_reaches_the_reload() -> None:
    """A panel that starts publishing the record must be able to gain the entities.

    Entities are created at setup, so a capability that appears later reaches a
    user only through `detect_capabilities` and the reload it triggers.
    """
    assert has_der_link_health(schema_one_snapshot()) is True
    assert has_der_link_health(SpanPanelSnapshotFactory.create()) is False
    assert has_der_link_health(_without_record(PV)) is True, (
        "the chargers still publish records; the gate must not be all-or-nothing"
    )

    assert "der_link_health" in detect_capabilities(schema_one_snapshot())
    assert "der_link_health" not in detect_capabilities(SpanPanelSnapshotFactory.create())
