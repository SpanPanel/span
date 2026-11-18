"""The control lock's state across a reload and an id edit, and what it is before there is one.

Separate from `test_control_gate.py` because these go through a real config
entry setup and a real `async_reload`. The gate tests construct the switch
against a mock coordinator and never add it to Home Assistant, so they cannot
see the entity lifecycle at all -- and the lifecycle is the whole subject here:
`ControlLock` is rebuilt by every `async_setup_entry`, and the only thing that
knows what the last run left behind is the entity.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import (
    ATTR_RESTORED,
    CONF_HOST,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockUser,
    mock_restore_cache_with_extra_data,
)

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
)
from custom_components.span_panel.control_gate import ControlLock, ControlMode
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import CONTROL_LOCK_TIMEOUT, CONTROL_MODE
from custom_components.span_panel.switch import SpanPanelControlLockSwitch

from .common import async_fire_time_changed
from .factories import SpanPanelSnapshotFactory
from .helpers import ensure_device_registered_stand_in, watch_add_and_remove

LOCK_ENTITY = "switch.span_panel_control_lock"
EDITED_LOCK_ENTITY = "switch.panel_control_lock"
SERIAL = "sp3-lock-001"


def _entry(timeout: float, mode: ControlMode | None) -> MockConfigEntry:
    """Build a v2 entry with the control-lock feature configured to `timeout`.

    `mode` None leaves the control mode unset, as most entries have it.
    """
    options: dict[str, object] = {CONTROL_LOCK_TIMEOUT: timeout}
    if mode is not None:
        options[CONTROL_MODE] = mode.value
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.168.1.50",
            CONF_EBUS_BROKER_HOST: "span-panel.local",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_EBUS_BROKER_PASSWORD: "hunter2-synthetic",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_HTTP_PORT: 80,
        },
        options=options,
        entry_id="entry-control-lock",
        title="SPAN Panel",
        unique_id=SERIAL,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )


@contextlib.asynccontextmanager
async def _panel(
    hass: HomeAssistant, *, timeout: float, mode: ControlMode | None = None
) -> AsyncIterator[MockConfigEntry]:
    """Set the entry up for real, with only the switch platform forwarded.

    The transport and the coordinator are stubs -- neither has anything to do
    with the lock -- but the entry setup, the platform forward, the entity
    registry and the unload path are all the real ones, because a reload is
    exactly those four things.
    """
    entry = _entry(timeout, mode)
    entry.add_to_hass(hass)

    snapshot = SpanPanelSnapshotFactory.create(serial_number=SERIAL, circuits={})
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    coordinator = MagicMock()
    # As the real coordinator is: the panel's device info is built from it.
    coordinator.config_entry = entry
    coordinator.data = snapshot
    coordinator.transport_dead = False
    coordinator.current_monitor = None
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.async_shutdown = AsyncMock()

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(side_effect=ensure_device_registered_stand_in),
        ),
        patch("custom_components.span_panel.PLATFORMS", [Platform.SWITCH]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        try:
            yield entry
        finally:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def _lock(entry: MockConfigEntry) -> ControlLock:
    """Return the live lock object this entry's gate consults."""
    lock = entry.runtime_data.control_lock
    assert isinstance(lock, ControlLock)
    return lock


def _lock_switch(hass: HomeAssistant) -> SpanPanelControlLockSwitch:
    """Return the live switch entity that arms and disarms the lock."""
    (platform,) = async_get_platforms(hass, DOMAIN)
    switch = platform.entities[LOCK_ENTITY]
    assert isinstance(switch, SpanPanelControlLockSwitch)
    return switch


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def _arm(hass: HomeAssistant) -> None:
    """Arm through the service, so the entity writes its own state."""
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": LOCK_ENTITY}, blocking=True
    )


async def _disarm(hass: HomeAssistant) -> None:
    """Disarm through the service as an administrator, the only caller allowed to."""
    admin = MockUser(is_owner=True).add_to_hass(hass)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": LOCK_ENTITY},
        blocking=True,
        context=Context(user_id=admin.id),
    )


# ---------- across a reload ----------


@pytest.mark.asyncio
async def test_an_armed_lock_is_still_armed_after_a_reload(hass: HomeAssistant) -> None:
    """A reload is not consent to operate the panel.

    Every options save, every `rotate_credentials`, every circuit rename that
    asks for a reload and every Home Assistant restart runs `async_setup_entry`
    again. A lock that came back disarmed would have the household's second
    factor removed by routine maintenance, with the switch showing off and no
    notice that anything had changed.
    """
    async with _panel(hass, timeout=0) as entry:
        await _arm(hass)
        assert _lock(entry).armed is True

        await _reload(hass, entry)

        assert _lock(entry).armed is True
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON


@pytest.mark.asyncio
async def test_a_disarmed_lock_is_still_disarmed_after_a_reload(hass: HomeAssistant) -> None:
    """The restore has to carry both answers, not only the safe one.

    Re-arming on every reload would be its own defect: an options save would
    lock a household out of its own circuits mid-task, and the alarm-panel
    metaphor only works if the disarmed state is as durable as the armed one.
    """
    async with _panel(hass, timeout=0) as entry:
        await _disarm(hass)
        assert _lock(entry).armed is False

        await _reload(hass, entry)

        assert _lock(entry).armed is False
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_OFF


@pytest.mark.asyncio
async def test_a_pending_auto_relock_survives_a_reload(hass: HomeAssistant) -> None:
    """A reload must not drop the countdown, nor turn it into an immediate re-arm."""
    async with _panel(hass, timeout=30) as entry:
        await _disarm(hass)
        assert _lock(entry).relock_in_seconds == pytest.approx(1800, abs=5)

        await _reload(hass, entry)

        assert _lock(entry).armed is False
        assert _lock(entry).relock_in_seconds == pytest.approx(1800, abs=5)


# ---------- across an entity-id edit ----------


@pytest.mark.asyncio
async def test_an_id_edit_leaves_the_lock_open_and_its_relock_due(hass: HomeAssistant) -> None:
    """Core changes the switch's id in place: nothing is restored and the countdown runs on.

    Removed and added again, the switch read its armed state back from what the
    removal had just stored and scheduled a fresh write for the deadline. In
    place it is the same switch: the lock stays as it was, the relock write it
    already scheduled falls due when it always would have, and that write lands
    under the new id, because the switch writes under the id it has by then.
    """
    async with _panel(hass, timeout=1) as entry:
        await _disarm(hass)
        due = _lock(entry).relock_in_seconds
        assert due is not None
        assert due == pytest.approx(60, abs=5)

        with watch_add_and_remove(_lock_switch(hass)) as calls:
            er.async_get(hass).async_update_entity(LOCK_ENTITY, new_entity_id=EDITED_LOCK_ENTITY)
            await hass.async_block_till_done()

        assert calls.count == (0, 0)
        assert _lock(entry).armed is False
        assert _lock(entry).relock_in_seconds == pytest.approx(due, abs=5)
        assert hass.states.get(LOCK_ENTITY) is None
        state = hass.states.get(EDITED_LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_OFF

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=90))
        await hass.async_block_till_done()

        assert _lock(entry).armed is True
        state = hass.states.get(EDITED_LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON
        assert hass.states.get(LOCK_ENTITY) is None


# ---------- the state before there is one to restore ----------


@pytest.mark.asyncio
async def test_the_lock_starts_armed_when_the_feature_is_first_enabled(
    hass: HomeAssistant,
) -> None:
    """The option text promises an armed lock, and nothing else can deliver it.

    "0 keeps the lock armed until someone disarms it" is only true if turning
    the feature on arms it. A lock that arrives disarmed protects nothing until
    the user notices it is off and arms it by hand -- and the user turned the
    option on precisely because they expected not to have to.
    """
    async with _panel(hass, timeout=0) as entry:
        assert _lock(entry).armed is True
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON


# ---------- what a restart does to a countdown ----------


@pytest.mark.asyncio
async def test_a_restart_resumes_the_remaining_window_rather_than_a_fresh_one(
    hass: HomeAssistant,
) -> None:
    """Ten minutes left before the restart is ten minutes left after it.

    `_relock_at` is a monotonic reading and cannot outlive the process, so the
    entity stores the deadline as a wall-clock instant and the restore converts
    it back. Restarting the full window instead would let an entry that saves
    its options every few minutes hold the lock open indefinitely, which is the
    opposite of what `ControlLock.armed` documents.
    """
    deadline = dt_util.utcnow() + timedelta(minutes=10)
    mock_restore_cache_with_extra_data(
        hass,
        ((State(LOCK_ENTITY, STATE_OFF), {"relock_at": deadline.isoformat()}),),
    )

    async with _panel(hass, timeout=30) as entry:
        assert _lock(entry).armed is False
        assert _lock(entry).relock_in_seconds == pytest.approx(600, abs=5)


@pytest.mark.asyncio
async def test_a_window_that_closed_while_home_assistant_was_down_comes_back_armed(
    hass: HomeAssistant,
) -> None:
    """The lock was asked to re-arm at a moment that has already passed."""
    deadline = dt_util.utcnow() - timedelta(minutes=1)
    mock_restore_cache_with_extra_data(
        hass,
        ((State(LOCK_ENTITY, STATE_OFF), {"relock_at": deadline.isoformat()}),),
    )

    async with _panel(hass, timeout=30) as entry:
        assert _lock(entry).armed is True
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON


@pytest.mark.asyncio
async def test_a_disarm_with_no_readable_deadline_comes_back_armed(
    hass: HomeAssistant,
) -> None:
    """An entry that auto-relocks cannot restore a window it cannot read.

    A record written before this release, or edited by hand, says the lock was
    open and says nothing about how much of its window was left. Granting a
    fresh one would hand back more open time than the previous run had, on no
    evidence at all — and a restart is not consent to operate the panel.
    """
    mock_restore_cache_with_extra_data(
        hass,
        ((State(LOCK_ENTITY, STATE_OFF), {"relock_at": "not-a-time"}),),
    )

    async with _panel(hass, timeout=30) as entry:
        assert _lock(entry).armed is True
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON


@pytest.mark.asyncio
async def test_a_disarm_with_no_countdown_configured_stays_open(
    hass: HomeAssistant,
) -> None:
    """A zero timeout has no window to lose, so there is nothing to fail closed on.

    "0 keeps the lock disarmed until someone arms it" is the option's own
    promise, and re-arming across a restart would break it for every entry that
    never asked for a countdown.
    """
    mock_restore_cache_with_extra_data(
        hass,
        ((State(LOCK_ENTITY, STATE_OFF), {}),),
    )

    async with _panel(hass, timeout=0) as entry:
        assert _lock(entry).armed is False
        assert _lock(entry).relock_in_seconds is None


# ---------- the auto-relock is visible when it happens ----------


@pytest.mark.asyncio
async def test_the_auto_relock_writes_the_state_when_it_falls_due(
    hass: HomeAssistant,
) -> None:
    """Otherwise the switch shows off until something else happens to read it.

    Auto-relock was evaluated only on read, and nothing reads a switch on a
    schedule. The panel was locked and the UI said it was not -- for hours, if
    nothing touched the entity.
    """
    async with _panel(hass, timeout=1) as entry:
        await _disarm(hass)
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_OFF

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=90))
        await hass.async_block_till_done()

        assert _lock(entry).armed is True
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON


# ---------- a dead transport does not take the lock with it ----------


@pytest.mark.asyncio
async def test_the_lock_stays_operable_when_the_transport_dies(hass: HomeAssistant) -> None:
    """Arming is local, so it is answerable with no panel at the other end.

    Every other control goes unavailable once the transport is gone, and should:
    they report something read from the panel. This one reports the state of an
    in-process object the interceptor consults, and the moment a transport dies
    is a plausible moment to want the lock shut before it comes back.

    Inheriting the base class's transport probe cost more than a greyed-out
    tile: core's entity-service helper skips an unavailable entity outright, so
    `switch.turn_on` was dropped without an error and the lock stayed open. That
    is what this asserts — the arm lands, not merely that the state renders.
    """
    async with _panel(hass, timeout=0) as entry:
        await _disarm(hass)
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_OFF

        entry.runtime_data.coordinator.transport_dead = True

        await _arm(hass)

        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_ON
        assert _lock(entry).armed is True


# ---------- while control is disabled ----------


async def _set_mode(hass: HomeAssistant, entry: MockConfigEntry, mode: ControlMode) -> None:
    """Change the control mode as an options save does, and let the reload it asks for run."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONTROL_MODE: mode.value}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.control_policy.mode is mode


def _registry_row(hass: HomeAssistant) -> er.RegistryEntry:
    row = er.async_get(hass).async_get(LOCK_ENTITY)
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_disabling_control_stops_creating_the_lock_switch_and_keeps_its_registry_entry(
    hass: HomeAssistant,
) -> None:
    """The lock switch is a control entity like the others, so `disabled` treats it as one.

    Not created, and not removed: its registry entry stays, with its entity id
    and unique id, for when control comes back. The lock object the gate holds
    is then disarmed, as for an entry with no lock, because the gate refuses
    every command under `disabled` before it consults the lock.
    """
    async with _panel(hass, timeout=0, mode=ControlMode.ALL_USERS) as entry:
        before = _registry_row(hass)

        await _set_mode(hass, entry, ControlMode.DISABLED)

        after = _registry_row(hass)
        assert (after.id, after.entity_id, after.unique_id) == (
            before.id,
            before.entity_id,
            before.unique_id,
        )
        assert after.unique_id == f"span_{SERIAL}_control_lock"
        # What Core shows for a registry entry with no entity behind it, as it
        # does for every other control under `disabled`.
        state = hass.states.get(LOCK_ENTITY)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE
        assert state.attributes.get(ATTR_RESTORED) is True
        assert _lock(entry).armed is False


@pytest.mark.parametrize(
    ("timeout", "leave_it", "state", "relock_in_seconds"),
    [
        pytest.param(0, _arm, STATE_ON, None, id="armed"),
        pytest.param(0, _disarm, STATE_OFF, None, id="disarmed"),
        pytest.param(30, _disarm, STATE_OFF, 1800, id="disarmed_with_a_relock_due"),
    ],
)
@pytest.mark.asyncio
async def test_turning_control_back_on_brings_the_same_lock_switch_back_as_it_was(
    hass: HomeAssistant,
    timeout: float,
    leave_it: Callable[[HomeAssistant], Awaitable[None]],
    state: str,
    relock_in_seconds: float | None,
) -> None:
    """The same registry entry, entity id, name and area, armed or disarmed as it was left, with any relock still due.

    Home Assistant keeps the state of an entity that stopped being created, and
    the switch restores from it exactly as it does across a reload.
    """
    async with _panel(hass, timeout=timeout, mode=ControlMode.ALL_USERS) as entry:
        await leave_it(hass)
        garage = ar.async_get(hass).async_create("Garage")
        er.async_get(hass).async_update_entity(LOCK_ENTITY, name="Breaker lock", area_id=garage.id)
        before = _registry_row(hass)

        await _set_mode(hass, entry, ControlMode.DISABLED)
        await _set_mode(hass, entry, ControlMode.ALL_USERS)

        after = _registry_row(hass)
        assert (after.id, after.entity_id, after.unique_id, after.name, after.area_id) == (
            before.id,
            before.entity_id,
            before.unique_id,
            "Breaker lock",
            garage.id,
        )
        restored = hass.states.get(LOCK_ENTITY)
        assert restored is not None
        assert restored.state == state
        assert restored.attributes.get(ATTR_RESTORED) is None
        assert _lock(entry).armed is (state == STATE_ON)
        assert _lock(entry).relock_in_seconds == (
            None if relock_in_seconds is None else pytest.approx(relock_in_seconds, abs=5)
        )


@pytest.mark.asyncio
async def test_a_restart_while_control_is_disabled_keeps_the_lock_state_for_later(
    hass: HomeAssistant,
) -> None:
    """A lock left disarmed before a restart with control off is still disarmed once control is back."""
    mock_restore_cache_with_extra_data(
        hass,
        ((State(LOCK_ENTITY, STATE_OFF), {}),),
    )

    async with _panel(hass, timeout=0, mode=ControlMode.DISABLED) as entry:
        assert _lock(entry).armed is False

        await _set_mode(hass, entry, ControlMode.ALL_USERS)

        assert _lock(entry).armed is False
        restored = hass.states.get(LOCK_ENTITY)
        assert restored is not None
        assert restored.state == STATE_OFF
