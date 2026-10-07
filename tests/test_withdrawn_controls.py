"""Controls the panel withdraws are removed at setup, in this entry only, and return with their identity.

The panel decides which circuits take a breaker switch and a shed-priority
select, and `helpers.circuit_has_a_breaker_switch` and
`circuit_has_a_priority_select` read its answer. Firmware r202639 locks the
circuits SPAN adds for a commissioned PV or backup system, so a switch and a
select that existed before are no longer offered. Setup removes their registry
entries instead of leaving them orphaned and unavailable. Core keeps each one's
tombstone while the config entry exists, so a control the panel offers again
returns with its entity id, its registry id and the user's customisations.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Final
from unittest.mock import MagicMock

from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er, label_registry as lr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData, ensure_device_registered
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.control_gate import ControlMode, ControlPolicy
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.helpers import (
    circuit_has_a_breaker_switch,
    remove_withdrawn_controls,
)
from custom_components.span_panel.id_builder import build_select_unique_id, build_switch_unique_id
from custom_components.span_panel.select import async_setup_entry as select_setup
from custom_components.span_panel.switch import async_setup_entry as switch_setup

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .test_pv_device import PANEL_NAME, _coordinator, _entry

SERIAL: Final = "sp3-withdrawn-001"
BACKUP: Final = "backup-system"
INVERTER: Final = "second-inverter"
KITCHEN: Final = "kitchen"
WELL: Final = "well-pump"
GONE: Final = "removed-from-panel"
UNMAPPED: Final = "unmapped_tab_32"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """The switch platform's relock and debounce timers may outlive a test."""
    return True


def _circuit(
    circuit_id: str,
    name: str,
    tabs: list[int],
    *,
    locked: bool = False,
    device_type: str = "circuit",
    never_backup: bool = False,
) -> SpanCircuitSnapshot:
    """A circuit as r202633 publishes it, or as r202639 locks it: not controllable, priority NEVER (spec §1 B)."""
    return SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=name,
        tabs=tabs,
        is_user_controllable=not locked,
        is_never_backup=locked or never_backup,
        priority="NEVER" if locked else "SOC_THRESHOLD",
        device_type=device_type,
    )


def _panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL, circuits={c.circuit_id: c for c in circuits}
    )


KITCHEN_CIRCUIT: Final = _circuit(KITCHEN, "Kitchen", [2])
R202633: Final = _panel(
    _circuit(BACKUP, "Commissioned Backup System", [5, 7]),
    _circuit(INVERTER, "Solar Inverter 2", [24, 26]),
    KITCHEN_CIRCUIT,
)
R202639: Final = _panel(
    _circuit(BACKUP, "Commissioned Backup System", [5, 7], locked=True),
    _circuit(INVERTER, "Solar Inverter 2", [24, 26], locked=True, device_type="pv"),
    KITCHEN_CIRCUIT,
)
WITHDRAWN: Final = (
    ("switch", build_switch_unique_id(SERIAL, BACKUP)),
    ("select", build_select_unique_id(SERIAL, BACKUP)),
    ("switch", build_switch_unique_id(SERIAL, INVERTER)),
    ("select", build_select_unique_id(SERIAL, INVERTER)),
)
KEPT: Final = (
    ("switch", build_switch_unique_id(SERIAL, KITCHEN)),
    ("select", build_select_unique_id(SERIAL, KITCHEN)),
)


@dataclass
class _Controls:
    coordinator: MagicMock
    platforms: list[MockEntityPlatform]

    async def unload(self) -> None:
        for platform in self.platforms:
            await platform.async_reset()

    def entity(self, entity_id: str) -> object:
        for platform in self.platforms:
            if entity_id in platform.entities:
                return platform.entities[entity_id]
        raise AssertionError(f"{entity_id} is not on a platform")


async def _set_up(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    snapshot: SpanPanelSnapshot,
    *,
    mode: ControlMode = ControlMode.ALL_USERS,
) -> _Controls:
    """Run the switch and select platforms' real `async_setup_entry` through real `EntityPlatform`s."""
    coordinator = _coordinator(hass, entry, snapshot)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=await ensure_device_registered(hass, entry, snapshot, PANEL_NAME),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
        control_policy=replace(ControlPolicy.default(), mode=mode),
    )
    platforms: list[MockEntityPlatform] = []
    for domain, setup in (("switch", switch_setup), ("select", select_setup)):
        added: list[object] = []
        await setup(hass, entry, lambda entities, **_: added.extend(entities))
        platform = MockEntityPlatform(
            hass, domain=domain, platform_name=DOMAIN, logger=logging.getLogger(__name__)
        )
        platform.config_entry = entry
        await platform.async_add_entities(added)
        platforms.append(platform)
    await hass.async_block_till_done()
    return _Controls(coordinator, platforms)


def _seed(hass: HomeAssistant, entry: MockConfigEntry, domain: str, unique_id: str, object_id: str) -> str:
    """Register a control as an earlier release left it."""
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, unique_id, config_entry=entry, suggested_object_id=object_id
    ).entity_id


SEEDED_OBJECT_IDS: Final = {
    build_switch_unique_id(SERIAL, BACKUP): "span_panel_commissioned_backup_system_breaker",
    build_select_unique_id(SERIAL, BACKUP): "span_panel_commissioned_backup_system_circuit_priority",
    build_switch_unique_id(SERIAL, INVERTER): "span_panel_solar_inverter_2_breaker",
    build_select_unique_id(SERIAL, INVERTER): "span_panel_solar_inverter_2_circuit_priority",
    build_switch_unique_id(SERIAL, KITCHEN): "span_panel_kitchen_breaker",
    build_select_unique_id(SERIAL, KITCHEN): "span_panel_kitchen_circuit_priority",
}


def _seed_a_2_1_1_registry(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """2.1.1 on r202639: both locked circuits' switch and select orphaned, the kitchen's live (spec §5.3, review H1)."""
    return {
        unique_id: _seed(hass, entry, domain, unique_id, SEEDED_OBJECT_IDS[unique_id])
        for domain, unique_id in (*WITHDRAWN, *KEPT)
    }


def _assert_tombstoned(hass: HomeAssistant, entry: MockConfigEntry, domain: str, unique_id: str) -> None:
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(domain, DOMAIN, unique_id) is None
    deleted = registry.deleted_entities[(domain, DOMAIN, unique_id)]
    assert deleted.config_entry_id == entry.entry_id
    assert deleted.orphaned_timestamp is None


# ---------------------------------------------------------------------------
# 2.1.1 on r202639, then this release (spec §5.3, §6.1 B)
# ---------------------------------------------------------------------------


async def test_setup_before_start_removes_the_orphans_and_start_restores_no_state(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-before-start", SERIAL)
    seeded = _seed_a_2_1_1_registry(hass, entry)

    await _set_up(hass, entry, R202639)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
    await hass.async_block_till_done()

    for domain, unique_id in WITHDRAWN:
        _assert_tombstoned(hass, entry, domain, unique_id)
        assert hass.states.get(seeded[unique_id]) is None
    for domain, unique_id in KEPT:
        assert er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id) == seeded[unique_id]


async def test_setup_after_start_removes_the_restored_unavailable_states(hass: HomeAssistant) -> None:
    """The setup-retry path: Core has already written `restored: true` states (spec F10)."""
    entry = _entry(hass, "entry-after-start", SERIAL)
    seeded = _seed_a_2_1_1_registry(hass, entry)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
    await hass.async_block_till_done()
    for _, unique_id in WITHDRAWN:
        restored = hass.states.get(seeded[unique_id])
        assert restored is not None and restored.attributes.get("restored") is True

    await _set_up(hass, entry, R202639)

    for domain, unique_id in WITHDRAWN:
        _assert_tombstoned(hass, entry, domain, unique_id)
        assert hass.states.get(seeded[unique_id]) is None


# ---------------------------------------------------------------------------
# What is judged, and what is not
# ---------------------------------------------------------------------------


async def test_a_never_backup_circuit_keeps_its_switch_and_loses_its_select(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-never-backup", SERIAL)
    switch_id = _seed(hass, entry, "switch", build_switch_unique_id(SERIAL, WELL), "span_panel_well_pump_breaker")
    _seed(hass, entry, "select", build_select_unique_id(SERIAL, WELL), "span_panel_well_pump_circuit_priority")

    await _set_up(hass, entry, _panel(_circuit(WELL, "Well Pump", [9], never_backup=True)))

    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, build_switch_unique_id(SERIAL, WELL)) == switch_id
    _assert_tombstoned(hass, entry, "select", build_select_unique_id(SERIAL, WELL))


async def test_a_circuit_absent_from_the_setup_snapshot_is_never_judged(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-absent", SERIAL)
    gone = _seed(hass, entry, "switch", build_switch_unique_id(SERIAL, GONE), "span_panel_gone_breaker")
    unmapped = SpanCircuitSnapshotFactory.create(circuit_id=UNMAPPED, tabs=[32], is_user_controllable=False)

    await _set_up(hass, entry, _panel(KITCHEN_CIRCUIT, unmapped))

    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, build_switch_unique_id(SERIAL, GONE)) == gone


async def test_another_entrys_registration_of_the_same_unique_id_is_untouched(hass: HomeAssistant) -> None:
    other = _entry(hass, "entry-other", "sp3-other-002")
    unique_id = build_switch_unique_id(SERIAL, BACKUP)
    held = _seed(hass, other, "switch", unique_id, "span_panel_other_breaker")
    entry = _entry(hass, "entry-mine", SERIAL)

    await _set_up(hass, entry, R202639)

    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, unique_id) == held


async def test_nobody_removes_nothing_and_administrators_only_then_removes_the_withdrawn(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-nobody", SERIAL)
    seeded = _seed_a_2_1_1_registry(hass, entry)

    controls = await _set_up(hass, entry, R202639, mode=ControlMode.DISABLED)
    for domain, unique_id in (*WITHDRAWN, *KEPT):
        assert er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id) == seeded[unique_id]
    await controls.unload()

    await _set_up(hass, entry, R202639, mode=ControlMode.ADMIN_ONLY)
    for domain, unique_id in WITHDRAWN:
        _assert_tombstoned(hass, entry, domain, unique_id)


async def test_the_function_reports_and_logs_what_it_removed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(hass, "entry-direct", SERIAL)
    backup = _seed(hass, entry, "switch", build_switch_unique_id(SERIAL, BACKUP), "span_panel_backup_breaker")
    _seed(hass, entry, "switch", build_switch_unique_id(SERIAL, KITCHEN), "span_panel_kitchen_breaker")

    with caplog.at_level(logging.INFO):
        removed = remove_withdrawn_controls(
            er.async_get(hass),
            entry.entry_id,
            "switch",
            R202639.circuits,
            circuit_has_a_breaker_switch,
            lambda circuit_id: build_switch_unique_id(SERIAL, circuit_id),
        )

    assert removed == [backup]
    assert backup in caplog.text


# ---------------------------------------------------------------------------
# Restoration (spec §3.4, F9, F11)
# ---------------------------------------------------------------------------


async def test_a_restored_control_returns_with_its_identity_and_customisations(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-restore", SERIAL)
    registry = er.async_get(hass)
    controls = await _set_up(hass, entry, R202633)
    switch_uid = build_switch_unique_id(SERIAL, BACKUP)
    select_uid = build_select_unique_id(SERIAL, BACKUP)
    switch_id = registry.async_get_entity_id("switch", DOMAIN, switch_uid)
    select_id = registry.async_get_entity_id("select", DOMAIN, select_uid)
    assert switch_id is not None and select_id is not None
    area = ar.async_get(hass).async_create("Garage")
    label = lr.async_get(hass).async_create("Backup loads")
    registry.async_update_entity(
        switch_id,
        name="Backup Panel Feed",
        area_id=area.id,
        labels={label.label_id},
        hidden_by=er.RegistryEntryHider.USER,
    )
    before = registry.async_get(switch_id)
    select_before = registry.async_get(select_id)
    assert before is not None and select_before is not None
    await controls.unload()

    controls = await _set_up(hass, entry, R202639)
    _assert_tombstoned(hass, entry, "switch", switch_uid)
    _assert_tombstoned(hass, entry, "select", select_uid)
    await controls.unload()

    controls = await _set_up(hass, entry, R202633)

    after = registry.async_get(switch_id)
    assert after is not None
    assert (after.id, after.unique_id, after.name, after.area_id, after.labels, after.hidden_by) == (
        before.id,
        before.unique_id,
        "Backup Panel Feed",
        area.id,
        {label.label_id},
        er.RegistryEntryHider.USER,
    )
    select_after = registry.async_get(select_id)
    assert select_after is not None and select_after.id == select_before.id
    # A named control asks for no reload; an unnamed one asks once, to sync its name.
    request_reload = controls.coordinator.request_reload
    request_reload.reset_mock()
    controls.entity(switch_id)._handle_coordinator_update()
    assert request_reload.call_count == 0
    controls.entity(select_id)._handle_coordinator_update()
    assert request_reload.call_count == 1


async def test_a_user_disabled_control_comes_back_disabled(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-disabled", SERIAL)
    registry = er.async_get(hass)
    controls = await _set_up(hass, entry, R202633)
    switch_id = registry.async_get_entity_id("switch", DOMAIN, build_switch_unique_id(SERIAL, BACKUP))
    assert switch_id is not None
    registry.async_update_entity(switch_id, disabled_by=er.RegistryEntryDisabler.USER)
    await controls.unload()

    await (await _set_up(hass, entry, R202639)).unload()
    await _set_up(hass, entry, R202633)

    restored = registry.async_get(switch_id)
    assert restored is not None
    assert restored.disabled_by is er.RegistryEntryDisabler.USER


async def test_the_entity_id_survives_every_withdrawal_and_return(hass: HomeAssistant) -> None:
    entry = _entry(hass, "entry-permanence", SERIAL)
    registry = er.async_get(hass)
    await (await _set_up(hass, entry, R202633)).unload()
    original = registry.async_get_entity_id("switch", DOMAIN, build_switch_unique_id(SERIAL, BACKUP))

    for snapshot in (R202639, R202633, R202639, R202633):
        await (await _set_up(hass, entry, snapshot)).unload()

    assert registry.async_get_entity_id("switch", DOMAIN, build_switch_unique_id(SERIAL, BACKUP)) == original


async def test_cores_exception_another_entity_taking_the_id_moves_the_restored_one(hass: HomeAssistant) -> None:
    """Pinned, not endorsed: only a newly created collision or a user rename can do this (spec §3.4)."""
    entry = _entry(hass, "entry-collision", SERIAL)
    registry = er.async_get(hass)
    await (await _set_up(hass, entry, R202633)).unload()
    unique_id = build_switch_unique_id(SERIAL, BACKUP)
    original = registry.async_get_entity_id("switch", DOMAIN, unique_id)
    assert original is not None
    await (await _set_up(hass, entry, R202639)).unload()

    squatter = registry.async_get_or_create(
        "switch", "another_integration", "squatter", suggested_object_id=original.removeprefix("switch.")
    )
    assert squatter.entity_id == original
    await _set_up(hass, entry, R202633)

    restored = registry.async_get_entity_id("switch", DOMAIN, unique_id)
    assert restored is not None and restored != original
