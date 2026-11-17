"""A breaker switch that joins an area the user never gave it is reported.

An entity with no area of its own is in its device's, and an area-wide action,
such as turning off everything in the kitchen, reaches every switch in the
area. A circuit's device can have an area before its breaker switch is on it,
so the switch can arrive in a room nobody put it in. Setup raises one notice
per such switch, naming the switch and the area.

A switch arrives in two ways:

- it is new: no row in the registry before the platforms add it, as when the
  panel starts offering control on a circuit or the user switches controls on
  for the first time. A row Home Assistant restores from its deleted entities
  is new too, and comes back with the area it had;
- an install from before circuits had devices still has it on the panel's
  device, and setup moves it to its circuit's device after the user gave that
  device an area.

A switch already on its circuit's device raises nothing, whatever area the user
gives the device afterwards.

Each case runs the integration's real `async_setup_entry` over a replayed
capture (`captures_replay.ReplayClient`): the real coordinator, platforms and
registries.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import contextlib
from dataclasses import dataclass
from typing import Final
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import pytest
from pytest_homeassistant.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot
from span_panel_api.protocol import SchemaAdapter

from custom_components.span_panel import switch as switch_platform
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.control_gate import ControlMode
from custom_components.span_panel.conversion_repairs import (
    async_clear_conversion_notice,
    async_raise_breaker_switch_area_notice,
)
from custom_components.span_panel.helpers import circuit_has_a_breaker_switch
from custom_components.span_panel.id_builder import (
    build_circuit_device_identifier,
    build_switch_unique_id,
)
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import CONTROL_MODE
from custom_components.span_panel.runtime import SpanPanelConfigEntry
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

from .captures_replay import ReplayClient, RetainedTree, capture, replay
from .helpers import PANEL_NAME, add_panel_entry

CAPTURE: Final = "r202639-d"
"""A capture with several circuits the panel offers a breaker switch on."""

KITCHEN: Final = "circuit-2"
"""A circuit with a breaker switch, named "Kitchen Outlets" here."""

LIGHTS: Final = "circuit-1"
"""A second circuit with a breaker switch, named "Lights" here."""

CONTROLS_ON: Final[Mapping[str, object]] = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
"""The options a user who accepts every default has, under which controls are built."""

CONTROLS_OFF: Final[Mapping[str, object]] = {
    **CONTROLS_ON,
    CONTROL_MODE: ControlMode.DISABLED.value,
}
"""The same with controls switched off: no breaker switch is built, and none is removed."""

NOTICE_PREFIX: Final = "breaker_switch_in_area_"

KITCHEN_BREAKER: Final = "Kitchen Outlets Breaker"
"""The name Home Assistant displays the kitchen circuit's breaker switch under."""

LIGHTS_BREAKER: Final = "Lights Breaker"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


# ---------------------------------------------------------------------------
# A panel set up for real, whose options a case can change
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Panel:
    """One panel's entry and the replay it is set up over."""

    hass: HomeAssistant
    entry: MockConfigEntry
    adapter: SchemaAdapter

    @property
    def snapshot(self) -> SpanPanelSnapshot:
        """What the panel publishes."""
        return self.adapter.build_snapshot()

    @property
    def serial(self) -> str:
        return self.snapshot.serial_number

    def switch_unique_id(self, circuit_id: str) -> str:
        """Return the unique ID of a circuit's breaker switch."""
        return build_switch_unique_id(self.serial, circuit_id)

    def find_switch(self, circuit_id: str) -> er.RegistryEntry | None:
        """Return the registry row of a circuit's breaker switch, if it has one."""
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "switch", DOMAIN, self.switch_unique_id(circuit_id)
        )
        return registry.async_get(entity_id) if entity_id is not None else None

    def switch(self, circuit_id: str) -> er.RegistryEntry:
        """Return the registry row of a circuit's breaker switch."""
        row = self.find_switch(circuit_id)
        assert row is not None, f"{circuit_id} has no breaker switch"
        return row

    def circuit_device(self, circuit_id: str) -> dr.ChildDeviceEntry:
        """Return the circuit's own device."""
        device = dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, build_circuit_device_identifier(self.serial, circuit_id)),
            self.entry.entry_id,
        )
        assert device is not None, f"{circuit_id} has no device of its own"
        return device

    def put_circuit_device_in(self, circuit_id: str, area_id: str) -> None:
        """Give a circuit's device an area, as its user would in Home Assistant."""
        dr.async_get(self.hass).async_update_child_device(
            self.circuit_device(circuit_id).id, area_id=area_id
        )

    def switch_as_noticed(self, circuit_id: str, displayed_name: str) -> str:
        """Return how a notice names a circuit's switch: its displayed name, then its entity ID."""
        return f"{displayed_name} `{self.switch(circuit_id).entity_id}`"

    def notice_id(self, circuit_id: str) -> str:
        """Return the ID of the notice for a circuit's breaker switch."""
        return f"{NOTICE_PREFIX}{self.entry.entry_id}_{self.switch_unique_id(circuit_id)}"

    def notices(self) -> dict[str, ir.IssueEntry]:
        """Return every breaker-switch notice raised for this entry, by ID."""
        return _notices(self.hass, self.entry.entry_id)

    async def save_options(self, options: Mapping[str, object]) -> None:
        """Save new options, which reloads the entry."""
        self.hass.config_entries.async_update_entry(self.entry, options=dict(options))
        await self.hass.async_block_till_done()
        assert self.entry.state is ConfigEntryState.LOADED

    async def reload(self) -> None:
        """Set the entry up again, as a restart does."""
        assert await self.hass.config_entries.async_reload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        assert self.entry.state is ConfigEntryState.LOADED


def _notices(hass: HomeAssistant, entry_id: str) -> dict[str, ir.IssueEntry]:
    """Return every breaker-switch notice raised for an entry, by ID."""
    return {
        issue_id: issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith(f"{NOTICE_PREFIX}{entry_id}_")
    }


def _tree() -> RetainedTree:
    """Return the capture with its two switched circuits named."""
    tree = capture(CAPTURE).tree()
    tree[KITCHEN]["info/name"] = "Kitchen Outlets"
    tree[LIGHTS]["info/name"] = "Lights"
    return tree


def _panel(hass: HomeAssistant, options: Mapping[str, object]) -> _Panel:
    """Add an entry for the panel, with an empty registry: a new installation."""
    adapter = replay(_tree())
    snapshot = adapter.build_snapshot()
    serial = snapshot.serial_number
    for circuit_id in (KITCHEN, LIGHTS):
        assert circuit_has_a_breaker_switch(snapshot.circuits[circuit_id])
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
        options=dict(options),
        title=PANEL_NAME,
        entry_id="entry-breaker-area",
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    return _Panel(hass, entry, adapter)


@contextlib.asynccontextmanager
async def _set_up(panel: _Panel) -> AsyncIterator[None]:
    """Set the entry up over its replay, and unload it afterwards.

    The replay stays patched in for the whole run, so a case can change the
    entry's options or reload it and have it set up over the same panel.
    """
    hass = panel.hass
    client = ReplayClient(panel.adapter)
    with patch("custom_components.span_panel.SpanMqttClient", return_value=client):
        assert await hass.config_entries.async_setup(panel.entry.entry_id) is True
        await hass.async_block_till_done()
        assert panel.entry.state is ConfigEntryState.LOADED
        try:
            yield
        finally:
            if panel.entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(panel.entry.entry_id)
            await hass.async_block_till_done()


def _area(hass: HomeAssistant, name: str) -> str:
    """Create an area and return its ID."""
    return ar.async_get(hass).async_create(name).id


def _withdrawn_switch(panel: _Panel, circuit_id: str, *, area_id: str | None) -> None:
    """Leave a circuit's breaker switch among the registry's deleted entities.

    As the integration leaves it when the panel stops offering control on the
    circuit. Home Assistant keeps the row, area included, and restores it when
    the switch is added again.
    """
    registry = er.async_get(panel.hass)
    row = registry.async_get_or_create(
        "switch",
        DOMAIN,
        panel.switch_unique_id(circuit_id),
        config_entry=panel.entry,
        has_entity_name=True,
        original_name="Breaker",
    )
    if area_id is not None:
        registry.async_update_entity(row.entity_id, area_id=area_id)
    registry.async_remove(row.entity_id)


def _switch_from_an_older_release(panel: _Panel, circuit_id: str, *, area_id: str | None) -> None:
    """Register a circuit's breaker switch as a release before 2.3.0 left it.

    On the panel's device, named with its circuit in front. Setup moves it to
    its circuit's device once a platform builds its entity again.
    """
    circuit = panel.snapshot.circuits[circuit_id]
    panel_device = dr.async_get(panel.hass).async_get_or_create(
        config_entry_id=panel.entry.entry_id,
        identifiers={(DOMAIN, panel.serial)},
        name=PANEL_NAME,
    )
    registry = er.async_get(panel.hass)
    row = registry.async_get_or_create(
        "switch",
        DOMAIN,
        panel.switch_unique_id(circuit_id),
        config_entry=panel.entry,
        device_id=panel_device.id,
        has_entity_name=True,
        original_name=f"{circuit.name} Breaker",
        object_id_base=f"{circuit.name} breaker",
    )
    if area_id is not None:
        registry.async_update_entity(row.entity_id, area_id=area_id)


# ---------------------------------------------------------------------------
# A new switch
# ---------------------------------------------------------------------------


async def test_new_switch_under_a_device_area_raises_the_notice(hass: HomeAssistant) -> None:
    """A switch added to a circuit's device that has an area is in that area, and says so."""
    panel = _panel(hass, CONTROLS_OFF)
    kitchen = _area(hass, "Kitchen")

    async with _set_up(panel):
        assert panel.find_switch(KITCHEN) is None
        panel.put_circuit_device_in(KITCHEN, kitchen)

        await panel.save_options(CONTROLS_ON)

        switch = panel.switch(KITCHEN)
        assert switch.device_id == panel.circuit_device(KITCHEN).id
        assert switch.area_id is None
        notice = panel.notices()[panel.notice_id(KITCHEN)]
        assert notice.translation_key == "breaker_switch_in_area"
        assert notice.translation_placeholders == {
            "switch": panel.switch_as_noticed(KITCHEN, KITCHEN_BREAKER),
            "area": "Kitchen",
        }
        assert notice.is_persistent
        assert not notice.is_fixable
        assert notice.severity is ir.IssueSeverity.WARNING


async def test_each_new_switch_in_an_area_has_a_notice_of_its_own(hass: HomeAssistant) -> None:
    """One notice per switch, each naming its own switch and area, and none for the rest."""
    panel = _panel(hass, CONTROLS_OFF)
    kitchen, hallway = _area(hass, "Kitchen"), _area(hass, "Hallway")

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)
        panel.put_circuit_device_in(LIGHTS, hallway)

        await panel.save_options(CONTROLS_ON)

        assert {
            issue_id: notice.translation_placeholders
            for issue_id, notice in panel.notices().items()
        } == {
            panel.notice_id(KITCHEN): {
                "switch": panel.switch_as_noticed(KITCHEN, KITCHEN_BREAKER),
                "area": "Kitchen",
            },
            panel.notice_id(LIGHTS): {
                "switch": panel.switch_as_noticed(LIGHTS, LIGHTS_BREAKER),
                "area": "Hallway",
            },
        }


async def test_new_switch_on_a_device_without_area_raises_nothing(hass: HomeAssistant) -> None:
    """A new installation's switches are all new, and none of their devices has an area."""
    panel = _panel(hass, CONTROLS_ON)

    async with _set_up(panel):
        assert panel.switch(KITCHEN).area_id is None
        assert panel.circuit_device(KITCHEN).area_id is None
        assert panel.notices() == {}


async def test_new_switch_in_only_the_panel_s_area_raises_nothing(hass: HomeAssistant) -> None:
    """A circuit's device with no area is in the panel's, where a new switch has always been.

    Every breaker switch was in the panel's area while it sat on the panel's
    device, so one added there now has joined nothing new.
    """
    panel = _panel(hass, CONTROLS_OFF)
    garage = _area(hass, "Garage")

    async with _set_up(panel):
        devices = dr.async_get(hass)
        panel_device = devices.async_get_device_by_identifier(
            (DOMAIN, panel.serial), panel.entry.entry_id
        )
        assert panel_device is not None
        devices.async_update_device(panel_device.id, area_id=garage)

        await panel.save_options(CONTROLS_ON)

        assert panel.switch(KITCHEN).area_id is None
        assert panel.circuit_device(KITCHEN).area_id is None
        assert panel.notices() == {}


async def test_existing_switch_raises_nothing(hass: HomeAssistant) -> None:
    """A switch already on the device is in whatever area its user then gives the device.

    That is the user's own doing, so the next setup has nothing to tell them.
    """
    panel = _panel(hass, CONTROLS_ON)
    kitchen = _area(hass, "Kitchen")

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)

        await panel.reload()

        assert panel.switch(KITCHEN).area_id is None
        assert panel.circuit_device(KITCHEN).area_id == kitchen
        assert panel.notices() == {}


async def test_a_switch_kept_while_controls_were_off_raises_nothing(hass: HomeAssistant) -> None:
    """Switching controls off keeps every switch's row, so switching them on adds none new."""
    panel = _panel(hass, CONTROLS_ON)
    kitchen = _area(hass, "Kitchen")

    async with _set_up(panel):
        await panel.save_options(CONTROLS_OFF)
        assert panel.switch(KITCHEN).device_id == panel.circuit_device(KITCHEN).id
        panel.put_circuit_device_in(KITCHEN, kitchen)

        await panel.save_options(CONTROLS_ON)

        assert panel.notices() == {}


async def test_restored_switch_with_its_own_area_raises_nothing(hass: HomeAssistant) -> None:
    """A switch Home Assistant restores comes back in the area it had, not its device's."""
    panel = _panel(hass, CONTROLS_OFF)
    kitchen, garage = _area(hass, "Kitchen"), _area(hass, "Garage")
    _withdrawn_switch(panel, KITCHEN, area_id=garage)

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)

        await panel.save_options(CONTROLS_ON)

        assert panel.switch(KITCHEN).area_id == garage
        assert panel.notices() == {}


async def test_restored_switch_with_no_area_of_its_own_raises_the_notice(
    hass: HomeAssistant,
) -> None:
    """A restored switch that had no area joins its device's, like any other new switch."""
    panel = _panel(hass, CONTROLS_OFF)
    kitchen = _area(hass, "Kitchen")
    _withdrawn_switch(panel, KITCHEN, area_id=None)

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)

        await panel.save_options(CONTROLS_ON)

        assert panel.switch(KITCHEN).area_id is None
        assert set(panel.notices()) == {panel.notice_id(KITCHEN)}


async def test_a_new_switch_registered_disabled_raises_the_notice(hass: HomeAssistant) -> None:
    """A switch registered disabled is in the area all the same, and enabling it is all it takes.

    The entry's own setting keeps new entities disabled, so the platform
    registers the switch and adds no entity for it.
    """
    panel = _panel(hass, CONTROLS_OFF)
    kitchen = _area(hass, "Kitchen")

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)
        hass.config_entries.async_update_entry(panel.entry, pref_disable_new_entities=True)
        await hass.async_block_till_done()

        await panel.save_options(CONTROLS_ON)

        switch = panel.switch(KITCHEN)
        assert switch.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(switch.entity_id) is None
        assert set(panel.notices()) == {panel.notice_id(KITCHEN)}


async def test_a_switch_registered_after_its_platform_returns_raises_the_notice(
    hass: HomeAssistant,
) -> None:
    """A switch Home Assistant has yet to register when the switch platform returns is new too.

    Home Assistant promises a platform's entities registered only once the
    platform's setup has returned to the forward. Here a breaker switch yields
    while it is added, so the switches after it are registered later than the
    platform's own return, and each is reported all the same.
    """
    panel = _panel(hass, CONTROLS_OFF)
    kitchen, hallway = _area(hass, "Kitchen"), _area(hass, "Hallway")
    switched = {KITCHEN, LIGHTS}
    registered_as_the_platform_returned: list[set[str]] = []
    set_up_the_platform = switch_platform.async_setup_entry
    added_to_hass = SpanPanelCircuitsSwitch.async_added_to_hass

    async def set_up_and_read_the_registry(
        hass: HomeAssistant,
        config_entry: SpanPanelConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        await set_up_the_platform(hass, config_entry, async_add_entities)
        registered_as_the_platform_returned.append(
            {circuit_id for circuit_id in switched if panel.find_switch(circuit_id) is not None}
        )

    async def yield_while_added(switch: SpanPanelCircuitsSwitch) -> None:
        await asyncio.sleep(0)
        await added_to_hass(switch)

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)
        panel.put_circuit_device_in(LIGHTS, hallway)

        with (
            patch.object(switch_platform, "async_setup_entry", set_up_and_read_the_registry),
            patch.object(SpanPanelCircuitsSwitch, "async_added_to_hass", yield_while_added),
        ):
            await panel.save_options(CONTROLS_ON)

        (registered,) = registered_as_the_platform_returned
        assert switched - registered, "every switch was registered before the platform returned"
        assert set(panel.notices()) == {panel.notice_id(KITCHEN), panel.notice_id(LIGHTS)}


async def test_a_notice_is_raised_once(hass: HomeAssistant) -> None:
    """The setup after the one that raised it leaves a notice the user dismissed dismissed."""
    panel = _panel(hass, CONTROLS_OFF)
    kitchen = _area(hass, "Kitchen")

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)
        await panel.save_options(CONTROLS_ON)
        ir.async_get(hass).async_ignore(DOMAIN, panel.notice_id(KITCHEN), True)

        await panel.reload()

        assert panel.notices()[panel.notice_id(KITCHEN)].dismissed_version is not None


# ---------------------------------------------------------------------------
# A switch moved from the panel's device
# ---------------------------------------------------------------------------


async def test_a_switch_converted_onto_a_device_in_an_area_raises_the_notice(
    hass: HomeAssistant,
) -> None:
    """A switch setup moves late, onto a device its user has given an area, joins that area.

    Controls were off when the install was upgraded, so the switch stayed on
    the panel's device. Its circuit's device was given an area since, and the
    switch, which has none of its own, is moved there when controls go on.
    """
    panel = _panel(hass, CONTROLS_OFF)
    kitchen = _area(hass, "Kitchen")
    _switch_from_an_older_release(panel, KITCHEN, area_id=None)

    async with _set_up(panel):
        assert panel.switch(KITCHEN).device_id != panel.circuit_device(KITCHEN).id
        panel.put_circuit_device_in(KITCHEN, kitchen)

        await panel.save_options(CONTROLS_ON)

        switch = panel.switch(KITCHEN)
        assert (switch.device_id, switch.area_id) == (panel.circuit_device(KITCHEN).id, None)
        assert {
            issue_id: notice.translation_placeholders
            for issue_id, notice in panel.notices().items()
        } == {
            panel.notice_id(KITCHEN): {
                "switch": panel.switch_as_noticed(KITCHEN, KITCHEN_BREAKER),
                "area": "Kitchen",
            }
        }


@pytest.mark.parametrize(
    "switch_area",
    [
        pytest.param("Kitchen", id="the-area-its-device-has"),
        pytest.param("Garage", id="another-area"),
    ],
)
async def test_a_converted_switch_that_stays_in_its_area_raises_nothing(
    hass: HomeAssistant, switch_area: str
) -> None:
    """A switch with an area of its own is in that area before the move and after it.

    Where the device's area is the same one, the switch gives its own up for
    the device's and is still in the room its user put it in.
    """
    panel = _panel(hass, CONTROLS_OFF)
    areas = {name: _area(hass, name) for name in ("Kitchen", "Garage")}
    _switch_from_an_older_release(panel, KITCHEN, area_id=areas[switch_area])

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, areas["Kitchen"])

        await panel.save_options(CONTROLS_ON)

        switch = panel.switch(KITCHEN)
        assert switch.device_id == panel.circuit_device(KITCHEN).id
        assert switch.area_id == (None if switch_area == "Kitchen" else areas["Garage"])
        assert panel.notices() == {}


async def test_a_switch_converted_with_its_circuit_raises_nothing(hass: HomeAssistant) -> None:
    """At the upgrade itself the circuit's device has no area for the switch to join."""
    panel = _panel(hass, CONTROLS_ON)
    _switch_from_an_older_release(panel, KITCHEN, area_id=None)

    async with _set_up(panel):
        switch = panel.switch(KITCHEN)
        assert switch.device_id == panel.circuit_device(KITCHEN).id
        assert panel.circuit_device(KITCHEN).area_id is None
        assert panel.notices() == {}


# ---------------------------------------------------------------------------
# The notice itself
# ---------------------------------------------------------------------------


def _raise_for(hass: HomeAssistant, entry: MockConfigEntry, unique_id: str) -> str:
    """Raise a notice for a switch registered under `unique_id`, and return the notice's ID."""
    row = er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, unique_id, config_entry=entry, original_name="Breaker"
    )
    async_raise_breaker_switch_area_notice(
        hass,
        entry,
        switch_entity_id=row.entity_id,
        switch_unique_id=unique_id,
        area_name="Kitchen",
    )
    return f"{NOTICE_PREFIX}{entry.entry_id}_{unique_id}"


async def test_a_switch_that_joins_an_area_again_is_reported_again(hass: HomeAssistant) -> None:
    """A notice the user dismissed is raised afresh when its switch joins an area once more.

    As when the panel withdraws a control and offers it again. Raised over the
    dismissed one, the notice would stay out of sight.
    """
    entry = add_panel_entry(hass, "entry-again", "sp3-242424-001")
    issue_id = _raise_for(hass, entry, "span_sp3-242424-001_relay_1")
    issues = ir.async_get(hass)
    issues.async_ignore(DOMAIN, issue_id, True)
    dismissed = issues.async_get_issue(DOMAIN, issue_id)
    assert dismissed is not None
    assert dismissed.dismissed_version is not None

    _raise_for(hass, entry, "span_sp3-242424-001_relay_1")

    raised = issues.async_get_issue(DOMAIN, issue_id)
    assert raised is not None
    assert raised.dismissed_version is None


async def test_a_notice_names_a_switch_with_no_name_by_its_entity_id(hass: HomeAssistant) -> None:
    """A row gone by the time the notice is raised is still named, by its entity ID alone."""
    entry = add_panel_entry(hass, "entry-unnamed", "sp3-242424-001")

    async_raise_breaker_switch_area_notice(
        hass,
        entry,
        switch_entity_id="switch.gone",
        switch_unique_id="span_sp3-242424-001_relay_1",
        area_name="Kitchen",
    )

    (notice,) = _notices(hass, entry.entry_id).values()
    assert notice.translation_placeholders == {"switch": "`switch.gone`", "area": "Kitchen"}


async def test_removing_the_entry_clears_its_notices(hass: HomeAssistant) -> None:
    """Home Assistant deletes no issue with its entry, so removing the panel clears them."""
    panel = _panel(hass, CONTROLS_OFF)
    kitchen = _area(hass, "Kitchen")

    async with _set_up(panel):
        panel.put_circuit_device_in(KITCHEN, kitchen)
        panel.put_circuit_device_in(LIGHTS, kitchen)
        await panel.save_options(CONTROLS_ON)
        assert len(panel.notices()) == 2

    assert await hass.config_entries.async_remove(panel.entry.entry_id)
    await hass.async_block_till_done()

    assert panel.notices() == {}


async def test_clearing_one_panel_s_notices_leaves_another_s(hass: HomeAssistant) -> None:
    """A notice's ID carries its entry's, so one panel's removal cannot take another's."""
    first = add_panel_entry(hass, "entry-first", "sp3-242424-001")
    second = add_panel_entry(hass, "entry-second", "sp3-242424-002")
    for entry, serial in ((first, "sp3-242424-001"), (second, "sp3-242424-002")):
        for circuit_id in ("1", "2"):
            _raise_for(hass, entry, f"span_{serial}_relay_{circuit_id}")

    async_clear_conversion_notice(hass, first.entry_id)

    assert _notices(hass, first.entry_id) == {}
    assert len(_notices(hass, second.entry_id)) == 2
