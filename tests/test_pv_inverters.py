"""A panel with more than one PV inverter gets a card and entities per inverter.

From firmware r202639 a panel publishes every commissioned inverter as its own
device, and the library carries all of them in `snapshot.pv_inverters`, keyed by
the feeding circuit's id or, for an inverter no circuit feeds, its device id.
Before that a panel published one, and the integration rendered it on the
`{serial}_pv` card under panel-scoped unique ids.

Two things are held here. Every inverter but the one the Solar card reads gets a
card and entities keyed by its circuit or, where none feeds it, its device id;
and the Solar card's entities never move.

The registry-shape expectations are literals, for the reason `test_pv_device`
gives: they record what an installation carries.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    async_remove_config_entry_device,
    ensure_device_registered,
)
from custom_components.span_panel.binary_sensor import (
    async_setup_entry as binary_sensor_setup_entry,
)
from custom_components.span_panel.const import DOMAIN, PV_PANEL_LINK_KEY
from custom_components.span_panel.curation import async_load_curation
from custom_components.span_panel.helpers import detect_capabilities
from custom_components.span_panel.id_builder import (
    build_binary_sensor_unique_id,
    build_panel_unique_id,
)
from custom_components.span_panel.pv_binding import async_resolve_pv_binding
from custom_components.span_panel.sensor import async_setup_entry as sensor_setup_entry
from custom_components.span_panel.sensor_definitions import (
    CIRCUIT_SENSORS,
    PV_METADATA_SENSORS,
    PV_POWER_SENSOR,
)
from custom_components.span_panel.util import (
    SUB_DEVICE_PV,
    classify_sub_device_identifier,
    pv_inverter_device_info,
)
from custom_components.span_panel.websocket import _classify_sub_device

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .test_pv_device import PANEL_NAME, SOLAR_CIRCUIT, _coordinator, _entry

SECOND_SOLAR_CIRCUIT: Final = "5be1d2c3a4f5061728394a5b6c7d8e9f"
FIRST_PV: Final = "pv-1"
SECOND_PV: Final = "pv-2"
UNFED_PV: Final = "pv-3"

METADATA_KEYS: Final = ("pv_vendor", "pv_product", "pv_nameplate_capacity")


def _tree(*, second: bool = True, unfed: bool = False) -> dict[str, dict[str, str]]:
    """Rewrite the capture with its inverter renamed and others added, as r202639 publishes them; the second sits on lower breaker spaces, where 2.1.2b3 would have ranked it first.

    The second also publishes a serial, so a serial is on the wire for the
    identity assertions to ignore.
    """
    tree = schema_one_tree()
    pv_topics = tree.pop("pv")
    tree[FIRST_PV] = dict(pv_topics)
    tree[SOLAR_CIRCUIT]["connection/feeds-device-id"] = FIRST_PV
    if second:
        tree[SECOND_PV] = {
            **pv_topics,
            "info/vendor-name": "Second Vendor",
            "info/serial-number": "INVERTER-SERIAL-0002",
            "info/nominal-power": "4000.0",
        }
        tree[SECOND_SOLAR_CIRCUIT] = {
            **tree[SOLAR_CIRCUIT],
            "connection/feeds-device-id": SECOND_PV,
            "info/name": "Garage Solar",
            "info/spaces": "5,7",
        }
    if unfed:
        tree[UNFED_PV] = dict(pv_topics)
    return tree


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    snapshot: SpanPanelSnapshot,
    *,
    published: SpanPanelSnapshot | None = None,
) -> list[MockEntityPlatform]:
    """Run both platforms' setup through real `EntityPlatform`s, as a (re)load does.

    In `async_setup_entry`'s order: the PV identity, then the curation overlay, then the platforms.
    `published` is what the coordinator holds by the time the platforms run, where
    the panel published more after setup took `snapshot`.
    """
    identity = await async_resolve_pv_binding(hass, entry, snapshot)
    coordinator = _coordinator(hass, entry, published if published is not None else snapshot)
    panel_device_id = await ensure_device_registered(hass, entry, snapshot, PANEL_NAME)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel_device_id,
        curation=await async_load_curation(hass, entry),
        pv_binding=identity,
        setup_snapshot=snapshot,
    )
    platforms: list[MockEntityPlatform] = []
    for domain, setup in (
        ("sensor", sensor_setup_entry),
        ("binary_sensor", binary_sensor_setup_entry),
    ):
        added: list[object] = []
        await setup(hass, entry, lambda entities, **_: added.extend(entities))
        platform = MockEntityPlatform(
            hass, domain=domain, platform_name=DOMAIN, logger=logging.getLogger(__name__)
        )
        platform.config_entry = entry
        await platform.platform_data.async_load_translations()
        await platform.async_add_entities(added)
        platforms.append(platform)
    return platforms


async def _unload(platforms: list[MockEntityPlatform]) -> None:
    for platform in platforms:
        await platform.async_reset()


def _unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """``{unique_id: entity_id}`` for every registered entity of one entry."""
    return {
        entity.unique_id: entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


def _device(hass: HomeAssistant, entry: MockConfigEntry, identifier: str) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id)
    assert device is not None, f"no device registered for {identifier}"
    return device


def _device_of(hass: HomeAssistant, unique_ids: dict[str, str], unique_id: str) -> dr.DeviceEntry:
    entity = er.async_get(hass).async_get(unique_ids[unique_id])
    assert entity is not None
    assert entity.device_id is not None
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device is not None
    return device


def solar_unique_ids(serial: str, *, link: bool = True) -> dict[str, str]:
    """`{suffix: unique_id}` of the Solar card's PV entities, built as the entities build them."""
    ids = {"pv_power": build_panel_unique_id(serial, PV_POWER_SENSOR.key)}
    ids.update(
        {
            description.key: build_panel_unique_id(serial, description.key)
            for description in PV_METADATA_SENSORS
        }
    )
    if link:
        ids[PV_PANEL_LINK_KEY] = build_binary_sensor_unique_id(serial, PV_PANEL_LINK_KEY)
    return ids


def solar_entities(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, tuple[str, str | None]]:
    """`{unique_id: (entity_id, device_id)}` of the Solar card's PV entities that are registered."""
    registry = er.async_get(hass)
    found: dict[str, tuple[str, str | None]] = {}
    for unique_id in solar_unique_ids(entry.unique_id or "").values():
        for domain in ("sensor", "binary_sensor"):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            entity = registry.async_get(entity_id) if entity_id is not None else None
            if entity is not None:
                found[unique_id] = (entity.entity_id, entity.device_id)
    return found


def inverter_cards(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    """Return the keys of every inverter card registered for the entry."""
    prefix = f"{entry.unique_id}_{SUB_DEVICE_PV}_"
    return {
        name.removeprefix(prefix)
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for _domain, name in device.identifiers
        if name.startswith(prefix)
    }


# ---------------------------------------------------------------------------
# One inverter: nothing moves
# ---------------------------------------------------------------------------


async def test_a_single_inverter_keeps_every_id_it_had(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot()
    assert len(snapshot.pv_inverters) == 1
    entry = _entry(hass, "entry-pv-single", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    assert set(solar_entities(hass, entry)) == set(solar_unique_ids(snapshot.serial_number).values())
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert not [
        name for device in devices for _, name in device.identifiers if f"_{SUB_DEVICE_PV}_" in name
    ]
    assert len([cap for cap in detect_capabilities(snapshot) if cap.startswith("pv_inverter:")]) == 1


# ---------------------------------------------------------------------------
# Two inverters, each fed by a circuit
# ---------------------------------------------------------------------------


async def test_each_inverter_gets_a_card_and_entities_keyed_by_its_circuit(
    hass: HomeAssistant,
) -> None:
    snapshot = schema_one_snapshot(_tree())
    assert set(snapshot.pv_inverters) == {SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT}
    entry = _entry(hass, "entry-pv-two", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    unique_ids = _unique_ids(hass, entry)
    serial = snapshot.serial_number
    for key in (SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT):
        expected = {
            f"span_{serial}_pv_{key}_{suffix}" for suffix in (*METADATA_KEYS, "pv_panel_link")
        }
        assert expected <= set(unique_ids)
        card = _device(hass, entry, f"{serial}_{SUB_DEVICE_PV}_{key}")
        assert _classify_sub_device(card) == SUB_DEVICE_PV
        for uid in expected:
            assert _device_of(hass, unique_ids, uid).id == card.id

    # Never the serial, which only the second inverter publishes.
    assert not [uid for uid in unique_ids if "INVERTER-SERIAL" in uid]

    second = _device(hass, entry, f"{serial}_{SUB_DEVICE_PV}_{SECOND_SOLAR_CIRCUIT}")
    assert second.name == f"{PANEL_NAME} Solar Inverter (Garage Solar)"
    assert second.manufacturer == "Second Vendor"
    panel = _device(hass, entry, serial)
    assert second.via_device_id == panel.id

    vendor = er.async_get(hass).async_get(
        unique_ids[f"span_{serial}_pv_{SECOND_SOLAR_CIRCUIT}_pv_vendor"]
    )
    assert vendor is not None
    nameplate = unique_ids[f"span_{serial}_pv_{SECOND_SOLAR_CIRCUIT}_pv_nameplate_capacity"]
    assert (
        er.async_get(hass).async_get(nameplate).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )


async def test_two_inverters_at_first_sight_leave_the_solar_card_unbound(
    hass: HomeAssistant,
) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-pv-two-unbound", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    assert set(solar_entities(hass, entry)) == set(solar_unique_ids(snapshot.serial_number).values())
    assert inverter_cards(hass, entry) == {SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT}
    solar = _device(hass, entry, f"{snapshot.serial_number}_{SUB_DEVICE_PV}")
    # The two vendors differ, so the card that reads them together claims neither.
    assert solar.manufacturer == "Unknown"
    assert solar.sw_version is None


def test_every_inverters_circuit_reads_as_solar() -> None:
    """Both feeding circuits are labeled PV, so both get the solar sign."""
    snapshot = schema_one_snapshot(_tree())
    power = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")

    first = snapshot.circuits[SOLAR_CIRCUIT]
    second = snapshot.circuits[SECOND_SOLAR_CIRCUIT]
    assert first.device_type == second.device_type == "pv"
    assert first.instant_power_w == second.instant_power_w
    assert power.value_fn(second) == power.value_fn(first)
    assert not [d for d in snapshot.adopted_devices if "pv" in d.device_type]


def test_every_inverter_is_a_capability_so_a_new_key_reloads() -> None:
    single = detect_capabilities(schema_one_snapshot())
    multi = detect_capabilities(schema_one_snapshot(_tree()))

    assert len([cap for cap in single if cap.startswith("pv_inverter:")]) == 1
    assert len([cap for cap in multi - single if cap.startswith("pv_inverter:")]) == 1
    assert not [cap for cap in multi if SOLAR_CIRCUIT in cap or SECOND_SOLAR_CIRCUIT in cap]


def test_the_identifier_grammar_reads_both_shapes() -> None:
    assert classify_sub_device_identifier(f"serial_{SUB_DEVICE_PV}") == SUB_DEVICE_PV
    assert (
        classify_sub_device_identifier(f"serial_{SUB_DEVICE_PV}_{SOLAR_CIRCUIT}") == SUB_DEVICE_PV
    )
    assert classify_sub_device_identifier(f"serial_{SUB_DEVICE_PV}_{UNFED_PV}") == SUB_DEVICE_PV


# ---------------------------------------------------------------------------
# An inverter no circuit feeds
# ---------------------------------------------------------------------------


async def test_an_unfed_inverter_is_keyed_by_its_device_id(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot(_tree(second=False, unfed=True))
    assert set(snapshot.pv_inverters) == {SOLAR_CIRCUIT, UNFED_PV}
    entry = _entry(hass, "entry-pv-unfed", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    unique_ids = _unique_ids(hass, entry)
    serial = snapshot.serial_number
    for suffix in METADATA_KEYS:
        assert f"span_{serial}_pv_{UNFED_PV}_{suffix}" in unique_ids
    # No circuit, so no link record to read and no entity for one.
    assert f"span_{serial}_pv_{UNFED_PV}_pv_panel_link" not in unique_ids
    assert f"span_{serial}_pv_{SOLAR_CIRCUIT}_pv_panel_link" in unique_ids

    card = _device(hass, entry, f"{serial}_{SUB_DEVICE_PV}_{UNFED_PV}")
    # No circuit to name it after, so it is numbered.
    assert card.name == f"{PANEL_NAME} Solar Inverter (1)"


def _names(hass: HomeAssistant, entry: MockConfigEntry, serial: str, keys: list[str]) -> list[str]:
    return [str(_device(hass, entry, f"{serial}_{SUB_DEVICE_PV}_{key}").name) for key in keys]


async def test_two_unfed_inverters_get_distinct_names(hass: HomeAssistant) -> None:
    tree = _tree(second=False, unfed=True)
    tree["pv-4"] = dict(tree[UNFED_PV])
    snapshot = schema_one_snapshot(tree)
    assert set(snapshot.pv_inverters) == {SOLAR_CIRCUIT, UNFED_PV, "pv-4"}
    entry = _entry(hass, "entry-pv-two-unfed", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    # The captured circuit is itself named "Solar Inverter", which the device
    # name already says, so it is not repeated as the suffix.
    assert _names(hass, entry, snapshot.serial_number, [SOLAR_CIRCUIT, UNFED_PV, "pv-4"]) == [
        f"{PANEL_NAME} Solar Inverter",
        f"{PANEL_NAME} Solar Inverter (1)",
        f"{PANEL_NAME} Solar Inverter (2)",
    ]


@pytest.mark.parametrize(
    ("display_suffix", "expected"),
    [
        ("Solar Inverter", f"{PANEL_NAME} Solar Inverter"),
        ("solar inverter", f"{PANEL_NAME} Solar Inverter (solar inverter)"),
        ("Garage Solar", f"{PANEL_NAME} Solar Inverter (Garage Solar)"),
        (None, f"{PANEL_NAME} Solar Inverter"),
    ],
)
def test_only_a_suffix_equal_to_the_label_is_dropped(
    display_suffix: str | None, expected: str
) -> None:
    pv = schema_one_snapshot(_tree()).pv_inverters[SOLAR_CIRCUIT]

    info = pv_inverter_device_info(
        "serial", SOLAR_CIRCUIT, pv, PANEL_NAME, display_suffix, panel_device_id="panel-device"
    )

    assert info.get("name") == expected


async def test_circuits_sharing_a_name_fall_back_to_their_breaker_positions(
    hass: HomeAssistant,
) -> None:
    tree = _tree()
    tree[SECOND_SOLAR_CIRCUIT]["info/name"] = tree[SOLAR_CIRCUIT]["info/name"]
    snapshot = schema_one_snapshot(tree)
    entry = _entry(hass, "entry-pv-same-name", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    assert _names(hass, entry, snapshot.serial_number, [SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT]) == [
        f"{PANEL_NAME} Solar Inverter (Circuit 36 38)",
        f"{PANEL_NAME} Solar Inverter (Circuit 5 7)",
    ]


# ---------------------------------------------------------------------------
# Inverters leaving the panel
# ---------------------------------------------------------------------------


def _inverter_devices(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    return {
        name
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for _, name in device.identifiers
        if f"_{SUB_DEVICE_PV}_" in name
    }


async def test_a_departed_inverter_keeps_its_device_until_its_owner_removes_it(
    hass: HomeAssistant,
) -> None:
    before = schema_one_snapshot(_tree(unfed=True))
    entry = _entry(hass, "entry-pv-departed", before.serial_number)
    serial = before.serial_number
    await _unload(await _setup(hass, entry, before))

    await _setup(hass, entry, schema_one_snapshot(_tree()))

    assert f"{serial}_{SUB_DEVICE_PV}_{UNFED_PV}" in _inverter_devices(hass, entry)
    assert [uid for uid in _unique_ids(hass, entry) if UNFED_PV in uid]
    device = _device(hass, entry, f"{serial}_{SUB_DEVICE_PV}_{UNFED_PV}")
    assert await async_remove_config_entry_device(hass, entry, device)
