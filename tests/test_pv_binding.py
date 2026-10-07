"""Nothing moves: the Solar card's PV entities through every shape a panel publishes."""

from __future__ import annotations

import json
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import async_remove_entry
from custom_components.span_panel.binary_sensor import (
    SpanPVInverterBinarySensor,
    SpanPVSolarLinkBinarySensor,
)
from custom_components.span_panel.const import DOMAIN, PV_PANEL_LINK_KEY
from custom_components.span_panel.id_builder import (
    build_panel_unique_id,
    build_pv_inverter_unique_id,
)
from custom_components.span_panel.pv_binding import async_resolve_pv_binding, keys_holding_cards
from custom_components.span_panel.sensor_panel import SpanPVMetadataSensor
from custom_components.span_panel.util import SUB_DEVICE_PV

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .test_pv_device import PANEL_NAME, PV_DEVICE, SOLAR_CIRCUIT, _entry
from .test_pv_inverters import (
    FIRST_PV,
    SECOND_PV,
    SECOND_SOLAR_CIRCUIT,
    _setup,
    _tree,
    _unload,
    inverter_cards,
    solar_entities,
    solar_unique_ids,
)

ENTRY_ID: Final = "entry-pv-forget"

FEED_TOPICS: Final = ("connection/feeds-device-id", "connection/feeds-device-status", "connection/feeds-device-type")
NEW_CIRCUIT: Final = "0123456789abcdef0123456789abcdef"


async def test_removing_the_entry_forgets_the_record(hass: HomeAssistant, hass_storage: dict[str, object]) -> None:
    """Driven through the removal hook, so the wiring is what is proved."""
    key = f"{DOMAIN}.pv_binding.{ENTRY_ID}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"circuit_id": "c"}}
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=ENTRY_ID, unique_id="sp3-001")
    entry.add_to_hass(hass)

    await async_remove_entry(hass, entry)

    assert key not in hass_storage


def _seed_2_1_1(
    hass: HomeAssistant, entry: MockConfigEntry, *, link: bool = True, renamed: dict[str, str] | None = None
) -> dict[str, tuple[str, str | None]]:
    """Write a 2.1.1 registry: the Solar card and its entities under 2.1.1's ids, no record."""
    card = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.unique_id}_{SUB_DEVICE_PV}")},
        name=f"{PANEL_NAME} Solar",
    )
    registry = er.async_get(hass)
    for suffix, unique_id in solar_unique_ids(entry.unique_id or "", link=link).items():
        domain = "binary_sensor" if suffix == PV_PANEL_LINK_KEY else "sensor"
        object_id = "span_panel_" + ("pv_power" if suffix == "pv_power" else suffix)
        created = registry.async_get_or_create(
            domain, DOMAIN, unique_id, config_entry=entry, device_id=card.id, suggested_object_id=object_id
        )
        new_id = (renamed or {}).get(suffix)
        if new_id is not None:
            registry.async_update_entity(created.entity_id, new_entity_id=new_id)
    return solar_entities(hass, entry)


def _unfed_tree() -> dict[str, dict[str, str]]:
    tree = schema_one_tree()
    for topic in FEED_TOPICS:
        tree[SOLAR_CIRCUIT].pop(topic, None)
    return tree


def _gateway_tree(*inverters: tuple[str, str, str | None]) -> dict[str, dict[str, str]]:
    """span#269: inverters no circuit feeds, as `(device id, model, recorded DC size or None)`."""
    tree = _unfed_tree()
    pv_topics = tree.pop(PV_DEVICE)
    for device_id, model, nominal in inverters:
        topics = {**pv_topics, "info/vendor-name": "SolarEdge", "info/model": model}
        topics.pop("info/nominal-power", None)
        if nominal is not None:
            topics["info/nominal-power"] = nominal
        tree[device_id] = topics
    return tree


def _record(hass_storage: dict[str, object], entry: MockConfigEntry) -> object:
    stored = hass_storage.get(f"{DOMAIN}.pv_binding.{entry.entry_id}")
    return stored.get("data") if isinstance(stored, dict) else None


async def test_scenario_a_a_second_inverter_on_a_lower_breaker_space_moves_nothing(hass: HomeAssistant) -> None:
    """The 2.1.2b3 regression, from a 2.1.1 registry: the Solar card stays on the Enphase on 36/38."""
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-a", one.serial_number)
    held = _seed_2_1_1(hass, entry, renamed={"pv_product": "sensor.my_pv_product"})
    await _unload(await _setup(hass, entry, one))
    assert solar_entities(hass, entry) == held

    two = schema_one_snapshot(_tree())
    platforms = await _setup(hass, entry, two)

    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == {SECOND_SOLAR_CIRCUIT}
    vendor = platforms[0].entities[held[solar_unique_ids(one.serial_number)["pv_vendor"]][0]]
    assert isinstance(vendor, SpanPVMetadataSensor)
    assert vendor.get_data_source(two) is two.pv_inverters[SOLAR_CIRCUIT]


async def test_scenario_b_inverters_behind_a_gateway_leave_the_solar_card_reading_them_together(
    hass: HomeAssistant,
) -> None:
    """span#269 from a 2.1.1 registry; r202639 shape pending confirmation."""
    before = schema_one_snapshot(_gateway_tree(("panel-se7600h-us", "SE7600H-US", "11680")))
    entry = _entry(hass, "entry-b", before.serial_number)
    held = _seed_2_1_1(hass, entry, link=False, renamed={"pv_vendor": "sensor.my_solar_brand"})
    await _unload(await _setup(hass, entry, before))
    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == set()

    after = schema_one_snapshot(
        _gateway_tree(("panel-se7600h-us-1", "SE7600H-US", "11680"), ("panel-use7600h-us-2", "USE7600H-US", None))
    )
    await _setup(hass, entry, after)

    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == {"panel-se7600h-us-1", "panel-use7600h-us-2"}
    source = entry.runtime_data.pv_binding.source(after)
    assert source.vendor_name == "SolarEdge"
    assert source.model is None
    assert source.nameplate_capacity_w is None


async def test_scenario_c_two_inverters_at_first_upgrade_leave_the_solar_card_unbound(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-c", snapshot.serial_number)
    held = _seed_2_1_1(hass, entry, renamed={"pv_vendor": "sensor.my_solar_brand"})

    await _setup(hass, entry, snapshot)

    assert solar_entities(hass, entry) == held
    assert "sensor.my_solar_brand" in {entity_id for entity_id, _device in held.values()}
    assert inverter_cards(hass, entry) == {SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT}


async def test_scenario_d_a_record_that_has_not_arrived_mints_no_card(hass: HomeAssistant) -> None:
    fed = schema_one_snapshot()
    entry = _entry(hass, "entry-d", fed.serial_number)
    held = _seed_2_1_1(hass, entry, renamed={"pv_product": "sensor.my_pv_product"})
    await _unload(await _setup(hass, entry, fed))

    late = schema_one_snapshot(_unfed_tree())
    await _setup(hass, entry, late)

    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == set()
    assert entry.runtime_data.pv_binding.withheld == frozenset({PV_DEVICE})


async def test_scenario_e_a_replaced_circuit_reads_unknown_and_the_inverter_gets_a_card(hass: HomeAssistant) -> None:
    """Not expected for in-panel PV; the honest outcome if a panel ever does it."""
    fed = schema_one_snapshot()
    entry = _entry(hass, "entry-e", fed.serial_number)
    held = _seed_2_1_1(hass, entry)
    await _unload(await _setup(hass, entry, fed))

    tree = schema_one_tree()
    tree[NEW_CIRCUIT] = tree.pop(SOLAR_CIRCUIT)
    moved = schema_one_snapshot(tree)
    await _setup(hass, entry, moved)

    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == {NEW_CIRCUIT}
    assert entry.runtime_data.pv_binding.source(moved).vendor_name is None


async def test_scenario_f_an_identity_only_inverter_given_a_circuit_keeps_the_solar_card(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The one refinement: unbound, then bound to the circuit the card already reads, then a newcomer."""
    unfed = schema_one_snapshot(_unfed_tree())
    entry = _entry(hass, "entry-f", unfed.serial_number)
    held = _seed_2_1_1(hass, entry)
    await _unload(await _setup(hass, entry, unfed))
    assert _record(hass_storage, entry) == {"circuit_id": None}
    assert inverter_cards(hass, entry) == set()

    await _unload(await _setup(hass, entry, schema_one_snapshot()))
    assert _record(hass_storage, entry) == {"circuit_id": SOLAR_CIRCUIT}

    await _setup(hass, entry, schema_one_snapshot(_tree()))
    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == {SECOND_SOLAR_CIRCUIT}


async def test_scenario_g_a_single_inverter_keeps_its_2_1_1_ids(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot()
    entry = _entry(hass, "entry-g", snapshot.serial_number)
    held = _seed_2_1_1(hass, entry)

    await _setup(hass, entry, snapshot)

    assert solar_entities(hass, entry) == held
    assert inverter_cards(hass, entry) == set()


async def test_without_the_record_two_inverters_leave_the_card_unbound(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The registry cannot carry the binding: remove the store between one inverter and two, and the card is unbound.

    This is exactly how an install upgrading from 2.1.1 meets scenario C. Neither circuit has a card, so
    nothing in the registry tells C from C2 without ranking or matching, and the store is the only carrier
    that does neither.
    """
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-no-record", one.serial_number)
    held = _seed_2_1_1(hass, entry, renamed={"pv_vendor": "sensor.my_solar_brand"})
    await _unload(await _setup(hass, entry, one))
    del hass_storage[f"{DOMAIN}.pv_binding.{entry.entry_id}"]

    await _setup(hass, entry, schema_one_snapshot(_tree()))

    assert _record(hass_storage, entry) == {"circuit_id": None}
    assert entry.runtime_data.pv_binding.mode == "unbound"
    assert inverter_cards(hass, entry) == {SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT}
    assert solar_entities(hass, entry) == held


async def test_a_key_that_holds_a_card_keeps_it_through_every_shape(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The invariant end to end: a carded inverter keeps its card, whatever the panel or the store does next.

    Runs a sequence of setups over one entry. Some steps remove the store or corrupt it first, the way a manual removal
    or a damaged file reaches a real install. After each setup, every inverter that had a card before it and is still
    published still has its own card.
    """
    entry = _entry(hass, "entry-invariant", schema_one_snapshot().serial_number)
    _seed_2_1_1(hass, entry, renamed={"pv_product": "sensor.my_pv_product"})
    key = f"{DOMAIN}.pv_binding.{entry.entry_id}"
    steps: tuple[tuple[str, SpanPanelSnapshot, str | None], ...] = (
        ("two at first sight", schema_one_snapshot(_tree()), None),
        ("back to one, store removed", schema_one_snapshot(_tree(second=False)), "remove"),
        ("record late, store corrupt", schema_one_snapshot(_unfed_tree()), "corrupt"),
        ("two again", schema_one_snapshot(_tree()), None),
        ("two again, store removed", schema_one_snapshot(_tree()), "remove"),
    )
    for label, snapshot, store_action in steps:
        if store_action == "remove":
            hass_storage.pop(key, None)
        elif store_action == "corrupt":
            hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": ["corrupt"]}
        carded = inverter_cards(hass, entry)
        await _unload(await _setup(hass, entry, snapshot))
        binding = entry.runtime_data.pv_binding
        for card_key in carded & set(snapshot.pv_inverters):
            assert binding.has_own_card(card_key), f"{label}: {card_key} lost its card"


async def test_a_wrong_shaped_record_does_not_stop_setup(hass: HomeAssistant, hass_storage: dict[str, object]) -> None:
    snapshot = schema_one_snapshot()
    entry = _entry(hass, "entry-bad-record", snapshot.serial_number)
    key = f"{DOMAIN}.pv_binding.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": ["not", "a", "record"]}

    await _setup(hass, entry, snapshot)

    assert _record(hass_storage, entry) == {"circuit_id": SOLAR_CIRCUIT}


async def test_a_second_setup_writes_nothing(hass: HomeAssistant, hass_storage: dict[str, object]) -> None:
    snapshot = schema_one_snapshot()
    entry = _entry(hass, "entry-idempotent", snapshot.serial_number)
    await _unload(await _setup(hass, entry, snapshot))
    written = _record(hass_storage, entry)

    await _unload(await _setup(hass, entry, schema_one_snapshot(_tree())))

    assert _record(hass_storage, entry) == written


def _with_vendor_readings(tree: dict[str, dict[str, str]], *device_ids: str) -> dict[str, dict[str, str]]:
    """Declare a vendor node on each inverter: one reading and one boolean no schema field addresses."""
    for device_id in device_ids:
        description = json.loads(tree[device_id]["$description"])
        description["nodes"]["acme"] = {
            "name": "acme",
            "type": "acme.inverter",
            "properties": {
                "string-voltage": {"name": "String voltage", "datatype": "float", "unit": "V"},
                "arc-fault": {"name": "Arc fault", "datatype": "boolean"},
            },
        }
        tree[device_id]["$description"] = json.dumps(description)
        tree[device_id]["acme/string-voltage"] = "412.0"
        tree[device_id]["acme/arc-fault"] = "false"
    return tree


async def test_vendor_readings_follow_the_binding_through_setup(hass: HomeAssistant) -> None:
    """The bound inverter's readings register on the Solar card under the keyless ids; the newcomer's on its own card."""
    one = schema_one_snapshot()
    serial = one.serial_number
    entry = _entry(hass, "entry-vendor", serial)
    await _unload(await _setup(hass, entry, one))
    two = schema_one_snapshot(_with_vendor_readings(_tree(), FIRST_PV, SECOND_PV))

    await _unload(await _setup(hass, entry, two))
    # The newcomer's card is minted by that setup, so its readings follow on the next.
    await _unload(await _setup(hass, entry, two))

    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    solar = devices.async_get_device_by_identifier((DOMAIN, f"{serial}_{SUB_DEVICE_PV}"), entry.entry_id)
    newcomer = devices.async_get_device_by_identifier(
        (DOMAIN, f"{serial}_{SUB_DEVICE_PV}_{SECOND_SOLAR_CIRCUIT}"), entry.entry_id
    )
    assert solar is not None and newcomer is not None
    for domain, path in (("sensor", "acme/string-voltage"), ("binary_sensor", "acme/arc-fault")):
        on_solar = entities.async_get_entity_id(domain, DOMAIN, f"span_{serial}_adopted_pv/{path}")
        assert on_solar is not None, f"{domain} {path} is not on the Solar card"
        assert entities.async_get(on_solar).device_id == solar.id
        assert entities.async_get_entity_id(domain, DOMAIN, f"span_{serial}_adopted_pv_{SOLAR_CIRCUIT}/{path}") is None
        own = entities.async_get_entity_id(domain, DOMAIN, f"span_{serial}_adopted_pv_{SECOND_SOLAR_CIRCUIT}/{path}")
        assert own is not None, f"{domain} {path} is not on the newcomer's card"
        assert entities.async_get(own).device_id == newcomer.id


# --- which inverters already hold a card -------------------------------------------


def test_a_card_is_counted_only_in_its_own_entry(hass: HomeAssistant) -> None:
    """An inverter id another entry owns says nothing about this entry's cards."""
    mine = _entry(hass, "entry-cards-mine", "sp3-cards-001")
    theirs = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-cards-theirs", unique_id="sp3-cards-002")
    theirs.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, build_pv_inverter_unique_id("sp3-cards-001", "c-1", "pv_vendor"), config_entry=theirs
    )

    assert keys_holding_cards(registry, mine.entry_id, "sp3-cards-001", ["c-1"]) == frozenset()
    assert keys_holding_cards(registry, theirs.entry_id, "sp3-cards-001", ["c-1"]) == frozenset({"c-1"})


def test_an_inverter_holding_only_its_link_sensor_holds_a_card(hass: HomeAssistant) -> None:
    """The link binary sensor alone is a card of its own; the Solar card's panel-scoped ids are not."""
    entry = _entry(hass, "entry-cards-link", "sp3-cards-003")
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        build_pv_inverter_unique_id("sp3-cards-003", "c-link", PV_PANEL_LINK_KEY),
        config_entry=entry,
    )
    registry.async_get_or_create(
        "sensor", DOMAIN, build_panel_unique_id("sp3-cards-003", "pv_vendor"), config_entry=entry
    )

    assert keys_holding_cards(registry, entry.entry_id, "sp3-cards-003", ["c-link", "c-none"]) == frozenset(
        {"c-link"}
    )


# --- the record on disk -------------------------------------------------------------


async def test_first_sight_writes_the_record_and_no_inverter_writes_none(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    empty = schema_one_snapshot(schema_one_tree(without=PV_DEVICE))
    entry = _entry(hass, "entry-record-write", empty.serial_number)
    key = f"{DOMAIN}.pv_binding.{entry.entry_id}"

    undecided = await async_resolve_pv_binding(hass, entry, empty)
    await hass.async_block_till_done()
    assert undecided.mode == "undecided"
    assert key not in hass_storage

    bound = await async_resolve_pv_binding(hass, entry, schema_one_snapshot())
    await hass.async_block_till_done()
    assert bound.bound_key == SOLAR_CIRCUIT
    stored = hass_storage[key]
    assert isinstance(stored, dict)
    assert stored["version"] == 1
    assert stored["data"] == {"circuit_id": SOLAR_CIRCUIT}


async def test_the_platforms_decide_from_the_snapshot_the_binding_was_resolved_from(hass: HomeAssistant) -> None:
    """An inverter published while setup runs waits for the reload its capability token requests.

    Setup resolved the binding before any inverter was seen. Had the platforms read
    the coordinator's newer snapshot, the newcomer would get a card of its own while
    the Solar card read it too, and that card would then hold it off the Solar card.
    """
    before = schema_one_snapshot(schema_one_tree(without=PV_DEVICE))
    after = schema_one_snapshot()
    entry = _entry(hass, "entry-drift", after.serial_number)

    await _unload(await _setup(hass, entry, before, published=after))
    assert inverter_cards(hass, entry) == set()

    await _setup(hass, entry, after)
    assert entry.runtime_data.pv_binding.bound_key == SOLAR_CIRCUIT
    assert inverter_cards(hass, entry) == set()


# --- the Solar card's link entity -----------------------------------------------------
#
# Created where it is already registered, or where every inverter the card reads
# publishes a link record -- never by what a link reads at setup.

STATUS_TOPIC: Final = "connection/feeds-device-status"


def _solar_link(platforms: list[MockEntityPlatform], serial: str) -> SpanPVSolarLinkBinarySensor | None:
    """The Solar card's link entity these platforms built, read once from the coordinator."""
    link_id = solar_unique_ids(serial)[PV_PANEL_LINK_KEY]
    for platform in platforms:
        for entity in platform.entities.values():
            if isinstance(entity, SpanPVSolarLinkBinarySensor) and entity.unique_id == link_id:
                entity._handle_coordinator_update()
                return entity
    return None


def _one_record_less(status: str) -> dict[str, dict[str, str]]:
    """Two inverters at first sight; the first circuit reports `status`, the second publishes no record."""
    tree = _tree()
    tree[SOLAR_CIRCUIT][STATUS_TOPIC] = status
    tree[SECOND_SOLAR_CIRCUIT].pop(STATUS_TOPIC)
    return tree


@pytest.mark.parametrize(("recorded", "expected"), [(True, True), (False, None)], ids=["record", "no-record"])
async def test_a_lone_inverter_has_the_link_exactly_as_2_1_1_did(
    hass: HomeAssistant, recorded: bool, expected: bool | None
) -> None:
    """With a record the link is created, reading it; without one it is not, as 2.1.1 decided it."""
    tree = schema_one_tree()
    if not recorded:
        tree[SOLAR_CIRCUIT].pop(STATUS_TOPIC)
    snapshot = schema_one_snapshot(tree)
    entry = _entry(hass, "entry-link-lone", snapshot.serial_number)

    link = _solar_link(await _setup(hass, entry, snapshot), snapshot.serial_number)

    assert (link is not None) is recorded
    assert link is None or link.is_on is expected


async def test_a_bound_link_is_kept_and_reads_unknown_while_its_inverter_is_not_published(
    hass: HomeAssistant,
) -> None:
    fed = schema_one_snapshot()
    entry = _entry(hass, "entry-link-bound", fed.serial_number)
    await _unload(await _setup(hass, entry, fed))

    pending = schema_one_snapshot(_unfed_tree())
    platforms = await _setup(hass, entry, pending)
    link = _solar_link(platforms, pending.serial_number)
    assert link is not None
    assert link.is_on is None and link.available
    await _unload(platforms)

    tree = schema_one_tree()
    tree[NEW_CIRCUIT] = tree.pop(SOLAR_CIRCUIT)
    departed = schema_one_snapshot(tree)
    link = _solar_link(await _setup(hass, entry, departed), departed.serial_number)
    assert link is not None
    assert link.is_on is None and link.available


async def test_an_unbound_link_over_several_does_not_come_and_go_with_a_reading(hass: HomeAssistant) -> None:
    """One inverter publishes no record: the link's existence is the same whether the other is OK or LOST."""
    entry = _entry(hass, "entry-link-unbound", schema_one_snapshot().serial_number)
    existence = []
    for status in ("OK", "LOST", "OK"):
        snapshot = schema_one_snapshot(_one_record_less(status))
        platforms = await _setup(hass, entry, snapshot)
        assert entry.runtime_data.pv_binding.mode == "unbound"
        existence.append(_solar_link(platforms, snapshot.serial_number) is not None)
        await _unload(platforms)
    assert existence == [False, False, False]


async def test_an_unbound_link_over_several_all_recorded_is_created(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-link-all", snapshot.serial_number)

    link = _solar_link(await _setup(hass, entry, snapshot), snapshot.serial_number)

    assert entry.runtime_data.pv_binding.mode == "unbound"
    assert link is not None and link.is_on is True


@pytest.mark.parametrize(("status", "reading"), [("OK", None), ("LOST", False)])
async def test_an_install_that_had_the_link_keeps_it_when_the_record_rule_fails(
    hass: HomeAssistant, status: str, reading: bool | None
) -> None:
    """A 2.1.1 registry holding the link, upgraded straight to two inverters one of which publishes no record.

    Kept whatever the recorded link reads; it reads the inverters together --
    unknown with one unreported and the other up, down once the other is down.
    """
    snapshot = schema_one_snapshot(_one_record_less(status))
    entry = _entry(hass, "entry-link-held", snapshot.serial_number)
    held = _seed_2_1_1(hass, entry)

    link = _solar_link(await _setup(hass, entry, snapshot), snapshot.serial_number)

    assert link is not None
    assert held[solar_unique_ids(snapshot.serial_number)[PV_PANEL_LINK_KEY]][0] == link.entity_id
    assert link.is_on is reading


async def test_an_inverters_own_link_is_kept_when_its_record_goes(hass: HomeAssistant) -> None:
    """The Solar link's rule per key: a link already registered is created whatever the record does, reading unknown."""
    entry = _entry(hass, "entry-own-link", schema_one_snapshot().serial_number)
    await _unload(await _setup(hass, entry, schema_one_snapshot(_tree())))
    tree = _tree()
    tree[SECOND_SOLAR_CIRCUIT].pop(STATUS_TOPIC)
    silent = schema_one_snapshot(tree)

    platforms = await _setup(hass, entry, silent)

    own = [
        entity
        for platform in platforms
        for entity in platform.entities.values()
        if isinstance(entity, SpanPVInverterBinarySensor)
        and entity.unique_id == build_pv_inverter_unique_id(silent.serial_number, SECOND_SOLAR_CIRCUIT, PV_PANEL_LINK_KEY)
    ]
    assert len(own) == 1
    own[0]._handle_coordinator_update()
    assert own[0].is_on is None and own[0].available
    assert SECOND_SOLAR_CIRCUIT in entry.runtime_data.pv_binding.inverter_links
