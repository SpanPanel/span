"""Recreate entity IDs proposes the ID the current panel data would produce.

Issue #252: renaming a circuit in the SPAN app left "Recreate entity IDs" (the
HA registry's `async_regenerate_entity_id`) proposing the entity's own ID, so
the button appeared to do nothing.

The registry generates an ID from three fields in priority order: the user's
`name` override, then `suggested_object_id`, then `object_id_base`. In
friendly-names mode this integration never writes a registry `name` -- the
panel name reaches the UI through `original_name` -- so `name` is None, the
generator short-circuits to `suggested_object_id`, and that field is whatever
was suggested when the entity was first added. It was frozen because the
integration preset the entity's *stored* ID on every reload, suggesting the
value already in place.

Every case here reloads before asserting. Asserting straight after creation
tests the first-add path, where the suggestion is trivially current and the bug
cannot appear.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from unittest.mock import MagicMock, patch

import attr
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import EntityNamePart
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockEntityPlatform,
)
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    circuit_object_ids,
    ensure_device_registered,
)
from custom_components.span_panel.circuit_object_ids import async_keep_circuit_id_bases_current
from custom_components.span_panel.const import (
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.entity import SpanPanelEntity
from custom_components.span_panel.id_builder import (
    build_circuit_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
)
from custom_components.span_panel.select import (
    CIRCUIT_PRIORITY_DESCRIPTION,
    SpanPanelCircuitsSelect,
)
from custom_components.span_panel.sensor_circuit import (
    SpanCircuitEnergySensor,
    SpanCircuitPowerSensor,
)
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .helpers import watch_add_and_remove

CIRCUIT_ID = "15"
SERIAL = "sp3-recreate-001"

ORIGINAL_NAME = "Refrigerator"
RENAMED = "Beer Fridge"

ORIGINAL_ENTITY_ID = "sensor.span_panel_refrigerator_power"
RENAMED_ENTITY_ID = "sensor.span_panel_beer_fridge_power"
CIRCUIT_NUMBERS_ENTITY_ID = "sensor.span_panel_circuit_15_power"

FRIENDLY_NAMES = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
CIRCUIT_NUMBERS = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

POWER_DESCRIPTION = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_power")


def _snapshot(circuit_name: str) -> SpanPanelSnapshot:
    """Build a one-circuit panel snapshot with the circuit named as given."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=CIRCUIT_ID, name=circuit_name, tabs=[15]
    )
    return SpanPanelSnapshotFactory.create(serial_number=SERIAL, circuits={CIRCUIT_ID: circuit})


def _coordinator(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, entry: MockConfigEntry
) -> MagicMock:
    """Build a coordinator standing in for a live one, bound to the real hass."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.config_entry = entry
    coordinator.request_reload = MagicMock()
    coordinator.register_energy_sensor = MagicMock()
    coordinator.dip_offset = MagicMock(return_value=0.0)
    return coordinator


class _Install[E: SpanPanelEntity]:
    """One install of one platform's circuit entity, reloadable.

    `load` a second time is what a reload is: the entry's entities are torn down
    and rebuilt from the current snapshot, against the entity registry that
    survived. Nothing here is faked -- `async_add_entities` is the real
    `EntityPlatform` path, so a preset entity_id travels the same route into
    `async_get_or_create` that it does in a running install, and the teardown is
    the same `async_reset` an entry unload performs.

    Four platforms take that route and it is the same route for all four, so
    there is one `load` and each subclass says only what differs: the domain it
    registers under and the entity it constructs. A subclass that owned a copy of
    `load` instead would be free to drift away from the path under test, which is
    the one thing these cases exist to exercise.
    """

    _domain: ClassVar[str]
    """The entity platform domain this install registers under."""

    label: ClassVar[str]
    """The label the entity carries; the circuit's name is its device's."""

    def __init__(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._platform: MockEntityPlatform | None = None
        self._coordinator: MagicMock | None = None
        self._entity: E | None = None

    @property
    def coordinator(self) -> MagicMock:
        """The coordinator behind the entity this install last loaded."""
        assert self._coordinator is not None, "nothing has been loaded yet"
        return self._coordinator

    def _build(self, coordinator: MagicMock, snapshot: SpanPanelSnapshot) -> E:
        """Construct the one entity this install adds. Subclasses answer."""
        raise NotImplementedError

    async def rename_on_the_panel(self, new_name: str) -> None:
        """Deliver a snapshot in which the circuit was renamed, as a push does.

        The real phase 2 path and not a stand-in for it: a poll finds a changed
        `circuit.name` and the coordinator notifies its listeners, which is
        `_handle_coordinator_update` -- the method each of the three platforms
        hangs its name sync off. Nothing else about the entity is touched, so
        what happens next is the platform's own decision.

        The install pushes to the entity it loaded rather than being handed one,
        because those two must be the same object for the push to reach the
        platform whose coordinator this is.
        """
        assert self._entity is not None, "nothing has been loaded yet"
        self.coordinator.data = _snapshot(new_name)
        self._entity._handle_coordinator_update()
        await self._hass.async_block_till_done()

    async def load(self, circuit_name: str, *, added: bool = True) -> E:
        """Tear down any previous platform, then set one up from fresh panel data.

        With `added` False the entity's row is disabled, so Core registers it
        and does not add it.
        """
        if self._platform is not None:
            await self._platform.async_reset()

        snapshot = _snapshot(circuit_name)
        coordinator = _coordinator(self._hass, snapshot, self._entry)
        self._coordinator = coordinator
        # The panel device as setup registers it, under the entry's device
        # name. A circuit entity no longer re-asserts the panel's device info,
        # so nothing after this renames it.
        panel_device_id = await ensure_device_registered(
            self._hass, self._entry, snapshot, self._entry.data["device_name"]
        )
        self._entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator,
            panel_device_id=panel_device_id,
            curation=CurationOverlay.empty(),
            pv_binding=pv_binding_for(snapshot),
            setup_snapshot=snapshot,
        )

        self._platform = MockEntityPlatform(
            self._hass, domain=self._domain, platform_name=DOMAIN
        )
        self._platform.config_entry = self._entry

        entity = self._build(coordinator, snapshot)
        await self._platform.async_add_entities([entity])
        await self._hass.async_block_till_done()

        if added:
            assert entity.hass is not None, "entity was rejected before it reached the registry"
        else:
            assert entity.hass is None, "a disabled entity was added"
        self._entity = entity
        return entity


class _SensorInstall(_Install[SpanCircuitPowerSensor]):
    """One install of the circuit power sensor.

    The only one of the four that can be shown on a sub-device's card, so it is
    the only one that takes a `device_info_override`.
    """

    _domain: ClassVar[str] = "sensor"
    label: ClassVar[str] = "Power"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MockConfigEntry,
        device_info_override: DeviceInfo | None = None,
    ) -> None:
        super().__init__(hass, entry)
        self._device_info_override = device_info_override

    def _build(
        self, coordinator: MagicMock, snapshot: SpanPanelSnapshot
    ) -> SpanCircuitPowerSensor:
        """Build the power sensor, on the panel's card or a sub-device's."""
        return SpanCircuitPowerSensor(
            coordinator,
            POWER_DESCRIPTION,
            snapshot,
            CIRCUIT_ID,
            device_info_override=self._device_info_override,
        )


class _SwitchInstall(_Install[SpanPanelCircuitsSwitch]):
    """One install of the breaker switch."""

    _domain: ClassVar[str] = "switch"
    label: ClassVar[str] = "Breaker"

    def _build(
        self, coordinator: MagicMock, snapshot: SpanPanelSnapshot
    ) -> SpanPanelCircuitsSwitch:
        """Build the breaker switch."""
        return SpanPanelCircuitsSwitch(coordinator, CIRCUIT_ID, "SPAN Panel")


class _SelectInstall(_Install[SpanPanelCircuitsSelect]):
    """One install of the circuit-priority select."""

    _domain: ClassVar[str] = "select"
    label: ClassVar[str] = CIRCUIT_PRIORITY_DESCRIPTION.name

    def _build(
        self, coordinator: MagicMock, snapshot: SpanPanelSnapshot
    ) -> SpanPanelCircuitsSelect:
        """Build the circuit-priority select."""
        return SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, CIRCUIT_ID, "SPAN Panel"
        )


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry in friendly-names mode."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.50", "device_name": "SPAN Panel"},
        options=dict(FRIENDLY_NAMES),
        title="SPAN Panel",
        unique_id=SERIAL,
        entry_id="entry-recreate",
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
def parts_without_area(hass: HomeAssistant) -> None:
    """Compose ids from the devices and the entity, as a user may globally choose to.

    `entity_id_parts` is a registry-wide setting whose default also includes the
    area. Cases about the suffix half of an id pin it here so the assertion is
    about the suffix and not about whether an area happens to exist.

    Both device parts stay: a circuit entity's device is the circuit's, a child
    of the panel's, and the panel's name reaches its id only through the
    parent-device part.
    """
    er.async_get(hass).async_update_settings(
        entity_id_parts=[
            EntityNamePart.PARENT_DEVICE,
            EntityNamePart.DEVICE,
            EntityNamePart.ENTITY,
        ]
    )


async def test_the_first_install_takes_its_entity_id_from_the_circuit_name(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Baseline for every case below: the ID before any rename."""
    sensor = await _SensorInstall(hass, entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == ORIGINAL_ENTITY_ID


@pytest.mark.parametrize(
    ("circuit_name", "expected"),
    [
        ("Kitchen Outlets", "sensor.span_panel_kitchen_outlets_power"),
        ("Fridge/Freezer", "sensor.span_panel_fridge_freezer_power"),
        ("A/C - Upstairs", "sensor.span_panel_a_c_upstairs_power"),
        ("Garage  Door", "sensor.span_panel_garage_door_power"),
        ("Café Lights", "sensor.span_panel_cafe_lights_power"),
        ("Solar Power", "sensor.span_panel_solar_power"),
        ("Power", "sensor.span_panel_power_power"),
        ("SPAN Panel", "sensor.span_panel_span_panel_power"),
    ],
)
async def test_composed_ids_match_the_preset_shape_for_odd_names(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    circuit_name: str,
    expected: str,
) -> None:
    """A new install must land on the id the deleted preset builder would have spelled.

    The builder slugified the circuit name on its own and joined the parts with
    `_`; Home Assistant slugifies `"{device} {base}"` as one string. The two
    agree for punctuation, runs of whitespace and accents, and these are the
    names a panel can actually carry -- so no new install diverges from today's
    shape under `(DEVICE, ENTITY)`.

    The last three are the cases where the wording, not the slug, decides:

    - "Solar Power" already ends with the suffix word, so the builder omitted it
      (`circuit_part.endswith(f"_{suffix}")`) and the id reads `..._solar_power`;
    - "Power" does *not* -- the builder's test carried a leading underscore, so a
      circuit named exactly "Power" got `..._power_power`;
    - a circuit named after the panel gets both, `..._span_panel_span_panel_power`,
      because the device half and the name half are composed independently.
    """
    sensor = await _SensorInstall(hass, entry).load(circuit_name)

    assert sensor.entity_id == expected


async def test_renaming_a_circuit_does_not_move_an_existing_entity_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The non-negotiable one: a rename must never move a live entity_id.

    Dashboards, automations, and recorder history all key off the entity_id.
    Recreate is an offer the user accepts; a rename is not.
    """
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    assert sensor.entity_id == ORIGINAL_ENTITY_ID

    registry = er.async_get(hass)
    assert registry.async_get(ORIGINAL_ENTITY_ID) is not None
    assert registry.async_get(RENAMED_ENTITY_ID) is None


async def test_renaming_a_circuit_does_not_move_the_unique_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Unique IDs are derived from the description key and never from a name.

    A moved unique_id orphans the entity and drops its long-term statistics, and
    it would break any future migration that has to predict what a unique_id
    looks like.
    """
    install = _SensorInstall(hass, entry)
    before = await install.load(ORIGINAL_NAME)
    unique_id_before = before.unique_id

    after = await install.load(RENAMED)

    assert after.unique_id == unique_id_before

    registry = er.async_get(hass)
    entry_after = registry.async_get(ORIGINAL_ENTITY_ID)
    assert entry_after is not None
    assert entry_after.unique_id == unique_id_before


async def test_renaming_a_circuit_refreshes_the_registrys_entity_id_suggestion(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """What Recreate composes from has to track the panel, not the install date.

    `async_regenerate_entity_id` composes from the stored base when there is no
    user `name` override, which in friendly-names mode is always. The base is
    the suffix alone; the circuit's name comes from the circuit's device, which
    the reload renamed. Core prefixes both devices and, where the user has
    assigned one, the area.
    """
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(ORIGINAL_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.object_id_base == "power"
    assert registry_entry.suggested_object_id is None
    assert registry_entry.device_id is not None
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device is not None
    assert device.name == RENAMED


async def test_recreate_entity_ids_proposes_the_renamed_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Issue #252 itself, at the API the button calls."""
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(ORIGINAL_ENTITY_ID)
    assert registry_entry is not None

    proposed = registry.async_regenerate_entity_id(registry_entry)

    assert proposed == RENAMED_ENTITY_ID
    assert proposed != registry_entry.entity_id


async def test_an_unrenamed_circuit_is_offered_its_own_entity_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Recreate must be a no-op when nothing changed.

    Without this the previous test passes for the wrong reason -- a suggestion
    that moves on every reload would satisfy it while offering every user a
    pointless rename.
    """
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(ORIGINAL_ENTITY_ID)
    assert registry_entry is not None

    assert registry.async_regenerate_entity_id(registry_entry) == ORIGINAL_ENTITY_ID


async def test_circuit_numbers_mode_is_offered_the_circuits_name(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Recreate offers a circuit-numbered entity the id its circuit's name composes.

    The mode gives an entity its breaker position as its id when the entity is
    created, and that id never moves on its own (R5). The circuit's device
    carries the circuit's name in this mode too, so what Recreate composes is
    the name -- the circuit's current one, after a rename on the panel. Offering
    the entity its own id instead would take the integration presetting it on
    every load, the exemption from the user's entity-id format that R2 forbids;
    the preset is made once, and the offer is the user's to accept or leave.

    This case used to pin the opposite, from before the circuit had a device
    of its own to be named by.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    assert sensor.entity_id == CIRCUIT_NUMBERS_ENTITY_ID

    registry = er.async_get(hass)
    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None

    assert registry.async_regenerate_entity_id(registry_entry) == RENAMED_ENTITY_ID


async def test_circuit_numbers_mode_still_shows_the_panels_name(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Phase 2 sync still follows the panel -- through its device, which cannot move an id.

    The entity carries its label only; the panel's name for the circuit is the
    device's, and Home Assistant composes what is displayed from the two.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None

    assert registry_entry.original_name == "Power"
    assert sensor.name == "Power"
    assert registry_entry.name is None
    assert er.async_get_full_entity_name(hass, registry_entry) == f"SPAN Panel {RENAMED} Power"


async def test_circuit_numbers_mode_releases_a_name_an_older_release_wrote(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An install upgrading has the old scheme's name handed back to it.

    With the name released, Recreate composes from the circuit's device and
    the suffix, like every other entity in this mode, and not from the name
    the old scheme wrote.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    # Exactly what the previous release's phase 2 sync would have written.
    registry.async_update_entity(sensor.entity_id, name=f"{ORIGINAL_NAME} Power")
    assert registry.async_get(sensor.entity_id).name == f"{ORIGINAL_NAME} Power"

    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.name is None
    assert registry.async_regenerate_entity_id(registry_entry) == ORIGINAL_ENTITY_ID


async def test_releasing_the_name_is_idempotent(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Two reloads in a row are the same as one. There is no migration to run twice."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    registry = er.async_get(hass)
    registry.async_update_entity(sensor.entity_id, name=f"{ORIGINAL_NAME} Power")

    await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.name is None
    assert registry_entry.entity_id == CIRCUIT_NUMBERS_ENTITY_ID


async def test_a_name_the_user_set_is_never_released(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The registry's `name` is the user's field, and only their own writes are theirs.

    A name we did not write is left exactly where it is -- which also means it
    keeps outranking the suggestion, so Recreate composes from it. That is what
    Home Assistant does for any integration once a user names an entity.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry.async_update_entity(sensor.entity_id, name="Beverage Cooling")

    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.name == "Beverage Cooling"
    assert registry_entry.entity_id == CIRCUIT_NUMBERS_ENTITY_ID


async def test_circuit_numbers_mode_does_not_move_ids_across_the_change(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Neither id moves, whatever happens to the name."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    before = await install.load(ORIGINAL_NAME)
    unique_id_before = before.unique_id

    registry = er.async_get(hass)
    registry.async_update_entity(before.entity_id, name=f"{ORIGINAL_NAME} Power")

    after = await install.load(RENAMED)

    assert after.entity_id == CIRCUIT_NUMBERS_ENTITY_ID
    assert after.unique_id == unique_id_before
    assert registry.async_get(RENAMED_ENTITY_ID) is None


# --- The two spellings a circuit energy id has shipped with -------------------
#
# Every case above builds its entities with the current code, so the base and
# the live id agree by construction and a suffix disagreement cannot appear. A
# real install upgrading is the case that matters, and there are two eras of
# them. Before the integration preset an entity_id, Home Assistant composed one
# from the descriptor name ("Consumed Energy" -> `..._consumed_energy`); the
# preset era spelled the same sensor `..._energy_consumed`, after the unique-id
# suffix mapping. Both are live on real panels, so the base an entity is given
# has to read its own spelling back out of the id it already carries --
# otherwise Recreate offers a rename for every circuit at once, which on a
# measured panel was 74 of them.

LEGACY_ENTITY_ID = "sensor.span_panel_refrigerator_consumed_energy"
PRESET_ERA_ENTITY_ID = "sensor.span_panel_refrigerator_energy_consumed"
RENAMED_LEGACY_ENTITY_ID = "sensor.span_panel_beer_fridge_consumed_energy"

ENERGY_DESCRIPTION = next(
    desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed"
)


class _LegacyInstall(_Install[SpanCircuitEnergySensor]):
    """An install whose energy sensor id was composed from the descriptor name."""

    _domain: ClassVar[str] = "sensor"

    def _seed(self) -> str:
        """Register the entity the way a pre-preset install left it.

        Called by the cases that need one, not by `load`: the point of several of
        them is what happens when there is nothing seeded at all.
        """
        registry = er.async_get(self._hass)
        unique_id = build_circuit_unique_id(SERIAL, CIRCUIT_ID, "consumedEnergyWh")
        entry = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            suggested_object_id="span_panel_refrigerator_consumed_energy",
            original_name=f"{ORIGINAL_NAME} Consumed Energy",
            config_entry=self._entry,
        )
        return entry.entity_id

    def _build(
        self, coordinator: MagicMock, snapshot: SpanPanelSnapshot
    ) -> SpanCircuitEnergySensor:
        """Build the energy sensor, whose id has shipped with two suffix spellings."""
        return SpanCircuitEnergySensor(coordinator, ENERGY_DESCRIPTION, snapshot, CIRCUIT_ID)


async def test_upgrading_does_not_offer_to_renormalise_a_legacy_suffix(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The 74-rename case. Nothing was renamed on the panel, so nothing is offered."""
    install = _LegacyInstall(hass, entry)
    seeded = install._seed()
    assert seeded == LEGACY_ENTITY_ID

    sensor = await install.load(ORIGINAL_NAME)

    assert sensor.entity_id == LEGACY_ENTITY_ID

    registry = er.async_get(hass)
    registry_entry = registry.async_get(LEGACY_ENTITY_ID)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == LEGACY_ENTITY_ID


async def test_a_legacy_entity_still_follows_a_circuit_rename(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Preserving the suffix must not cost the fix.

    Only the half the user changed moves. The circuit's new name reaches the
    proposal; the suffix keeps the spelling this entity has always carried,
    because renormalising it is a rename the user did not ask for and would ride
    along with the one they did.
    """
    install = _LegacyInstall(hass, entry)
    install._seed()

    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    assert sensor.entity_id == LEGACY_ENTITY_ID

    registry = er.async_get(hass)
    registry_entry = registry.async_get(LEGACY_ENTITY_ID)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == RENAMED_LEGACY_ENTITY_ID


async def test_a_new_energy_sensor_gets_the_noun_last_suffix(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """A new id uses the wording the panel level and the labels use.

    Nothing is seeded, so there is no spelling to read back and the default
    applies. It is the pre-preset wording, which is also what
    `main_meter_consumed_energy` has always said.
    """
    sensor = await _LegacyInstall(hass, entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == "sensor.span_panel_refrigerator_consumed_energy"


async def test_a_preset_era_energy_sensor_is_offered_its_own_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """An id spelled `..._energy_consumed` keeps that spelling in the proposal."""
    registry = er.async_get(hass)
    unique_id = build_circuit_unique_id(SERIAL, CIRCUIT_ID, "consumedEnergyWh")
    seeded = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        suggested_object_id="span_panel_refrigerator_energy_consumed",
        config_entry=entry,
    )
    assert seeded.entity_id == PRESET_ERA_ENTITY_ID

    install = _LegacyInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(seeded.entity_id)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == seeded.entity_id


async def test_a_pre_preset_energy_sensor_is_offered_its_own_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The other era: `..._consumed_energy` keeps its spelling too."""
    install = _LegacyInstall(hass, entry)
    install._seed()

    sensor = await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert sensor.entity_id == LEGACY_ENTITY_ID
    assert registry.async_regenerate_entity_id(registry_entry) == sensor.entity_id


async def test_circuit_numbers_mode_releases_the_energy_name_an_older_release_wrote(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """2.0.8 wrote "<circuit> Consumed Energy" into the registry, and it must be released.

    Recreate then composes from the circuit's device and the suffix, as it
    does for every entity in this mode.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    install = _LegacyInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    assert sensor.entity_id == "sensor.span_panel_circuit_15_consumed_energy"
    registry = er.async_get(hass)
    registry.async_update_entity(sensor.entity_id, name=f"{ORIGINAL_NAME} Consumed Energy")

    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry_entry.name is None
    assert registry.async_regenerate_entity_id(registry_entry) == LEGACY_ENTITY_ID


async def test_a_name_written_under_the_beta_label_is_released_as_well(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The label read "Energy Consumed" for a few betas; that write is ours too."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    install = _LegacyInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    registry = er.async_get(hass)
    registry.async_update_entity(sensor.entity_id, name=f"{ORIGINAL_NAME} Energy Consumed")

    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry_entry.name is None


@pytest.mark.parametrize(
    ("mode", "created"),
    [
        pytest.param(FRIENDLY_NAMES, ORIGINAL_ENTITY_ID, id="friendly"),
        pytest.param(CIRCUIT_NUMBERS, CIRCUIT_NUMBERS_ENTITY_ID, id="circuit_numbers"),
    ],
)
async def test_an_area_the_user_assigned_reaches_the_proposal_under_default_parts(
    hass: HomeAssistant, entry: MockConfigEntry, mode: dict[str, bool], created: str
) -> None:
    """R2: the area is the user's global choice, and the integration does not exempt itself.

    The area is the panel's, and it reaches a circuit entity through the
    circuit's device, which has none of its own and inherits its parent's.
    Handing Core a base means the area part of `entity_id_parts` applies here
    exactly as it does to every other integration -- and only where the user
    has actually assigned an area.

    In both modes: the circuit-numbers preset is spent by the reload, and the
    offer after it is composed like a circuit-names one.
    """
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = _SensorInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    assert sensor.entity_id == created  # no area yet

    area = ar.async_get(hass).async_get_or_create("Basement")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier((DOMAIN, SERIAL), entry.entry_id)
    assert device is not None
    device_registry.async_update_device(device.id, area_id=area.id)

    await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(created)
    assert registry_entry is not None
    assert registry_entry.entity_id == created  # R5: nothing moved
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "sensor.basement_span_panel_refrigerator_power"
    )


async def test_an_unnamed_circuit_in_friendly_mode_still_gets_a_distinct_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """An unnamed circuit has no name half to compose from, so it falls back to its tabs.

    Letting Core compose from the display name alone would give every unnamed
    circuit on the panel `sensor.span_panel_power`, and the registry would
    disambiguate them with `_2`, `_3`, ... in whatever order they were added.
    """
    sensor = await _SensorInstall(hass, entry).load("")

    assert sensor.entity_id == "sensor.span_panel_circuit_15_power"


# --- Entities composition spells differently than the deleted preset did ------
#
# Two shapes disagree with what the old preset builder wrote: a circuit sensor
# shown on a sub-device card, where the DEVICE part is the charger rather than
# the panel, and any entity on an install that turned the device prefix off,
# which `has_entity_name` prefixes anyway. Neither is bypassed. R1 (clarified)
# asks the base to reproduce an existing id *where Home Assistant composes it
# under the options the install was built with*; where composition yields a
# different device part, that is the user's `entity_id_parts` at work and the
# offer is legitimate. R5 still holds throughout: nothing moves until Recreate
# is pressed.

EVSE_DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, f"{SERIAL}_evse_node-1")},
    name="SPAN Panel EV Charger",
)

LEGACY_NAMES = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}


def _seed_power_entity(hass: HomeAssistant, entry: MockConfigEntry, object_id: str) -> str:
    """Register the circuit power entity the way an install already has it."""
    registry = er.async_get(hass)
    seeded = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, "instantPowerW"),
        suggested_object_id=object_id,
        config_entry=entry,
    )
    assert seeded.entity_id == f"sensor.{object_id}"
    return seeded.entity_id


async def test_an_existing_sub_device_sensor_is_offered_the_composed_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """An EVSE feed circuit's sensor lives on the charger, and composes from it.

    Its id says the panel because the deleted preset builder always did. Under
    the user's parts the DEVICE half is the entity's own device -- the charger --
    and the ENTITY half is the label it carries there, which on a sub-device card
    is the bare description name. So Recreate offers `sensor.<charger>_power`,
    the shape the charger's own sensors already have. Nothing moves until the
    user accepts it (R5).
    """
    seeded = _seed_power_entity(hass, entry, "span_panel_refrigerator_power")

    install = _SensorInstall(hass, entry, device_info_override=EVSE_DEVICE_INFO)
    sensor = await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    assert sensor.entity_id == seeded  # R5: two reloads, nothing moved

    registry = er.async_get(hass)
    registry_entry = registry.async_get(seeded)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded
    assert (
        registry.async_regenerate_entity_id(registry_entry) == "sensor.span_panel_ev_charger_power"
    )


async def test_a_rename_does_not_change_what_a_sub_device_sensor_composes(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The circuit's name is not in this sensor's label, so #252 cannot reach its id.

    A feed sensor on the charger's card is labelled "Power", not "<circuit>
    Power" -- the card already says which device it belongs to. Renaming the
    circuit on the panel therefore leaves both the live id and the offer alone.
    """
    seeded = _seed_power_entity(hass, entry, "span_panel_refrigerator_power")

    install = _SensorInstall(hass, entry, device_info_override=EVSE_DEVICE_INFO)
    await install.load(ORIGINAL_NAME)
    await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(seeded)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded  # R5: nothing moved
    assert (
        registry.async_regenerate_entity_id(registry_entry) == "sensor.span_panel_ev_charger_power"
    )


async def test_an_existing_sensor_on_a_no_prefix_install_is_offered_the_composed_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The pre-1.0.4 shape: no device prefix at all, which `has_entity_name` re-adds.

    The base still reads the suffix back off the bare id, so the ENTITY half is
    the entity's own `refrigerator_power`; the DEVICE half is the user's
    `entity_id_parts` asking for one. The offer is theirs to accept, and the live
    id does not move on its own (R5).
    """
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))
    seeded = _seed_power_entity(hass, entry, "refrigerator_power")

    install = _SensorInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    assert sensor.entity_id == seeded  # R5: two reloads, nothing moved

    registry = er.async_get(hass)
    registry_entry = registry.async_get(seeded)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded
    assert registry.async_regenerate_entity_id(registry_entry) == ORIGINAL_ENTITY_ID


async def test_a_new_sensor_on_a_no_prefix_install_composes_like_every_other_entity(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """`USE_DEVICE_PREFIX` off cannot take the device out of a composed id."""
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))

    sensor = await _SensorInstall(hass, entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == ORIGINAL_ENTITY_ID


# --- Every shipped spelling, on the circuit's own device ----------------------
#
# The circuit's name moved from the base to the circuit's device, so the base is
# the suffix alone. It is still read back from the id the entity has: both power
# spellings, and the circuit named after the suffix word whose id left the
# suffix out, each compose back to themselves from the device and that suffix.


@pytest.mark.parametrize(
    ("circuit_name", "object_id"),
    [
        ("Refrigerator", "span_panel_refrigerator_power"),
        ("Refrigerator", "span_panel_refrigerator_current_power"),
        ("Solar Power", "span_panel_solar_power"),
        ("Solar Power", "span_panel_solar_power_power"),
        ("Power", "span_panel_power_power"),
        ("Current", "span_panel_current_power"),
    ],
)
async def test_an_existing_power_id_is_offered_itself_on_the_circuits_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    circuit_name: str,
    object_id: str,
) -> None:
    """Nothing was renamed, so Recreate offers each existing id itself (R1)."""
    seeded = _seed_power_entity(hass, entry, object_id)

    install = _SensorInstall(hass, entry)
    sensor = await install.load(circuit_name)
    await install.load(circuit_name)

    assert sensor.entity_id == seeded  # R5: two reloads, nothing moved
    registry = er.async_get(hass)
    registry_entry = registry.async_get(seeded)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == seeded


@pytest.mark.parametrize(
    ("before", "after"),
    [("Solar Power", "Solar"), ("Solar", "Solar Power")],
    ids=["off-the-suffix-word", "onto-the-suffix-word"],
)
async def test_a_rename_across_the_suffix_word_is_offered_the_id_it_has(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    before: str,
    after: str,
) -> None:
    """The base is read against the name the device takes on this reload, not the one it had.

    Both names compose `sensor.span_panel_solar_power`: "Solar" then `power`,
    or "Solar Power" with the suffix left out. A rename between them is the
    user's, and the id it composes is the one the entity already has, so there
    is nothing to offer. Read against the device's name from before the reload,
    the base answered the old name's question -- `power` kept for "Solar Power",
    or left out for "Solar" -- and offered `..._solar_power_power` or
    `..._solar` until the next restart.
    """
    install = _SensorInstall(hass, entry)
    sensor = await install.load(before)
    await install.load(before)
    assert sensor.entity_id == "sensor.span_panel_solar_power"

    await install.load(after)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == sensor.entity_id


# --- A panel device name this integration generated ---------------------------
#
# The config flow names the device: the second panel on a system becomes "Span
# Panel 2" without anyone typing it (`get_unique_device_name`). Composition
# spells the DEVICE part from that name, while every circuit id already on that
# panel says `span_panel_` -- the literal the deleted preset builder fell back to
# on every install. Recreate therefore offers `sensor.span_panel_2_...`: that is
# the device the entity is on and the parts the user chose, and the offer stands
# until they accept it. The same holds for a name the user gave the device
# themselves, which Home Assistant composes from ahead of the generated one.

SECOND_PANEL_NAME = "Span Panel 2"


@pytest.fixture
def second_panel_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return the config entry of a second panel, whose device name was generated."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.51", "device_name": SECOND_PANEL_NAME},
        options=dict(FRIENDLY_NAMES),
        title=SECOND_PANEL_NAME,
        unique_id=SERIAL,
        entry_id="entry-recreate-second",
    )
    config_entry.add_to_hass(hass)
    return config_entry


async def test_an_existing_sensor_on_a_generated_second_panel_is_offered_the_composed_id(
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The DEVICE half is the device's name, whoever wrote it."""
    seeded = _seed_power_entity(hass, second_panel_entry, "span_panel_refrigerator_power")

    install = _SensorInstall(hass, second_panel_entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(ORIGINAL_NAME)

    assert sensor.entity_id == seeded  # R5: two reloads, nothing moved

    registry = er.async_get(hass)
    registry_entry = registry.async_get(seeded)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "sensor.span_panel_2_refrigerator_power"
    )


async def test_the_controls_on_a_generated_second_panel_are_offered_the_composed_ids(
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The breaker switch and the priority select carry the same ids and the same rule."""
    registry = er.async_get(hass)
    seeded_switch = registry.async_get_or_create(
        "switch",
        DOMAIN,
        build_switch_unique_id(SERIAL, CIRCUIT_ID),
        suggested_object_id="span_panel_refrigerator_breaker",
        config_entry=second_panel_entry,
    )
    seeded_select = registry.async_get_or_create(
        "select",
        DOMAIN,
        build_select_unique_id(SERIAL, CIRCUIT_ID),
        suggested_object_id="span_panel_refrigerator_circuit_priority",
        config_entry=second_panel_entry,
    )

    switches = _SwitchInstall(hass, second_panel_entry)
    await switches.load(ORIGINAL_NAME)
    switch = await switches.load(ORIGINAL_NAME)
    selects = _SelectInstall(hass, second_panel_entry)
    await selects.load(ORIGINAL_NAME)
    select = await selects.load(ORIGINAL_NAME)

    # R5: two reloads each, and neither live id moved.
    assert switch.entity_id == seeded_switch.entity_id
    assert select.entity_id == seeded_select.entity_id

    switch_entry = registry.async_get(seeded_switch.entity_id)
    assert switch_entry is not None
    assert (
        registry.async_regenerate_entity_id(switch_entry)
        == "switch.span_panel_2_refrigerator_breaker"
    )

    select_entry = registry.async_get(seeded_select.entity_id)
    assert select_entry is not None
    assert (
        registry.async_regenerate_entity_id(select_entry)
        == "select.span_panel_2_refrigerator_circuit_priority"
    )


async def test_a_new_sensor_on_a_generated_second_panel_composes_with_that_name(
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, parts_without_area: None
) -> None:
    """A new entity is spelled with its own device, as an existing one is offered."""
    sensor = await _SensorInstall(hass, second_panel_entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == "sensor.span_panel_2_refrigerator_power"


async def test_a_panel_device_the_user_renamed_is_offered_the_renamed_id(
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, parts_without_area: None
) -> None:
    """A device the user renamed is composed from the name they gave it.

    The generated name is still "Span Panel 2" underneath. Home Assistant
    composes the DEVICE half from `name_by_user` ahead of `name`, and the offer
    follows it -- the integration does not read either field itself.
    """
    seeded = _seed_power_entity(hass, second_panel_entry, "span_panel_refrigerator_power")

    install = _SensorInstall(hass, second_panel_entry)
    await install.load(ORIGINAL_NAME)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERIAL), second_panel_entry.entry_id
    )
    assert device is not None
    device_registry.async_update_device(device.id, name_by_user="Garage Panel")

    await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(seeded)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded  # R5: nothing moved
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "sensor.garage_panel_refrigerator_power"
    )


# --- The two circuit controls -------------------------------------------------
#
# The breaker switch and the priority select are the other two circuit entities
# whose ids this integration used to spell out in full. They take the same route
# as the sensors, without exception: one base, and Core composes the rest.

SWITCH_ENTITY_ID = "switch.span_panel_circuit_15_breaker"
SELECT_ENTITY_ID = "select.span_panel_circuit_15_circuit_priority"


async def test_a_new_switch_and_select_get_their_circuit_number_ids(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """Circuit-numbers mode gives both their breaker position at creation, as it does a sensor."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    switch = await _SwitchInstall(hass, entry).load(ORIGINAL_NAME)
    select = await _SelectInstall(hass, entry).load(ORIGINAL_NAME)

    assert switch.entity_id == SWITCH_ENTITY_ID
    assert select.entity_id == SELECT_ENTITY_ID


async def test_switch_and_select_are_offered_the_circuits_new_name_in_circuit_numbers_mode(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """#252 in this mode: the id does not move, and the offer follows the panel's rename.

    The controls are composed like the sensors: from the circuit's device,
    which the reload renamed, and their suffix.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    switches = _SwitchInstall(hass, entry)
    await switches.load(ORIGINAL_NAME)
    switch = await switches.load(RENAMED)
    selects = _SelectInstall(hass, entry)
    await selects.load(ORIGINAL_NAME)
    select = await selects.load(RENAMED)

    registry = er.async_get(hass)

    assert switch.entity_id == SWITCH_ENTITY_ID
    switch_entry = registry.async_get(switch.entity_id)
    assert switch_entry is not None
    assert switch_entry.original_name == "Breaker"
    assert (
        registry.async_regenerate_entity_id(switch_entry) == "switch.span_panel_beer_fridge_breaker"
    )

    assert select.entity_id == SELECT_ENTITY_ID
    select_entry = registry.async_get(select.entity_id)
    assert select_entry is not None
    assert select_entry.original_name == "Circuit Priority"
    assert (
        registry.async_regenerate_entity_id(select_entry)
        == "select.span_panel_beer_fridge_circuit_priority"
    )


async def test_switch_and_select_release_the_name_an_older_release_wrote(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """2.0.8 wrote the panel's name into the registry's `name` for these two as well.

    Released, they are offered what their circuit's device and suffix compose.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    registry = er.async_get(hass)

    switches = _SwitchInstall(hass, entry)
    switch = await switches.load(ORIGINAL_NAME)
    registry.async_update_entity(switch.entity_id, name=f"{ORIGINAL_NAME} Breaker")
    await switches.load(ORIGINAL_NAME)

    selects = _SelectInstall(hass, entry)
    select = await selects.load(ORIGINAL_NAME)
    registry.async_update_entity(select.entity_id, name=f"{ORIGINAL_NAME} Circuit Priority")
    await selects.load(ORIGINAL_NAME)

    switch_entry = registry.async_get(SWITCH_ENTITY_ID)
    assert switch_entry is not None
    assert switch_entry.name is None
    assert (
        registry.async_regenerate_entity_id(switch_entry)
        == "switch.span_panel_refrigerator_breaker"
    )

    select_entry = registry.async_get(SELECT_ENTITY_ID)
    assert select_entry is not None
    assert select_entry.name is None
    assert (
        registry.async_regenerate_entity_id(select_entry)
        == "select.span_panel_refrigerator_circuit_priority"
    )


async def test_a_name_the_user_set_on_a_control_is_never_released(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """Only a name this integration would have written is handed back."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    registry = er.async_get(hass)

    switches = _SwitchInstall(hass, entry)
    switch = await switches.load(ORIGINAL_NAME)
    registry.async_update_entity(switch.entity_id, name="Beverage Cooling")

    await switches.load(ORIGINAL_NAME)

    switch_entry = registry.async_get(switch.entity_id)
    assert switch_entry is not None
    assert switch_entry.name == "Beverage Cooling"


async def test_an_existing_switch_on_a_no_prefix_install_is_offered_the_composed_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The pre-1.0.4 shape reaches the controls too, and composition re-adds a device."""
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))
    registry = er.async_get(hass)
    seeded = registry.async_get_or_create(
        "switch",
        DOMAIN,
        build_switch_unique_id(SERIAL, CIRCUIT_ID),
        suggested_object_id="refrigerator_breaker",
        config_entry=entry,
    )
    assert seeded.entity_id == "switch.refrigerator_breaker"

    install = _SwitchInstall(hass, entry)
    switch = await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    assert switch.entity_id == seeded.entity_id  # R5: two reloads, nothing moved

    registry_entry = registry.async_get(seeded.entity_id)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded.entity_id
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "switch.span_panel_refrigerator_breaker"
    )


async def test_an_existing_select_on_a_no_prefix_install_is_offered_the_composed_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The same shape, composed the same way."""
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))
    registry = er.async_get(hass)
    seeded = registry.async_get_or_create(
        "select",
        DOMAIN,
        build_select_unique_id(SERIAL, CIRCUIT_ID),
        suggested_object_id="refrigerator_circuit_priority",
        config_entry=entry,
    )
    assert seeded.entity_id == "select.refrigerator_circuit_priority"

    install = _SelectInstall(hass, entry)
    select = await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    assert select.entity_id == seeded.entity_id  # R5: two reloads, nothing moved

    registry_entry = registry.async_get(seeded.entity_id)
    assert registry_entry is not None
    assert registry_entry.entity_id == seeded.entity_id
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "select.span_panel_refrigerator_circuit_priority"
    )


async def test_a_new_control_on_a_no_prefix_install_composes_like_every_other_entity(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """`USE_DEVICE_PREFIX` off cannot take the device out of a composed id."""
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))

    switch = await _SwitchInstall(hass, entry).load(ORIGINAL_NAME)
    select = await _SelectInstall(hass, entry).load(ORIGINAL_NAME)

    assert switch.entity_id == "switch.span_panel_refrigerator_breaker"
    assert select.entity_id == "select.span_panel_refrigerator_circuit_priority"


async def test_an_area_reaches_the_proposal_for_a_control_too(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """R2: the controls stop exempting themselves from the user's `entity_id_parts`.

    Handing Core a base means the switch is composed exactly like every other
    integration's entity -- and only where the user has actually assigned an
    area. The circuit-numbers preset is spent by the reload, so the offer
    after it carries the area and the circuit's name.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    install = _SwitchInstall(hass, entry)
    switch = await install.load(ORIGINAL_NAME)
    assert switch.entity_id == SWITCH_ENTITY_ID  # no area yet

    area = ar.async_get(hass).async_get_or_create("Basement")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier((DOMAIN, SERIAL), entry.entry_id)
    assert device is not None
    device_registry.async_update_device(device.id, area_id=area.id)

    await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(SWITCH_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.entity_id == SWITCH_ENTITY_ID  # R5: nothing moved
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "switch.basement_span_panel_refrigerator_breaker"
    )


# --- Circuit-numbers mode: the id at creation, and the first offer after it ----
#
# The mode presets a circuit-number id for an entity being created, once. From
# the reload after it the id is on file and is read back like any other: the
# tabs it was built from locate the suffix's form, and the circuit's device,
# which Core composes the offer from, decides whether the suffix repeats the
# name. So the first offer is the id the same circuit has on a circuit-names
# install, and accepting it leaves nothing more to offer.

type _CircuitInstall = type[_SensorInstall] | type[_SwitchInstall] | type[_SelectInstall]


@pytest.mark.parametrize(
    ("make_install", "circuit_name", "created", "offered"),
    [
        pytest.param(
            _SensorInstall,
            "Solar Power",
            CIRCUIT_NUMBERS_ENTITY_ID,
            "sensor.span_panel_solar_power",
            id="name-ends-on-the-suffix",
        ),
        pytest.param(
            _SwitchInstall,
            "Main Breaker",
            SWITCH_ENTITY_ID,
            "switch.span_panel_main_breaker",
            id="switch-name-ends-on-the-suffix",
        ),
        pytest.param(
            _SensorInstall,
            "Kitchen Outlets",
            CIRCUIT_NUMBERS_ENTITY_ID,
            "sensor.span_panel_kitchen_outlets_power",
            id="ordinary-name",
        ),
        pytest.param(
            _SensorInstall,
            "Power",
            CIRCUIT_NUMBERS_ENTITY_ID,
            "sensor.span_panel_power_power",
            id="name-is-the-suffix-word",
        ),
    ],
)
async def test_numbers_mode_first_offer_matches_names_mode(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    make_install: _CircuitInstall,
    circuit_name: str,
    created: str,
    offered: str,
) -> None:
    """Recreate offers a circuit-numbered entity the id a circuit-names install gives it.

    A circuit named "Solar Power" is `sensor.span_panel_solar_power` on a
    circuit-names install: the name already ends on the suffix word, so the
    word is not repeated (`test_composed_ids_match_the_preset_shape_for_odd_names`).
    The circuit-number id spells the suffix after the tabs, and reading the
    omission from that id as well offered `..._solar_power_power`, an id no
    install of either mode has. A circuit named exactly "Power" repeats
    nothing and keeps both halves, as it does under circuit names.

    The offer is accepted and the entry reloaded once more, because an offer
    that changes again after it is taken is a rename the user did not cause.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    install = make_install(hass, entry)
    entity = await install.load(circuit_name)
    assert entity.entity_id == created

    await install.load(circuit_name)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(created)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == offered

    registry.async_update_entity(created, new_entity_id=offered)
    await hass.async_block_till_done()
    reloaded = await install.load(circuit_name)

    assert reloaded.entity_id == offered
    accepted = registry.async_get(offered)
    assert accepted is not None
    assert registry.async_regenerate_entity_id(accepted) == offered


async def test_a_circuit_sensor_on_a_chargers_device_is_not_given_a_circuit_number_id(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """A charger's feed circuit's sensor is on the charger's device, in both modes.

    The preset is the id Core would compose if the circuit's device were named
    by its number, and this sensor is not on the circuit's device: it composes
    from its label on the charger's, like the charger's own sensors.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry, device_info_override=EVSE_DEVICE_INFO)
    sensor = await install.load(ORIGINAL_NAME)

    assert sensor.entity_id == "sensor.span_panel_ev_charger_power"
    registry_entry = er.async_get(hass).async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry_entry.suggested_object_id is None


# --- Phase 2: a rename in the SPAN app still reaches the display name ---------
#
# The README promises it in both modes, under "Entity Naming Pattern": a rename
# in the SPAN app renames the circuit's device, and with it every entity of the
# circuit, and displayed names follow the SPAN app whichever naming pattern was
# chosen. One mechanism serves both. A coordinator push
# carrying a changed circuit name asks the entry to reload, and the reload is
# what renames the circuit's device, which Home Assistant composes the displayed
# name from ahead of the entity's own label. The shortcut the old scheme took,
# writing the registry's `name` in place, is exactly what made Recreate propose
# a friendly-name id for a circuit-numbered entity, so the mechanism has to keep
# costing a reload.
#
# The per-platform unit tests drive this against a `MagicMock` registry, which
# cannot see where the name landed. These drive it through a real
# `EntityPlatform` and the real registry, for all three circuit platforms in
# both modes, because handing composition the id moved where the name is
# written.

CIRCUIT_INSTALLS: list[_CircuitInstall] = [_SensorInstall, _SwitchInstall, _SelectInstall]
PLATFORM_IDS = ["sensor", "switch", "select"]


@pytest.mark.parametrize(
    "mode", [FRIENDLY_NAMES, CIRCUIT_NUMBERS], ids=["friendly", "circuit_numbers"]
)
@pytest.mark.parametrize("make_install", CIRCUIT_INSTALLS, ids=PLATFORM_IDS)
async def test_a_rename_in_the_span_app_reaches_the_display_name_after_the_reload_it_requests(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    mode: dict[str, bool],
    make_install: _CircuitInstall,
) -> None:
    """The whole promise in one case: push, reload, and the name has followed.

    Two loads before the push, because that is what a real install has done by
    the time a rename arrives: the first add finds no registry entry and asks
    for a reload on its first update regardless, and after the reload the entity
    is on file and its remembered name is the panel's. Asserting on a
    freshly-added entity would let that first-update reload stand in for the one
    the rename is supposed to cause.

    So the unchanged push comes first and must ask for nothing. Only then does
    the rename, and only it, buy a reload.
    """
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = make_install(hass, entry)
    await install.load(ORIGINAL_NAME)
    entity = await install.load(ORIGINAL_NAME)
    original_entity_id = entity.entity_id

    registry = er.async_get(hass)
    before = registry.async_get(original_entity_id)
    assert before is not None
    assert before.original_name == make_install.label
    assert er.async_get_full_entity_name(hass, before) == (
        f"SPAN Panel {ORIGINAL_NAME} {make_install.label}"
    )

    await install.rename_on_the_panel(ORIGINAL_NAME)
    install.coordinator.request_reload.assert_not_called()

    await install.rename_on_the_panel(RENAMED)
    install.coordinator.request_reload.assert_called_once()

    reloaded = await install.load(RENAMED)

    after = registry.async_get(original_entity_id)
    assert after is not None
    assert after.original_name == make_install.label
    assert after.device_id == before.device_id  # the circuit's device was renamed, not replaced
    assert er.async_get_full_entity_name(hass, after) == (
        f"SPAN Panel {RENAMED} {make_install.label}"
    )
    assert reloaded.entity_id == original_entity_id  # R5: the id did not move
    assert after.name is None  # R4: the registry's `name` was never written


@pytest.mark.parametrize("make_install", CIRCUIT_INSTALLS, ids=PLATFORM_IDS)
async def test_a_span_app_rename_reaches_the_device_when_every_entity_is_named_by_the_user(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    make_install: _CircuitInstall,
) -> None:
    """The user's name is the entity's; the circuit's name is its device's, and follows the panel.

    The install's one entity is every enabled entity its circuit has, and the
    user has named it. The rename leaves that name alone (R4), but it still
    asks for the reload: the circuit's name lives on its device, which only
    the reload renames, so skipping it for a named entity left the device on
    the old name for good -- and with it the panel half of what Home Assistant
    displays and offers for every entity on the circuit.
    """
    install = make_install(hass, entry)
    await install.load(ORIGINAL_NAME)
    entity = await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry.async_update_entity(entity.entity_id, name="Beverage Cooling")

    await install.rename_on_the_panel(ORIGINAL_NAME)
    install.coordinator.request_reload.assert_not_called()

    await install.rename_on_the_panel(RENAMED)
    install.coordinator.request_reload.assert_called_once()

    await install.load(RENAMED)

    after = registry.async_get(entity.entity_id)
    assert after is not None
    assert after.name == "Beverage Cooling"  # R4: the user's name is untouched
    assert after.device_id is not None
    device = dr.async_get(hass).async_get(after.device_id)
    assert device is not None
    assert device.name == RENAMED


@pytest.mark.parametrize(
    ("mode", "created"),
    [
        pytest.param(FRIENDLY_NAMES, ORIGINAL_ENTITY_ID, id="friendly"),
        pytest.param(CIRCUIT_NUMBERS, CIRCUIT_NUMBERS_ENTITY_ID, id="circuit_numbers"),
    ],
)
async def test_recreate_offers_the_renamed_id_after_a_pushed_rename_in_both_modes(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    mode: dict[str, bool],
    created: str,
) -> None:
    """The two modes part company at the id an entity is created with, and nowhere later.

    Both followed the same push and the same reload, both display the new
    name, and both keep the id they had (R5). Recreate proposes the renamed id
    in both, which is issue #252: the offer is composed from the circuit's
    device, and the device carries the circuit's name whichever mode named the
    entity's id.
    """
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    await install.rename_on_the_panel(RENAMED)
    sensor = await install.load(RENAMED)

    assert sensor.entity_id == created
    registry = er.async_get(hass)
    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry_entry.original_name == "Power"
    assert er.async_get_full_entity_name(hass, registry_entry) == f"SPAN Panel {RENAMED} Power"
    assert registry.async_regenerate_entity_id(registry_entry) == RENAMED_ENTITY_ID


# --- Entity-id formats that do not end with the device and then the entity ----
#
# The format is the user's to reorder, and Core asks only that it name the
# device and the entity. Composed in another order, an id ends wherever that
# order ends, on the device's name under (entity, device). Each case creates an
# entity, reloads, accepts whatever Recreate offers and reloads again: an offer
# that changes once taken, or one made to an id nothing changed, is a rename
# the user did not cause (R1).

OTHER_FORMATS: dict[str, tuple[list[EntityNamePart], str]] = {
    "entity-device": (
        [EntityNamePart.ENTITY, EntityNamePart.DEVICE],
        "{suffix}_{device}",
    ),
    "parent-entity-device": (
        [EntityNamePart.PARENT_DEVICE, EntityNamePart.ENTITY, EntityNamePart.DEVICE],
        "span_panel_{suffix}_{device}",
    ),
    "device-entity-parent": (
        [EntityNamePart.DEVICE, EntityNamePart.ENTITY, EntityNamePart.PARENT_DEVICE],
        "{device}_{suffix}_span_panel",
    ),
    "area-entity-parent-device": (
        [
            EntityNamePart.AREA,
            EntityNamePart.ENTITY,
            EntityNamePart.PARENT_DEVICE,
            EntityNamePart.DEVICE,
        ],
        "{suffix}_span_panel_{device}",
    ),
}
"""Formats with the entity somewhere other than last, and the object id each composes."""

INSTALLS: dict[str, tuple[_CircuitInstall, str, str]] = {
    "sensor": (_SensorInstall, "sensor", "power"),
    "switch": (_SwitchInstall, "switch", "breaker"),
    "select": (_SelectInstall, "select", "circuit_priority"),
}
"""Each platform's install, its domain and the suffix its ids carry."""


def _save_format(hass: HomeAssistant, format_id: str) -> str:
    """Save one of `OTHER_FORMATS` as the user's, and return the object id it composes."""
    parts, shape = OTHER_FORMATS[format_id]
    er.async_get(hass).async_update_settings(entity_id_parts=parts)
    return shape


async def _accept_what_recreate_offers(
    hass: HomeAssistant, install: _Install[SpanPanelEntity], circuit_name: str, entity_id: str
) -> str:
    """Reload, take Recreate's offer for the entity, and return the id it then has."""
    await install.load(circuit_name)
    registry = er.async_get(hass)
    row = registry.async_get(entity_id)
    assert row is not None
    offered = registry.async_regenerate_entity_id(row)
    if offered != entity_id:
        registry.async_update_entity(entity_id, new_entity_id=offered)
        await hass.async_block_till_done()
    return offered


async def _assert_recreate_offers_nothing(
    hass: HomeAssistant, install: _Install[SpanPanelEntity], circuit_name: str, entity_id: str
) -> er.RegistryEntry:
    """Reload, and hold Recreate to offering the entity the id it has."""
    reloaded = await install.load(circuit_name)
    assert reloaded.entity_id == entity_id
    registry = er.async_get(hass)
    row = registry.async_get(entity_id)
    assert row is not None
    assert registry.async_regenerate_entity_id(row) == entity_id
    return row


@pytest.mark.parametrize("format_id", OTHER_FORMATS)
@pytest.mark.parametrize("platform", INSTALLS)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(FRIENDLY_NAMES, id="friendly"),
        pytest.param(CIRCUIT_NUMBERS, id="circuit_numbers"),
    ],
)
async def test_an_id_composed_under_another_format_reads_back_to_its_base(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    format_id: str,
    platform: str,
    mode: dict[str, bool],
) -> None:
    """An entity created under a reordered format is offered the id that format composes.

    In circuit-names mode that is the id it was created with, at every reload.
    In circuit-numbers mode it is the circuit-names id, once, and nothing after
    it is accepted. Read as ending on the device's name with the suffix left
    out, every entity of the circuit was offered the device's bare id.
    """
    shape = _save_format(hass, format_id)
    make_install, domain, suffix = INSTALLS[platform]
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    named = f"{domain}.{shape.format(suffix=suffix, device='kitchen_outlets')}"
    numbered = f"{domain}.{shape.format(suffix=suffix, device='circuit_15')}"

    install = make_install(hass, entry)
    created = (await install.load("Kitchen Outlets")).entity_id
    assert created == (numbered if mode is CIRCUIT_NUMBERS else named)

    accepted = await _accept_what_recreate_offers(hass, install, "Kitchen Outlets", created)

    assert accepted == named
    row = await _assert_recreate_offers_nothing(hass, install, "Kitchen Outlets", named)
    assert row.object_id_base == suffix.replace("_", " ")


@pytest.mark.parametrize(
    ("mode", "created"),
    [
        pytest.param(FRIENDLY_NAMES, "sensor.solar_power", id="friendly"),
        pytest.param(CIRCUIT_NUMBERS, "sensor.power_circuit_15", id="circuit_numbers"),
    ],
)
async def test_a_name_ending_on_the_suffix_leaves_it_out_under_another_format(
    hass: HomeAssistant, entry: MockConfigEntry, mode: dict[str, bool], created: str
) -> None:
    """A circuit named "Solar Power" is `sensor.solar_power` whichever part comes first."""
    _save_format(hass, "entity-device")
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = _SensorInstall(hass, entry)
    assert (await install.load("Solar Power")).entity_id == created

    accepted = await _accept_what_recreate_offers(hass, install, "Solar Power", created)

    assert accepted == "sensor.solar_power"
    row = await _assert_recreate_offers_nothing(hass, install, "Solar Power", accepted)
    assert row.object_id_base == ""


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(FRIENDLY_NAMES, id="friendly"),
        pytest.param(CIRCUIT_NUMBERS, id="circuit_numbers"),
    ],
)
async def test_an_unnamed_circuit_keeps_its_suffix_under_another_format(
    hass: HomeAssistant, entry: MockConfigEntry, mode: dict[str, bool]
) -> None:
    """An unnamed circuit's device is named by its position, and its ids keep their suffix."""
    shape = _save_format(hass, "area-entity-parent-device")
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = _SensorInstall(hass, entry)
    created = (await install.load("")).entity_id
    assert created == f"sensor.{shape.format(suffix='power', device='circuit_15')}"

    row = await _assert_recreate_offers_nothing(hass, install, "", created)

    assert row.object_id_base == "power"


def _seed_preset_era_energy_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Register the energy sensor under the id a release that preset ids gave it."""
    seeded = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, "consumedEnergyWh"),
        suggested_object_id="span_panel_refrigerator_energy_consumed",
        config_entry=entry,
    )
    assert seeded.entity_id == PRESET_ERA_ENTITY_ID
    return seeded.entity_id


@pytest.mark.parametrize(
    ("format_id", "offered"),
    [
        ("entity-device", "sensor.energy_consumed_refrigerator"),
        ("parent-entity-device", "sensor.span_panel_energy_consumed_refrigerator"),
        ("device-entity-parent", "sensor.refrigerator_energy_consumed_span_panel"),
    ],
)
async def test_an_older_release_s_id_keeps_its_wording_under_another_format(
    hass: HomeAssistant, entry: MockConfigEntry, format_id: str, offered: str
) -> None:
    """The user's format reorders an id an older release preset, in the wording it has.

    The offer is the format's doing, which the user chose. Once accepted it is
    the entity's id and nothing more is offered: read from its end alone, an id
    with a part after the entity's lost its wording and was offered
    `consumed_energy` for `energy_consumed`.
    """
    _save_format(hass, format_id)
    seeded = _seed_preset_era_energy_sensor(hass, entry)
    install = _LegacyInstall(hass, entry)

    accepted = await _accept_what_recreate_offers(hass, install, ORIGINAL_NAME, seeded)

    assert accepted == offered
    row = await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, offered)
    assert row.object_id_base == "energy consumed"


async def test_an_area_assigned_later_keeps_the_wording_under_another_format(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An id no wording reproduces any more is still read for the wording it spells.

    The panel is given an area after the id was accepted, so the id is not what
    the format composes now. The offer gains the area, which the user caused,
    and keeps both the suffix and its wording.
    """
    er.async_get(hass).async_update_settings(
        entity_id_parts=[EntityNamePart.AREA, EntityNamePart.ENTITY, EntityNamePart.DEVICE]
    )
    seeded = _seed_preset_era_energy_sensor(hass, entry)
    install = _LegacyInstall(hass, entry)
    accepted = await _accept_what_recreate_offers(hass, install, ORIGINAL_NAME, seeded)
    assert accepted == "sensor.energy_consumed_refrigerator"
    await install.load(ORIGINAL_NAME)

    basement = ar.async_get(hass).async_create("Basement")
    dr.async_get(hass).async_update_device(entry.runtime_data.panel_device_id, area_id=basement.id)
    with_the_area = await _accept_what_recreate_offers(hass, install, ORIGINAL_NAME, accepted)

    assert with_the_area == "sensor.basement_energy_consumed_refrigerator"
    await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, with_the_area)


async def test_a_panel_name_that_runs_into_the_suffix_is_not_read_as_part_of_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An id Core composes today is never offered another, whatever its parts spell together.

    A panel the user named "Shop Current" sits in front of the entity's part
    under this format, so a power sensor's id spells `current_power`, which is
    also a wording the suffix once shipped with. Read for the words it spells,
    the id was offered `..._current_current_power_...`. Composed for each
    wording in turn, only `power` gives the id the entity has.
    """
    _save_format(hass, "parent-entity-device")
    install = _SensorInstall(hass, entry)
    created = (await install.load("Kitchen Outlets")).entity_id
    assert created == "sensor.span_panel_power_kitchen_outlets"
    dr.async_get(hass).async_update_device(
        entry.runtime_data.panel_device_id, name_by_user="Shop Current"
    )

    accepted = await _accept_what_recreate_offers(hass, install, "Kitchen Outlets", created)

    assert accepted == "sensor.shop_current_power_kitchen_outlets"
    row = await _assert_recreate_offers_nothing(hass, install, "Kitchen Outlets", accepted)
    assert row.object_id_base == "power"


async def test_an_id_core_numbered_keeps_the_wording_it_has(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """An id with a number after it, because the composed one was taken, is that composition.

    Two circuits of one name share every composed id, and the second's carry
    `_2`. Read from its end, such an id spells no wording at all, so one in an
    older release's wording was offered the new one.
    """
    registry = er.async_get(hass)
    taken = registry.async_get_or_create(
        "sensor", "other", "taken", suggested_object_id="span_panel_refrigerator_energy_consumed"
    )
    assert taken.entity_id == PRESET_ERA_ENTITY_ID
    numbered = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, "consumedEnergyWh"),
        suggested_object_id="span_panel_refrigerator_energy_consumed",
        config_entry=entry,
    ).entity_id
    assert numbered == f"{PRESET_ERA_ENTITY_ID}_2"
    install = _LegacyInstall(hass, entry)
    await install.load(ORIGINAL_NAME)

    row = await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, numbered)

    assert row.object_id_base == "energy consumed"


# --- A circuit's device renamed in Home Assistant ------------------------------
#
# Core composes ids from the name the user gave a device ahead of the device's
# own, and the base depends on that name: it is empty where the name already
# ends on the suffix words. The rename reloads nothing, so the bases of the
# circuit's entities are worked out again as the rename is stored
# (`circuit_object_ids.async_keep_circuit_id_bases_current`).

RENAMED_DEVICES = [
    pytest.param(
        _SensorInstall,
        FRIENDLY_NAMES,
        "Solar Power",
        "sensor.span_panel_solar_power",
        "Roof Array",
        "sensor.span_panel_roof_array_power",
        id="the-new-name-no-longer-ends-on-the-suffix",
    ),
    pytest.param(
        _SwitchInstall,
        FRIENDLY_NAMES,
        "Main Breaker",
        "switch.span_panel_main_breaker",
        "Whole House",
        "switch.span_panel_whole_house_breaker",
        id="switch",
    ),
    pytest.param(
        _SensorInstall,
        FRIENDLY_NAMES,
        "Solar",
        "sensor.span_panel_solar_power",
        "Solar Power",
        "sensor.span_panel_solar_power",
        id="the-new-name-ends-on-the-suffix",
    ),
    pytest.param(
        _SensorInstall,
        CIRCUIT_NUMBERS,
        "Garage Power",
        CIRCUIT_NUMBERS_ENTITY_ID,
        "Garage Subpanel",
        "sensor.span_panel_garage_subpanel_power",
        id="circuit-numbers",
    ),
]
"""An install, its mode, a circuit and its entity's id, a new device name, and the offer."""


def _rename_the_circuit_device(hass: HomeAssistant, entity_id: str, name: str | None) -> None:
    """Rename the device an entity is on, as its user would in Home Assistant."""
    row = er.async_get(hass).async_get(entity_id)
    assert row is not None
    assert row.device_id is not None
    dr.async_get(hass).async_update_child_device(row.device_id, name_by_user=name)


@pytest.mark.parametrize(
    ("make_install", "mode", "circuit_name", "entity_id", "device_name", "offered"),
    RENAMED_DEVICES,
)
async def test_a_device_the_user_renamed_decides_what_recreate_offers(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    make_install: _CircuitInstall,
    mode: dict[str, bool],
    circuit_name: str,
    entity_id: str,
    device_name: str,
    offered: str,
) -> None:
    """After a reload, the offer is the id a new entity on the renamed device gets.

    A circuit named "Solar Power" left the suffix out of its id. Renamed "Roof
    Array" in Home Assistant, its device no longer says "power", so the offer
    is `..._roof_array_power`, and it is the entity's own id once accepted.
    """
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = make_install(hass, entry)
    assert (await install.load(circuit_name)).entity_id == entity_id
    await install.load(circuit_name)

    _rename_the_circuit_device(hass, entity_id, device_name)
    accepted = await _accept_what_recreate_offers(hass, install, circuit_name, entity_id)

    assert accepted == offered
    await _assert_recreate_offers_nothing(hass, install, circuit_name, offered)


@pytest.mark.parametrize(
    ("make_install", "mode", "circuit_name", "entity_id", "device_name", "offered"),
    RENAMED_DEVICES,
)
async def test_a_device_rename_reaches_recreate_without_a_reload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts_without_area: None,
    make_install: _CircuitInstall,
    mode: dict[str, bool],
    circuit_name: str,
    entity_id: str,
    device_name: str,
    offered: str,
) -> None:
    """The offer is right the moment the rename is stored, which is when the user is asked.

    Home Assistant asks whether to rename a device's entity ids straight after
    the device is renamed, and composes the answer from each row as stored.
    Left as decided for the old name, the base offered `..._roof_array` for a
    power sensor, and that id stayed once accepted.

    Taking the user's name away again brings back the offer the circuit's own
    name composes.
    """
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = make_install(hass, entry)
    assert (await install.load(circuit_name)).entity_id == entity_id
    await install.load(circuit_name)
    registry = er.async_get(hass)
    row = registry.async_get(entity_id)
    assert row is not None
    before = registry.async_regenerate_entity_id(row)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _rename_the_circuit_device(hass, entity_id, device_name)

    row = registry.async_get(entity_id)
    assert row is not None
    assert registry.async_regenerate_entity_id(row) == offered

    _rename_the_circuit_device(hass, entity_id, None)

    row = registry.async_get(entity_id)
    assert row is not None
    assert registry.async_regenerate_entity_id(row) == before
    stop()


async def test_a_device_rename_moves_no_entity_and_stores_only_a_changed_base(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The refresh writes a row's base and nothing else, and leaves an unchanged base alone."""
    install = _SensorInstall(hass, entry)
    await install.load("Solar Power")
    await install.load("Solar Power")
    registry = er.async_get(hass)
    entity_id = "sensor.span_panel_solar_power"
    before = registry.async_get(entity_id)
    assert before is not None
    assert before.object_id_base == ""
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _rename_the_circuit_device(hass, entity_id, "Roof Power")
    unchanged = registry.async_get(entity_id)
    _rename_the_circuit_device(hass, entity_id, "Roof Array")
    after = registry.async_get(entity_id)
    stop()

    assert unchanged is before
    assert after is not None
    assert after.object_id_base == "power"
    assert attr.evolve(after, object_id_base="", modified_at=before.modified_at) == before


# --- An entity with an area of its own ----------------------------------------
#
# Core composes an entity that has an area of its own from that area and the
# entity's base, with neither device in front. So the base cannot be left out
# for it: a circuit named "Solar Power" leaves the suffix out of an id its
# device's name already ends, and with no device in front that leaves the id
# with nothing to say for the entity. R1's own-area exception offers such an
# entity its label alone, `sensor.backyard_power`.

DEVICE_ENTITY_FORMAT = [EntityNamePart.DEVICE, EntityNamePart.ENTITY]

OWN_AREA_CASES = [
    pytest.param(
        _SensorInstall,
        None,
        "Solar Power",
        "sensor.span_panel_solar_power",
        "sensor.backyard_power",
        id="sensor",
    ),
    pytest.param(
        _SwitchInstall,
        None,
        "Main Breaker",
        "switch.span_panel_main_breaker",
        "switch.backyard_breaker",
        id="switch",
    ),
    pytest.param(
        _SelectInstall,
        None,
        "Pool Circuit Priority",
        "select.span_panel_pool_circuit_priority",
        "select.backyard_circuit_priority",
        id="select",
    ),
    # No area part to say, and no device in front: the label is all there is.
    pytest.param(
        _SensorInstall,
        DEVICE_ENTITY_FORMAT,
        "Solar Power",
        "sensor.solar_power",
        "sensor.power",
        id="a-format-with-no-area-part",
    ),
]
"""An install, a saved format or none, a circuit whose name ends on the suffix, its id, the offer."""


def _give_the_entity_an_area(hass: HomeAssistant, entity_id: str, area_id: str | None) -> None:
    """Set or clear an entity's own area, as its user would in Home Assistant."""
    er.async_get(hass).async_update_entity(entity_id, area_id=area_id)


@pytest.mark.parametrize(
    ("make_install", "parts", "circuit_name", "entity_id", "offered"), OWN_AREA_CASES
)
async def test_an_entity_with_an_area_of_its_own_is_offered_its_label_alone(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    make_install: _CircuitInstall,
    parts: list[EntityNamePart] | None,
    circuit_name: str,
    entity_id: str,
    offered: str,
) -> None:
    """The suffix is not left out of an id Core composes with no device in front.

    Left out, the offer was the area alone, `sensor.backyard`, or under a
    format with no area part Core's fallback, the platform and the unique id.
    Once accepted, the label-only id is the entity's own.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=parts)
    install = make_install(hass, entry)
    assert (await install.load(circuit_name)).entity_id == entity_id
    await _assert_recreate_offers_nothing(hass, install, circuit_name, entity_id)

    backyard = ar.async_get(hass).async_create("Backyard")
    _give_the_entity_an_area(hass, entity_id, backyard.id)
    accepted = await _accept_what_recreate_offers(hass, install, circuit_name, entity_id)

    assert accepted == offered
    await _assert_recreate_offers_nothing(hass, install, circuit_name, offered)


async def test_an_entity_with_an_area_of_its_own_keeps_the_wording_its_id_has(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The label-only offer spells the suffix as the entity's id does (R1)."""
    seeded = _seed_preset_era_energy_sensor(hass, entry)
    install = _LegacyInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    backyard = ar.async_get(hass).async_create("Backyard")
    _give_the_entity_an_area(hass, seeded, backyard.id)

    accepted = await _accept_what_recreate_offers(hass, install, ORIGINAL_NAME, seeded)

    assert accepted == "sensor.backyard_energy_consumed"
    await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, accepted)


async def test_an_id_that_is_the_area_alone_is_offered_the_label_too(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The empty base is not kept for an entity with an area, even where it spells the id.

    `sensor.backyard` is what the empty base composes for a sensor in
    Backyard. It names nothing of the entity, so the base is its label's
    words all the same.
    """
    install = _SensorInstall(hass, entry)
    created = (await install.load("Solar Power")).entity_id
    backyard = ar.async_get(hass).async_create("Backyard")
    registry = er.async_get(hass)
    registry.async_update_entity(created, area_id=backyard.id, new_entity_id="sensor.backyard")

    accepted = await _accept_what_recreate_offers(hass, install, "Solar Power", "sensor.backyard")

    assert accepted == "sensor.backyard_power"
    await _assert_recreate_offers_nothing(hass, install, "Solar Power", accepted)


@pytest.mark.parametrize(
    ("make_install", "parts", "circuit_name", "entity_id", "offered"), OWN_AREA_CASES
)
async def test_an_area_given_to_an_entity_reaches_recreate_without_a_reload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    make_install: _CircuitInstall,
    parts: list[EntityNamePart] | None,
    circuit_name: str,
    entity_id: str,
    offered: str,
) -> None:
    """The offer is right the moment the area is stored, and again once it is cleared.

    Setting an entity's area reloads nothing, and Core composes Recreate's
    offer from the row as stored. A base left empty for the device's name
    offered the area alone, and one kept for the area offered the suffix twice
    over, `..._solar_power_power`, once the area was taken away.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=parts)
    install = make_install(hass, entry)
    assert (await install.load(circuit_name)).entity_id == entity_id
    await install.load(circuit_name)
    registry = er.async_get(hass)
    backyard = ar.async_get(hass).async_create("Backyard")
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, entity_id, backyard.id)

    row = registry.async_get(entity_id)
    assert row is not None
    assert row.area_id == backyard.id
    assert registry.async_regenerate_entity_id(row) == offered

    _give_the_entity_an_area(hass, entity_id, None)

    row = registry.async_get(entity_id)
    assert row is not None
    assert row.area_id is None
    assert registry.async_regenerate_entity_id(row) == entity_id
    stop()


async def test_an_area_change_moves_no_entity_and_stores_only_a_changed_base(
    hass: HomeAssistant, entry: MockConfigEntry, parts_without_area: None
) -> None:
    """The refresh writes the row's base beside the area the user set, and nothing else.

    A circuit whose name does not end on the suffix keeps the base it has, so
    its row is written once, by the user.
    """
    install = _SensorInstall(hass, entry)
    await install.load("Solar Power")
    await install.load("Solar Power")
    registry = er.async_get(hass)
    entity_id = "sensor.span_panel_solar_power"
    before = registry.async_get(entity_id)
    assert before is not None
    assert before.object_id_base == ""
    backyard = ar.async_get(hass).async_create("Backyard")
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, entity_id, backyard.id)
    after = registry.async_get(entity_id)
    stop()

    assert after is not None
    assert after.object_id_base == "power"
    assert (
        attr.evolve(after, object_id_base="", area_id=None, modified_at=before.modified_at)
        == before
    )


async def test_an_area_given_to_another_entry_s_entity_is_left_alone(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Only this entry's circuit entities are refreshed; any other row is not touched."""
    install = _SensorInstall(hass, entry)
    await install.load("Solar Power")
    other = MockConfigEntry(domain="other_domain", entry_id="entry-other")
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    stranger = registry.async_get_or_create(
        "sensor", "other_domain", "stranger-power", config_entry=other, object_id_base=""
    )
    backyard = ar.async_get(hass).async_create("Backyard")
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, stranger.entity_id, backyard.id)
    stop()

    row = registry.async_get(stranger.entity_id)
    assert row is not None
    assert row.object_id_base == ""


# An id composed before its entity had an area of its own was composed with the
# device in front, and that composition still spells it, number and all, in the
# wording it has. Read for its words instead, an id whose panel name runs into
# the suffix spells the wrong one, so the label-only offer took a wording the
# id never had.


@dataclass(frozen=True, slots=True)
class _IdFromBeforeTheArea:
    """An entity's id from before it had an area of its own, and its offer once it has one."""

    make_install: type[_LegacyInstall] | type[_SensorInstall]
    api_key: str
    """The key the sensor's unique id is built from."""

    entity_id: str
    """The id Core composed for the entity with the device in front."""

    offered: str
    """The offer once the entity is in Garage: its label alone, in the wording `entity_id` has."""

    numbered: bool = False
    """Whether another entity had the composed id first, so Core numbered this one's."""

    circuit_name: str = ORIGINAL_NAME
    parts: tuple[EntityNamePart, ...] | None = None
    """The entity-id format the user saved, or None for Core's default."""

    panel_name: str | None = None
    """The name the user gave the panel's device, or None."""


IDS_FROM_BEFORE_THE_AREA = [
    # Two circuits named alike: the second's ids carry Core's number.
    pytest.param(
        _IdFromBeforeTheArea(
            _LegacyInstall,
            "consumedEnergyWh",
            f"{PRESET_ERA_ENTITY_ID}_2",
            "sensor.garage_energy_consumed",
            numbered=True,
        ),
        id="numbered-energy-consumed",
    ),
    pytest.param(
        _IdFromBeforeTheArea(
            _SensorInstall,
            "instantPowerW",
            "sensor.span_panel_refrigerator_current_power_2",
            "sensor.garage_current_power",
            numbered=True,
        ),
        id="numbered-current-power",
    ),
    # A panel named "Shop Current" in front of the entity's part: the id spells
    # `current_power`, a wording the suffix once shipped with, but was composed
    # from `power`. The format has no area part, so the label is all there is.
    pytest.param(
        _IdFromBeforeTheArea(
            _SensorInstall,
            "instantPowerW",
            "sensor.shop_current_power_kitchen_outlets",
            "sensor.power",
            circuit_name="Kitchen Outlets",
            parts=(EntityNamePart.PARENT_DEVICE, EntityNamePart.ENTITY, EntityNamePart.DEVICE),
            panel_name="Shop Current",
        ),
        id="a-panel-name-running-into-the-suffix",
    ),
]


async def _load_an_id_from_before_the_area(
    hass: HomeAssistant, entry: MockConfigEntry, case: _IdFromBeforeTheArea
) -> tuple[_LegacyInstall | _SensorInstall, str]:
    """Register the case's entity on its id, load it, and hold it to no offer while it has no area.

    Returns the install and the entity's id.
    """
    registry = er.async_get(hass)
    if case.parts is not None:
        registry.async_update_settings(entity_id_parts=list(case.parts))
    object_id = case.entity_id.split(".", 1)[1]
    if case.numbered:
        object_id = object_id.rpartition("_")[0]
        taken = registry.async_get_or_create(
            "sensor", "other", "taken", suggested_object_id=object_id
        )
        assert taken.entity_id == f"sensor.{object_id}"
    seeded = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, case.api_key),
        suggested_object_id=object_id,
        config_entry=entry,
    ).entity_id
    assert seeded == case.entity_id

    install = case.make_install(hass, entry)
    await install.load(case.circuit_name)
    if case.panel_name is not None:
        dr.async_get(hass).async_update_device(
            entry.runtime_data.panel_device_id, name_by_user=case.panel_name
        )
    await _assert_recreate_offers_nothing(hass, install, case.circuit_name, seeded)
    return install, seeded


@pytest.mark.parametrize("case", IDS_FROM_BEFORE_THE_AREA)
async def test_an_id_from_before_the_entity_s_area_keeps_its_wording_in_the_label_only_offer(
    hass: HomeAssistant, entry: MockConfigEntry, case: _IdFromBeforeTheArea
) -> None:
    """The label-only offer spells the suffix as the id the device in front composed does (R1).

    Read for its words, the "Shop Current" id was offered
    `sensor.current_power`. Once accepted, the offer is the entity's own id.
    """
    install, seeded = await _load_an_id_from_before_the_area(hass, entry, case)
    garage = ar.async_get(hass).async_create("Garage")
    _give_the_entity_an_area(hass, seeded, garage.id)

    accepted = await _accept_what_recreate_offers(hass, install, case.circuit_name, seeded)

    assert accepted == case.offered
    await _assert_recreate_offers_nothing(hass, install, case.circuit_name, accepted)


@pytest.mark.parametrize("case", IDS_FROM_BEFORE_THE_AREA)
async def test_an_area_given_to_an_id_from_before_it_keeps_its_wording_without_a_reload(
    hass: HomeAssistant, entry: MockConfigEntry, case: _IdFromBeforeTheArea
) -> None:
    """The listener's refresh reads the wording as a reload does, and gives the id back on clearing."""
    await _load_an_id_from_before_the_area(hass, entry, case)
    registry = er.async_get(hass)
    garage = ar.async_get(hass).async_create("Garage")
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, case.entity_id, garage.id)

    row = registry.async_get(case.entity_id)
    assert row is not None
    assert row.area_id == garage.id
    assert registry.async_regenerate_entity_id(row) == case.offered

    _give_the_entity_an_area(hass, case.entity_id, None)

    row = registry.async_get(case.entity_id)
    assert row is not None
    assert row.area_id is None
    assert registry.async_regenerate_entity_id(row) == case.entity_id
    stop()


async def test_an_id_core_composes_in_the_entity_s_area_is_read_before_one_from_before_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An id Core composes for the entity in its own area is offered nothing.

    Under `(DEVICE, ENTITY)` a circuit named "Current" composes
    `sensor.current_power` from `power` with its device in front. In an area
    of its own the entity composes the same id from `current power`, its
    label alone in a wording the suffix shipped with. Read first for the
    wording from before the area, it was offered `sensor.power`.
    """
    er.async_get(hass).async_update_settings(entity_id_parts=DEVICE_ENTITY_FORMAT)
    install = _SensorInstall(hass, entry)
    created = (await install.load("Current")).entity_id
    assert created == "sensor.current_power"
    await _assert_recreate_offers_nothing(hass, install, "Current", created)
    backyard = ar.async_get(hass).async_create("Backyard")
    _give_the_entity_an_area(hass, created, backyard.id)

    row = await _assert_recreate_offers_nothing(hass, install, "Current", created)

    assert row.object_id_base == "current power"


# --- Issue #252 for an id in a spelling a new entity no longer gets -------------
#
# A circuit renamed in the SPAN app leaves its entities on ids that name
# neither the device nor the identifier any more. No wording composes such an
# id, so it is read by its tail, and the suffix has to come back in the
# spelling the id carries: `energy_consumed` from the releases that preset
# ids, `current_power` from the first ones. Offered the new wording instead,
# the rename the user made would bring one they did not make along with it.

NON_DEFAULT_SPELLINGS = [
    pytest.param(
        _LegacyInstall,
        "consumedEnergyWh",
        None,
        "span_panel_refrigerator_energy_consumed",
        "sensor.span_panel_beer_fridge_energy_consumed",
        id="energy-consumed",
    ),
    pytest.param(
        _SensorInstall,
        "instantPowerW",
        None,
        "span_panel_refrigerator_current_power",
        "sensor.span_panel_beer_fridge_current_power",
        id="current-power",
    ),
    # The same under a format that puts the entity first: the id ends on the
    # old name, and the spelling is found as whole words ahead of it.
    pytest.param(
        _LegacyInstall,
        "consumedEnergyWh",
        [EntityNamePart.ENTITY, EntityNamePart.DEVICE],
        "energy_consumed_refrigerator",
        "sensor.energy_consumed_beer_fridge",
        id="energy-consumed-entity-first",
    ),
    pytest.param(
        _SensorInstall,
        "instantPowerW",
        [EntityNamePart.ENTITY, EntityNamePart.DEVICE],
        "current_power_refrigerator",
        "sensor.current_power_beer_fridge",
        id="current-power-entity-first",
    ),
]
"""An install, its sensor's API key, a saved format or none, the object id it has, the offer."""


@pytest.mark.parametrize(
    ("make_install", "api_key", "parts", "object_id", "offered"), NON_DEFAULT_SPELLINGS
)
async def test_a_renamed_circuit_keeps_a_spelling_new_entities_no_longer_get(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    make_install: type[_LegacyInstall] | type[_SensorInstall],
    api_key: str,
    parts: list[EntityNamePart] | None,
    object_id: str,
    offered: str,
) -> None:
    """Only the half the user changed moves, and the id it is offered is then its own.

    Nothing is offered until the rename. After it the offer carries the new
    name and the spelling the id has, and once accepted it reads back to the
    same base, so the next restart offers nothing more.
    """
    if parts is None:
        parts = [EntityNamePart.PARENT_DEVICE, EntityNamePart.DEVICE, EntityNamePart.ENTITY]
    er.async_get(hass).async_update_settings(entity_id_parts=parts)
    registry = er.async_get(hass)
    seeded = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, api_key),
        suggested_object_id=object_id,
        config_entry=entry,
    ).entity_id
    install = make_install(hass, entry)
    await install.load(ORIGINAL_NAME)
    row = await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, seeded)
    words = row.object_id_base

    accepted = await _accept_what_recreate_offers(hass, install, RENAMED, seeded)

    assert accepted == offered
    row = await _assert_recreate_offers_nothing(hass, install, RENAMED, offered)
    assert row.object_id_base == words
    assert words in {"energy consumed", "current power"}


# The same for an id Core numbered. Circuits of one name share every composed
# id, so Core gives the second's `_2` and a third's `_3`. Renamed in the SPAN
# app, such an id names neither the device nor the identifier, and is read by
# its tail, which ends on the number. Read only as it stands, it spelled no
# wording at all, and the offer took the one a new entity gets.

NUMBERED_SPELLINGS = [
    pytest.param(
        _LegacyInstall,
        "consumedEnergyWh",
        "span_panel_refrigerator_energy_consumed",
        "sensor.span_panel_beer_fridge_energy_consumed",
        id="energy-consumed",
    ),
    # The spelling a new entity gets, which the offer took for every id.
    pytest.param(
        _LegacyInstall,
        "consumedEnergyWh",
        "span_panel_refrigerator_consumed_energy",
        "sensor.span_panel_beer_fridge_consumed_energy",
        id="consumed-energy",
    ),
    pytest.param(
        _SensorInstall,
        "instantPowerW",
        "span_panel_refrigerator_current_power",
        "sensor.span_panel_beer_fridge_current_power",
        id="current-power",
    ),
]
"""An install, its sensor's API key, the object id Core composed for it, the offer after the rename."""


@pytest.mark.parametrize("number", [2, 3])
@pytest.mark.parametrize(("make_install", "api_key", "composed", "offered"), NUMBERED_SPELLINGS)
async def test_a_renamed_circuit_keeps_its_spelling_past_the_number_core_added(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    make_install: type[_LegacyInstall] | type[_SensorInstall],
    api_key: str,
    composed: str,
    offered: str,
    number: int,
) -> None:
    """The number Core added to the id is not read as its end, and the offer carries none.

    Entities of circuits named alike hold the composed id and the numbers
    before this one's. Renamed "Beer Fridge", the circuit is offered its new
    name in the spelling its id has, and once accepted that id reads back to
    the same base, so the next restart offers nothing more.
    """
    registry = er.async_get(hass)
    for earlier in range(1, number):
        registry.async_get_or_create(
            "sensor", "other", f"earlier-{earlier}", suggested_object_id=composed
        )
    seeded = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, api_key),
        suggested_object_id=composed,
        config_entry=entry,
    ).entity_id
    assert seeded == f"sensor.{composed}_{number}"
    install = make_install(hass, entry)
    await install.load(ORIGINAL_NAME)
    row = await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, seeded)
    words = row.object_id_base

    accepted = await _accept_what_recreate_offers(hass, install, RENAMED, seeded)

    assert accepted == offered
    row = await _assert_recreate_offers_nothing(hass, install, RENAMED, offered)
    assert row.object_id_base == words


# --- An id that is the suffix alone -------------------------------------------
#
# Under a format with no area part, Core composes an entity with an area of its
# own from its label alone, so the label-only id it accepts is the suffix and
# nothing else: `sensor.energy_consumed`, or `sensor.energy_consumed_2` where
# another circuit's entity had that first. Once its area is cleared, or set on
# the circuit's device instead as the README advises to show the circuit's name
# again, the entity is composed with its device in front, nothing composes the
# id, and it is read by its tail. Read as ending on no form, `energy_consumed`
# was offered `consumed_energy` and `current_power` was offered `power`. Under
# the default format no offer is such an id, but one accepted under another
# format is read the same way, in the area and out of it.


@dataclass(frozen=True, slots=True)
class _FormatForTheSuffixAlone:
    """An entity-id format, and what it offers an entity whose id is the suffix alone.

    Each offer is an object-id shape: `{form}` is the suffix form the id is, and
    `{object_id}` the id itself, number and all.
    """

    parts: tuple[EntityNamePart, ...] | None
    """The format the user saved, or None for Core's default."""

    in_the_area: str
    """The offer while the entity is in Garage."""

    cleared: str
    """The offer once the entity's area is cleared, with no area anywhere."""

    on_the_device: str
    """The offer once the circuit's device is in Kitchen and the entity's own area is cleared."""


FORMATS_FOR_THE_SUFFIX_ALONE = [
    pytest.param(
        _FormatForTheSuffixAlone(
            (EntityNamePart.PARENT_DEVICE, EntityNamePart.DEVICE, EntityNamePart.ENTITY),
            in_the_area="{object_id}",
            cleared="span_panel_refrigerator_{form}",
            on_the_device="refrigerator_{form}",
        ),
        id="no-area-part",
    ),
    # The entity's part first. `current_power` was read for the `power` it ends
    # on here too.
    pytest.param(
        _FormatForTheSuffixAlone(
            (EntityNamePart.ENTITY, EntityNamePart.DEVICE),
            in_the_area="{object_id}",
            cleared="{form}_refrigerator",
            on_the_device="{form}_refrigerator",
        ),
        id="entity-first",
    ),
    # Core's default puts the area in front, so the entity is offered it.
    pytest.param(
        _FormatForTheSuffixAlone(
            None,
            in_the_area="garage_{form}",
            cleared="span_panel_refrigerator_{form}",
            on_the_device="kitchen_refrigerator_{form}",
        ),
        id="default",
    ),
]

SUFFIXES_ALONE = [
    pytest.param(_LegacyInstall, "consumedEnergyWh", "energy_consumed", id="energy-consumed"),
    pytest.param(_LegacyInstall, "consumedEnergyWh", "consumed_energy", id="consumed-energy"),
    pytest.param(_SensorInstall, "instantPowerW", "current_power", id="current-power"),
    pytest.param(_SensorInstall, "instantPowerW", "power", id="power"),
]
"""An install, its sensor's API key, and a form of its suffix that is the whole object id."""


async def _load_the_suffix_alone_in_garage(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    the_format: _FormatForTheSuffixAlone,
    make_install: type[_LegacyInstall] | type[_SensorInstall],
    api_key: str,
    form: str,
    *,
    numbered: bool,
) -> tuple[_LegacyInstall | _SensorInstall, str]:
    """Register the entity on the id that is `form`, give it the area Garage, and load it.

    With `numbered`, another entity holds that id first, so Core numbers this
    one's. Returns the install and the entity's id.
    """
    registry = er.async_get(hass)
    if the_format.parts is not None:
        registry.async_update_settings(entity_id_parts=list(the_format.parts))
    if numbered:
        taken = registry.async_get_or_create("sensor", "other", "taken", suggested_object_id=form)
        assert taken.entity_id == f"sensor.{form}"
    entity_id = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, api_key),
        suggested_object_id=form,
        config_entry=entry,
    ).entity_id
    assert entity_id == (f"sensor.{form}_2" if numbered else f"sensor.{form}")
    garage = ar.async_get(hass).async_create("Garage")
    _give_the_entity_an_area(hass, entity_id, garage.id)

    install = make_install(hass, entry)
    await install.load(ORIGINAL_NAME)
    return install, entity_id


@pytest.mark.parametrize("numbered", [False, True], ids=["as-composed", "numbered"])
@pytest.mark.parametrize(("make_install", "api_key", "form"), SUFFIXES_ALONE)
@pytest.mark.parametrize("the_format", FORMATS_FOR_THE_SUFFIX_ALONE)
async def test_an_id_that_is_the_suffix_alone_keeps_its_wording_once_the_area_is_the_device_s(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    the_format: _FormatForTheSuffixAlone,
    make_install: type[_LegacyInstall] | type[_SensorInstall],
    api_key: str,
    form: str,
    numbered: bool,
) -> None:
    """The README's way back to the circuit's name keeps the wording the id has (R1).

    In Garage the entity is offered its label alone, which under a format with
    no area part is the id it has. With the area set on the circuit's device
    and the entity's own cleared, it is offered the circuit's id in that
    wording after a reload, and once accepted that id reads back to the same
    base.
    """
    install, entity_id = await _load_the_suffix_alone_in_garage(
        hass, entry, the_format, make_install, api_key, form, numbered=numbered
    )
    registry = er.async_get(hass)
    row = registry.async_get(entity_id)
    assert row is not None
    assert row.device_id is not None
    in_the_area = the_format.in_the_area.format(form=form, object_id=entity_id.split(".", 1)[1])
    assert registry.async_regenerate_entity_id(row) == f"sensor.{in_the_area}"

    kitchen = ar.async_get(hass).async_create("Kitchen")
    dr.async_get(hass).async_update_child_device(row.device_id, area_id=kitchen.id)
    _give_the_entity_an_area(hass, entity_id, None)
    accepted = await _accept_what_recreate_offers(hass, install, ORIGINAL_NAME, entity_id)

    assert accepted == f"sensor.{the_format.on_the_device.format(form=form)}"
    row = await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, accepted)
    assert row.object_id_base == form.replace("_", " ")


@pytest.mark.parametrize("numbered", [False, True], ids=["as-composed", "numbered"])
@pytest.mark.parametrize(("make_install", "api_key", "form"), SUFFIXES_ALONE)
@pytest.mark.parametrize("the_format", FORMATS_FOR_THE_SUFFIX_ALONE)
async def test_an_id_that_is_the_suffix_alone_keeps_its_wording_without_a_reload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    the_format: _FormatForTheSuffixAlone,
    make_install: type[_LegacyInstall] | type[_SensorInstall],
    api_key: str,
    form: str,
    numbered: bool,
) -> None:
    """The listener's refresh reads the id as a reload does, as the area is cleared and set again."""
    _, entity_id = await _load_the_suffix_alone_in_garage(
        hass, entry, the_format, make_install, api_key, form, numbered=numbered
    )
    registry = er.async_get(hass)
    row = registry.async_get(entity_id)
    assert row is not None
    garage_id = row.area_id
    assert garage_id is not None
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, entity_id, None)

    row = registry.async_get(entity_id)
    assert row is not None
    assert row.area_id is None
    assert registry.async_regenerate_entity_id(row) == (
        f"sensor.{the_format.cleared.format(form=form)}"
    )

    _give_the_entity_an_area(hass, entity_id, garage_id)

    row = registry.async_get(entity_id)
    assert row is not None
    assert row.area_id == garage_id
    in_the_area = the_format.in_the_area.format(form=form, object_id=entity_id.split(".", 1)[1])
    assert registry.async_regenerate_entity_id(row) == f"sensor.{in_the_area}"
    stop()


# --- A circuit named for what its entity measures -----------------------------
#
# The label-only id an entity with an area of its own accepts, `sensor.garage_power`
# or under a format with no area part `sensor.power`, stays its id once the area
# is cleared or moved to the circuit's device. On a circuit named "Power",
# "Energy" or "Consumed Energy" that id also ends on the device's name, and read
# as stopping there it lost the measurement: `sensor.span_panel_power`, where
# the circuit's own id is `sensor.span_panel_power_power`. It is read for the
# form at its end instead, as the id of a circuit renamed "Power" in the SPAN
# app is. Where Core composes the id today from the device with the suffix left
# out, as it does with the device in the area the label was composed in, the id
# is offered nothing, so the base follows the device's area and the panel's.

PARENT_DEVICE_ENTITY = (EntityNamePart.PARENT_DEVICE, EntityNamePart.DEVICE, EntityNamePart.ENTITY)
DEVICE_ENTITY = (EntityNamePart.DEVICE, EntityNamePart.ENTITY)


@dataclass(frozen=True, slots=True)
class _NamedForTheMeasurement:
    """A circuit named for what its entity measures, and what Recreate offers as its area moves."""

    make_install: type[_SensorInstall] | type[_LegacyInstall] | type[_SwitchInstall]
    parts: tuple[EntityNamePart, ...] | None
    """The entity-id format the user saved, or None for Core's default."""

    circuit_name: str
    created: str
    """The entity's id before it has an area."""

    in_the_area: str
    """The offer in Garage, its label alone, which the case accepts."""

    cleared: str
    """The offer once the entity's area is cleared, with no area anywhere."""

    on_the_device: str
    """The offer once the circuit's device is in Kitchen and the entity's own area is cleared."""

    seed_api_key: str | None = None
    """Where set, the sensor is registered on `created` first, under this key's unique id."""

    label_taken: bool = False
    """Whether another entity has the label-only id first, so Core numbers this one's."""


NAMED_FOR_THE_MEASUREMENT = [
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            PARENT_DEVICE_ENTITY,
            "Power",
            "sensor.span_panel_power_power",
            "sensor.power",
            "sensor.span_panel_power_power",
            # The device alone composes the label-only id, so it is offered nothing.
            "sensor.power",
        ),
        id="no-area-part-power",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            PARENT_DEVICE_ENTITY,
            "Power",
            "sensor.span_panel_power_current_power",
            "sensor.current_power",
            "sensor.span_panel_power_current_power",
            "sensor.power_current_power",
            seed_api_key="instantPowerW",
        ),
        id="no-area-part-current-power",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _LegacyInstall,
            PARENT_DEVICE_ENTITY,
            "Energy",
            "sensor.span_panel_energy_consumed_energy",
            "sensor.consumed_energy",
            "sensor.span_panel_energy_consumed_energy",
            "sensor.energy_consumed_energy",
        ),
        id="no-area-part-energy",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _LegacyInstall,
            PARENT_DEVICE_ENTITY,
            "Consumed Energy",
            "sensor.span_panel_consumed_energy_consumed_energy",
            "sensor.consumed_energy",
            "sensor.span_panel_consumed_energy_consumed_energy",
            "sensor.consumed_energy",
        ),
        id="no-area-part-consumed-energy",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SwitchInstall,
            PARENT_DEVICE_ENTITY,
            "Breaker",
            "switch.span_panel_breaker_breaker",
            "switch.breaker",
            "switch.span_panel_breaker_breaker",
            "switch.breaker",
        ),
        id="no-area-part-breaker",
    ),
    # The device's name leaves `power` out, so the id stops at it again.
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            PARENT_DEVICE_ENTITY,
            "Current Power",
            "sensor.span_panel_current_power",
            "sensor.current_power",
            "sensor.span_panel_current_power",
            "sensor.current_power",
        ),
        id="no-area-part-current-power-circuit",
    ),
    # With no parent device part the device alone composes the label-only id
    # wherever its area is, so the two that keep both halves are offered nothing.
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            DEVICE_ENTITY,
            "Power",
            "sensor.power_power",
            "sensor.power",
            "sensor.power",
            "sensor.power",
        ),
        id="device-entity-power",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            DEVICE_ENTITY,
            "Power",
            "sensor.power_current_power",
            "sensor.current_power",
            "sensor.power_current_power",
            "sensor.power_current_power",
            seed_api_key="instantPowerW",
        ),
        id="device-entity-current-power",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _LegacyInstall,
            DEVICE_ENTITY,
            "Energy",
            "sensor.energy_consumed_energy",
            "sensor.consumed_energy",
            "sensor.energy_consumed_energy",
            "sensor.energy_consumed_energy",
        ),
        id="device-entity-energy",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _LegacyInstall,
            DEVICE_ENTITY,
            "Consumed Energy",
            "sensor.consumed_energy_consumed_energy",
            "sensor.consumed_energy",
            "sensor.consumed_energy",
            "sensor.consumed_energy",
        ),
        id="device-entity-consumed-energy",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SwitchInstall,
            DEVICE_ENTITY,
            "Breaker",
            "switch.breaker_breaker",
            "switch.breaker",
            "switch.breaker",
            "switch.breaker",
        ),
        id="device-entity-breaker",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            None,
            "Power",
            "sensor.span_panel_power_power",
            "sensor.garage_power",
            "sensor.span_panel_power_power",
            "sensor.kitchen_power_power",
        ),
        id="default-power",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            None,
            "Power",
            "sensor.span_panel_power_current_power",
            "sensor.garage_current_power",
            "sensor.span_panel_power_current_power",
            "sensor.kitchen_power_current_power",
            seed_api_key="instantPowerW",
        ),
        id="default-current-power",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _LegacyInstall,
            None,
            "Energy",
            "sensor.span_panel_energy_consumed_energy",
            "sensor.garage_consumed_energy",
            "sensor.span_panel_energy_consumed_energy",
            "sensor.kitchen_energy_consumed_energy",
        ),
        id="default-energy",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _LegacyInstall,
            None,
            "Consumed Energy",
            "sensor.span_panel_consumed_energy_consumed_energy",
            "sensor.garage_consumed_energy",
            "sensor.span_panel_consumed_energy_consumed_energy",
            "sensor.kitchen_consumed_energy_consumed_energy",
        ),
        id="default-consumed-energy",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SwitchInstall,
            None,
            "Breaker",
            "switch.span_panel_breaker_breaker",
            "switch.garage_breaker",
            "switch.span_panel_breaker_breaker",
            "switch.kitchen_breaker_breaker",
        ),
        id="default-breaker",
    ),
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            None,
            "Current Power",
            "sensor.span_panel_current_power",
            "sensor.garage_current_power",
            "sensor.span_panel_current_power",
            "sensor.kitchen_current_power",
        ),
        id="default-current-power-circuit",
    ),
    # Another sensor in Garage has the label first, so Core numbers this one's.
    pytest.param(
        _NamedForTheMeasurement(
            _SensorInstall,
            None,
            "Power",
            "sensor.span_panel_power_power",
            "sensor.garage_power_2",
            "sensor.span_panel_power_power",
            "sensor.kitchen_power_power",
            label_taken=True,
        ),
        id="default-power-numbered",
    ),
]

WHERE_THE_AREA_GOES = [
    pytest.param("cleared", id="cleared"),
    pytest.param("device-then-entity", id="onto-the-device"),
    pytest.param("entity-then-device", id="onto-the-device-entity-first"),
]
"""How the area leaves the entity: cleared, or the README's remedy in either order.

The README gives its two steps, set the area on the circuit's device and clear
the entity's own, in no order, and the listener sees each as it happens.
"""


async def _accept_the_label_alone_in_garage(
    hass: HomeAssistant, entry: MockConfigEntry, case: _NamedForTheMeasurement
) -> tuple[_SensorInstall | _LegacyInstall | _SwitchInstall, str]:
    """Create the case's entity, give it the area Garage, and accept its label-only offer.

    Returns the install and the id the entity then has.
    """
    registry = er.async_get(hass)
    if case.parts is not None:
        registry.async_update_settings(entity_id_parts=list(case.parts))
    if case.seed_api_key is not None:
        seeded = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            build_circuit_unique_id(SERIAL, CIRCUIT_ID, case.seed_api_key),
            suggested_object_id=case.created.split(".", 1)[1],
            config_entry=entry,
        )
        assert seeded.entity_id == case.created
    install = case.make_install(hass, entry)
    assert (await install.load(case.circuit_name)).entity_id == case.created
    await _assert_recreate_offers_nothing(hass, install, case.circuit_name, case.created)

    if case.label_taken:
        domain, object_id = case.in_the_area.split(".", 1)
        taken = registry.async_get_or_create(
            domain, "other", "taken", suggested_object_id=object_id.rpartition("_")[0]
        )
        assert taken.entity_id == case.in_the_area.rpartition("_")[0]
    garage = ar.async_get(hass).async_get_or_create("Garage")
    _give_the_entity_an_area(hass, case.created, garage.id)
    accepted = await _accept_what_recreate_offers(hass, install, case.circuit_name, case.created)
    assert accepted == case.in_the_area
    await _assert_recreate_offers_nothing(hass, install, case.circuit_name, accepted)
    return install, accepted


def _put_the_device_in(hass: HomeAssistant, entity_id: str, area_name: str | None) -> None:
    """Set the area on the device an entity is on, or clear it, as its user would."""
    row = er.async_get(hass).async_get(entity_id)
    assert row is not None
    assert row.device_id is not None
    area_id = None if area_name is None else ar.async_get(hass).async_get_or_create(area_name).id
    dr.async_get(hass).async_update_child_device(row.device_id, area_id=area_id)


def _take_the_area_off(hass: HomeAssistant, entity_id: str, where: str, area_name: str) -> None:
    """Clear the entity's own area, and put its device in `area_name` before or after as `where` says."""
    if where == "device-then-entity":
        _put_the_device_in(hass, entity_id, area_name)
    _give_the_entity_an_area(hass, entity_id, None)
    if where == "entity-then-device":
        _put_the_device_in(hass, entity_id, area_name)


def _offer_as_stored(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    """Return the entity's row as stored, which Recreate composes its offer from (§3.4)."""
    row = er.async_get(hass).async_get(entity_id)
    assert row is not None
    return row


@pytest.mark.parametrize("where", WHERE_THE_AREA_GOES)
@pytest.mark.parametrize("case", NAMED_FOR_THE_MEASUREMENT)
async def test_a_circuit_named_for_its_measurement_keeps_it_once_the_area_leaves_the_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    case: _NamedForTheMeasurement,
    where: str,
) -> None:
    """The label-only id is read for its form, not as an id stopping at the device's name.

    Once the area is cleared, or set on the circuit's device and cleared from
    the entity as the README advises, the offer after a reload carries the
    measurement in the wording the label-only id has, and once accepted it
    reads back to the same base. Read as stopping at the name, the default
    format offered `sensor.span_panel_power`, or `sensor.kitchen_power` after
    the README's remedy.
    """
    install, accepted = await _accept_the_label_alone_in_garage(hass, entry, case)
    _take_the_area_off(hass, accepted, where, "Kitchen")

    offered = await _accept_what_recreate_offers(hass, install, case.circuit_name, accepted)

    assert offered == (case.cleared if where == "cleared" else case.on_the_device)
    await _assert_recreate_offers_nothing(hass, install, case.circuit_name, offered)


@pytest.mark.parametrize("where", WHERE_THE_AREA_GOES)
@pytest.mark.parametrize("case", NAMED_FOR_THE_MEASUREMENT)
async def test_a_circuit_named_for_its_measurement_keeps_it_without_a_reload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    case: _NamedForTheMeasurement,
    where: str,
) -> None:
    """The listener stores, as each step happens, the base a reload then gives too.

    With the entity's own area cleared before the device's is set, a base
    stored for the device with no area offered `sensor.power_power` under
    `(PARENT_DEVICE, DEVICE, ENTITY)`, for an id Core composes today.
    """
    install, accepted = await _accept_the_label_alone_in_garage(hass, entry, case)
    expected = case.cleared if where == "cleared" else case.on_the_device
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _take_the_area_off(hass, accepted, where, "Kitchen")

    row = _offer_as_stored(hass, accepted)
    stop()
    assert row.area_id is None
    assert registry.async_regenerate_entity_id(row) == expected
    await install.load(case.circuit_name)
    assert registry.async_regenerate_entity_id(_offer_as_stored(hass, accepted)) == expected


@pytest.mark.parametrize(
    "where", [param for param in WHERE_THE_AREA_GOES if param.values[0] != "cleared"]
)
async def test_the_label_alone_is_kept_where_the_device_s_name_alone_composes_it(
    hass: HomeAssistant, entry: MockConfigEntry, where: str
) -> None:
    """An id Core composes today is offered nothing, even where it is the label alone too.

    With the circuit's device put in Garage, the area its "Power" sensor had,
    `sensor.garage_power` is what Core composes from that area and the device
    with the suffix left out. So the README's remedy offers nothing here, in
    either order, before a restart and after it, and the id does not take the
    measurement back. Before the device's area was watched, clearing the
    entity's area first stored `power`, and `sensor.garage_power_power` was
    offered until the next restart.
    """
    install = _SensorInstall(hass, entry)
    created = (await install.load("Power")).entity_id
    garage = ar.async_get(hass).async_create("Garage")
    _give_the_entity_an_area(hass, created, garage.id)
    accepted = await _accept_what_recreate_offers(hass, install, "Power", created)
    assert accepted == "sensor.garage_power"
    await install.load("Power")
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _take_the_area_off(hass, accepted, where, "Garage")

    row = _offer_as_stored(hass, accepted)
    stop()
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == ("", accepted)
    row = await _assert_recreate_offers_nothing(hass, install, "Power", accepted)
    assert row.object_id_base == ""


@pytest.mark.parametrize(
    "parts",
    [pytest.param(None, id="default"), pytest.param(PARENT_DEVICE_ENTITY, id="no-area-part")],
)
@pytest.mark.parametrize(
    ("before", "created"),
    [
        pytest.param("Solar Power", "sensor.span_panel_solar_power", id="from-a-name-ending-on-it"),
        pytest.param(
            "Kitchen Outlets", "sensor.span_panel_kitchen_outlets_power", id="from-another-name"
        ),
    ],
)
async def test_a_circuit_renamed_to_its_measurement_is_offered_the_id_a_new_entity_gets(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    before: str,
    created: str,
) -> None:
    """Renamed "Power" in the SPAN app, a circuit keeps the `power` its id ends on.

    The omission is decided from the new name, as for any rename on the panel,
    and a name that is exactly the suffix word leaves nothing out, so the offer
    is `sensor.span_panel_power_power`, the id a new entity on that device
    gets. Once accepted it reads back to the same base. Read as stopping at
    the new name, the offer was `sensor.span_panel_power`.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    install = _SensorInstall(hass, entry)
    assert (await install.load(before)).entity_id == created
    await _assert_recreate_offers_nothing(hass, install, before, created)

    accepted = await _accept_what_recreate_offers(hass, install, "Power", created)

    assert accepted == "sensor.span_panel_power_power"
    row = await _assert_recreate_offers_nothing(hass, install, "Power", accepted)
    assert row.object_id_base == "power"


def _seed_the_id_that_stops_at_power(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Register the power sensor of a circuit named "Power" on `sensor.span_panel_power`."""
    seeded = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, CIRCUIT_ID, "instantPowerW"),
        suggested_object_id="span_panel_power",
        config_entry=entry,
    )
    assert seeded.entity_id == "sensor.span_panel_power"
    return seeded.entity_id


@pytest.mark.parametrize(
    "parts",
    [pytest.param(None, id="default"), pytest.param(PARENT_DEVICE_ENTITY, id="no-area-part")],
)
async def test_an_id_that_stops_at_the_measurement_and_is_composed_today_is_offered_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, parts: tuple[EntityNamePart, ...] | None
) -> None:
    """`sensor.span_panel_power` on a circuit named "Power" keeps its id, on reload and in the listener.

    An install may have it: accepted when a circuit renamed "Power" was
    offered the id that stopped at its name, or carried from 2.2.x. Core
    composes it today from the panel and the device with the suffix left out,
    so its base is empty and nothing is offered. An area set and cleared again,
    on the entity or on its device, brings that back.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    entity_id = _seed_the_id_that_stops_at_power(hass, entry)
    install = _SensorInstall(hass, entry)
    await install.load("Power")
    row = await _assert_recreate_offers_nothing(hass, install, "Power", entity_id)
    assert row.object_id_base == ""
    registry = er.async_get(hass)
    garage = ar.async_get(hass).async_create("Garage")
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, entity_id, garage.id)
    _give_the_entity_an_area(hass, entity_id, None)
    after_the_entity = _offer_as_stored(hass, entity_id)
    _put_the_device_in(hass, entity_id, "Kitchen")
    _put_the_device_in(hass, entity_id, None)
    after_the_device = _offer_as_stored(hass, entity_id)
    stop()

    for row in (after_the_entity, after_the_device):
        assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == ("", entity_id)
    row = await _assert_recreate_offers_nothing(hass, install, "Power", entity_id)
    assert row.object_id_base == ""


@pytest.mark.parametrize(
    "parts",
    [pytest.param(None, id="default"), pytest.param(PARENT_DEVICE_ENTITY, id="no-area-part")],
)
async def test_a_panel_renamed_reaches_an_id_that_stops_at_the_measurement_without_a_reload(
    hass: HomeAssistant, entry: MockConfigEntry, parts: tuple[EntityNamePart, ...] | None
) -> None:
    """Under the panel's new name, `sensor.span_panel_power` is no composition, and keeps `power`.

    The offer is the id a new entity on the device gets under that name, and
    the listener stores the base as the rename happens. Left as it was, the
    base offered `sensor.main_panel_power` until the next restart, and
    `sensor.main_panel_power_power` after it.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    entity_id = _seed_the_id_that_stops_at_power(hass, entry)
    install = _SensorInstall(hass, entry)
    await install.load("Power")
    await _assert_recreate_offers_nothing(hass, install, "Power", entity_id)
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    dr.async_get(hass).async_update_device(
        entry.runtime_data.panel_device_id, name_by_user="Main Panel"
    )

    row = _offer_as_stored(hass, entity_id)
    stop()
    assert registry.async_regenerate_entity_id(row) == "sensor.main_panel_power_power"
    await install.load("Power")
    assert (
        registry.async_regenerate_entity_id(_offer_as_stored(hass, entity_id))
        == "sensor.main_panel_power_power"
    )


async def test_the_panel_s_area_reaches_a_circuit_named_for_its_measurement_without_a_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A circuit device with no area takes the panel's, so the panel's area decides the base too.

    Under `(AREA, DEVICE, ENTITY)` the "Power" sensor's label-only id in
    Garage, `sensor.garage_power`, is offered `sensor.power_power` once its area
    is cleared. Put the panel in Garage and Core composes that id again from
    the panel's area and the device with the suffix left out, so nothing is
    offered. A base left as it was offered `sensor.garage_power_power` until
    the next restart.
    """
    er.async_get(hass).async_update_settings(
        entity_id_parts=[EntityNamePart.AREA, EntityNamePart.DEVICE, EntityNamePart.ENTITY]
    )
    install = _SensorInstall(hass, entry)
    created = (await install.load("Power")).entity_id
    assert created == "sensor.power_power"
    garage = ar.async_get(hass).async_create("Garage")
    _give_the_entity_an_area(hass, created, garage.id)
    accepted = await _accept_what_recreate_offers(hass, install, "Power", created)
    assert accepted == "sensor.garage_power"
    await install.load("Power")
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _give_the_entity_an_area(hass, accepted, None)
    cleared = registry.async_regenerate_entity_id(_offer_as_stored(hass, accepted))
    dr.async_get(hass).async_update_device(entry.runtime_data.panel_device_id, area_id=garage.id)

    row = _offer_as_stored(hass, accepted)
    stop()
    assert cleared == "sensor.power_power"
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == ("", accepted)
    row = await _assert_recreate_offers_nothing(hass, install, "Power", accepted)
    assert row.object_id_base == ""


async def test_a_device_renamed_for_the_measurement_keeps_it_in_the_label_alone_without_a_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A label-only id read against a device the user renamed "Power" keeps its `power`.

    "Refrigerator" accepted `sensor.garage_power` in Garage and its area was
    cleared. Renamed "Power" in Home Assistant, the device's name ends the id
    as `power` does, and the offer is the id a new entity on that device gets,
    the same through the listener and after a restart.
    """
    install = _SensorInstall(hass, entry)
    created = (await install.load(ORIGINAL_NAME)).entity_id
    garage = ar.async_get(hass).async_create("Garage")
    _give_the_entity_an_area(hass, created, garage.id)
    accepted = await _accept_what_recreate_offers(hass, install, ORIGINAL_NAME, created)
    assert accepted == "sensor.garage_power"
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)
    _give_the_entity_an_area(hass, accepted, None)
    assert (
        registry.async_regenerate_entity_id(_offer_as_stored(hass, accepted)) == ORIGINAL_ENTITY_ID
    )

    _rename_the_circuit_device(hass, accepted, "Power")

    row = _offer_as_stored(hass, accepted)
    stop()
    assert registry.async_regenerate_entity_id(row) == "sensor.span_panel_power_power"
    await install.load(ORIGINAL_NAME)
    assert (
        registry.async_regenerate_entity_id(_offer_as_stored(hass, accepted))
        == "sensor.span_panel_power_power"
    )


@pytest.mark.parametrize(
    ("parts", "in_kitchen", "renamed", "moved_to", "moved"),
    [
        pytest.param(
            None,
            "sensor.kitchen_kitchen_outlets_power",
            "sensor.kitchen_power_power",
            "Utility",
            "sensor.utility_power_power",
            id="default-to-another-area",
        ),
        pytest.param(
            None,
            "sensor.kitchen_kitchen_outlets_power",
            "sensor.kitchen_power_power",
            None,
            "sensor.span_panel_power_power",
            id="default-to-no-area",
        ),
        # With no area part a device's area names nothing, so moving it
        # between areas changes nothing.
        pytest.param(
            PARENT_DEVICE_ENTITY,
            "sensor.kitchen_outlets_power",
            "sensor.power_power",
            "Utility",
            "sensor.power_power",
            id="no-area-part-to-another-area",
        ),
        pytest.param(
            PARENT_DEVICE_ENTITY,
            "sensor.kitchen_outlets_power",
            "sensor.power_power",
            None,
            "sensor.span_panel_power_power",
            id="no-area-part-to-no-area",
        ),
        pytest.param(
            (EntityNamePart.AREA, EntityNamePart.DEVICE, EntityNamePart.ENTITY),
            "sensor.kitchen_kitchen_outlets_power",
            "sensor.kitchen_power_power",
            None,
            "sensor.power_power",
            id="area-device-entity-to-no-area",
        ),
    ],
)
async def test_a_circuit_renamed_to_its_measurement_follows_its_device_s_area_without_a_reload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    in_kitchen: str,
    renamed: str,
    moved_to: str | None,
    moved: str,
) -> None:
    """A circuit whose device is in Kitchen, renamed "Power", keeps `power` wherever the device goes.

    Renamed in the SPAN app, it is offered the id a new entity on that device
    in Kitchen gets. Once that is accepted and the device is moved, the offer
    follows the device's area through the listener, and a restart gives the
    same.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    install = _SensorInstall(hass, entry)
    created = (await install.load("Kitchen Outlets")).entity_id
    _put_the_device_in(hass, created, "Kitchen")
    assert await _accept_what_recreate_offers(hass, install, "Kitchen Outlets", created) == (
        in_kitchen
    )
    assert await _accept_what_recreate_offers(hass, install, "Power", in_kitchen) == renamed
    await _assert_recreate_offers_nothing(hass, install, "Power", renamed)
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _put_the_device_in(hass, renamed, moved_to)

    row = _offer_as_stored(hass, renamed)
    stop()
    assert registry.async_regenerate_entity_id(row) == moved
    await install.load("Power")
    assert registry.async_regenerate_entity_id(_offer_as_stored(hass, renamed)) == moved


# B1 in an install: an id from before the device prefix, on a circuit "Garage",
# renamed "Breaker". It ends on the new name and on the form `breaker` alike, so
# it is read for the form, as every rename is.


async def _load_a_no_prefix_breaker_on_garage(
    hass: HomeAssistant, entry: MockConfigEntry
) -> tuple[_SwitchInstall, str]:
    """Register the switch on `switch.garage_breaker`, load it on "Garage", and hold its first offer."""
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))
    seeded = er.async_get(hass).async_get_or_create(
        "switch",
        DOMAIN,
        build_switch_unique_id(SERIAL, CIRCUIT_ID),
        suggested_object_id="garage_breaker",
        config_entry=entry,
    )
    assert seeded.entity_id == "switch.garage_breaker"
    install = _SwitchInstall(hass, entry)
    await install.load("Garage")
    await install.load("Garage")
    registry = er.async_get(hass)
    # The device half Core composes, which §7 lists for an install with no prefix.
    assert (
        registry.async_regenerate_entity_id(_offer_as_stored(hass, seeded.entity_id))
        == "switch.span_panel_garage_breaker"
    )
    return install, seeded.entity_id


async def test_a_no_prefix_breaker_renamed_breaker_in_the_span_app_keeps_the_measurement(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Renamed "Breaker" on the panel, the switch is offered the id a new switch on that device gets."""
    install, entity_id = await _load_a_no_prefix_breaker_on_garage(hass, entry)

    accepted = await _accept_what_recreate_offers(hass, install, "Breaker", entity_id)

    assert accepted == "switch.span_panel_breaker_breaker"
    row = await _assert_recreate_offers_nothing(hass, install, "Breaker", accepted)
    assert row.object_id_base == "breaker"


async def test_a_no_prefix_breaker_whose_device_is_renamed_breaker_keeps_it_without_a_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Renamed "Breaker" in Home Assistant, the offer is the same through the listener and after a restart."""
    install, entity_id = await _load_a_no_prefix_breaker_on_garage(hass, entry)
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    _rename_the_circuit_device(hass, entity_id, "Breaker")

    row = _offer_as_stored(hass, entity_id)
    stop()
    assert registry.async_regenerate_entity_id(row) == "switch.span_panel_breaker_breaker"
    await install.load("Garage")
    assert (
        registry.async_regenerate_entity_id(_offer_as_stored(hass, entity_id))
        == "switch.span_panel_breaker_breaker"
    )


# --- An area or a floor renamed ---------------------------------------------------
#
# Under a format with an area part, Core composes an id from the area's name,
# and under one with a floor part from that area's floor's name. Where the id a
# circuit entity has is a composition today, its base is the one that composes
# it, so a rename can change the base without reloading anything.

FLOOR_AREA_DEVICE_ENTITY = (
    EntityNamePart.FLOOR,
    EntityNamePart.AREA,
    EntityNamePart.DEVICE,
    EntityNamePart.ENTITY,
)
AREA_DEVICE_ENTITY = (EntityNamePart.AREA, EntityNamePart.DEVICE, EntityNamePart.ENTITY)


def _rename(hass: HomeAssistant, what: str, area_id: str, floor_id: str) -> None:
    """Rename the area, rename its floor, or move it to another floor, as its user would."""
    areas = ar.async_get(hass)
    if what == "area":
        areas.async_update(area_id, name="Workshop")
    elif what == "floor":
        fr.async_get(hass).async_update(floor_id, name="Main")
    else:
        upper = fr.async_get(hass).async_create("Upper")
        areas.async_update(area_id, floor_id=upper.floor_id)


@pytest.mark.parametrize(
    ("parts", "what", "accepted", "offered"),
    [
        pytest.param(
            None, "area", "sensor.garage_power", "sensor.workshop_power_power", id="default"
        ),
        pytest.param(
            AREA_DEVICE_ENTITY,
            "area",
            "sensor.garage_power",
            "sensor.workshop_power_power",
            id="area-device-entity",
        ),
        pytest.param(
            FLOOR_AREA_DEVICE_ENTITY,
            "area",
            "sensor.ground_garage_power",
            "sensor.ground_workshop_power_power",
            id="floor-format-area-renamed",
        ),
        pytest.param(
            FLOOR_AREA_DEVICE_ENTITY,
            "floor",
            "sensor.ground_garage_power",
            "sensor.main_garage_power_power",
            id="floor-renamed",
        ),
        pytest.param(
            FLOOR_AREA_DEVICE_ENTITY,
            "area-floor",
            "sensor.ground_garage_power",
            "sensor.upper_garage_power_power",
            id="area-moved-to-another-floor",
        ),
    ],
)
async def test_an_area_renamed_reaches_an_id_composed_from_it_without_a_reload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    what: str,
    accepted: str,
    offered: str,
) -> None:
    """A circuit named "Power" composed with the suffix left out keeps `power` once the area's name moves.

    Its label-only id in Garage, accepted, is offered nothing once its device
    is put in Garage too, since Core composes that id from the area and the
    device with the suffix left out, and the base is empty. Renamed, the area
    composes another id, so the base is the form the id ends on, and the offer
    the id a new entity on that device gets. Left as it was, the empty base
    offered `sensor.workshop_power` until the next restart.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    ground = fr.async_get(hass).async_create("Ground")
    garage = ar.async_get(hass).async_create("Garage", floor_id=ground.floor_id)
    install = _SensorInstall(hass, entry)
    created = (await install.load("Power")).entity_id
    _give_the_entity_an_area(hass, created, garage.id)
    assert await _accept_what_recreate_offers(hass, install, "Power", created) == accepted
    await install.load("Power")
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)
    _take_the_area_off(hass, accepted, "device-then-entity", "Garage")
    row = _offer_as_stored(hass, accepted)
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == ("", accepted)

    _rename(hass, what, garage.id, ground.floor_id)

    row = _offer_as_stored(hass, accepted)
    stop()
    assert registry.async_regenerate_entity_id(row) == offered
    await install.load("Power")
    assert registry.async_regenerate_entity_id(_offer_as_stored(hass, accepted)) == offered


@pytest.mark.parametrize(
    ("parts", "edit", "refreshes"),
    [
        pytest.param(None, "area-name", True, id="default-area-name"),
        pytest.param(None, "area-icon", False, id="default-area-icon"),
        pytest.param(None, "area-floor", False, id="default-area-floor"),
        pytest.param(None, "floor-name", False, id="default-floor-name"),
        pytest.param(PARENT_DEVICE_ENTITY, "area-name", False, id="no-area-part-area-name"),
        pytest.param(FLOOR_AREA_DEVICE_ENTITY, "area-floor", True, id="floor-format-area-floor"),
        pytest.param(FLOOR_AREA_DEVICE_ENTITY, "floor-name", True, id="floor-format-floor-name"),
        pytest.param(FLOOR_AREA_DEVICE_ENTITY, "floor-icon", False, id="floor-format-floor-icon"),
    ],
)
async def test_an_area_edit_refreshes_the_bases_only_where_it_changes_what_a_composition_reads(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    edit: str,
    refreshes: bool,
) -> None:
    """Core announces every edit to an area, so the listener works the bases out only for a name it reads.

    A composition reads an area's name under an area part, and its floor's
    name under a floor part. An icon, or a name the user's format leaves out,
    changes no id.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    floors = fr.async_get(hass)
    ground = floors.async_create("Ground")
    areas = ar.async_get(hass)
    garage = areas.async_create("Garage", floor_id=ground.floor_id)
    install = _SensorInstall(hass, entry)
    await install.load("Power")
    await install.load("Power")
    stop = async_keep_circuit_id_bases_current(hass, entry)
    upper = floors.async_create("Upper")

    with patch.object(
        circuit_object_ids,
        "async_refresh_circuit_id_bases",
        wraps=circuit_object_ids.async_refresh_circuit_id_bases,
    ) as refresh:
        if edit == "area-name":
            areas.async_update(garage.id, name="Workshop")
        elif edit == "area-icon":
            areas.async_update(garage.id, icon="mdi:garage")
        elif edit == "area-floor":
            areas.async_update(garage.id, floor_id=upper.floor_id)
        elif edit == "floor-name":
            floors.async_update(ground.floor_id, name="Main")
        else:
            floors.async_update(ground.floor_id, icon="mdi:home-floor-0")
    stop()

    assert refresh.called is refreshes


# --- An entity id edited by hand ---------------------------------------------------
#
# The base reproduces the id the entity has, so an id the user edits by hand is
# read again as the edit is stored. Left with the base worked out from the old
# id, Recreate offered the old id back until the next restart, reverting an edit
# the user made, and nothing after it.


@pytest.mark.parametrize(
    ("parts", "created", "edited", "base", "offered"),
    [
        # A wording of the label: Core composes the edit from `current power`.
        pytest.param(
            None,
            ORIGINAL_ENTITY_ID,
            "sensor.span_panel_refrigerator_current_power",
            "current power",
            "sensor.span_panel_refrigerator_current_power",
            id="default-a-wording-of-the-label",
        ),
        # An id that stops at the device's name: Core composes it from nothing.
        pytest.param(
            None,
            ORIGINAL_ENTITY_ID,
            "sensor.span_panel_refrigerator",
            "",
            "sensor.span_panel_refrigerator",
            id="default-stopping-at-the-name",
        ),
        pytest.param(
            DEVICE_ENTITY,
            "sensor.refrigerator_power",
            "sensor.refrigerator_current_power",
            "current power",
            "sensor.refrigerator_current_power",
            id="device-entity-a-wording-of-the-label",
        ),
        pytest.param(
            DEVICE_ENTITY,
            "sensor.refrigerator_power",
            "sensor.refrigerator",
            "",
            "sensor.refrigerator",
            id="device-entity-stopping-at-the-name",
        ),
        # No composition spells the edit and no reading reads a form in it, so
        # it gets the wording a new entity does and is offered the composed id,
        # as it is after a restart.
        pytest.param(
            None,
            ORIGINAL_ENTITY_ID,
            "sensor.fridge_watts",
            "power",
            ORIGINAL_ENTITY_ID,
            id="default-an-id-nothing-composes",
        ),
    ],
)
async def test_an_entity_id_edited_by_hand_is_offered_what_a_restart_offers(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    created: str,
    edited: str,
    base: str,
    offered: str,
) -> None:
    """An id Core composes today is offered nothing, the moment the user stores it (§8)."""
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    install = _SensorInstall(hass, entry)
    assert (await install.load(ORIGINAL_NAME)).entity_id == created
    await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, created)
    registry = er.async_get(hass)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    registry.async_update_entity(created, new_entity_id=edited)
    # Core changes the live entity's id in place and writes no base, so the
    # listener stores the one the edited id calls for.
    await hass.async_block_till_done()

    row = _offer_as_stored(hass, edited)
    stop()
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == (base, offered)
    await install.load(ORIGINAL_NAME)
    row = _offer_as_stored(hass, edited)
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == (base, offered)


@pytest.mark.parametrize("taken", [False, True], ids=["as-composed", "numbered"])
@pytest.mark.parametrize(
    ("parts", "composed"),
    [
        pytest.param(None, "sensor.span_panel_beer_fridge_power", id="default"),
        pytest.param(DEVICE_ENTITY, "sensor.beer_fridge_power", id="device-entity"),
    ],
)
async def test_an_accepted_offer_read_as_it_is_stored_offers_nothing_more(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    composed: str,
    taken: bool,
) -> None:
    """Accepting Recreate's offer is an id edit too, and reading it again gives the base it came from.

    The device renamed "Beer Fridge" in Home Assistant is offered the id Core
    composes for it, or that id under Core's number where another entity has
    it, and once accepted the listener stores the same base, so nothing more
    is offered, before a restart or after it.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    install = _SensorInstall(hass, entry)
    created = (await install.load(ORIGINAL_NAME)).entity_id
    await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, created)
    registry = er.async_get(hass)
    if taken:
        domain, object_id = composed.split(".", 1)
        registry.async_get_or_create(domain, "other", "taken", suggested_object_id=object_id)
    stop = async_keep_circuit_id_bases_current(hass, entry)
    _rename_the_circuit_device(hass, created, RENAMED)
    offered = registry.async_regenerate_entity_id(_offer_as_stored(hass, created))
    assert offered == (f"{composed}_2" if taken else composed)

    registry.async_update_entity(created, new_entity_id=offered)
    await hass.async_block_till_done()

    row = _offer_as_stored(hass, offered)
    stop()
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == ("power", offered)
    row = await _assert_recreate_offers_nothing(hass, install, ORIGINAL_NAME, offered)
    assert row.object_id_base == "power"


@pytest.mark.parametrize(
    ("parts", "created", "edited", "base"),
    [
        pytest.param(
            None,
            ORIGINAL_ENTITY_ID,
            "sensor.span_panel_refrigerator_current_power",
            "current power",
            id="default-a-wording-of-the-label",
        ),
        pytest.param(
            None,
            ORIGINAL_ENTITY_ID,
            "sensor.span_panel_refrigerator",
            "",
            id="default-stopping-at-the-name",
        ),
        pytest.param(
            DEVICE_ENTITY,
            "sensor.refrigerator_power",
            "sensor.refrigerator_current_power",
            "current power",
            id="device-entity-a-wording-of-the-label",
        ),
        pytest.param(
            DEVICE_ENTITY,
            "sensor.refrigerator_power",
            "sensor.refrigerator",
            "",
            id="device-entity-stopping-at-the-name",
        ),
    ],
)
async def test_a_hand_edit_is_read_when_the_listener_hears_it_before_the_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    parts: tuple[EntityNamePart, ...] | None,
    created: str,
    edited: str,
    base: str,
) -> None:
    """The base ends on the edited id's when the listener is handed the edit before the entity is.

    Core changes a live entity's id in place and writes no base, so the listener
    alone stores it, as it does for a disabled entity. Here the listener
    subscribes before the entity is added, so the bus gives it the edit first:
    it stores the base while the entity is still under its old id, and Core
    moves the entity after it, without removing it.
    `test_an_entity_id_edited_by_hand_is_offered_what_a_restart_offers` has the
    other order. When Core removed the entity and added it again instead, that
    add wrote the base the entity handed it after the listener's, and a base
    kept from when the entity was built offered the old id back.
    """
    if parts is not None:
        er.async_get(hass).async_update_settings(entity_id_parts=list(parts))
    stop = async_keep_circuit_id_bases_current(hass, entry)
    install = _SensorInstall(hass, entry)
    entity = await install.load(ORIGINAL_NAME)
    assert entity.entity_id == created
    registry = er.async_get(hass)

    with watch_add_and_remove(entity) as calls:
        registry.async_update_entity(created, new_entity_id=edited)
        await hass.async_block_till_done()

    assert calls.count == (0, 0)
    assert entity.entity_id == edited
    row = _offer_as_stored(hass, edited)
    stop()
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == (base, edited)
    await install.load(ORIGINAL_NAME)
    row = _offer_as_stored(hass, edited)
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == (base, edited)


async def test_a_disabled_entity_edited_by_hand_is_offered_what_a_restart_offers(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A disabled entity is not live, so nothing of Core's handles its id edit, and the listener keeps its base.

    Its row is registered as the entry sets up, with the base worked out then,
    but the entity is not added, so there is no entity for Core to move when
    its id is edited. Left to that, the base offered the old id back until the
    next restart.
    """
    install = _SensorInstall(hass, entry)
    created = (await install.load(ORIGINAL_NAME)).entity_id
    registry = er.async_get(hass)
    registry.async_update_entity(created, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    await install.load(ORIGINAL_NAME, added=False)
    stop = async_keep_circuit_id_bases_current(hass, entry)

    registry.async_update_entity(created, new_entity_id="sensor.span_panel_refrigerator")
    await hass.async_block_till_done()

    row = _offer_as_stored(hass, "sensor.span_panel_refrigerator")
    stop()
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == (
        "",
        "sensor.span_panel_refrigerator",
    )
    await install.load(ORIGINAL_NAME, added=False)
    row = _offer_as_stored(hass, "sensor.span_panel_refrigerator")
    assert (row.object_id_base, registry.async_regenerate_entity_id(row)) == (
        "",
        "sensor.span_panel_refrigerator",
    )
