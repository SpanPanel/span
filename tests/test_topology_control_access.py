"""`span_panel/panel_topology` tells the card whether the user asking may operate the panel.

`control_access` answers from two rules and restates neither: the control mode,
by `ControlPolicy.refusal_for`, which the gate refuses a command by, and then the
user's own Home Assistant permissions over the circuit controls, which Core
refuses an entity service call by before the gate is asked. The card disables
the panel's controls for a user it says may not operate them, so what it says
must not come apart from what either rule does: two tests here run the rule
itself -- the gate, and a service call through Core -- beside the topology and
hold them to one answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import CLIENT_ID, MockConfigEntry, MockUser
from pytest_homeassistant.typing import WebSocketGenerator
from span_panel_api import ControlCommand, FieldMetadata

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.control_access import PermissionRefusal, control_access
from custom_components.span_panel.control_gate import (
    CONTROL_CALLER,
    ControlAccessRefusal,
    ControlGate,
    ControlLock,
    ControlMode,
    ControlPolicy,
    async_bind_caller,
)
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.id_builder import build_select_unique_id, build_switch_unique_id
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.websocket import async_register_commands

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device

_SERIAL: Final = "sp3-synthetic-access-001"
_CIRCUIT_ID: Final = "circuit-3"
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}

_PRIORITY: Final = ControlCommand(
    device_id=_SERIAL,
    node_id=_CIRCUIT_ID,
    property_id="priority",
    value="OFF_GRID",
    topic=f"ebus/5/{_CIRCUIT_ID}/load-shed/priority/set",
)
"""A command the relay debounce never touches, so a refusal is only ever about who asked."""

_ADMINISTRATOR: Final = "administrator"
_ORDINARY: Final = "ordinary user"
_READ_ONLY: Final = "read-only user"

_GROUPS: Final = {
    _ADMINISTRATOR: GROUP_ID_ADMIN,
    _ORDINARY: GROUP_ID_USER,
    _READ_ONLY: GROUP_ID_READ_ONLY,
}
"""Each group a Home Assistant user can be in, by the name its tests carry."""

EXPECTED_REASON: Final[Mapping[tuple[ControlMode, str], str | None]] = {
    (ControlMode.ALL_USERS, _ADMINISTRATOR): None,
    (ControlMode.ALL_USERS, _ORDINARY): None,
    (ControlMode.ALL_USERS, _READ_ONLY): "read_only",
    (ControlMode.ADMIN_ONLY, _ADMINISTRATOR): None,
    (ControlMode.ADMIN_ONLY, _ORDINARY): "admin_only",
    # The mode's answer comes first, so a read-only user is told it rather than
    # their own permissions'.
    (ControlMode.ADMIN_ONLY, _READ_ONLY): "admin_only",
    (ControlMode.DISABLED, _ADMINISTRATOR): "control_disabled",
    (ControlMode.DISABLED, _ORDINARY): "control_disabled",
    (ControlMode.DISABLED, _READ_ONLY): "control_disabled",
}
"""`control_access.reason` by control mode and the group the asker is in, on a panel with circuit controls.

Written as the wire values the card reads, so renaming one fails here.
"""

GATE_REFUSAL: Final[Mapping[str | None, str | None]] = {
    None: None,
    "admin_only": "admin_only_control",
    "control_disabled": "control_disabled",
    # Core's reason, reported only where the mode has none, so the gate lets
    # the command through: Core refuses it before the gate is asked.
    "read_only": None,
}
"""The refusal the gate gives a command, by the `control_access.reason` the topology reports.

None is no refusal by the gate.
"""


@pytest.fixture(params=list(_GROUPS), ids=list(_GROUPS))
async def asker(hass: HomeAssistant, request: pytest.FixtureRequest) -> tuple[str, MockUser]:
    """Return a logged-in user from each group, with the group's name."""
    name: str = request.param
    group = await hass.auth.async_get_group(_GROUPS[name])
    assert group is not None
    user = MockUser(groups=[group]).add_to_hass(hass)
    assert user.is_admin is (name == _ADMINISTRATOR)
    return name, user


async def _load_panel(
    hass: HomeAssistant, mode: ControlMode, *, with_controls: bool = True
) -> tuple[MockConfigEntry, str, list[str]]:
    """Load a one-circuit panel whose control mode is `mode`.

    Returns its entry, its device's registry ID, and the entity ids of the
    circuit's relay switch and priority select, which are registered only
    `with_controls`.
    """
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number=_SERIAL,
        circuits={
            _CIRCUIT_ID: SpanCircuitSnapshotFactory.create(
                circuit_id=_CIRCUIT_ID,
                name="Synthetic Circuit",
                tabs=[3],
                breaker_rating_a=20.0,
                is_user_controllable=True,
                is_never_backup=False,
            )
        },
        main_breaker_rating_a=100,
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=_SERIAL)
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.package_rating_manager = manager
    # Home Assistant unloads the loaded entry as the test ends, which shuts the
    # coordinator down.
    coordinator.async_shutdown = AsyncMock()
    device_id = register_panel_device(hass, entry, _SERIAL)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=device_id,
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
        control_policy=replace(ControlPolicy.default(), mode=mode),
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)

    controls: list[str] = []
    if with_controls:
        entities = er.async_get(hass)
        for domain, unique_id in (
            ("switch", build_switch_unique_id(_SERIAL, _CIRCUIT_ID)),
            ("select", build_select_unique_id(_SERIAL, _CIRCUIT_ID)),
        ):
            registered = entities.async_get_or_create(
                domain, DOMAIN, unique_id, config_entry=entry, device_id=device_id
            )
            controls.append(registered.entity_id)

    async_register_commands(hass)
    return entry, device_id, controls


async def _topology(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, user: MockUser, device_id: str
) -> dict[str, object]:
    """Ask for the topology as `user` and return it."""
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    client = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh_token))
    await client.send_json_auto_id({"type": "span_panel/panel_topology", "device_id": device_id})
    reply = await client.receive_json()
    assert reply["success"] is True, reply
    topology = reply["result"]
    assert isinstance(topology, dict)
    return topology


async def _control_access(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, user: MockUser, device_id: str
) -> dict[str, object]:
    """Ask for the topology as `user` and return its `control_access`."""
    control_access_record = (await _topology(hass, hass_ws_client, user, device_id))[
        "control_access"
    ]
    assert isinstance(control_access_record, dict)
    return control_access_record


async def _gate_refusal(hass: HomeAssistant, entry: MockConfigEntry, user: MockUser) -> str | None:
    """Return the refusal the entry's gate gives `user`'s command, or None where it lets it through.

    The gate is built as setup builds it, from the policy the runtime data
    holds, with the lock open so the only refusal left is about who asked.
    """
    gate = ControlGate(hass, entry, entry.runtime_data.control_policy, ControlLock(armed=False))
    token = async_bind_caller(Context(user_id=user.id), "select.span_panel_synthetic_circuit")
    try:
        await gate.before_publish(_PRIORITY)
    except ServiceValidationError as refused:
        return refused.translation_key
    finally:
        CONTROL_CALLER.reset(token)
    return None


async def _core_refuses_control(hass: HomeAssistant, user: MockUser, entity_id: str) -> bool:
    """Whether Core refuses `user` a `switch.turn_on` of `entity_id` for want of control permission.

    The switch is only registered, never added, so a call Core lets through
    reaches no entity and does nothing: what is asked is Core's permission
    check, which comes first.
    """
    try:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
            context=Context(user_id=user.id),
        )
    except Unauthorized:
        return True
    return False


# ---------------------------------------------------------------------------
# What the topology answers
# ---------------------------------------------------------------------------


def test_every_mode_has_an_expected_answer_for_every_group() -> None:
    """A mode or group added later has to be given its answers here before the card can rely on them."""
    assert set(EXPECTED_REASON) == {(mode, group) for mode in ControlMode for group in _GROUPS}


@pytest.mark.parametrize("mode", list(ControlMode))
async def test_the_topology_says_whether_the_asker_may_operate_the_panel(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mode: ControlMode,
    asker: tuple[str, MockUser],
) -> None:
    """`may_operate` is true exactly where `reason` is null, and `reason` is the first refusal that applies."""
    group, user = asker
    _, device_id, _ = await _load_panel(hass, mode)

    reason = EXPECTED_REASON[(mode, group)]
    assert await _control_access(hass, hass_ws_client, user, device_id) == {
        "may_operate": reason is None,
        "reason": reason,
    }


async def test_a_panel_offering_no_circuit_control_reports_no_permission_refusal(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """With no relay switch or priority select there is nothing to disable, and nothing for Core to refuse."""
    group = await hass.auth.async_get_group(GROUP_ID_READ_ONLY)
    assert group is not None
    user = MockUser(groups=[group]).add_to_hass(hass)
    _, device_id, _ = await _load_panel(hass, ControlMode.ALL_USERS, with_controls=False)

    assert await _control_access(hass, hass_ws_client, user, device_id) == {
        "may_operate": True,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# The topology and the rules it reports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(ControlMode))
async def test_the_topology_and_the_gate_give_the_asker_the_same_answer(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mode: ControlMode,
    asker: tuple[str, MockUser],
) -> None:
    """The gate refuses the asker's command exactly where the topology reports one of its reasons, and for that reason."""
    _, user = asker
    entry, device_id, _ = await _load_panel(hass, mode)

    control_access_record = await _control_access(hass, hass_ws_client, user, device_id)
    refusal = await _gate_refusal(hass, entry, user)

    reason = control_access_record["reason"]
    assert reason is None or isinstance(reason, str)
    assert GATE_REFUSAL[reason] == refusal


async def test_the_topology_reports_read_only_exactly_where_core_refuses_control(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, asker: tuple[str, MockUser]
) -> None:
    """Core refuses the asker the panel's relay switch exactly where the topology says `read_only`.

    Under All users, where the mode refuses no one, so the permissions are the
    whole answer.
    """
    _, user = asker
    assert await async_setup_component(hass, "switch", {})
    _, device_id, controls = await _load_panel(hass, ControlMode.ALL_USERS)
    topology = await _topology(hass, hass_ws_client, user, device_id)
    circuit = topology["circuits"]
    assert isinstance(circuit, dict)
    switch = circuit[_CIRCUIT_ID]["entities"]["switch"]
    assert switch in controls

    control_access_record = topology["control_access"]
    assert isinstance(control_access_record, dict)
    assert (control_access_record["reason"] == "read_only") is await _core_refuses_control(
        hass, user, switch
    )


# ---------------------------------------------------------------------------
# The rule, directly
# ---------------------------------------------------------------------------


_ALL_USERS: Final = replace(ControlPolicy.default(), mode=ControlMode.ALL_USERS)
_SWITCH: Final = "switch.synthetic_circuit_breaker"
_SELECT: Final = "select.synthetic_circuit_priority"


def test_a_user_who_may_control_some_circuit_controls_may_operate_the_panel() -> None:
    """One flag for the whole panel: a user who can operate something is not told they can operate nothing."""
    user = MockUser()
    user.mock_policy({"entities": {"entity_ids": {_SWITCH: True}}})

    assert control_access(_ALL_USERS, user, [_SWITCH, _SELECT]) == {
        "may_operate": True,
        "reason": None,
    }


def test_a_user_who_may_read_but_not_control_them_is_read_only() -> None:
    """Reading a control is not operating it."""
    user = MockUser()
    user.mock_policy({"entities": {"entity_ids": {_SWITCH: {"read": True}}}})

    assert control_access(_ALL_USERS, user, [_SWITCH, _SELECT]) == {
        "may_operate": False,
        "reason": PermissionRefusal.READ_ONLY,
    }


@pytest.mark.parametrize("mode", list(ControlMode))
def test_an_administrator_is_never_read_only(mode: ControlMode) -> None:
    """Core does not check an administrator's permissions at all, so neither does this.

    The administrator here holds a policy that would refuse anyone else, which
    is what shows the check is skipped rather than passed.
    """
    user = MockUser(is_owner=True)
    user.mock_policy({"entities": {"entity_ids": {_SWITCH: {"read": True}}}})
    policy = replace(ControlPolicy.default(), mode=mode)

    reason = control_access(policy, user, [_SWITCH, _SELECT])["reason"]

    assert reason is not PermissionRefusal.READ_ONLY
    assert reason is (
        ControlAccessRefusal.CONTROL_DISABLED if mode is ControlMode.DISABLED else None
    )
