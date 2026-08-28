"""The control lock's state across a reload, and what it is before there is one.

Separate from `test_control_gate.py` because these go through a real config
entry setup and a real `async_reload`. The gate tests construct the switch
against a mock coordinator and never add it to Home Assistant, so they cannot
see the entity lifecycle at all -- and the lifecycle is the whole subject here:
`ControlLock` is rebuilt by every `async_setup_entry`, and the only thing that
knows what the last run left behind is the entity.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import contextlib
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, Platform
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
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
from custom_components.span_panel.control_gate import ControlLock
from custom_components.span_panel.migrations import CURRENT_CONFIG_VERSION
from custom_components.span_panel.options import CONTROL_LOCK_TIMEOUT

from .common import async_fire_time_changed
from .factories import SpanPanelSnapshotFactory

LOCK_ENTITY = "switch.span_panel_control_lock"
SERIAL = "sp3-lock-001"


def _entry(timeout: float) -> MockConfigEntry:
    """Build a v2 entry with the control-lock feature configured to `timeout`."""
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
        options={CONTROL_LOCK_TIMEOUT: timeout},
        entry_id="entry-control-lock",
        title="SPAN Panel",
        unique_id=SERIAL,
        version=CURRENT_CONFIG_VERSION,
    )


@contextlib.asynccontextmanager
async def _panel(hass: HomeAssistant, *, timeout: float) -> AsyncIterator[MockConfigEntry]:
    """Set the entry up for real, with only the switch platform forwarded.

    The transport and the coordinator are stubs -- neither has anything to do
    with the lock -- but the entry setup, the platform forward, the entity
    registry and the unload path are all the real ones, because a reload is
    exactly those four things.
    """
    entry = _entry(timeout)
    entry.add_to_hass(hass)

    snapshot = SpanPanelSnapshotFactory.create(serial_number=SERIAL, circuits={})
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.transport_dead = False
    coordinator.current_monitor = None
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.async_shutdown = AsyncMock()

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
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
