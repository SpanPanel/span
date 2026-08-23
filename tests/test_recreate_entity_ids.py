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
from homeassistant.helpers import entity_registry as er
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
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
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
    """The stored suggestion has to track the panel, not the install date.

    This is the field `async_regenerate_entity_id` reads when there is no user
    `name` override, which in friendly-names mode is always.
    """
    install = _Install(hass, entry)
    await install.load(ORIGINAL_NAME)
    await install.load(RENAMED)

    registry = er.async_get(hass)
    registry_entry = registry.async_get(ORIGINAL_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.suggested_object_id == "span_panel_beer_fridge_power"


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


async def test_circuit_numbers_mode_keeps_its_id_its_display_name_and_its_sync(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Regression guard: nothing in circuit-numbers mode may change.

    There the registry `name` written by phase 2 name sync is both what puts the
    panel's name in the UI and what outranks `suggested_object_id` during
    regeneration. That second effect means Recreate in this mode proposes a
    friendly-name ID for a circuit-numbered entity -- a known limitation, and
    the assertion below pins it deliberately: it is what the mode did before
    this fix, and this fix must not disturb it.

    The two are the same write, so correcting Recreate here would mean dropping
    or rerouting phase 2 sync. That is a product decision, recorded in the design
    doc, not something to change while fixing friendly-names mode.
    """
    hass.config_entries.async_update_entry(entry, options=dict(CIRCUIT_NUMBERS))

    install = _Install(hass, entry)
    await install.load(ORIGINAL_NAME)
    sensor = await install.load(RENAMED)

    assert sensor.entity_id == CIRCUIT_NUMBERS_ENTITY_ID

    registry = er.async_get(hass)
    registry_entry = registry.async_get(CIRCUIT_NUMBERS_ENTITY_ID)
    assert registry_entry is not None

    # Phase 2 sync still writes the panel's name as the display name.
    assert registry_entry.name == f"{RENAMED} Power"

    # And that name still outranks the suggestion, so the offer is composed
    # from it -- unchanged, limitation included.
    assert registry.async_regenerate_entity_id(registry_entry) == RENAMED_ENTITY_ID


async def test_the_breaker_switch_gets_the_same_refreshed_suggestion(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Switches and selects preset their IDs through the same helper.

    They call it from their own constructors rather than through
    `_construct_entity_id`, so a fix that only reached the sensor path would
    leave a renamed circuit's breaker switch still offering its old ID.
    """
    platform = MockEntityPlatform(hass, domain="switch", platform_name=DOMAIN)
    platform.config_entry = entry

    for circuit_name in (ORIGINAL_NAME, RENAMED):
        snapshot = _snapshot(circuit_name)
        coordinator = _coordinator(hass, snapshot, entry)
        switch = SpanPanelCircuitsSwitch(coordinator, CIRCUIT_ID, circuit_name, "SPAN Panel")
        await platform.async_add_entities([switch])
        await hass.async_block_till_done()
        await platform.async_reset()

    registry = er.async_get(hass)
    registry_entry = registry.async_get("switch.span_panel_refrigerator_breaker")
    assert registry_entry is not None
    assert registry_entry.suggested_object_id == "span_panel_beer_fridge_breaker"
    assert (
        registry.async_regenerate_entity_id(registry_entry)
        == "switch.span_panel_beer_fridge_breaker"
    )
