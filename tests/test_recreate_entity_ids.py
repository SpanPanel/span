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

from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_registry import EntityNamePart
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import (
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.id_builder import (
    build_circuit_unique_id,
    preserve_legacy_entity_id_suffix,
)
from custom_components.span_panel.sensor_circuit import (
    SpanCircuitEnergySensor,
    SpanCircuitPowerSensor,
)
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS
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


def _snapshot(circuit_name: str):
    """Build a one-circuit panel snapshot with the circuit named as given."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=CIRCUIT_ID, name=circuit_name, tabs=[15]
    )
    return SpanPanelSnapshotFactory.create(serial_number=SERIAL, circuits={CIRCUIT_ID: circuit})


def _coordinator(hass: HomeAssistant, snapshot, entry: MockConfigEntry) -> MagicMock:
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


class _Install:
    """One install of the sensor platform, reloadable.

    `load` a second time is what a reload is: the entry's entities are torn down
    and rebuilt from the current snapshot, against the entity registry that
    survived. Nothing here is faked -- `async_add_entities` is the real
    `EntityPlatform` path, so a preset entity_id travels the same route into
    `async_get_or_create` that it does in a running install, and the teardown is
    the same `async_reset` an entry unload performs.
    """

    def __init__(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._platform: MockEntityPlatform | None = None

    async def load(self, circuit_name: str) -> SpanCircuitPowerSensor:
        """Tear down any previous platform, then set one up from fresh panel data."""
        if self._platform is not None:
            await self._platform.async_reset()

        snapshot = _snapshot(circuit_name)
        coordinator = _coordinator(self._hass, snapshot, self._entry)
        self._entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator, panel_device_id="panel-device-id"
        )

        self._platform = MockEntityPlatform(self._hass, domain="sensor", platform_name=DOMAIN)
        self._platform.config_entry = self._entry

        sensor = SpanCircuitPowerSensor(coordinator, POWER_DESCRIPTION, snapshot, CIRCUIT_ID)
        await self._platform.async_add_entities([sensor])
        await self._hass.async_block_till_done()

        assert sensor.hass is not None, "entity was rejected before it reached the registry"
        return sensor


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry in friendly-names mode."""
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
    sensor = await _Install(hass, entry).load(ORIGINAL_NAME)

    assert sensor.entity_id == ORIGINAL_ENTITY_ID


async def test_renaming_a_circuit_does_not_move_an_existing_entity_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The non-negotiable one: a rename must never move a live entity_id.

    Dashboards, automations, and recorder history all key off the entity_id.
    Recreate is an offer the user accepts; a rename is not.
    """
    install = _Install(hass, entry)
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
    install = _Install(hass, entry)
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
    install = _Install(hass, entry)
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
    install = _Install(hass, entry)
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
    install = _Install(hass, entry)
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

    install = _Install(hass, entry)
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

    install = _Install(hass, entry)
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

    install = _Install(hass, entry)
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

    install = _Install(hass, entry)
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

    install = _Install(hass, entry)
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

    install = _Install(hass, entry)
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


class _LegacyInstall(_Install):
    """An install whose energy sensor id was composed from the descriptor name."""

    def _seed(self) -> str:
        """Register the entity the way a pre-preset install left it."""
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

    async def load(self, circuit_name: str) -> SpanCircuitEnergySensor:  # type: ignore[override]
        """Set the platform up for the energy sensor, tearing down any previous one."""
        if self._platform is not None:
            await self._platform.async_reset()

        snapshot = _snapshot(circuit_name)
        coordinator = _coordinator(self._hass, snapshot, self._entry)
        self._entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator, panel_device_id="panel-device-id"
        )

        self._platform = MockEntityPlatform(self._hass, domain="sensor", platform_name=DOMAIN)
        self._platform.config_entry = self._entry

        sensor = SpanCircuitEnergySensor(
            coordinator, ENERGY_DESCRIPTION, snapshot, CIRCUIT_ID
        )
        await self._platform.async_add_entities([sensor])
        await self._hass.async_block_till_done()

        assert sensor.hass is not None, "entity was rejected before it reached the registry"
        return sensor


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
    install = _Install(hass, entry)
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
    sensor = await _Install(hass, entry).load("")

    assert sensor.entity_id == "sensor.span_panel_circuit_15_power"


def test_a_suffix_with_no_older_spelling_is_left_alone() -> None:
    """A name that merely looks like a suffix change is a rename, not a legacy form.

    Renaming a circuit "Kitchen Outlets" to "Kitchen" leaves an existing id whose
    trailing segments differ from the computed suffix. That is exactly the case
    #252 exists to offer, so it must not be mistaken for an older spelling.
    """
    assert (
        preserve_legacy_entity_id_suffix(
            "sensor.span_panel_kitchen_power",
            "sensor.span_panel_kitchen_outlets_power",
            "power",
        )
        == "sensor.span_panel_kitchen_power"
    )
    assert (
        preserve_legacy_entity_id_suffix(
            "sensor.span_panel_kitchen_energy_consumed",
            "sensor.span_panel_kitchen_consumed_energy",
            "energy_consumed",
        )
        == "sensor.span_panel_kitchen_consumed_energy"
    )
