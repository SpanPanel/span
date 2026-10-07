"""Nothing moves: the Solar card's PV entities through every shape a panel publishes."""

from __future__ import annotations

import json
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import async_remove_entry
from custom_components.span_panel.const import DOMAIN, PV_PANEL_LINK_KEY
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
