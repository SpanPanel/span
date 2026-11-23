"""A curated control's errors name its device as the user's device list does.

A breaker switch and a priority select sit on their circuit's own device, and a
charger's lock and charge-current limit on the charger's. The user can rename
any of those devices in Home Assistant, which reloads nothing, and an error that
names the device by a name it no longer carries names one the user cannot find.
The adopted controls are held to the same in `test_adoption.py`.

Every control here is set up by its platform's own `async_setup_entry` and added
through a real platform, because the name under test is the one Core holds: the
platform gives each entity its `device_entry` as it adds it, and Core replaces
that entry as the device is renamed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import pytest
from pytest_homeassistant.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import ControlTarget, PublishOutcome, PublishState, SpanPanelSnapshot
from span_panel_api.exceptions import SpanPanelServerError

from custom_components.span_panel import SpanPanelRuntimeData, ensure_device_registered
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.lock import SpanEvseLock, async_setup_entry as lock_setup
from custom_components.span_panel.number import SpanEvseNumber, async_setup_entry as number_setup
from custom_components.span_panel.runtime import SpanPanelConfigEntry
from custom_components.span_panel.select import (
    SpanPanelCircuitsSelect,
    async_setup_entry as select_setup,
)
from custom_components.span_panel.switch import (
    SpanPanelCircuitsSwitch,
    async_setup_entry as switch_setup,
)

from .factories import (
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
    pv_binding_for,
)
from .helpers import PANEL_NAME, add_panel_entry, add_through_platform
from .test_pv_device import _coordinator

SERIAL: Final = "sp3-error-names-001"
KITCHEN: Final = "kitchen-outlets"
GARAGE: Final = "garage-feed"
ROOF: Final = "roof-array"
CHARGER: Final = "evse-garage"


def _panel() -> SpanPanelSnapshot:
    """Return a panel with a controllable circuit, an unnamed solar one, and a charger fed by a third."""
    charger = replace(
        SpanEvseSnapshotFactory.create(node_id=CHARGER, feed_circuit_id=GARAGE),
        lock_control=ControlTarget(
            topic=f"ebus/5/{CHARGER}/switch/lock-state/set",
            device_id=CHARGER,
            node_id="switch",
            property_id="lock-state",
        ),
        lock_state_options=("LOCKED", "UNLOCKED"),
        charge_current_limit_a=24,
        charge_current_ceiling_a=32,
        charge_current_limit_settable=True,
        charge_current_limit_minimum_a=6,
        charge_current_limit_maximum_a=32,
    )
    circuits = (
        SpanCircuitSnapshotFactory.create(
            circuit_id=KITCHEN, name="Kitchen Outlets", tabs=[2], is_user_controllable=True
        ),
        # At a breaker slot, which is what gives a solar circuit a breaker switch.
        SpanCircuitSnapshotFactory.create(
            circuit_id=ROOF,
            name="",
            tabs=[30, 32],
            device_type="pv",
            relative_position="DOWNSTREAM",
            is_user_controllable=True,
        ),
        SpanCircuitSnapshotFactory.create(
            circuit_id=GARAGE, name="Garage", tabs=[9, 11], is_user_controllable=False
        ),
    )
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL,
        circuits={circuit.circuit_id: circuit for circuit in circuits},
        evse={CHARGER: charger},
    )


SNAPSHOT: Final = _panel()


async def _turn_on(entity: Entity) -> None:
    assert isinstance(entity, SpanPanelCircuitsSwitch)
    await entity.async_turn_on()


async def _select_never(entity: Entity) -> None:
    assert isinstance(entity, SpanPanelCircuitsSelect)
    await entity.async_select_option("never")


async def _lock(entity: Entity) -> None:
    assert isinstance(entity, SpanEvseLock)
    await entity.async_lock()


async def _limit_to_16_a(entity: Entity) -> None:
    assert isinstance(entity, SpanEvseNumber)
    await entity.async_set_native_value(16.0)


@dataclass(frozen=True)
class _Family:
    """One kind of control: its platform, how it is operated, and what its errors are keyed and named by."""

    domain: str
    setup: Callable[
        [HomeAssistant, SpanPanelConfigEntry, AddConfigEntryEntitiesCallback], Awaitable[None]
    ]
    operate: Callable[[Entity], Awaitable[None]]
    client_write: str
    placeholder: str
    failed_key: str
    not_delivered_key: str
    shown_name: str
    """What the user's device list calls the control's device before anyone renames it."""
    entity_type: type[Entity]
    subject: str
    """The circuit or charger, by wire id, whose control this family's test operates."""


def _subject_of(entity: Entity) -> str | None:
    """Return the circuit or charger, by wire id, that a control operates."""
    if isinstance(entity, SpanPanelCircuitsSwitch):
        return entity._circuit_id
    if isinstance(entity, SpanPanelCircuitsSelect):
        return entity.id
    if isinstance(entity, SpanEvseLock | SpanEvseNumber):
        return entity._evse_id
    return None


BREAKER: Final = _Family(
    domain="switch",
    setup=switch_setup,
    operate=_turn_on,
    client_write="set_circuit_relay",
    placeholder="circuit",
    failed_key="circuit_relay_failed",
    not_delivered_key="circuit_relay_not_delivered",
    shown_name="Kitchen Outlets",
    entity_type=SpanPanelCircuitsSwitch,
    subject=KITCHEN,
)
PRIORITY: Final = _Family(
    domain="select",
    setup=select_setup,
    operate=_select_never,
    client_write="set_circuit_priority",
    placeholder="circuit",
    failed_key="circuit_priority_failed",
    not_delivered_key="circuit_priority_not_delivered",
    shown_name="Kitchen Outlets",
    entity_type=SpanPanelCircuitsSelect,
    subject=KITCHEN,
)
CHARGER_LOCK: Final = _Family(
    domain="lock",
    setup=lock_setup,
    operate=_lock,
    client_write="set_evse_lock",
    placeholder="charger",
    failed_key="evse_lock_failed",
    not_delivered_key="evse_lock_not_delivered",
    shown_name="SPAN Panel SPAN Drive (Garage)",
    entity_type=SpanEvseLock,
    subject=CHARGER,
)
CHARGE_LIMIT: Final = _Family(
    domain="number",
    setup=number_setup,
    operate=_limit_to_16_a,
    client_write="set_evse_charge_limit",
    placeholder="charger",
    failed_key="evse_charge_limit_failed",
    not_delivered_key="evse_charge_limit_not_delivered",
    shown_name="SPAN Panel SPAN Drive (Garage)",
    entity_type=SpanEvseNumber,
    subject=CHARGER,
)

EVERY_FAMILY_BOTH_WAYS: Final = pytest.mark.parametrize(
    ("family", "resolved"),
    [
        pytest.param(
            family, resolved, id=f"{family.domain}-{'not-delivered' if resolved else 'refused'}"
        )
        for family in (BREAKER, PRIORITY, CHARGER_LOCK, CHARGE_LIMIT)
        for resolved in (False, True)
    ],
)
"""Each family, through a write the library refuses and one never handed to the broker.

`resolved` is True for the second: that write resolved an address and was never
delivered, where a refused one never resolved one.
"""

BOTH_CIRCUIT_CONTROLS: Final = pytest.mark.parametrize(
    "family", [BREAKER, PRIORITY], ids=["switch", "select"]
)
"""The two controls that name a circuit rather than a device of their own."""


@dataclass
class _Live:
    """One control added to Home Assistant, the coordinator its client hangs off, and its platform."""

    control: Entity
    coordinator: MagicMock
    platform: MockEntityPlatform


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry, family: _Family) -> _Live:
    """Set the family's platform up as a load does, and add its control through a real platform.

    Only the control under test is added. The platform builds the others it
    always builds -- the control lock's switch, the solar circuit's controls --
    and none of them bears on what this control's errors say.
    """
    coordinator = _coordinator(hass, entry, SNAPSHOT)
    coordinator.client = MagicMock()
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=await ensure_device_registered(hass, entry, SNAPSHOT, PANEL_NAME),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SNAPSHOT),
        setup_snapshot=SNAPSHOT,
    )
    built: list[Entity] = []

    def add_entities(
        new_entities: Iterable[Entity],
        update_before_add: bool = False,
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        built.extend(new_entities)

    await family.setup(hass, entry, add_entities)
    (control,) = [
        entity
        for entity in built
        if isinstance(entity, family.entity_type) and _subject_of(entity) == family.subject
    ]
    platform = await add_through_platform(hass, entry, family.domain, control)
    return _Live(control, coordinator, platform)


def _rename(hass: HomeAssistant, live: _Live, name: str) -> None:
    """Rename the control's device in Home Assistant, as the user does from its device page.

    A circuit's device is a child device, which the registry renames through
    its own call; a charger's is a sub-device like any other.
    """
    device = live.control.device_entry
    assert device is not None
    registry = dr.async_get(hass)
    if isinstance(device, dr.ChildDeviceEntry):
        registry.async_update_child_device(device.id, name_by_user=name)
    else:
        registry.async_update_device(device.id, name_by_user=name)


def _deliver(live: _Live, snapshot: SpanPanelSnapshot) -> None:
    """Hand the control a snapshot the way the coordinator does, without the reload it may ask for."""
    assert isinstance(live.control, SpanPanelCircuitsSwitch | SpanPanelCircuitsSelect)
    live.coordinator.data = snapshot
    live.control._handle_coordinator_update()


def _failing_write(resolved: bool) -> AsyncMock:
    """Return a write the library refuses, or one resolved and never handed to the broker."""
    if not resolved:
        return AsyncMock(
            side_effect=SpanPanelServerError("the panel declares this not commandable")
        )
    return AsyncMock(
        return_value=PublishOutcome(
            state=PublishState.FAILED,
            topic=None,
            value="",
            detail="broker not connected; refused rather than queued",
        )
    )


async def _named_by_failed_write(live: _Live, family: _Family, *, resolved: bool) -> str:
    """Operate the control through a write that fails, and return the device its error names."""
    setattr(live.coordinator.client, family.client_write, _failing_write(resolved))

    with pytest.raises(HomeAssistantError) as raised:
        await family.operate(live.control)

    assert raised.value.translation_key == (
        family.not_delivered_key if resolved else family.failed_key
    )
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    return placeholders[family.placeholder]


@EVERY_FAMILY_BOTH_WAYS
async def test_an_error_names_the_device_as_the_user_renamed_it(
    hass: HomeAssistant, family: _Family, resolved: bool
) -> None:
    """The device was renamed before this setup, as a restart after a rename finds it."""
    entry = add_panel_entry(hass, "entry-renamed", SERIAL)
    first = await _set_up(hass, entry, family)
    _rename(hass, first, "Workshop Bench")
    await first.platform.async_reset()

    live = await _set_up(hass, entry, family)

    assert await _named_by_failed_write(live, family, resolved=resolved) == "Workshop Bench"


@EVERY_FAMILY_BOTH_WAYS
async def test_a_rename_after_setup_reaches_the_next_error(
    hass: HomeAssistant, family: _Family, resolved: bool
) -> None:
    """Renaming a device sets none of its entities up again, so a name captured at setup stays old."""
    entry = add_panel_entry(hass, "entry-renamed-live", SERIAL)
    live = await _set_up(hass, entry, family)
    before = await _named_by_failed_write(live, family, resolved=resolved)

    _rename(hass, live, "Workshop Bench")
    await hass.async_block_till_done()
    after = await _named_by_failed_write(live, family, resolved=resolved)

    assert (before, after) == (family.shown_name, "Workshop Bench")


@EVERY_FAMILY_BOTH_WAYS
async def test_a_device_nobody_renamed_keeps_its_name(
    hass: HomeAssistant, family: _Family, resolved: bool
) -> None:
    """With no rename the error names the device by the name it was registered under."""
    entry = add_panel_entry(hass, "entry-not-renamed", SERIAL)
    live = await _set_up(hass, entry, family)

    assert await _named_by_failed_write(live, family, resolved=resolved) == family.shown_name


@BOTH_CIRCUIT_CONTROLS
async def test_a_panel_rename_not_yet_reloaded_keeps_the_name_the_device_list_shows(
    hass: HomeAssistant, family: _Family
) -> None:
    """A circuit renamed in the SPAN app renames its device only on the reload that follows.

    Until that reload has run, the device and the device list still carry the
    old name, so the error does too. Naming the panel's new name there would
    name a device the user cannot yet find. Set up a second time, as a restart
    finds an existing entity, so the update below is read as the rename it is
    and asks for the reload.
    """
    entry = add_panel_entry(hass, "entry-panel-rename", SERIAL)
    first = await _set_up(hass, entry, family)
    await first.platform.async_reset()
    live = await _set_up(hass, entry, family)
    renamed = replace(SNAPSHOT.circuits[KITCHEN], name="Kitchen Island")

    _deliver(live, replace(SNAPSHOT, circuits={**SNAPSHOT.circuits, KITCHEN: renamed}))

    live.coordinator.request_reload.assert_called_once()
    assert await _named_by_failed_write(live, family, resolved=False) == "Kitchen Outlets"


@BOTH_CIRCUIT_CONTROLS
async def test_a_circuit_gone_from_the_snapshot_is_named_by_its_device(
    hass: HomeAssistant, family: _Family
) -> None:
    """A circuit can drop out of the snapshot between a command and its failure.

    The panel's label for it then has nothing to read but its wire id, which
    names nothing the user can find. Its device is still there, under the name
    the device list shows.
    """
    entry = add_panel_entry(hass, "entry-circuit-gone", SERIAL)
    live = await _set_up(hass, entry, family)
    remaining = {cid: circuit for cid, circuit in SNAPSHOT.circuits.items() if cid != KITCHEN}

    _deliver(live, replace(SNAPSHOT, circuits=remaining))

    assert await _named_by_failed_write(live, family, resolved=False) == "Kitchen Outlets"


@pytest.mark.parametrize("resolved", [False, True], ids=["refused", "not-delivered"])
async def test_an_unnamed_solar_circuit_is_named_as_its_device_is(
    hass: HomeAssistant, resolved: bool
) -> None:
    """Its device is "Solar", the name its entities have carried since v1, not its breaker positions.

    An unnamed solar circuit is the one case where the panel's label for a
    circuit and its device's name differ, so it is the case that shows which
    of the two an error reads.
    """
    solar_breaker = replace(BREAKER, subject=ROOF)
    entry = add_panel_entry(hass, "entry-solar", SERIAL)
    live = await _set_up(hass, entry, solar_breaker)

    assert await _named_by_failed_write(live, solar_breaker, resolved=resolved) == "Solar"
