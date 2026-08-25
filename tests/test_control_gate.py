"""Tests for the control gate: who may operate the panel, and the record of it.

These exercise the interceptor directly rather than going through the platforms.
Every existing control test assigns a mock client to `coordinator.client`, and a
mock has no interceptor — so a platform test proves nothing about the gate, and
assuming otherwise would leave the gate untested while looking covered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from span_panel_api import ControlCommand, PublishOutcome, PublishState

from custom_components.span_panel.const import DOMAIN, EVENT_CONTROL_COMMAND
from custom_components.span_panel.control_gate import (
    CONTROL_CALLER,
    ControlGate,
    ControlLock,
    ControlMode,
    ControlPolicy,
    async_bind_caller,
    outcome_is_failure,
)
from homeassistant.core import Context, Event, HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

RELAY = ControlCommand(
    device_id="sp3-test-001",
    node_id="circuit-1",
    property_id="relay",
    value="CLOSED",
    topic="ebus/5/circuit-1/switch/relay/set",
)
PRIORITY = ControlCommand(
    device_id="sp3-test-001",
    node_id="circuit-1",
    property_id="priority",
    value="OFF_GRID",
    topic="ebus/5/circuit-1/load-shed/priority/set",
)

CONFIRMED = PublishOutcome(state=PublishState.CONFIRMED, topic=RELAY.topic, value="CLOSED")
VETOED = PublishOutcome(
    state=PublishState.FAILED, topic=RELAY.topic, value="CLOSED", detail="vetoed"
)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Span Panel", version=7)
    entry.add_to_hass(hass)
    return entry


def _gate(
    hass: HomeAssistant,
    *,
    mode: ControlMode = ControlMode.ALL_USERS,
    allow_contextless: bool = True,
    debounce: float = 0.0,
    lock: ControlLock | None = None,
) -> ControlGate:
    policy = ControlPolicy(
        mode=mode,
        allow_contextless=allow_contextless,
        lock_timeout_minutes=None,
        relay_debounce_seconds=debounce,
    )
    return ControlGate(hass, _entry(hass), policy, lock or ControlLock())


# ---------- authorization ----------


@pytest.mark.asyncio
async def test_all_users_lets_anyone_through(hass: HomeAssistant) -> None:
    """The default reproduces the behaviour every existing entry already has."""
    gate = _gate(hass)
    user = MockUser().add_to_hass(hass)

    token = async_bind_caller(Context(user_id=user.id), "switch.span_panel_kitchen")
    try:
        await gate.before_publish(RELAY)
    finally:
        CONTROL_CALLER.reset(token)


@pytest.mark.asyncio
async def test_admin_only_allows_an_admin(hass: HomeAssistant) -> None:
    """An administrator is exactly who admin_only is for."""
    gate = _gate(hass, mode=ControlMode.ADMIN_ONLY)
    admin = MockUser(is_owner=True).add_to_hass(hass)

    token = async_bind_caller(Context(user_id=admin.id), "switch.span_panel_kitchen")
    try:
        await gate.before_publish(RELAY)
    finally:
        CONTROL_CALLER.reset(token)


@pytest.mark.asyncio
async def test_admin_only_refuses_a_non_admin(hass: HomeAssistant) -> None:
    """Home Assistant's default user policy grants a non-admin control of everything."""
    gate = _gate(hass, mode=ControlMode.ADMIN_ONLY)
    user = MockUser().add_to_hass(hass)

    token = async_bind_caller(Context(user_id=user.id), "switch.span_panel_kitchen")
    try:
        with pytest.raises(ServiceValidationError) as err:
            await gate.before_publish(RELAY)
    finally:
        CONTROL_CALLER.reset(token)

    assert err.value.translation_key == "admin_only_control"


@pytest.mark.asyncio
async def test_admin_only_refuses_a_user_that_no_longer_resolves(
    hass: HomeAssistant,
) -> None:
    """A deleted user is not an administrator."""
    gate = _gate(hass, mode=ControlMode.ADMIN_ONLY)

    token = async_bind_caller(Context(user_id="gone"), "switch.span_panel_kitchen")
    try:
        with pytest.raises(ServiceValidationError) as err:
            await gate.before_publish(RELAY)
    finally:
        CONTROL_CALLER.reset(token)

    assert err.value.translation_key == "admin_only_control"


@pytest.mark.asyncio
async def test_admin_only_does_not_by_itself_refuse_a_contextless_caller(
    hass: HomeAssistant,
) -> None:
    """The two options are independent, and conflating them would be a surprise.

    `admin_only` is about *which user*; there is no user here to judge. Refusing
    an automation is what `allow_contextless_control` is for, and an installation
    that tightened one option must not silently get the other.
    """
    gate = _gate(hass, mode=ControlMode.ADMIN_ONLY, allow_contextless=True)

    token = async_bind_caller(Context(), "switch.span_panel_kitchen")
    try:
        await gate.before_publish(RELAY)
    finally:
        CONTROL_CALLER.reset(token)


@pytest.mark.asyncio
async def test_contextless_control_can_be_refused(hass: HomeAssistant) -> None:
    """An automation, script or integration calls with no user attached."""
    gate = _gate(hass, allow_contextless=False)

    token = async_bind_caller(Context(parent_id="automation-context"), "switch.x")
    try:
        with pytest.raises(ServiceValidationError) as err:
            await gate.before_publish(RELAY)
    finally:
        CONTROL_CALLER.reset(token)

    assert err.value.translation_key == "contextless_control_refused"


@pytest.mark.asyncio
async def test_an_unbound_caller_is_treated_as_contextless(hass: HomeAssistant) -> None:
    """This is the fail-closed default, and the reason the gate is not in the helper.

    A future control path that forgets `_async_guarded_control` arrives here with
    nothing bound. Permitting it would make the gate a suggestion.
    """
    gate = _gate(hass, allow_contextless=False)

    # Deliberately no `async_bind_caller`.
    with pytest.raises(ServiceValidationError) as err:
        await gate.before_publish(RELAY)

    assert err.value.translation_key == "contextless_control_refused"


# ---------- the control lock ----------


@pytest.mark.asyncio
async def test_an_armed_lock_refuses_every_path(hass: HomeAssistant) -> None:
    """Armed means armed, for an administrator as much as for anyone else."""
    lock = ControlLock()
    lock.arm()
    gate = _gate(hass, lock=lock)
    admin = MockUser(is_owner=True).add_to_hass(hass)

    for command in (RELAY, PRIORITY):
        token = async_bind_caller(Context(user_id=admin.id), "switch.x")
        try:
            with pytest.raises(ServiceValidationError) as err:
                await gate.before_publish(command)
        finally:
            CONTROL_CALLER.reset(token)
        assert err.value.translation_key == "control_locked"


def test_a_disarm_with_no_timeout_stays_disarmed() -> None:
    """Zero means manual relock, which is a real choice and not "immediately"."""
    lock = ControlLock()
    lock.arm()

    lock.disarm(0)

    assert lock.armed is False


def test_auto_relock_fires_once_the_window_passes() -> None:
    """Evaluated on read, so a suspended event loop cannot skip it."""
    lock = ControlLock()
    lock.arm()

    with patch("custom_components.span_panel.control_gate.time.monotonic", return_value=0.0):
        lock.disarm(5)
        assert lock.armed is False

    with patch("custom_components.span_panel.control_gate.time.monotonic", return_value=299.0):
        assert lock.armed is False

    with patch("custom_components.span_panel.control_gate.time.monotonic", return_value=301.0):
        assert lock.armed is True


# ---------- relay debounce ----------


@pytest.mark.asyncio
async def test_a_second_relay_command_inside_the_window_is_refused(
    hass: HomeAssistant,
) -> None:
    """Refused, not queued: a late relay command against a changed panel is worse."""
    gate = _gate(hass, debounce=2.0)

    with patch(
        "custom_components.span_panel.control_gate.time.monotonic", return_value=100.0
    ):
        await gate.before_publish(RELAY)

    with patch(
        "custom_components.span_panel.control_gate.time.monotonic", return_value=101.0
    ):
        with pytest.raises(ServiceValidationError) as err:
            await gate.before_publish(RELAY)

    assert err.value.translation_key == "relay_debounced"


@pytest.mark.asyncio
async def test_the_debounce_is_per_circuit(hass: HomeAssistant) -> None:
    """Two breakers are two independent physical things."""
    gate = _gate(hass, debounce=2.0)
    other = ControlCommand(
        device_id=RELAY.device_id,
        node_id="circuit-2",
        property_id="relay",
        value="OPEN",
        topic="ebus/5/circuit-2/switch/relay/set",
    )

    with patch(
        "custom_components.span_panel.control_gate.time.monotonic", return_value=100.0
    ):
        await gate.before_publish(RELAY)
        await gate.before_publish(other)


@pytest.mark.asyncio
async def test_the_debounce_does_not_touch_other_controls(hass: HomeAssistant) -> None:
    """A priority write is not a relay actuation and has no contactor to protect."""
    gate = _gate(hass, debounce=2.0)

    with patch(
        "custom_components.span_panel.control_gate.time.monotonic", return_value=100.0
    ):
        await gate.before_publish(PRIORITY)
        await gate.before_publish(PRIORITY)


@pytest.mark.asyncio
async def test_a_zero_debounce_disables_it(hass: HomeAssistant) -> None:
    """The option has to be switchable off, and zero is how."""
    gate = _gate(hass, debounce=0.0)

    await gate.before_publish(RELAY)
    await gate.before_publish(RELAY)


# ---------- the audit ----------


def _events(hass: HomeAssistant) -> list[Event]:
    captured: list[Event] = []
    hass.bus.async_listen(EVENT_CONTROL_COMMAND, captured.append)
    return captured


@pytest.mark.asyncio
async def test_a_successful_command_is_recorded_with_its_caller(
    hass: HomeAssistant,
) -> None:
    """The audit needs who, what and how it went."""
    gate = _gate(hass)
    captured = _events(hass)
    user = MockUser().add_to_hass(hass)

    token = async_bind_caller(Context(user_id=user.id), "switch.span_panel_kitchen")
    try:
        await gate.after_publish(RELAY, CONFIRMED)
    finally:
        CONTROL_CALLER.reset(token)
    await hass.async_block_till_done()

    assert len(captured) == 1
    data = captured[0].data
    assert data["user_id"] == user.id
    assert data["entity_id"] == "switch.span_panel_kitchen"
    assert data["command"] == "circuit-1/relay"
    assert data["value"] == "CLOSED"
    assert data["outcome"] == "confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [PublishState.CONFIRMED, PublishState.ACCEPTED, PublishState.UNCONFIRMED, PublishState.FAILED],
)
async def test_every_publish_state_reaches_the_audit_intact(
    hass: HomeAssistant, state: PublishState
) -> None:
    """Four states, not two — each sends an investigation somewhere different."""
    gate = _gate(hass)
    captured = _events(hass)

    await gate.after_publish(
        RELAY, PublishOutcome(state=state, topic=RELAY.topic, value="CLOSED")
    )
    await hass.async_block_till_done()

    assert captured[0].data["outcome"] == state.value


@pytest.mark.asyncio
async def test_a_refusal_is_recorded_with_its_reason(hass: HomeAssistant) -> None:
    """An audit that omitted the refusals would be worse than no audit.

    The reason survives from `before_publish` to `after_publish` because the
    library fires the callback as a task created from the same context.
    """
    gate = _gate(hass, mode=ControlMode.ADMIN_ONLY)
    captured = _events(hass)
    user = MockUser().add_to_hass(hass)

    token = async_bind_caller(Context(user_id=user.id), "switch.span_panel_kitchen")
    try:
        with pytest.raises(ServiceValidationError):
            await gate.before_publish(RELAY)
        # The library fires this itself on a veto, with FAILED and "vetoed".
        await gate.after_publish(RELAY, VETOED)
    finally:
        CONTROL_CALLER.reset(token)
    await hass.async_block_till_done()

    assert captured[0].data["outcome"] == "refused:admin_only_control"


@pytest.mark.asyncio
async def test_a_contextless_command_is_attributed_to_its_parent(
    hass: HomeAssistant,
) -> None:
    """A write with no user is attributed to *what* rather than left blank."""
    gate = _gate(hass)
    captured = _events(hass)

    token = async_bind_caller(Context(parent_id="automation-context"), "switch.x")
    try:
        await gate.after_publish(RELAY, CONFIRMED)
    finally:
        CONTROL_CALLER.reset(token)
    await hass.async_block_till_done()

    assert captured[0].data["user_id"] is None
    assert captured[0].data["parent_id"] == "automation-context"


# ---------- outcome classification ----------


@pytest.mark.parametrize(
    ("state", "is_failure"),
    [
        (PublishState.CONFIRMED, False),
        # Handed to the broker and possibly already acted on. Calling either of
        # these a failure would tell a user something untrue about their panel.
        (PublishState.ACCEPTED, False),
        (PublishState.UNCONFIRMED, False),
        (PublishState.FAILED, True),
    ],
)
def test_only_failed_means_it_will_never_be_delivered(
    state: PublishState, is_failure: bool
) -> None:
    """`FAILED` is the one state that is a promise about the future."""
    outcome = PublishOutcome(state=state, topic=RELAY.topic, value="CLOSED")

    assert outcome_is_failure(outcome) is is_failure


# ---------- policy resolution ----------


def test_the_defaults_change_nothing_for_an_existing_entry() -> None:
    """A silent tightening on upgrade is worse than the status quo."""
    policy = ControlPolicy.from_options({})

    assert policy.mode is ControlMode.ALL_USERS
    assert policy.allow_contextless is True
    assert policy.lock_enabled is False
    assert policy.relay_debounce_seconds == 2.0


def test_an_unrecognised_mode_falls_back_to_the_permissive_one() -> None:
    """A typo in `.storage` must not lock a household out of its own panel."""
    policy = ControlPolicy.from_options({"control_mode": "nonsense"})

    assert policy.mode is ControlMode.ALL_USERS


def test_a_negative_lock_timeout_turns_the_feature_off() -> None:
    """Absent and zero are different states, so the option needs a third value."""
    assert ControlPolicy.from_options({"control_lock_timeout": -1}).lock_enabled is False
    assert ControlPolicy.from_options({"control_lock_timeout": 0}).lock_enabled is True


@pytest.mark.asyncio
async def test_a_disabled_entry_creates_no_control_entities(hass: HomeAssistant) -> None:
    """No control entities, and no registry entries removed either."""
    from custom_components.span_panel.switch import async_setup_entry

    from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", is_user_controllable=True
    )
    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": circuit})
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.client = AsyncMock()

    entry = MagicMock()
    entry.title = "SPAN Panel"
    entry.data = {}
    entry.runtime_data = MagicMock(
        coordinator=coordinator,
        control_policy=ControlPolicy(
            mode=ControlMode.DISABLED,
            allow_contextless=True,
            lock_timeout_minutes=None,
            relay_debounce_seconds=2.0,
        ),
    )

    added: list[object] = []
    await async_setup_entry(hass, entry, lambda e, **kw: added.extend(e))

    assert added == []


# ---------- the lock entity's own gate ----------


def _lock_switch(hass: HomeAssistant, lock: ControlLock, timeout: float | None):
    """Build the control-lock switch against a mock coordinator."""
    from custom_components.span_panel.switch import SpanPanelControlLockSwitch

    from .factories import SpanPanelSnapshotFactory

    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={})
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}

    policy = ControlPolicy(
        mode=ControlMode.ALL_USERS,
        allow_contextless=True,
        lock_timeout_minutes=timeout,
        relay_debounce_seconds=0.0,
    )
    entity = SpanPanelControlLockSwitch(coordinator, lock, policy, "SPAN Panel")
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.mark.asyncio
async def test_anyone_may_arm_the_lock(hass: HomeAssistant) -> None:
    """Making a household safer should not require admin."""
    lock = ControlLock()
    entity = _lock_switch(hass, lock, 0)
    user = MockUser().add_to_hass(hass)
    entity._context = Context(user_id=user.id)

    await entity.async_turn_on()

    assert lock.armed is True
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_an_automation_may_arm_the_lock(hass: HomeAssistant) -> None:
    """Arming is the safe direction, so it is not gated on a user at all."""
    lock = ControlLock()
    entity = _lock_switch(hass, lock, 0)
    entity._context = Context()

    await entity.async_turn_on()

    assert lock.armed is True


@pytest.mark.asyncio
async def test_a_non_admin_cannot_disarm_the_lock(hass: HomeAssistant) -> None:
    """A lock a non-admin can disarm is not a lock."""
    lock = ControlLock()
    lock.arm()
    entity = _lock_switch(hass, lock, 0)
    user = MockUser().add_to_hass(hass)
    entity._context = Context(user_id=user.id)

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_turn_off()

    assert err.value.translation_key == "control_lock_disarm_requires_admin"
    assert lock.armed is True


@pytest.mark.asyncio
async def test_an_automation_can_never_disarm_the_lock(hass: HomeAssistant) -> None:
    """Refused regardless of `allow_contextless_control`.

    An automation that can unlock the panel defeats the purpose of the lock, so
    this is not the same decision as whether an automation may operate circuits.
    """
    lock = ControlLock()
    lock.arm()
    entity = _lock_switch(hass, lock, 0)
    entity._context = Context()

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_turn_off()

    assert err.value.translation_key == "control_lock_disarm_requires_user"
    assert lock.armed is True


@pytest.mark.asyncio
async def test_an_admin_can_disarm_the_lock(hass: HomeAssistant) -> None:
    """Making a household less safe is the direction that needs admin."""
    lock = ControlLock()
    lock.arm()
    entity = _lock_switch(hass, lock, 0)
    admin = MockUser(is_owner=True).add_to_hass(hass)
    entity._context = Context(user_id=admin.id)

    await entity.async_turn_off()

    assert lock.armed is False


# ---------- optimistic state ----------


@pytest.mark.asyncio
async def test_a_refused_relay_leaves_the_switch_where_it_was(
    hass: HomeAssistant,
) -> None:
    """A gate refusal must not leave the UI showing a position the relay never took.

    Nothing changed on the panel, so no coordinator update is coming to correct
    an optimistic write — it would simply stay wrong.
    """
    from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

    from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", relay_state="OPEN", is_user_controllable=True
    )
    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": circuit})
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.client = MagicMock()
    coordinator.client.set_circuit_relay = AsyncMock(
        side_effect=ServiceValidationError("refused")
    )

    entity = SpanPanelCircuitsSwitch(coordinator, "1", "Kitchen", "SPAN Panel")
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    before = entity.is_on

    with pytest.raises(ServiceValidationError):
        await entity.async_turn_on()

    assert entity.is_on == before
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_an_undelivered_relay_command_does_not_move_the_switch(
    hass: HomeAssistant,
) -> None:
    """`FAILED` is the one outcome that promises the command will never arrive."""
    from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

    from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", relay_state="OPEN", is_user_controllable=True
    )
    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": circuit})
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.client = MagicMock()
    coordinator.client.set_circuit_relay = AsyncMock(
        return_value=PublishOutcome(
            state=PublishState.FAILED,
            topic=RELAY.topic,
            value="CLOSED",
            detail="broker not connected; refused rather than queued",
        )
    )

    entity = SpanPanelCircuitsSwitch(coordinator, "1", "Kitchen", "SPAN Panel")
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    before = entity.is_on

    await entity.async_turn_on()

    assert entity.is_on == before
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_an_unconfirmed_relay_command_still_shows_the_requested_state(
    hass: HomeAssistant,
) -> None:
    """`UNCONFIRMED` most often means the relay was already in that position."""
    from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

    from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", relay_state="OPEN", is_user_controllable=True
    )
    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": circuit})
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.client = MagicMock()
    coordinator.client.set_circuit_relay = AsyncMock(
        return_value=PublishOutcome(
            state=PublishState.UNCONFIRMED,
            topic=RELAY.topic,
            value="CLOSED",
            no_op=True,
            detail="the property already reports this value",
        )
    )

    entity = SpanPanelCircuitsSwitch(coordinator, "1", "Kitchen", "SPAN Panel")
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on()

    assert entity.is_on is True
    entity.async_write_ha_state.assert_called_once()


# ---------- the logbook rendering ----------


def _describe(hass: HomeAssistant, data: dict[str, object]) -> dict[str, str]:
    """Run the registered describer over one event's data."""
    from custom_components.span_panel.logbook import async_describe_events

    described: dict[str, object] = {}

    def register(domain: str, event: str, describer: object) -> None:
        described["fn"] = describer

    async_describe_events(hass, register)  # type: ignore[arg-type]
    describer = described["fn"]
    assert callable(describer)
    return describer(Event(EVENT_CONTROL_COMMAND, data))


@pytest.mark.asyncio
async def test_the_logbook_names_the_user_and_the_outcome(hass: HomeAssistant) -> None:
    """A logbook line has to say who and how it went, or it is not worth writing."""
    described = _describe(
        hass,
        {
            "user_id": "abc123",
            "parent_id": None,
            "entity_id": "switch.span_panel_kitchen",
            "command": "circuit-1/relay",
            "value": "CLOSED",
            "outcome": "confirmed",
        },
    )

    assert "circuit-1/relay" in described["message"]
    assert "abc123" in described["message"]
    assert "confirmed it" in described["message"]
    assert described["entity_id"] == "switch.span_panel_kitchen"


@pytest.mark.asyncio
async def test_the_logbook_does_not_call_unconfirmed_a_failure(
    hass: HomeAssistant,
) -> None:
    """The distinction is the reason `PublishState` has four members."""
    described = _describe(
        hass,
        {
            "user_id": None,
            "parent_id": None,
            "command": "circuit-1/relay",
            "value": "CLOSED",
            "outcome": "unconfirmed",
        },
    )

    assert "fail" not in described["message"].lower()
    assert "no acknowledgement" in described["message"]


@pytest.mark.asyncio
async def test_the_logbook_explains_a_refusal(hass: HomeAssistant) -> None:
    """A refused command that read as a plain failure would send someone hunting."""
    described = _describe(
        hass,
        {
            "user_id": None,
            "parent_id": None,
            "command": "circuit-1/relay",
            "value": "CLOSED",
            "outcome": "refused:relay_debounced",
        },
    )

    assert "operated moments ago" in described["message"]
