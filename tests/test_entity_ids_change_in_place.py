"""An entity-id edit changes every entity of this integration in place.

Home Assistant 2026.11 handles an edit to a live entity's id in one of two ways
(`Entity._async_process_registry_update_or_remove` in `helpers/entity.py`). In
place, the state under the old id is removed, the entity takes the new one,
Core moves what it keeps for the entity -- the platform's index, entity
sources, the registry tracker and the restore-state tracking -- and then calls
`async_entity_id_changed` so the entity can move what it keeps itself. Or it
removes the entity and adds it again, which it does to a custom integration's
entity wherever a class in its MRO defines `async_added_to_hass`,
`async_will_remove_from_hass` or `async_prepare_to_add_to_hass` and neither
that class nor a subclass of it defines `async_entity_id_changed`. That path
is for compatibility, and Core says it can be removed in 2027.11.

Every class here inherits both hooks from `SpanPanelEntity`, and two define
`async_added_to_hass` again, so each of those three defines
`async_entity_id_changed` beside them. These tests hold the result for every
class, through Home Assistant's real setup of a panel that builds every kind
of entity the integration has: no entity is removed or added again, and what
the entities keep moves or stays as it should.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Iterator
import importlib
import json
import logging
from typing import Final
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, restore_state
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform, async_get_platforms
import pytest
from pytest_homeassistant.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanPanelSnapshot

import custom_components.span_panel as span_panel
from custom_components.span_panel import PLATFORMS, SpanPanelRuntimeData
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
    ENABLE_ENERGY_DIP_COMPENSATION,
)
from custom_components.span_panel.control_gate import ControlMode
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.entity import SpanPanelEntity
from custom_components.span_panel.id_builder import build_circuit_unique_id
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import CONTROL_LOCK_TIMEOUT, CONTROL_MODE
from custom_components.span_panel.runtime import loaded_runtime_data
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import PANEL_ENERGY_SENSORS
from custom_components.span_panel.sensor_panel import SpanPanelEnergySensor
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

from .adapter_fixtures import (
    FIRST_PV,
    SCHEMA_ONE_PANEL,
    SECOND_PV,
    SECOND_SOLAR_CIRCUIT,
    pv_inverters_tree,
    schema_one_tree,
)
from .captures_replay import ReplayClient, RetainedTree, description, replay
from .factories import SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device, watch_add_and_remove

PANEL_NAME: Final = "Span Panel"

CHARGER: Final = "evse"
"""The capture's first charger, whose connector lock is declared read-only."""

GENERATOR: Final = "generator-1"
"""A device of a type this integration models nothing for, added so it is adopted."""

VENDOR_NODE: Final = "vendor"
"""A node of vendor readings added to the panel's own device, which become extension entities."""

KITCHEN_LIGHTS: Final = "0ab966b95f92a6a51ec548485aa85f54"
"""A circuit of the capture whose name does not end on what its power sensor measures."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Let the switch platform's relock and debounce timers outlive a test."""
    return True


# ---------------------------------------------------------------------------
# A panel that builds every kind of entity
# ---------------------------------------------------------------------------


def _member(container: object, key: str) -> dict[str, object]:
    """Return one member of a parsed `$description`, itself an object, to rewrite in place."""
    assert isinstance(container, dict), f"what holds {key!r} is not an object"
    member = container[key]
    assert isinstance(member, dict), f"{key!r} is not an object"
    return member


def _every_kind_tree() -> RetainedTree:
    """Grow the schema-1 reference capture until the integration builds every kind of entity.

    Each addition is published as a panel publishes it:

    - a second inverter, fed by a circuit of its own (`pv_inverters_tree`),
      which gets an inverter card beside the Solar card;
    - the charger's connector lock declared settable, which makes it a control;
    - a device of a type the integration models nothing for, with a property
      for each platform an adopted property can take;
    - two vendor readings on the panel's own device, which become extension
      entities on the panel's card.

    The devices added are listed among the panel's children, without which the
    tree never completes and the replay never becomes ready.
    """
    tree = pv_inverters_tree()

    panel = dict(description(tree, SCHEMA_ONE_PANEL))
    children = panel["children"]
    assert isinstance(children, list)
    panel["children"] = [
        *(FIRST_PV if child == "pv" else child for child in children),
        SECOND_PV,
        SECOND_SOLAR_CIRCUIT,
        GENERATOR,
    ]
    panel["nodes"] = {
        **_member(panel, "nodes"),
        VENDOR_NODE: {
            "name": "Vendor",
            "type": "com.example.vendor",
            "properties": {
                "fan-speed": {"name": "Fan speed", "datatype": "float", "unit": "rpm"},
                "fan-running": {"name": "Fan running", "datatype": "boolean"},
            },
        },
    }
    tree[SCHEMA_ONE_PANEL] = {
        **tree[SCHEMA_ONE_PANEL],
        "$description": json.dumps(panel),
        f"{VENDOR_NODE}/fan-speed": "1200",
        f"{VENDOR_NODE}/fan-running": "true",
    }

    charger = dict(description(tree, CHARGER))
    lock_state = _member(
        _member(_member(_member(charger, "nodes"), "switch"), "properties"), "lock-state"
    )
    lock_state["settable"] = True
    tree[CHARGER] = {**tree[CHARGER], "$description": json.dumps(charger)}

    generator = {
        "homie": "5.0",
        "version": 1,
        "type": "energy.ebus.device.generator",
        "name": "Backup Generator",
        "nodes": {
            "meter": {
                "name": "Meter",
                "type": "energy.ebus.capability.meter",
                "properties": {"active-power": {"name": "Power", "datatype": "float", "unit": "W"}},
            },
            "status": {
                "name": "Status",
                "type": "com.example.status",
                "properties": {"running": {"name": "Running", "datatype": "boolean"}},
            },
            "control": {
                "name": "Control",
                "type": "com.example.control",
                "properties": {
                    "enabled": {"name": "Enabled", "datatype": "boolean", "settable": True},
                    "mode": {
                        "name": "Mode",
                        "datatype": "enum",
                        "format": "AUTO,MANUAL",
                        "settable": True,
                    },
                    "setpoint": {
                        "name": "Setpoint",
                        "datatype": "float",
                        "format": "0:100:1",
                        "settable": True,
                    },
                },
            },
        },
        "children": [],
        "root": SCHEMA_ONE_PANEL,
        "parent": SCHEMA_ONE_PANEL,
        "extensions": [],
    }
    tree[GENERATOR] = {
        "$description": json.dumps(generator),
        "$state": "ready",
        "meter/active-power": "2400",
        "status/running": "true",
        "control/enabled": "false",
        "control/mode": "AUTO",
        "control/setpoint": "50",
    }
    return tree


async def _install(hass: HomeAssistant, tree: RetainedTree) -> MockConfigEntry:
    """Set the tree up the way a fresh installation is set up, with every control and the lock."""
    adapter = replay(tree)
    serial = adapter.build_snapshot().serial_number
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.0.2.10",
            "device_name": PANEL_NAME,
            CONF_EBUS_BROKER_HOST: f"{serial}.local",
            CONF_EBUS_BROKER_USERNAME: "replay-user",
            CONF_EBUS_BROKER_PASSWORD: "replay-password",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        options={CONTROL_MODE: ControlMode.ALL_USERS.value, CONTROL_LOCK_TIMEOUT: 5},
        title=PANEL_NAME,
        entry_id="entry-in-place",
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.span_panel.SpanMqttClient", return_value=ReplayClient(adapter)):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
    return entry


def _span_platform(hass: HomeAssistant, platform: Platform) -> EntityPlatform:
    """Return the entity platform the entry forwarded for this domain."""
    (found,) = [
        entity_platform
        for entity_platform in async_get_platforms(hass, DOMAIN)
        if entity_platform.domain == platform.value
    ]
    return found


def _first(hass: HomeAssistant, platform: Platform, entity_class: type[Entity]) -> Entity:
    """Return the live entity of exactly this class with the first id, on its platform."""
    entities = _span_platform(hass, platform).entities
    return next(
        entities[entity_id]
        for entity_id in sorted(entities)
        if type(entities[entity_id]) is entity_class
    )


def _subclasses(cls: type[SpanPanelEntity]) -> Iterator[type[SpanPanelEntity]]:
    """Yield every subclass of `cls`, however far down."""
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


def _entity_classes() -> frozenset[type[SpanPanelEntity]]:
    """Return every entity class the integration defines that none of its other classes extends.

    Core decides how to change an id from the class of the live entity, by its
    whole MRO, so an entity of every one of these reaches every class the
    integration has. Classes a test defines are not the integration's and are
    left out. Each platform is imported first, so a class no test has imported
    yet is still found.
    """
    for platform in PLATFORMS:
        importlib.import_module(f"{span_panel.__name__}.{platform.value}")
    ours = {
        cls
        for cls in _subclasses(SpanPanelEntity)
        if cls.__module__.startswith(f"{span_panel.__name__}.")
    }
    return frozenset(
        cls
        for cls in ours
        if not any(other is not cls and issubclass(other, cls) for other in ours)
    )


def _platforms_of(entity_class: type[Entity]) -> set[Platform]:
    """Return the platforms whose Core entity class this class extends, by that class's package."""
    return {
        platform
        for platform in PLATFORMS
        if any(
            base.__module__.split(".")[:3] == ["homeassistant", "components", platform.value]
            for base in entity_class.__mro__
        )
    }


def test_every_entity_class_is_an_entity_of_one_platform_the_entry_forwards() -> None:
    """The platform cases below reach every class between them, each class in one of them."""
    classes = _entity_classes()
    assert classes, "found no entity class, so the platform cases reach nothing"

    platforms = {cls.__name__: sorted(_platforms_of(cls)) for cls in classes}

    assert {name: len(found) for name, found in platforms.items()} == dict.fromkeys(platforms, 1)


# ---------------------------------------------------------------------------
# Every kind of entity is changed in place
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("platform", PLATFORMS, ids=[platform.value for platform in PLATFORMS])
async def test_an_id_edit_changes_every_kind_of_entity_on_the_platform_in_place(
    hass: HomeAssistant, platform: Platform
) -> None:
    """No entity is removed or added again when its id is edited: Core moves the one that is live.

    One live entity of each class the platform holds has its id edited, and its
    own add and remove hooks are watched while Core handles the edit. Removed
    and added again, each ran its add hook a second time: an energy sensor
    restored its dip state, and the control lock its armed state, from what
    the removal had just stored.

    Every class the integration defines for the platform has to be among them:
    adopted and extension entities, which are disabled until a user enables
    them, are enabled here.
    """
    await _install(hass, _every_kind_tree())
    entity_platform = _span_platform(hass, platform)

    built: dict[type[Entity], Entity] = {}
    for entity_id in sorted(entity_platform.entities):
        entity = entity_platform.entities[entity_id]
        built.setdefault(type(entity), entity)
    defined = {cls for cls in _entity_classes() if platform in _platforms_of(cls)}
    assert defined, f"the integration defines no {platform.value} entity, so this reads nothing"
    assert sorted(cls.__name__ for cls in defined - set(built)) == []

    registry = er.async_get(hass)
    for entity_class, entity in sorted(built.items(), key=lambda item: item[0].__name__):
        old = entity.entity_id
        new = f"{old}_edited"

        with watch_add_and_remove(entity) as calls:
            registry.async_update_entity(old, new_entity_id=new)
            await hass.async_block_till_done()

        assert calls.count == (0, 0), f"{entity_class.__name__} was removed and added again"
        assert entity.entity_id == new
        assert entity_platform.entities.get(new) is entity
        assert hass.states.get(old) is None
        assert hass.states.get(new) is not None


# ---------------------------------------------------------------------------
# What the entities keep
# ---------------------------------------------------------------------------


async def _unload_the_entry(hass: HomeAssistant, entry: MockConfigEntry, _entity_id: str) -> None:
    """Unload the entry, which removes every entity it added."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _disable_the_entity(hass: HomeAssistant, _entry: MockConfigEntry, entity_id: str) -> None:
    """Disable the entity as a user does, which removes it while the entry stays loaded."""
    er.async_get(hass).async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "leave",
    [
        pytest.param(_unload_the_entry, id="the-entry-unloads"),
        pytest.param(_disable_the_entity, id="the-user-disables-it"),
    ],
)
@pytest.mark.parametrize(
    ("platform", "entity_class"),
    [
        pytest.param(Platform.SENSOR, SpanCircuitPowerSensor, id="its-description-and-residuals"),
        pytest.param(Platform.SWITCH, SpanPanelCircuitsSwitch, id="its-residuals-alone"),
    ],
)
async def test_the_fields_an_entity_reads_follow_its_id_and_leave_with_it(
    hass: HomeAssistant,
    platform: Platform,
    entity_class: type[Entity],
    leave: Callable[[HomeAssistant, MockConfigEntry, str], Awaitable[None]],
) -> None:
    """Repairs name an entity by the id it has, and forget it once it is gone.

    The coordinator records which entities read which snapshot field, keyed by
    their ids, and a schema Repair lists them from it. The one entry an edit
    changes is the edited entity's, under every field it reads, and every
    other entity reading those fields stays. Removed afterwards, the entity is
    forgotten under the id it has, which is the one it is counted under, so
    neither id is left. Left under its old id, a user-disabled entity would go
    on swelling a Repair's count for as long as the coordinator lives.
    """
    entry = await _install(hass, schema_one_tree())
    runtime_data = loaded_runtime_data(entry)
    assert runtime_data is not None
    coordinator = runtime_data.coordinator
    entity = _first(hass, platform, entity_class)
    old = entity.entity_id
    new = f"{old}_edited"
    before = coordinator.entity_ids_by_field_path
    assert [path for path, entity_ids in before.items() if old in entity_ids], (
        "the entity reads no field, so there is nothing to follow it"
    )

    er.async_get(hass).async_update_entity(old, new_entity_id=new)
    await hass.async_block_till_done()

    assert coordinator.entity_ids_by_field_path == {
        path: sorted(new if entity_id == old else entity_id for entity_id in entity_ids)
        for path, entity_ids in before.items()
    }

    await leave(hass, entry, new)

    left = {
        entity_id
        for entity_ids in coordinator.entity_ids_by_field_path.values()
        for entity_id in entity_ids
    }
    assert {old, new}.isdisjoint(left)


def _compensating_coordinator(hass: HomeAssistant, snapshot: SpanPanelSnapshot) -> MagicMock:
    """Stand in for the coordinator of an entry that compensates energy dips, online."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = hass
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.dip_offset = MagicMock(return_value=0.0)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        options={ENABLE_ENERGY_DIP_COMPENSATION: True},
        title=PANEL_NAME,
        unique_id=snapshot.serial_number,
    )
    entry.add_to_hass(hass)
    coordinator.config_entry = entry
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, snapshot.serial_number),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    return coordinator


async def _consumed_energy_reads(
    hass: HomeAssistant, sensor: SpanPanelEnergySensor, coordinator: MagicMock, wh: float
) -> None:
    """Deliver a snapshot reading `wh` on the main meter to the sensor, as the coordinator does."""
    coordinator.data = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=wh)
    sensor._handle_coordinator_update()
    await hass.async_block_till_done()


async def test_an_energy_sensor_keeps_its_dip_offset_and_grace_state_across_an_id_edit(
    hass: HomeAssistant,
) -> None:
    """The edit costs the counter nothing it holds, and a shutdown stores it all under the new id.

    The main meter's Consumed Energy reads 1250 Wh and then 1200: a dip of 50,
    compensated and not yet settled. After its id is edited it still holds
    that offset, the pending dip and the last valid reading the grace period
    serves, and Core stores them under the new id at the next shutdown. The
    next reading is compensated by the same offset, and through an outage the
    grace period holds the reading under the new id.
    """
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _compensating_coordinator(hass, snapshot)
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )
    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)
    platform = MockEntityPlatform(
        hass, domain="sensor", platform_name=DOMAIN, logger=logging.getLogger(__name__)
    )
    platform.config_entry = coordinator.config_entry
    await platform.async_add_entities([sensor])
    await hass.async_block_till_done()
    await _consumed_energy_reads(hass, sensor, coordinator, 1250.0)
    await _consumed_energy_reads(hass, sensor, coordinator, 1200.0)
    held = sensor.extra_restore_state_data.as_dict()
    assert (held["energy_offset"], held["pending_dip_delta"], held["last_valid_state"]) == (
        50.0,
        50.0,
        1250.0,
    )
    assert held["last_valid_changed"] is not None
    old = sensor.entity_id
    new = "sensor.main_meter_consumed_energy_edited"

    with watch_add_and_remove(sensor) as calls:
        er.async_get(hass).async_update_entity(old, new_entity_id=new)
        await hass.async_block_till_done()

    assert calls.count == (0, 0)
    assert sensor.extra_restore_state_data.as_dict() == held
    stored = {
        stored.state.entity_id: stored
        for stored in restore_state.async_get(hass).async_get_stored_states()
    }
    assert old not in stored
    extra_data = stored[new].extra_data
    assert extra_data is not None
    assert extra_data.as_dict() == held

    await _consumed_energy_reads(hass, sensor, coordinator, 1210.0)
    reading = hass.states.get(new)
    assert reading is not None
    assert reading.state == "1260.0"

    coordinator.panel_offline = True
    await _consumed_energy_reads(hass, sensor, coordinator, 1210.0)
    offline = hass.states.get(new)
    assert offline is not None
    assert offline.state == "1260.0"
    assert offline.attributes["last_valid_state"] == "1260.0"
    assert offline.attributes["using_grace_period"] == "True"
    assert hass.states.get(old) is None


# ---------------------------------------------------------------------------
# The base of an id edited as the entry sets up
# ---------------------------------------------------------------------------


async def test_an_id_edited_as_the_entry_sets_up_is_given_the_base_it_calls_for(
    hass: HomeAssistant,
) -> None:
    """The listener that keeps circuit bases current is in place before any circuit entity is.

    Core changes a live entity's id in place and writes no base for it, so the
    listener is the one thing that reads an edited id again
    (`circuit_object_ids.async_keep_circuit_id_bases_current`). Here a circuit's
    power sensor is edited to stop at its device's name the moment the
    platforms are forwarded, before the entry has finished setting up, and the
    row is given the empty base that id calls for, so Recreate offers it
    nothing. Registered once the setup had finished, the listener missed the
    edit and the old id was offered back until the next restart.
    """
    registry = er.async_get(hass)
    forward = hass.config_entries.async_forward_entry_setups
    edited: list[str] = []

    async def forward_then_edit(entry: ConfigEntry, platforms: Iterable[Platform | str]) -> None:
        await forward(entry, platforms)
        created = registry.async_get_entity_id(
            Platform.SENSOR,
            DOMAIN,
            build_circuit_unique_id(entry.unique_id or "", KITCHEN_LIGHTS, "instantPowerW"),
        )
        assert created is not None
        assert created.endswith("_power")
        edited.append(created.removesuffix("_power"))
        registry.async_update_entity(created, new_entity_id=edited[0])

    with patch.object(hass.config_entries, "async_forward_entry_setups", forward_then_edit):
        await _install(hass, schema_one_tree())

    (entity_id,) = edited
    row = registry.async_get(entity_id)
    assert row is not None
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == ("", entity_id)
