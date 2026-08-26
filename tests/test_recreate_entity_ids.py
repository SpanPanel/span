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

from typing import ClassVar
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import EntityNamePart
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import (
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
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
    SpanUnmappedCircuitSensor,
)
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS, UNMAPPED_SENSORS
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

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
    coordinator.config_entry = entry
    coordinator.request_reload = MagicMock()
    coordinator.register_circuit_energy_sensor = MagicMock()
    coordinator.get_circuit_dip_offset = MagicMock(return_value=0.0)
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

    async def load(self, circuit_name: str) -> E:
        """Tear down any previous platform, then set one up from fresh panel data."""
        if self._platform is not None:
            await self._platform.async_reset()

        snapshot = _snapshot(circuit_name)
        coordinator = _coordinator(self._hass, snapshot, self._entry)
        self._coordinator = coordinator
        self._entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator, panel_device_id="panel-device-id"
        )

        self._platform = MockEntityPlatform(
            self._hass, domain=self._domain, platform_name=DOMAIN
        )
        self._platform.config_entry = self._entry

        entity = self._build(coordinator, snapshot)
        await self._platform.async_add_entities([entity])
        await self._hass.async_block_till_done()

        assert entity.hass is not None, "entity was rejected before it reached the registry"
        self._entity = entity
        return entity


class _SensorInstall(_Install[SpanCircuitPowerSensor]):
    """One install of the circuit power sensor.

    The only one of the four that can be shown on a sub-device's card, so it is
    the only one that takes a `device_info_override`.
    """

    _domain: ClassVar[str] = "sensor"

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

    def _build(
        self, coordinator: MagicMock, snapshot: SpanPanelSnapshot
    ) -> SpanPanelCircuitsSwitch:
        """Build the breaker switch."""
        return SpanPanelCircuitsSwitch(coordinator, CIRCUIT_ID, "SPAN Panel")


class _SelectInstall(_Install[SpanPanelCircuitsSelect]):
    """One install of the circuit-priority select."""

    _domain: ClassVar[str] = "select"

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
def device_and_entity_parts(hass: HomeAssistant) -> None:
    """Compose ids from the device and the entity, as a user may globally choose to.

    `entity_id_parts` is a registry-wide setting whose default also includes the
    area. Cases about the suffix half of an id pin it here so the assertion is
    about the suffix and not about whether an area happens to exist.
    """
    er.async_get(hass).async_update_settings(
        entity_id_parts=[EntityNamePart.DEVICE, EntityNamePart.ENTITY]
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
    device_and_entity_parts: None,
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
    """The stored base has to track the panel, not the install date.

    This is what `async_regenerate_entity_id` composes from when there is no
    user `name` override, which in friendly-names mode is always. It is the
    *base*, not a whole object id: Core prefixes the device and, where the user
    has assigned one, the area.
    """
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(ORIGINAL_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.object_id_base == "Beer Fridge power"
    assert registry_entry.suggested_object_id is None


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


async def test_circuit_numbers_mode_is_offered_its_own_tab_based_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Recreate must not offer to convert a circuit-numbered panel to friendly names.

    The mode exists so an id follows the breaker position rather than the name.
    Accepting a friendly-name proposal would undo that for every circuit at once.

    It used to be offered because phase 2 sync wrote the panel's name into the
    registry's `name`, which Home Assistant reads ahead of `suggested_object_id`.
    The name now travels as `original_name`, which ranks below it.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    assert sensor.entity_id == CIRCUIT_NUMBERS_ENTITY_ID

    registry = er.async_get(hass)
    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None

    assert registry.async_regenerate_entity_id(registry_entry) == CIRCUIT_NUMBERS_ENTITY_ID


async def test_circuit_numbers_mode_still_shows_the_panels_name(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Phase 2 sync still follows the panel -- through a field that cannot move an id."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None

    assert registry_entry.original_name == f"{RENAMED} Power"
    assert sensor.name == f"{RENAMED} Power"
    assert registry_entry.name is None


async def test_circuit_numbers_mode_releases_a_name_an_older_release_wrote(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An install upgrading has the old scheme's name handed back to it."""
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
    assert registry.async_regenerate_entity_id(registry_entry) == CIRCUIT_NUMBERS_ENTITY_ID


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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
) -> None:
    """A new id uses the wording the panel level and the labels use.

    Nothing is seeded, so there is no spelling to read back and the default
    applies. It is the pre-preset wording, which is also what
    `main_meter_consumed_energy` has always said.
    """
    sensor = await _LegacyInstall(hass, entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == "sensor.span_panel_refrigerator_consumed_energy"


async def test_a_preset_era_energy_sensor_is_offered_its_own_id(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
) -> None:
    """2.0.8 wrote "<circuit> Consumed Energy" into the registry, and it must be released."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    install = _LegacyInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    registry = er.async_get(hass)
    registry.async_update_entity(sensor.entity_id, name=f"{ORIGINAL_NAME} Consumed Energy")

    await install.load(ORIGINAL_NAME)

    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry_entry.name is None
    assert registry.async_regenerate_entity_id(registry_entry) == sensor.entity_id


async def test_a_name_written_under_the_beta_label_is_released_as_well(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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


async def test_an_area_the_user_assigned_reaches_the_proposal_under_default_parts(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """R2: the area is the user's global choice, and the integration does not exempt itself.

    A preset id could not carry one. Handing Core a base instead means the area
    part of `entity_id_parts` applies here exactly as it does to every other
    integration -- and only where the user has actually assigned an area.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    install = _SensorInstall(hass, entry)
    sensor = await install.load(ORIGINAL_NAME)
    assert sensor.entity_id == CIRCUIT_NUMBERS_ENTITY_ID  # no area yet: identical to today

    area = ar.async_get(hass).async_get_or_create("Basement")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, SERIAL)})
    assert device is not None
    device_registry.async_update_device(device.id, area_id=area.id)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.entity_id == CIRCUIT_NUMBERS_ENTITY_ID  # R5: nothing moved
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "sensor.basement_span_panel_circuit_15_power"
    )


async def test_an_unnamed_circuit_in_friendly_mode_still_gets_a_distinct_id(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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


async def test_an_unmapped_tab_sensor_keeps_its_prefix_on_a_no_prefix_install(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The hidden unmapped-tab sensors were always composed, and are unaffected.

    Home Assistant has always built their ids from the display name, so they
    carry a device prefix that the naming flag never reached. Turning the flag
    off does not take it away.
    """
    hass.config_entries.async_update_entry(entry, options=dict(LEGACY_NAMES))

    registry = er.async_get(hass)
    seeded = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(SERIAL, "unmapped_tab_32", "instantPowerW"),
        suggested_object_id="span_panel_unmapped_tab_32_power",
        config_entry=entry,
    )
    assert seeded.entity_id == "sensor.span_panel_unmapped_tab_32_power"

    circuit = SpanCircuitSnapshotFactory.create(circuit_id="unmapped_tab_32", name="", tabs=[32])
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number=SERIAL, circuits={"unmapped_tab_32": circuit}
    )
    coordinator = _coordinator(hass, snapshot, entry)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )

    platform = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
    platform.config_entry = entry
    sensor = SpanUnmappedCircuitSensor(coordinator, UNMAPPED_SENSORS[0], snapshot, "unmapped_tab_32")
    await platform.async_add_entities([sensor])
    await hass.async_block_till_done()

    assert sensor.entity_id == seeded.entity_id

    registry_entry = registry.async_get(seeded.entity_id)
    assert registry_entry is not None
    assert registry.async_regenerate_entity_id(registry_entry) == seeded.entity_id


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
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, device_and_entity_parts: None
) -> None:
    """A new entity is spelled with its own device, as an existing one is offered."""
    sensor = await _SensorInstall(hass, second_panel_entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == "sensor.span_panel_2_refrigerator_power"


async def test_a_panel_device_the_user_renamed_is_offered_the_renamed_id(
    hass: HomeAssistant, second_panel_entry: MockConfigEntry, device_and_entity_parts: None
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
    device = device_registry.async_get_device(identifiers={(DOMAIN, SERIAL)})
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


async def test_a_new_switch_and_select_compose_todays_ids(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
) -> None:
    """Composition spells both exactly as the preset builder used to."""
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    switch = await _SwitchInstall(hass, entry).load(ORIGINAL_NAME)
    select = await _SelectInstall(hass, entry).load(ORIGINAL_NAME)

    assert switch.entity_id == SWITCH_ENTITY_ID
    assert select.entity_id == SELECT_ENTITY_ID


async def test_switch_and_select_are_offered_their_own_ids_after_a_rename_in_circuit_numbers_mode(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
) -> None:
    """#252's other half: the name follows the panel and the id does not move."""
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
    assert switch_entry.original_name == f"{RENAMED} Breaker"
    assert registry.async_regenerate_entity_id(switch_entry) == switch.entity_id

    assert select.entity_id == SELECT_ENTITY_ID
    select_entry = registry.async_get(select.entity_id)
    assert select_entry is not None
    assert select_entry.original_name == f"{RENAMED} Circuit Priority"
    assert registry.async_regenerate_entity_id(select_entry) == select.entity_id


async def test_switch_and_select_release_the_name_an_older_release_wrote(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
) -> None:
    """2.0.8 wrote the panel's name into the registry's `name` for these two as well."""
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

    switch_entry = registry.async_get(switch.entity_id)
    assert switch_entry is not None
    assert switch_entry.name is None
    assert registry.async_regenerate_entity_id(switch_entry) == SWITCH_ENTITY_ID

    select_entry = registry.async_get(select.entity_id)
    assert select_entry is not None
    assert select_entry.name is None
    assert registry.async_regenerate_entity_id(select_entry) == SELECT_ENTITY_ID


async def test_a_name_the_user_set_on_a_control_is_never_released(
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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
    hass: HomeAssistant, entry: MockConfigEntry, device_and_entity_parts: None
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

    A preset id could not carry an area. Handing Core a base means the switch is
    composed exactly like every other integration's entity -- and only where the
    user has actually assigned one.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))
    switch = await _SwitchInstall(hass, entry).load(ORIGINAL_NAME)
    assert switch.entity_id == SWITCH_ENTITY_ID  # no area yet: identical to today

    area = ar.async_get(hass).async_get_or_create("Basement")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, SERIAL)})
    assert device is not None
    device_registry.async_update_device(device.id, area_id=area.id)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(SWITCH_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.entity_id == SWITCH_ENTITY_ID  # R5: nothing moved
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "switch.basement_span_panel_circuit_15_breaker"
    )


# --- Phase 2: a rename in the SPAN app still reaches the display name ---------
#
# The README promises it in both modes (:594, :602): friendly names
# "automatically updates when you rename circuits in the SPAN panel", and
# circuit-numbers mode keeps ids stable while "friendly names still sync from
# SPAN panel for display". One mechanism serves both. A coordinator push
# carrying a changed circuit name asks the entry to reload, and the reload is
# what rebuilds `_attr_name` -- which Home Assistant stores as `original_name`.
# The shortcut the old scheme took, writing the registry's `name` in place, is
# exactly what made Recreate propose a friendly-name id for a circuit-numbered
# entity, so the mechanism has to keep costing a reload.
#
# The per-platform unit tests drive this against a `MagicMock` registry, which
# cannot see where the name landed. These drive it through a real
# `EntityPlatform` and the real registry, for all three circuit platforms in
# both modes, because handing composition the id moved where the name is
# written.

type _CircuitInstall = type[_SensorInstall] | type[_SwitchInstall] | type[_SelectInstall]

CIRCUIT_INSTALLS: list[_CircuitInstall] = [_SensorInstall, _SwitchInstall, _SelectInstall]
PLATFORM_IDS = ["sensor", "switch", "select"]


@pytest.mark.parametrize(
    "mode", [FRIENDLY_NAMES, CIRCUIT_NUMBERS], ids=["friendly", "circuit_numbers"]
)
@pytest.mark.parametrize("make_install", CIRCUIT_INSTALLS, ids=PLATFORM_IDS)
async def test_a_rename_in_the_span_app_reaches_the_display_name_after_the_reload_it_requests(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    device_and_entity_parts: None,
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
    assert before.original_name is not None
    assert before.original_name.startswith(ORIGINAL_NAME)

    await install.rename_on_the_panel(ORIGINAL_NAME)
    install.coordinator.request_reload.assert_not_called()

    await install.rename_on_the_panel(RENAMED)
    install.coordinator.request_reload.assert_called_once()

    reloaded = await install.load(RENAMED)

    after = registry.async_get(original_entity_id)
    assert after is not None
    assert after.original_name is not None
    assert after.original_name.startswith(RENAMED)
    assert reloaded.entity_id == original_entity_id  # R5: the id did not move
    assert after.name is None  # R4: the registry's `name` was never written


@pytest.mark.parametrize("make_install", CIRCUIT_INSTALLS, ids=PLATFORM_IDS)
async def test_a_name_the_user_set_is_not_overridden_by_a_span_app_rename(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    device_and_entity_parts: None,
    make_install: _CircuitInstall,
) -> None:
    """A user who named an entity has said what it is called, and the panel has not.

    The reload is skipped as well as the name: reloading could not change what
    is displayed while the registry holds a `name`, so asking for one would be a
    whole-entry teardown that no user could see the point of.
    """
    install = make_install(hass, entry)
    await install.load(ORIGINAL_NAME)
    entity = await install.load(ORIGINAL_NAME)

    registry = er.async_get(hass)
    registry.async_update_entity(entity.entity_id, name="Beverage Cooling")

    await install.rename_on_the_panel(RENAMED)
    install.coordinator.request_reload.assert_not_called()

    await install.load(RENAMED)

    after = registry.async_get(entity.entity_id)
    assert after is not None
    assert after.name == "Beverage Cooling"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(FRIENDLY_NAMES, RENAMED_ENTITY_ID), (CIRCUIT_NUMBERS, CIRCUIT_NUMBERS_ENTITY_ID)],
    ids=["friendly", "circuit_numbers"],
)
async def test_what_recreate_offers_after_a_pushed_rename_depends_on_the_mode(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    device_and_entity_parts: None,
    mode: dict[str, bool],
    expected: str,
) -> None:
    """The two modes part company at the offer, and nowhere earlier.

    Both followed the same push and the same reload, and both display the new
    name. What differs is what Recreate proposes: friendly names offers the
    renamed id, which is issue #252; circuit numbers offers the entity its own
    id, because an id that follows the breaker position is the point of the
    mode and a whole-panel rename would undo it.
    """
    hass.config_entries.async_update_entry(entry, options=dict(mode))
    install = _SensorInstall(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(ORIGINAL_NAME)

    await install.rename_on_the_panel(RENAMED)
    sensor = await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(sensor.entity_id)
    assert registry_entry is not None
    assert registry_entry.original_name is not None
    assert registry_entry.original_name.startswith(RENAMED)
    assert registry.async_regenerate_entity_id(registry_entry) == expected
