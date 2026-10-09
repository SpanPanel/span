"""A charger whose connector lock is settable gets a lock entity; one whose lock is read-only does not.

The library sets `lock_control` only where the charger declares its lock state
`$settable`, and its `set_evse_lock` publishes the command to that target. The
lock reads the position the charger reports, and the read-only lock-state
sensor stays beside it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest
from span_panel_api import ControlTarget, PublishOutcome, PublishState, SpanPanelSnapshot

from custom_components.span_panel import PLATFORMS
from custom_components.span_panel.control_gate import ControlMode, ControlPolicy
from custom_components.span_panel.id_builder import build_evse_unique_id
from custom_components.span_panel.lock import SpanEvseLock, async_setup_entry

from .factories import SpanEvseSnapshotFactory, SpanPanelSnapshotFactory
from .test_evse_charge_limit import _coordinator

SERIAL: Final = "sp3-lock-001"
SETTABLE: Final = "evse-settable"
READ_ONLY: Final = "evse-read-only"

LOCK_TARGET: Final = ControlTarget(
    topic=f"ebus/5/{SETTABLE}/switch/lock-state/set",
    device_id=SETTABLE,
    node_id="switch",
    property_id="lock-state",
)


def _snapshot(lock_state: str = "LOCKED") -> SpanPanelSnapshot:
    settable = replace(
        SpanEvseSnapshotFactory.create(
            node_id=SETTABLE, feed_circuit_id="c-1", lock_state=lock_state
        ),
        lock_control=LOCK_TARGET,
        lock_state_options=("LOCKED", "UNLOCKED"),
    )
    read_only = SpanEvseSnapshotFactory.create(node_id=READ_ONLY, feed_circuit_id="c-2")
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL, evse={SETTABLE: settable, READ_ONLY: read_only}
    )


async def _created(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, client: object | None = None
) -> list[SpanEvseLock]:
    coordinator = _coordinator(snapshot, client)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, coordinator.config_entry, async_add_entities)

    added: Sequence[SpanEvseLock] = [
        entity for call in async_add_entities.call_args_list for entity in call.args[0]
    ]
    assert all(isinstance(entity, SpanEvseLock) for entity in added)
    return list(added)


def test_the_lock_platform_is_forwarded() -> None:
    assert "lock" in {str(platform) for platform in PLATFORMS}


async def test_only_a_settable_lock_becomes_a_lock_entity(hass: HomeAssistant) -> None:
    (lock,) = await _created(hass, _snapshot())

    assert lock.unique_id == build_evse_unique_id(SERIAL, SETTABLE, "lock")


async def test_no_lock_entity_while_controls_are_disabled(hass: HomeAssistant) -> None:
    snapshot = _snapshot()
    coordinator = _coordinator(snapshot)
    coordinator.config_entry.runtime_data = replace(
        coordinator.config_entry.runtime_data,
        control_policy=replace(ControlPolicy.default(), mode=ControlMode.DISABLED),
    )
    async_add_entities = MagicMock()

    await async_setup_entry(hass, coordinator.config_entry, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.parametrize(
    ("lock_state", "is_locked"),
    [("LOCKED", True), ("UNLOCKED", False), ("UNKNOWN", None), ("", None)],
)
async def test_the_lock_reads_the_position_the_charger_reports(
    hass: HomeAssistant, lock_state: str, is_locked: bool | None
) -> None:
    (lock,) = await _created(hass, _snapshot(lock_state))

    assert lock.is_locked is is_locked


@pytest.mark.parametrize(("method", "locked"), [("async_lock", True), ("async_unlock", False)])
async def test_a_command_goes_through_the_librarys_lock_control(
    hass: HomeAssistant, method: str, locked: bool
) -> None:
    client = MagicMock()
    client.set_evse_lock = AsyncMock(
        return_value=PublishOutcome(
            state=PublishState.CONFIRMED, topic=LOCK_TARGET.topic, value="LOCKED"
        )
    )
    (lock,) = await _created(hass, _snapshot(), client)
    lock.hass = hass

    await getattr(lock, method)()

    client.set_evse_lock.assert_awaited_once_with(SETTABLE, locked)
    lock.coordinator.async_request_refresh.assert_awaited_once()


async def test_the_lock_is_unavailable_while_the_panel_is_offline(hass: HomeAssistant) -> None:
    (lock,) = await _created(hass, _snapshot())
    lock.coordinator.panel_offline = True

    assert lock.available is False
