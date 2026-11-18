"""`span_panel/panel_topology` tells the card whether the user asking may operate the panel.

`control_access` is the control mode's answer for the WebSocket connection's
user, by `ControlPolicy.refusal_for` -- the rule the gate refuses a command by.
The card disables the panel's controls for a user it says may not operate them,
so what it says and what the gate does must not come apart: the last test runs
both, for every mode and every kind of user, and holds them to one answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant.common import CLIENT_ID, MockConfigEntry, MockUser
from pytest_homeassistant.typing import WebSocketGenerator
from span_panel_api import ControlCommand, FieldMetadata

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.control_gate import (
    CONTROL_CALLER,
    ControlGate,
    ControlLock,
    ControlMode,
    ControlPolicy,
    async_bind_caller,
)
from custom_components.span_panel.curation import CurationOverlay
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

EXPECTED_REASON: Final[Mapping[tuple[ControlMode, bool], str | None]] = {
    (ControlMode.ALL_USERS, True): None,
    (ControlMode.ALL_USERS, False): None,
    (ControlMode.ADMIN_ONLY, True): None,
    (ControlMode.ADMIN_ONLY, False): "admin_only",
    (ControlMode.DISABLED, True): "control_disabled",
    (ControlMode.DISABLED, False): "control_disabled",
}
"""`control_access.reason` by control mode and whether the asker is an administrator.

Written as the wire values the card reads, so renaming one fails here.
"""

GATE_REFUSAL: Final[Mapping[str | None, str | None]] = {
    None: None,
    "admin_only": "admin_only_control",
    "control_disabled": "control_disabled",
}
"""The refusal the gate gives a command, by the `control_access.reason` the topology reports.

None is no refusal on either side: the topology says the user may operate the
panel, and the gate lets the command through.
"""

_GROUPS: Final = {
    "administrator": GROUP_ID_ADMIN,
    "ordinary user": GROUP_ID_USER,
    "read-only user": GROUP_ID_READ_ONLY,
}


@pytest.fixture(params=list(_GROUPS.values()), ids=list(_GROUPS))
async def asker(hass: HomeAssistant, request: pytest.FixtureRequest) -> MockUser:
    """Return a logged-in user from each group a Home Assistant user can be in."""
    group = await hass.auth.async_get_group(request.param)
    assert group is not None
    user = MockUser(groups=[group]).add_to_hass(hass)
    assert user.is_admin is (request.param == GROUP_ID_ADMIN)
    return user


async def _load_panel(hass: HomeAssistant, mode: ControlMode) -> tuple[MockConfigEntry, str]:
    """Load a one-circuit panel whose control mode is `mode`, and return its entry and device's registry ID."""
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number=_SERIAL,
        circuits={
            _CIRCUIT_ID: SpanCircuitSnapshotFactory.create(
                circuit_id=_CIRCUIT_ID, name="Synthetic Circuit", tabs=[3], breaker_rating_a=20.0
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
    async_register_commands(hass)
    return entry, device_id


async def _control_access(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, user: MockUser, device_id: str
) -> dict[str, object]:
    """Ask for the topology as `user` and return its `control_access`."""
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    client = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh_token))
    await client.send_json_auto_id({"type": "span_panel/panel_topology", "device_id": device_id})
    reply = await client.receive_json()
    assert reply["success"] is True, reply
    control_access = reply["result"]["control_access"]
    assert isinstance(control_access, dict)
    return control_access


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


def test_every_mode_has_an_expected_answer_for_both_kinds_of_user() -> None:
    """A mode added later has to be given its answers here before the card can rely on them."""
    assert set(EXPECTED_REASON) == {
        (mode, is_admin) for mode in ControlMode for is_admin in (True, False)
    }


@pytest.mark.parametrize("mode", list(ControlMode))
async def test_the_topology_says_whether_the_asker_may_operate_the_panel(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mode: ControlMode, asker: MockUser
) -> None:
    """`may_operate` is true exactly where `reason` is null, and `reason` is the mode's answer for the asker."""
    _, device_id = await _load_panel(hass, mode)

    control_access = await _control_access(hass, hass_ws_client, asker, device_id)

    reason = EXPECTED_REASON[(mode, asker.is_admin)]
    assert control_access == {"may_operate": reason is None, "reason": reason}


@pytest.mark.parametrize("mode", list(ControlMode))
async def test_the_topology_and_the_gate_give_the_asker_the_same_answer(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mode: ControlMode, asker: MockUser
) -> None:
    """The gate refuses the asker's command exactly where the topology says they may not operate the panel, and for the reason it gives."""
    entry, device_id = await _load_panel(hass, mode)

    control_access = await _control_access(hass, hass_ws_client, asker, device_id)
    refusal = await _gate_refusal(hass, entry, asker)

    assert control_access["may_operate"] is (refusal is None)
    reason = control_access["reason"]
    assert reason is None or isinstance(reason, str)
    assert GATE_REFUSAL[reason] == refusal
