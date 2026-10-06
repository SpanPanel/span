"""A panel with more than one PV inverter gets a card and entities per inverter.

From firmware r202639 a panel publishes every commissioned inverter as its own
device, and the library carries all of them in `snapshot.pv_inverters`, keyed by
the feeding circuit's id or, for an inverter no circuit feeds, its device id.
Before that a panel published one, and the integration rendered it on the
`{serial}_pv` card under panel-scoped unique ids.

Three things are held here. A panel with one inverter keeps every unique id and
entity id it has. A panel with several gets per-inverter entities keyed by that
stable key, never by a serial. And crossing between the two layouts keeps every
entity_id: the single-inverter metadata entities are re-keyed in place onto the
primary inverter's per-inverter ids (and back), `pv_power`, the panel's
aggregate, keeps its identity on the solar card, and nothing is deleted -- an
inverter the panel stops publishing keeps its device until its owner removes it.

The registry-shape expectations are literals, for the reason `test_pv_device`
gives: they record what an installation carries.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import SensorStateClass
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
from custom_components.span_panel.additions import (
    async_announce_new_entities,
    async_rekey_announced,
)
from custom_components.span_panel.binary_sensor import (
    async_setup_entry as binary_sensor_setup_entry,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import (
    CurationRecord,
    async_load_curation,
    async_save_record,
)
from custom_components.span_panel.helpers import detect_capabilities
from custom_components.span_panel.pv_inverter_layout import (
    async_reconcile_pv_inverter_layout,
    primary_inverter_key,
)
from custom_components.span_panel.sensor import async_setup_entry as sensor_setup_entry
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS
from custom_components.span_panel.util import (
    SUB_DEVICE_PV,
    classify_sub_device_identifier,
    pv_inverter_device_info,
)
from custom_components.span_panel.websocket import _classify_sub_device

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .test_pv_device import PANEL_NAME, _coordinator, _entry

SOLAR_CIRCUIT: Final = "573066aaddd7b75114c4563ce3af18c4"
"""The capture's solar circuit, which feeds its one inverter."""

SECOND_SOLAR_CIRCUIT: Final = "5be1d2c3a4f5061728394a5b6c7d8e9f"
FIRST_PV: Final = "pv-1"
SECOND_PV: Final = "pv-2"
UNFED_PV: Final = "pv-3"

METADATA_KEYS: Final = ("pv_vendor", "pv_product", "pv_nameplate_capacity")

EXTENSION_PATH: Final = "acme/string-voltage"
"""A solar extension property's `{node}/{property}` wire path."""

CURATED: Final = CurationRecord(state_class=SensorStateClass.MEASUREMENT, device_class="voltage")
"""A curation record on that property, to follow through a crossing."""

_SINGLE_INVERTER_IDS: Final[tuple[tuple[str, str], ...]] = (
    ("sensor", "pv_power"),
    ("sensor", "pv_vendor"),
    ("sensor", "pv_product"),
    ("sensor", "pv_nameplate_capacity"),
    ("binary_sensor", "pv_panel_link"),
)
"""``(platform, unique_id suffix)`` a single-inverter installation holds."""


def _tree(*, second: bool = True, unfed: bool = False) -> dict[str, dict[str, str]]:
    """Rewrite the capture with its inverter renamed and others added, as r202639 publishes them.

    The second inverter's circuit sits on lower breaker spaces than the captured
    solar circuit and publishes a serial, so the primary inverter is not the
    first one and a serial is on the wire for the identity assertions to ignore.
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
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot
) -> list[MockEntityPlatform]:
    """Run both platforms' setup through real `EntityPlatform`s, as a (re)load does.

    In `async_setup_entry`'s order: the layout reconcile and the announcement
    re-key, then the curation overlay load, then the platforms.
    """
    coordinator = _coordinator(hass, entry, snapshot)
    moves = await async_reconcile_pv_inverter_layout(hass, entry, coordinator, snapshot)
    await async_rekey_announced(hass, entry, moves)
    panel_device_id = await ensure_device_registered(hass, entry, snapshot, PANEL_NAME)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel_device_id,
        curation=await async_load_curation(hass, entry),
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


# ---------------------------------------------------------------------------
# One inverter: nothing moves
# ---------------------------------------------------------------------------


async def test_a_single_inverter_keeps_every_id_it_had(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot()
    assert len(snapshot.pv_inverters) == 1
    entry = _entry(hass, "entry-pv-single", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    unique_ids = _unique_ids(hass, entry)
    pv_ids = {uid for uid in unique_ids if "_pv_" in uid or uid.endswith("_pv_power")}
    assert pv_ids == {f"span_{entry.unique_id}_{suffix}" for _, suffix in _SINGLE_INVERTER_IDS}
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert not [
        name for device in devices for _, name in device.identifiers if f"_{SUB_DEVICE_PV}_" in name
    ]
    assert not [cap for cap in detect_capabilities(snapshot) if cap.startswith("pv_inverter:")]


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


async def test_the_single_inverter_entities_are_not_created_and_pv_power_stays_aggregate(
    hass: HomeAssistant,
) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-pv-two-aggregate", snapshot.serial_number)

    await _setup(hass, entry, snapshot)

    unique_ids = _unique_ids(hass, entry)
    for platform, suffix in _SINGLE_INVERTER_IDS:
        uid = f"span_{entry.unique_id}_{suffix}"
        if suffix == "pv_power":
            assert uid in unique_ids
            assert unique_ids[uid].startswith(f"{platform}.")
        else:
            assert uid not in unique_ids, f"{suffix} would describe one inverter of two"

    solar = _device(hass, entry, f"{snapshot.serial_number}_{SUB_DEVICE_PV}")
    assert _device_of(hass, unique_ids, f"span_{entry.unique_id}_pv_power").id == solar.id
    # The two vendors differ, so the aggregate card claims neither.
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


def test_a_second_inverter_is_a_capability_that_reloads() -> None:
    """Expansion-only tokens, one per inverter, so the upgrade reloads into the new layout."""
    single = detect_capabilities(schema_one_snapshot())
    multi = detect_capabilities(schema_one_snapshot(_tree()))

    added = multi - single
    assert len([cap for cap in added if cap.startswith("pv_inverter:")]) == 2
    assert not [cap for cap in added if SOLAR_CIRCUIT in cap or SECOND_SOLAR_CIRCUIT in cap]


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
# Crossing between the one-inverter and many-inverter layouts
# ---------------------------------------------------------------------------


async def test_the_upgrade_keeps_every_entity_id(hass: HomeAssistant) -> None:
    """Crossing to several inverters keeps every entity_id.

    The single-inverter entities are re-keyed onto the primary inverter, so the
    user's entity_ids, dashboards and automations stay.
    """
    before = schema_one_snapshot()
    entry = _entry(hass, "entry-pv-upgrade", before.serial_number)
    await _unload(await _setup(hass, entry, before))
    held = _unique_ids(hass, entry)
    single = {
        suffix: held[f"span_{entry.unique_id}_{suffix}"] for _, suffix in _SINGLE_INVERTER_IDS
    }

    after = schema_one_snapshot(_tree())
    await _setup(hass, entry, after)

    unique_ids = _unique_ids(hass, entry)
    key = primary_inverter_key(after)
    assert key is not None
    assert unique_ids[f"span_{entry.unique_id}_pv_power"] == single["pv_power"]
    for _, suffix in _SINGLE_INVERTER_IDS[1:]:
        assert unique_ids[f"span_{entry.unique_id}_pv_{key}_{suffix}"] == single[suffix]
        assert f"span_{entry.unique_id}_{suffix}" not in unique_ids
    for suffix in (*METADATA_KEYS, "pv_panel_link"):
        assert len([uid for uid in unique_ids if uid.endswith(f"_{suffix}")]) == 2
    assert _device_of(
        hass, unique_ids, f"span_{entry.unique_id}_pv_{key}_pv_vendor"
    ).identifiers == {(DOMAIN, f"{after.serial_number}_{SUB_DEVICE_PV}_{key}")}


async def test_back_to_one_inverter_re_keys_its_entities_to_the_single_layout(
    hass: HomeAssistant,
) -> None:
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-pv-downgrade", one.serial_number)
    key = next(iter(one.pv_inverters))
    registry = er.async_get(hass)
    held = {
        suffix: registry.async_get_or_create(
            platform, DOMAIN, f"span_{entry.unique_id}_pv_{key}_{suffix}", config_entry=entry
        ).entity_id
        for platform, suffix in _SINGLE_INVERTER_IDS[1:]
    }

    await _setup(hass, entry, one)

    unique_ids = _unique_ids(hass, entry)
    for _, suffix in _SINGLE_INVERTER_IDS[1:]:
        assert unique_ids[f"span_{entry.unique_id}_{suffix}"] == held[suffix]


async def test_the_singleton_solar_extension_entities_follow_the_primary_inverter(
    hass: HomeAssistant,
) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-pv-extension", snapshot.serial_number)
    registry = er.async_get(hass)
    held = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"span_{snapshot.serial_number}_adopted_pv/{EXTENSION_PATH}",
        config_entry=entry,
    )
    await async_save_record(hass, entry, f"pv/{EXTENSION_PATH}", CURATED)

    await _setup(hass, entry, snapshot)

    moved = registry.async_get(held.entity_id)
    assert moved is not None
    key = primary_inverter_key(snapshot)
    assert moved.unique_id == f"span_{snapshot.serial_number}_adopted_pv_{key}/{EXTENSION_PATH}"
    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for(f"pv_{key}/{EXTENSION_PATH}") == CURATED
    assert overlay.record_for(f"pv/{EXTENSION_PATH}") is None


async def test_one_inverter_takes_the_extension_entities_and_their_curation_back(
    hass: HomeAssistant,
) -> None:
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-pv-extension-back", one.serial_number)
    key = next(iter(one.pv_inverters))
    registry = er.async_get(hass)
    held = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"span_{one.serial_number}_adopted_pv_{key}/{EXTENSION_PATH}",
        config_entry=entry,
    )
    await async_save_record(hass, entry, f"pv_{key}/{EXTENSION_PATH}", CURATED)

    await _setup(hass, entry, one)

    moved = registry.async_get(held.entity_id)
    assert moved is not None
    assert moved.unique_id == f"span_{one.serial_number}_adopted_pv/{EXTENSION_PATH}"
    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for(f"pv/{EXTENSION_PATH}") == CURATED
    assert overlay.record_for(f"pv_{key}/{EXTENSION_PATH}") is None


async def test_a_curation_record_whose_target_is_taken_stays_where_it_is(
    hass: HomeAssistant,
) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-pv-curation-collision", snapshot.serial_number)
    key = primary_inverter_key(snapshot)
    other = CurationRecord(promote=True)
    await async_save_record(hass, entry, f"pv/{EXTENSION_PATH}", CURATED)
    await async_save_record(hass, entry, f"pv_{key}/{EXTENSION_PATH}", other)

    await _setup(hass, entry, snapshot)

    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for(f"pv/{EXTENSION_PATH}") == CURATED
    assert overlay.record_for(f"pv_{key}/{EXTENSION_PATH}") == other


async def test_back_to_a_non_primary_inverter_restores_the_original_entity_ids(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """The user's original entities come back even when the primary inverter left.

    Upgrade to two inverters, whose primary is not the one the panel published
    alone, then go back to that one. The entities the upgrade moved onto the
    primary are the oldest per-inverter entities, so they are the ones moved back.
    """
    before = schema_one_snapshot()
    remaining = next(iter(before.pv_inverters))
    serial = before.serial_number
    entry = _entry(hass, "entry-pv-round-trip", serial)
    registry = er.async_get(hass)
    extension = registry.async_get_or_create(
        "sensor", DOMAIN, f"span_{serial}_adopted_pv/{EXTENSION_PATH}", config_entry=entry
    )
    await async_save_record(hass, entry, f"pv/{EXTENSION_PATH}", CURATED)
    await _unload(await _setup(hass, entry, before))
    held = _unique_ids(hass, entry)
    single = {suffix: held[f"span_{serial}_{suffix}"] for _, suffix in _SINGLE_INVERTER_IDS}

    freezer.tick(60)
    upgraded = schema_one_snapshot(_tree())
    primary = primary_inverter_key(upgraded)
    assert primary is not None and primary != remaining
    await _unload(await _setup(hass, entry, upgraded))
    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for(f"pv_{primary}/{EXTENSION_PATH}") == CURATED

    freezer.tick(60)
    await _setup(hass, entry, schema_one_snapshot())

    unique_ids = _unique_ids(hass, entry)
    for _, suffix in _SINGLE_INVERTER_IDS:
        assert unique_ids[f"span_{serial}_{suffix}"] == single[suffix]
    # The remaining inverter's own per-inverter entities stay, unavailable.
    assert f"span_{serial}_pv_{remaining}_pv_vendor" in unique_ids
    moved = registry.async_get(extension.entity_id)
    assert moved is not None
    assert moved.unique_id == f"span_{serial}_adopted_pv/{EXTENSION_PATH}"
    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for(f"pv/{EXTENSION_PATH}") == CURATED
    assert overlay.record_for(f"pv_{primary}/{EXTENSION_PATH}") is None

    # Home again: the remaining inverter's own per-inverter entities are not
    # pushed onto the user's ids at every later setup.
    caplog.clear()
    assert (
        await async_reconcile_pv_inverter_layout(
            hass, entry, entry.runtime_data.coordinator, schema_one_snapshot()
        )
        == []
    )
    assert "Not re-keying" not in caplog.text


async def test_the_announcement_record_follows_the_re_keyed_ids(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Entities moved by the crossing are not announced as new."""
    before = schema_one_snapshot()
    serial = before.serial_number
    entry = _entry(hass, "entry-pv-announced", serial)
    await _unload(await _setup(hass, entry, before))
    await async_announce_new_entities(hass, entry)

    after = schema_one_snapshot(_tree())
    await _setup(hass, entry, after)

    key = primary_inverter_key(after)
    announced = set(
        hass_storage[f"{DOMAIN}.announced.{entry.entry_id}"]["data"]["announced_unique_ids"]
    )
    for _, suffix in _SINGLE_INVERTER_IDS[1:]:
        assert f"span_{serial}_pv_{key}_{suffix}" in announced
        assert f"span_{serial}_{suffix}" not in announced
    assert f"span_{serial}_pv_power" in announced


async def test_a_target_already_registered_is_left_alone(hass: HomeAssistant) -> None:
    snapshot = schema_one_snapshot(_tree())
    entry = _entry(hass, "entry-pv-collision", snapshot.serial_number)
    key = primary_inverter_key(snapshot)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"span_{entry.unique_id}_pv_vendor", config_entry=entry
    )
    new = registry.async_get_or_create(
        "sensor", DOMAIN, f"span_{entry.unique_id}_pv_{key}_pv_vendor", config_entry=entry
    )

    await _setup(hass, entry, snapshot)

    kept = registry.async_get(old.entity_id)
    assert kept is not None and kept.unique_id == f"span_{entry.unique_id}_pv_vendor"
    assert registry.async_get(new.entity_id) is not None


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
