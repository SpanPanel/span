"""Circuit-numbers mode gives a circuit entity its circuit-number id when it is created.

A circuit's entities sit on a device named by the circuit, so Home Assistant
composes a new entity's id from the circuit's name. Circuit-numbers mode exists
so the id carries the breaker position instead, and it does so by presetting
the id for an entity being created: the id Core would compose under the user's
entity-id format if the circuit's device were named by its number.

The preset is made once. Core stores it as the row's `suggested_object_id` and
clears it the next time the entity is added, when the integration supplies
none, so from the first reload "Recreate entity IDs" composes from the
circuit's device and offers the circuit's name. An entity Core already has a
row for, live or deleted, is never given one.

The composer (`circuit_object_ids.compose_circuit_object_id`) mirrors Core's own
composition, which is private. The first test here is the guard against that: it asks Core for the id of an entity on a real
child device named "Circuit 15" and fails wherever the composer disagrees, so a
Home Assistant release that changes the rules fails here instead of handing
new installs ids Core would not have composed.

The behaviour cases run the sensor, switch and select platforms' real
`async_setup_entry` through real `EntityPlatform`s, against the real
registries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Final
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.helpers.entity_registry import EntityNamePart
from homeassistant.util import slugify
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData, ensure_device_registered
from custom_components.span_panel.circuit_number_ids import preset_circuit_number_entity_id
from custom_components.span_panel.circuit_object_ids import compose_circuit_object_id
from custom_components.span_panel.const import DOMAIN, USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.id_builder import (
    build_circuit_device_identifier,
    build_circuit_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
)
from custom_components.span_panel.select import async_setup_entry as select_setup
from custom_components.span_panel.sensor import async_setup_entry as sensor_setup
from custom_components.span_panel.switch import async_setup_entry as switch_setup

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .helpers import PANEL_NAME, add_panel_entry, register_panel_device
from .test_pv_device import _coordinator

SERIAL: Final = "sp3-242424-001"
KITCHEN: Final = "kitchen"
RANGE: Final = "range"

CIRCUIT_NUMBER: Final = "Circuit 15"
CIRCUIT_DEVICE: Final = (DOMAIN, build_circuit_device_identifier(SERIAL, KITCHEN))

POWER_UNIQUE_ID: Final = build_circuit_unique_id(SERIAL, KITCHEN, "instantPowerW")
SWITCH_UNIQUE_ID: Final = build_switch_unique_id(SERIAL, KITCHEN)
SELECT_UNIQUE_ID: Final = build_select_unique_id(SERIAL, KITCHEN)

POWER_ENTITY_ID: Final = "sensor.span_panel_circuit_15_power"
SWITCH_ENTITY_ID: Final = "switch.span_panel_circuit_15_breaker"

NAMES_MODE: Final = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
NUMBERS_MODE: Final = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


# ---------------------------------------------------------------------------
# The composer against Core
# ---------------------------------------------------------------------------

FORMATS: Final[dict[str, list[EntityNamePart] | None]] = {
    "default": None,
    "device-entity": [EntityNamePart.DEVICE, EntityNamePart.ENTITY],
    "area-device-entity": [EntityNamePart.AREA, EntityNamePart.DEVICE, EntityNamePart.ENTITY],
    "floor-area-parent-device-entity": [
        EntityNamePart.FLOOR,
        EntityNamePart.AREA,
        EntityNamePart.PARENT_DEVICE,
        EntityNamePart.DEVICE,
        EntityNamePart.ENTITY,
    ],
    # The order is the user's, and the read-back composes under whichever it is.
    "entity-device": [EntityNamePart.ENTITY, EntityNamePart.DEVICE],
    "parent-entity-device": [
        EntityNamePart.PARENT_DEVICE,
        EntityNamePart.ENTITY,
        EntityNamePart.DEVICE,
    ],
    "device-entity-parent": [
        EntityNamePart.DEVICE,
        EntityNamePart.ENTITY,
        EntityNamePart.PARENT_DEVICE,
    ],
    "area-entity-parent-device": [
        EntityNamePart.AREA,
        EntityNamePart.ENTITY,
        EntityNamePart.PARENT_DEVICE,
        EntityNamePart.DEVICE,
    ],
}
AREAS: Final = ("no-area", "panel-area", "circuit-area")
PANEL_RENAMES: Final[dict[str, str | None]] = {
    "panel-as-named": None,
    "panel-renamed": "Garage Panel",
}


@dataclass(frozen=True, slots=True)
class _Arranged:
    """A panel device arranged for one composition case, and the entry it belongs to."""

    entry: MockConfigEntry
    panel_device_id: str

    def compose(self, hass: HomeAssistant, suffix_words: str = "power") -> str:
        """Ask the composer for this panel's circuit, numbered `CIRCUIT_NUMBER`."""
        return compose_circuit_object_id(
            hass,
            panel_device_id=self.panel_device_id,
            circuit_device_identifier=CIRCUIT_DEVICE,
            device_name=CIRCUIT_NUMBER,
            suffix_words=suffix_words,
            config_entry_id=self.entry.entry_id,
        )

    def register_circuit_device(self, hass: HomeAssistant, name: str) -> dr.ChildDeviceEntry:
        """Register the circuit's device as a child of the panel's, named as given."""
        return dr.async_get(hass).async_get_or_create_child(
            config_entry_id=self.entry.entry_id,
            identifiers={CIRCUIT_DEVICE},
            name=name,
            parent_device_id=self.panel_device_id,
        )


def _arrange(
    hass: HomeAssistant, parts: list[EntityNamePart] | None, panel_name_by_user: str | None
) -> _Arranged:
    """Register a panel device under the entity-id format and the panel name a case asks for."""
    entry = add_panel_entry(hass, "entry-circuit-numbers", SERIAL)
    panel_device_id = register_panel_device(hass, entry, SERIAL)
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=parts)
    if panel_name_by_user is not None:
        dr.async_get(hass).async_update_device(panel_device_id, name_by_user=panel_name_by_user)
    return _Arranged(entry, panel_device_id)


def _basement(hass: HomeAssistant) -> str:
    """Create an area on a floor and return its id, so a floor part has something to say."""
    floor = fr.async_get(hass).async_create("Ground Floor")
    return ar.async_get(hass).async_create("Basement", floor_id=floor.floor_id).id


@pytest.mark.parametrize("parts", FORMATS.values(), ids=FORMATS.keys())
@pytest.mark.parametrize("area_on", AREAS)
@pytest.mark.parametrize("panel_name_by_user", PANEL_RENAMES.values(), ids=PANEL_RENAMES.keys())
async def test_composer_matches_core(
    hass: HomeAssistant,
    parts: list[EntityNamePart] | None,
    area_on: str,
    panel_name_by_user: str | None,
) -> None:
    """The drift guard: the composer spells what Core composes for a device named by the number.

    Core is asked through `async_regenerate_entity_id`, for a row on a real
    child device named "Circuit 15" whose base is the suffix. The composer is
    told the number and never reads the device's name, so the two agree only
    while the composer mirrors Core's rules for which parts apply.
    """
    arranged = _arrange(hass, parts, panel_name_by_user)
    circuit_device = arranged.register_circuit_device(hass, CIRCUIT_NUMBER)
    area_id = _basement(hass)
    if area_on == "panel-area":
        dr.async_get(hass).async_update_device(arranged.panel_device_id, area_id=area_id)
    elif area_on == "circuit-area":
        dr.async_get(hass).async_update_child_device(circuit_device.id, area_id=area_id)

    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        POWER_UNIQUE_ID,
        config_entry=arranged.entry,
        device_id=circuit_device.id,
        has_entity_name=True,
        object_id_base="power",
    )

    assert f"sensor.{slugify(arranged.compose(hass))}" == registry.async_regenerate_entity_id(row)


@pytest.mark.parametrize(
    ("format_id", "area_on", "panel_name_by_user", "expected"),
    [
        ("default", "no-area", None, "SPAN Panel Circuit 15 power"),
        ("default", "panel-area", None, "Basement SPAN Panel Circuit 15 power"),
        # A circuit device with an area of its own is named without its parent.
        ("default", "circuit-area", None, "Basement Circuit 15 power"),
        ("device-entity", "panel-area", None, "Circuit 15 power"),
        (
            "floor-area-parent-device-entity",
            "panel-area",
            "Garage Panel",
            "Ground Floor Basement Garage Panel Circuit 15 power",
        ),
    ],
)
async def test_composer_spells_each_part_in_the_formats_order(
    hass: HomeAssistant,
    format_id: str,
    area_on: str,
    panel_name_by_user: str | None,
    expected: str,
) -> None:
    """What the matrix above compares, spelled out, so a reader can see each rule at work."""
    arranged = _arrange(hass, FORMATS[format_id], panel_name_by_user)
    circuit_device = arranged.register_circuit_device(hass, "Kitchen Outlets")
    area_id = _basement(hass)
    if area_on == "panel-area":
        dr.async_get(hass).async_update_device(arranged.panel_device_id, area_id=area_id)
    elif area_on == "circuit-area":
        dr.async_get(hass).async_update_child_device(circuit_device.id, area_id=area_id)

    assert arranged.compose(hass) == expected


async def test_composer_answers_before_the_circuits_device_exists(hass: HomeAssistant) -> None:
    """A new circuit's entities are built before their device is registered.

    Core registers the device when it adds the first of them, with no area of
    its own, so what the composer answers beforehand is what it answers once
    the device is there: the panel's area and the panel's name.
    """
    arranged = _arrange(hass, None, None)
    dr.async_get(hass).async_update_device(arranged.panel_device_id, area_id=_basement(hass))

    before = arranged.compose(hass)
    arranged.register_circuit_device(hass, "Kitchen Outlets")

    assert before == "Basement SPAN Panel Circuit 15 power"
    assert arranged.compose(hass) == before


# ---------------------------------------------------------------------------
# Which entities are given a preset
# ---------------------------------------------------------------------------


async def test_a_new_entity_is_preset_the_available_id(hass: HomeAssistant) -> None:
    """The domain is prefixed and the object id slugified, as Core does."""
    assert (
        preset_circuit_number_entity_id(
            hass,
            domain="sensor",
            unique_id=POWER_UNIQUE_ID,
            object_id="SPAN Panel Circuit 15 power",
        )
        == POWER_ENTITY_ID
    )


async def test_a_taken_id_gets_cores_own_clash_suffix(hass: HomeAssistant) -> None:
    """The clash suffix is Core's, from `async_get_available_entity_id`."""
    er.async_get(hass).async_get_or_create(
        "sensor", "other_integration", "theirs", suggested_object_id="span_panel_circuit_15_power"
    )

    assert (
        preset_circuit_number_entity_id(
            hass,
            domain="sensor",
            unique_id=POWER_UNIQUE_ID,
            object_id="SPAN Panel Circuit 15 power",
        )
        == f"{POWER_ENTITY_ID}_2"
    )


async def test_no_preset_for_an_entity_with_a_registry_row(hass: HomeAssistant) -> None:
    """R5: an entity Home Assistant already knows keeps the id it has."""
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, POWER_UNIQUE_ID, suggested_object_id="span_panel_kitchen_outlets_power"
    )

    assert (
        preset_circuit_number_entity_id(
            hass,
            domain="sensor",
            unique_id=POWER_UNIQUE_ID,
            object_id="SPAN Panel Circuit 15 power",
        )
        is None
    )


async def test_no_preset_for_an_entity_core_remembers_deleting(hass: HomeAssistant) -> None:
    """A removed entity returns with the id Core kept for it, so there is nothing to preset."""
    registry = er.async_get(hass)
    removed = registry.async_get_or_create(
        "switch", DOMAIN, SWITCH_UNIQUE_ID, suggested_object_id="span_panel_kitchen_outlets_breaker"
    )
    registry.async_remove(removed.entity_id)
    assert ("switch", DOMAIN, SWITCH_UNIQUE_ID) in registry.deleted_entities

    assert (
        preset_circuit_number_entity_id(
            hass,
            domain="switch",
            unique_id=SWITCH_UNIQUE_ID,
            object_id="SPAN Panel Circuit 15 breaker",
        )
        is None
    )


async def test_no_preset_where_the_format_composes_nothing(hass: HomeAssistant) -> None:
    """An object id with nothing in it is left for Core, which has a fallback of its own."""
    assert (
        preset_circuit_number_entity_id(
            hass, domain="sensor", unique_id=POWER_UNIQUE_ID, object_id=""
        )
        is None
    )


# ---------------------------------------------------------------------------
# A panel set up through the real platforms
# ---------------------------------------------------------------------------


def _circuit(
    circuit_id: str, name: str, tabs: list[int], *, controllable: bool = True
) -> SpanCircuitSnapshot:
    """Build a circuit that reports every reading, its relay operable or locked by the panel."""
    return SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=name,
        tabs=tabs,
        is_user_controllable=controllable,
        is_never_backup=not controllable,
        priority="SOC_THRESHOLD" if controllable else "NEVER",
        current_a=1.5,
        breaker_rating_a=20.0,
    )


def _panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    """Build the panel's snapshot from its circuits."""
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL, circuits={circuit.circuit_id: circuit for circuit in circuits}
    )


KITCHEN_OUTLETS: Final = _circuit(KITCHEN, "Kitchen Outlets", [15])
KITCHEN_OUTLETS_LOCKED: Final = _circuit(KITCHEN, "Kitchen Outlets", [15], controllable=False)
ELECTRIC_RANGE: Final = _circuit(RANGE, "Range", [30, 32])


def _entry(hass: HomeAssistant, options: dict[str, bool]) -> MockConfigEntry:
    """Add the panel's config entry in the naming mode given."""
    entry = add_panel_entry(hass, "entry-circuit-numbers", SERIAL)
    hass.config_entries.async_update_entry(entry, options=dict(options))
    return entry


@dataclass
class _Loaded:
    """The platforms one setup added entities through."""

    coordinator: MagicMock
    platforms: list[MockEntityPlatform]

    async def unload(self) -> None:
        """Tear the platforms down, as unloading the entry does."""
        for platform in self.platforms:
            await platform.async_reset()


async def _set_up(
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot
) -> _Loaded:
    """Run the three circuit platforms' real `async_setup_entry` through real `EntityPlatform`s."""
    coordinator = _coordinator(hass, entry, snapshot)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=await ensure_device_registered(hass, entry, snapshot, PANEL_NAME),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    platforms: list[MockEntityPlatform] = []
    setups: tuple[tuple[str, Callable[..., object]], ...] = (
        ("sensor", sensor_setup),
        ("switch", switch_setup),
        ("select", select_setup),
    )
    for domain, setup in setups:
        added: list[object] = []
        await setup(hass, entry, lambda entities, added=added, **_: added.extend(entities))
        platform = MockEntityPlatform(
            hass, domain=domain, platform_name=DOMAIN, logger=logging.getLogger(__name__)
        )
        platform.config_entry = entry
        await platform.platform_data.async_load_translations()
        await platform.async_add_entities(added)
        platforms.append(platform)
    await hass.async_block_till_done()
    return _Loaded(coordinator, platforms)


async def _reload(
    hass: HomeAssistant, entry: MockConfigEntry, loaded: _Loaded, snapshot: SpanPanelSnapshot
) -> _Loaded:
    """Unload and set up again from `snapshot`, against the registries that survived."""
    await loaded.unload()
    return await _set_up(hass, entry, snapshot)


def _circuit_entity_ids(hass: HomeAssistant, entry: MockConfigEntry, circuit_id: str) -> set[str]:
    """Return the id of every entity on one circuit's device."""
    device = dr.async_get(hass).async_get_child_device_by_identifier(
        (DOMAIN, build_circuit_device_identifier(SERIAL, circuit_id)), entry.entry_id
    )
    assert device is not None
    return {
        row.entity_id
        for row in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
    }


async def test_numbers_mode_new_install_gets_circuit_number_ids(hass: HomeAssistant) -> None:
    """Every circuit entity a new install creates carries its breaker position.

    The circuits' devices are named by the circuits all the same: the number is
    in the ids only.
    """
    entry = _entry(hass, NUMBERS_MODE)

    await _set_up(hass, entry, _panel(KITCHEN_OUTLETS, ELECTRIC_RANGE))

    assert _circuit_entity_ids(hass, entry, KITCHEN) == {
        "sensor.span_panel_circuit_15_power",
        "sensor.span_panel_circuit_15_current",
        "sensor.span_panel_circuit_15_breaker_rating",
        "sensor.span_panel_circuit_15_produced_energy",
        "sensor.span_panel_circuit_15_consumed_energy",
        "sensor.span_panel_circuit_15_net_energy",
        "switch.span_panel_circuit_15_breaker",
        "select.span_panel_circuit_15_circuit_priority",
    }
    assert _circuit_entity_ids(hass, entry, RANGE) == {
        "sensor.span_panel_circuit_30_32_power",
        "sensor.span_panel_circuit_30_32_current",
        "sensor.span_panel_circuit_30_32_breaker_rating",
        "sensor.span_panel_circuit_30_32_produced_energy",
        "sensor.span_panel_circuit_30_32_consumed_energy",
        "sensor.span_panel_circuit_30_32_net_energy",
        "switch.span_panel_circuit_30_32_breaker",
        "select.span_panel_circuit_30_32_circuit_priority",
    }
    device = dr.async_get(hass).async_get_child_device_by_identifier(CIRCUIT_DEVICE, entry.entry_id)
    assert device is not None
    assert device.name == "Kitchen Outlets"


async def test_names_mode_presets_nothing(hass: HomeAssistant) -> None:
    """Circuit names leave the id to Core, which composes it from the circuit's device."""
    entry = _entry(hass, NAMES_MODE)

    await _set_up(hass, entry, _panel(KITCHEN_OUTLETS))

    registry = er.async_get(hass)
    row = registry.async_get("sensor.span_panel_kitchen_outlets_power")
    assert row is not None
    assert row.unique_id == POWER_UNIQUE_ID
    assert row.suggested_object_id is None
    assert row.object_id_base == "power"


async def test_preset_is_gone_after_the_next_reload(hass: HomeAssistant) -> None:
    """The preset is made at creation only, so Recreate then offers the circuit's name.

    Core stores a preset as the row's `suggested_object_id`, which it composes
    from ahead of everything but a name the user set. The reload supplies none,
    so Core clears it and composes from the circuit's device and the suffix
    from then on. The id itself stays where it is (R5).
    """
    entry = _entry(hass, NUMBERS_MODE)
    registry = er.async_get(hass)

    loaded = await _set_up(hass, entry, _panel(KITCHEN_OUTLETS))
    created = registry.async_get(POWER_ENTITY_ID)
    assert created is not None
    assert created.unique_id == POWER_UNIQUE_ID
    assert created.suggested_object_id == "span_panel_circuit_15_power"

    await _reload(hass, entry, loaded, _panel(KITCHEN_OUTLETS))

    row = registry.async_get(POWER_ENTITY_ID)
    assert row is not None
    assert row.suggested_object_id is None
    assert row.object_id_base == "power"
    assert registry.async_regenerate_entity_id(row) == "sensor.span_panel_kitchen_outlets_power"


async def test_a_switch_added_later_gets_its_circuit_number_id(hass: HomeAssistant) -> None:
    """An entity added to a circuit that already has its device is numbered like the rest.

    The circuit's device exists by then and is named by the circuit, so
    without the preset Core would compose the new switch from that name.
    """
    entry = _entry(hass, NUMBERS_MODE)
    registry = er.async_get(hass)
    loaded = await _set_up(hass, entry, _panel(KITCHEN_OUTLETS_LOCKED))
    loaded = await _reload(hass, entry, loaded, _panel(KITCHEN_OUTLETS_LOCKED))
    assert registry.async_get_entity_id("switch", DOMAIN, SWITCH_UNIQUE_ID) is None
    before = _circuit_entity_ids(hass, entry, KITCHEN)

    await _reload(hass, entry, loaded, _panel(KITCHEN_OUTLETS))

    assert registry.async_get_entity_id("switch", DOMAIN, SWITCH_UNIQUE_ID) == SWITCH_ENTITY_ID
    assert _circuit_entity_ids(hass, entry, KITCHEN) == before | {
        SWITCH_ENTITY_ID,
        "select.span_panel_circuit_15_circuit_priority",
    }


async def test_no_preset_for_a_withdrawn_switch(hass: HomeAssistant) -> None:
    """A switch the panel withdrew and offers again returns with its old id, and no preset.

    Setup removes a withdrawn switch and Core keeps its tombstone, which is not
    an active row: the unique id looks new. The id Core restores is the one the
    user knew, so the integration hands Core nothing to compose from ahead of it.
    """
    entry = _entry(hass, NUMBERS_MODE)
    registry = er.async_get(hass)
    old_id = registry.async_get_or_create(
        "switch",
        DOMAIN,
        SWITCH_UNIQUE_ID,
        config_entry=entry,
        suggested_object_id="span_panel_kitchen_outlets_breaker",
    ).entity_id
    loaded = await _set_up(hass, entry, _panel(KITCHEN_OUTLETS_LOCKED))
    assert registry.async_get_entity_id("switch", DOMAIN, SWITCH_UNIQUE_ID) is None
    assert ("switch", DOMAIN, SWITCH_UNIQUE_ID) in registry.deleted_entities

    await _reload(hass, entry, loaded, _panel(KITCHEN_OUTLETS))

    assert registry.async_get_entity_id("switch", DOMAIN, SWITCH_UNIQUE_ID) == old_id
    restored = registry.async_get(old_id)
    assert restored is not None
    assert restored.suggested_object_id is None
    assert restored.object_id_base == "breaker"


async def test_the_panels_area_reaches_a_preset_id_through_the_circuits_device(
    hass: HomeAssistant,
) -> None:
    """R2: a preset id carries the user's area, as the id Core composes would.

    The area is the panel's. The circuit's device has none of its own and
    inherits it, which is how Core finds an area for an entity on a child
    device, and the preset follows the same route.
    """
    entry = _entry(hass, NUMBERS_MODE)
    panel_device_id = register_panel_device(hass, entry, SERIAL)
    area = ar.async_get(hass).async_create("Basement")
    dr.async_get(hass).async_update_device(panel_device_id, area_id=area.id)

    await _set_up(hass, entry, _panel(KITCHEN_OUTLETS))

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, POWER_UNIQUE_ID)
        == "sensor.basement_span_panel_circuit_15_power"
    )
    assert (
        registry.async_get_entity_id("switch", DOMAIN, SWITCH_UNIQUE_ID)
        == "switch.basement_span_panel_circuit_15_breaker"
    )
    device = dr.async_get(hass).async_get_child_device_by_identifier(CIRCUIT_DEVICE, entry.entry_id)
    assert device is not None
    assert device.area_id is None


async def test_a_saved_entity_id_format_shapes_the_preset(hass: HomeAssistant) -> None:
    """R2: the user's `entity_id_parts` decide which parts a preset id has."""
    entry = _entry(hass, NUMBERS_MODE)
    er.async_get(hass).async_update_settings(
        entity_id_parts=[EntityNamePart.DEVICE, EntityNamePart.ENTITY]
    )

    await _set_up(hass, entry, _panel(KITCHEN_OUTLETS))

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, POWER_UNIQUE_ID) == "sensor.circuit_15_power"
    )
    assert (
        registry.async_get_entity_id("select", DOMAIN, SELECT_UNIQUE_ID)
        == "select.circuit_15_circuit_priority"
    )


async def test_a_meter_without_a_breaker_space_is_created_with_its_label(
    hass: HomeAssistant,
) -> None:
    """A meter outside the panel has no position to number, so its id carries its label.

    The preset is built from the identifier the naming flag resolves, which for
    such a meter is its label in both modes, and not from a number made up
    from its id.
    """
    entry = _entry(hass, NUMBERS_MODE)
    meter = SpanCircuitSnapshotFactory.create(
        circuit_id="meter-3",
        name="",
        tabs=[],
        is_user_controllable=False,
        measures_outside_panel=True,
    )

    await _set_up(hass, entry, _panel(meter))

    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, build_circuit_unique_id(SERIAL, "meter-3", "instantPowerW")
        )
        == "sensor.span_panel_remote_ct_3_power"
    )
