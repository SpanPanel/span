"""Who may send each of the integration's WebSocket commands.

`span_panel/panel_topology` answers every logged-in user. The sidebar dashboard
and the card read it for whoever is viewing them, and what it carries -- the
panel's layout, what the panel publishes about each circuit and device, the
ratings current monitoring judges each circuit against, and the registry IDs
that tie them together -- holds no host, credential or certificate. The adopted
commands decide what an adopted entity means, an administrator's act, so anyone
else is refused with Core's `unauthorized` before the panel is resolved.

The commands are classified here by name, and the integration's registered
commands must match the classification exactly, so a new command cannot be
added without deciding which rule it follows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant.common import CLIENT_ID, MockConfigEntry, MockUser
from pytest_homeassistant.typing import MockHAClientWebSocket, WebSocketGenerator
from span_panel_api import FieldMetadata

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.id_builder import (
    build_bess_unique_id,
    build_circuit_unique_id,
    build_switch_unique_id,
)
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.util import bess_device_info
from custom_components.span_panel.websocket import async_register_commands

from .factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanPanelSnapshotFactory,
    pv_binding_for,
)
from .helpers import PANEL_NAME, register_circuit_device, register_panel_device

type CommandFields = Callable[[str], Mapping[str, object]]
"""A command's fields, apart from `type`, for the panel whose device registry ID it is given."""

_SERIAL: Final = "sp3-synthetic-ws-001"
_CIRCUIT_ID: Final = "circuit-2"
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}

ADMINISTRATOR_ONLY: Final[Mapping[str, CommandFields]] = {
    "span_panel/adopted/list": lambda device_id: {"device_id": device_id},
    "span_panel/adopted/curate": lambda device_id: {
        "device_id": device_id,
        "key": "synthetic-key",
        "record": {},
    },
}
"""The commands only an administrator may send.

Each with fields its schema accepts, so a refusal comes from the rule rather
than from the schema, which Core applies first.
"""

OPEN_TO_EVERY_USER: Final[Mapping[str, CommandFields]] = {
    "span_panel/panel_topology": lambda device_id: {"device_id": device_id},
}
"""The commands every logged-in user may send."""


async def _non_admin(hass: HomeAssistant, group_id: str) -> MockUser:
    """Add a logged-in user in `group_id`, one of the groups a user who is not an administrator is in."""
    group = await hass.auth.async_get_group(group_id)
    assert group is not None
    user = MockUser(groups=[group]).add_to_hass(hass)
    assert not user.is_admin
    return user


@pytest.fixture(params=[GROUP_ID_USER, GROUP_ID_READ_ONLY], ids=["ordinary user", "read-only user"])
async def non_admin_user(hass: HomeAssistant, request: pytest.FixtureRequest) -> MockUser:
    """Return a logged-in user who is not an administrator, from each group such a user can be in."""
    return await _non_admin(hass, request.param)


async def _client_for(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, user: MockUser
) -> MockHAClientWebSocket:
    """Open a WebSocket connection authenticated as `user`."""
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return await hass_ws_client(hass, hass.auth.async_create_access_token(refresh_token))


async def _send(
    client: MockHAClientWebSocket, command: str, fields: Mapping[str, object]
) -> dict[str, object]:
    """Send one command and return Core's reply to it."""
    await client.send_json_auto_id({"type": command, **fields})
    reply: dict[str, object] = await client.receive_json()
    return reply


def _error_code(reply: Mapping[str, object]) -> object:
    """Return the code of a refused command's error."""
    error = reply["error"]
    assert isinstance(error, dict)
    return error["code"]


@pytest.fixture
async def panel_device_id(hass: HomeAssistant) -> str:
    """Load a panel and return its device's registry ID, with the commands registered.

    One circuit on a device of its own, with its power sensor and breaker
    switch, and a battery sub-device with a sensor, so a topology answer has a
    circuit, its entities and a sub-device in it to differ on.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=_CIRCUIT_ID,
        name="Synthetic Circuit",
        tabs=[2],
        breaker_rating_a=20.0,
        is_user_controllable=True,
    )
    battery = SpanBatterySnapshotFactory.create()
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number=_SERIAL,
        circuits={_CIRCUIT_ID: circuit},
        battery=battery,
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
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)

    circuit_device = register_circuit_device(
        hass, entry, _SERIAL, circuit, panel_device_id=device_id
    )
    battery_device = dr.async_get(hass).async_get_or_create_from_info(
        config_entry_id=entry.entry_id,
        device_info=bess_device_info(_SERIAL, battery, PANEL_NAME, panel_device_id=device_id),
    )
    entities = er.async_get(hass)
    for domain, unique_id, on_device in (
        ("sensor", build_circuit_unique_id(_SERIAL, _CIRCUIT_ID, "instantPowerW"), circuit_device),
        ("switch", build_switch_unique_id(_SERIAL, _CIRCUIT_ID), circuit_device),
        ("sensor", build_bess_unique_id(_SERIAL, "battery_level"), battery_device),
    ):
        entities.async_get_or_create(
            domain, DOMAIN, unique_id, config_entry=entry, device_id=on_device.id
        )

    async_register_commands(hass)
    return device_id


# ---------------------------------------------------------------------------
# The classification, against what the integration registers
# ---------------------------------------------------------------------------


def test_the_classifications_do_not_overlap() -> None:
    """No command is named under both rules."""
    assert not set(ADMINISTRATOR_ONLY) & set(OPEN_TO_EVERY_USER)


def _registered_commands(hass: HomeAssistant) -> set[str]:
    """Return the type of every WebSocket command registered on `hass`, whoever registered it."""
    handlers = hass.data.get(websocket_api.DOMAIN, {})
    assert isinstance(handlers, dict)
    commands: set[str] = set()
    for command in handlers:
        assert isinstance(command, str)
        commands.add(command)
    return commands


async def test_every_registered_command_is_classified(hass: HomeAssistant) -> None:
    """Each command the integration registers is named in exactly one classification.

    Every command its registration adds is compared, whatever its namespace, so
    one registered under a mistyped prefix is caught rather than filtered out.
    Whatever was registered before -- by Core, if the fixture registers
    anything -- is set aside, and none of it may be one of the classified
    commands, or the comparison would miss that command.
    """
    classified = set(ADMINISTRATOR_ONLY) | set(OPEN_TO_EVERY_USER)
    before = _registered_commands(hass)
    assert before.isdisjoint(classified)

    async_register_commands(hass)

    assert _registered_commands(hass) - before == classified


# ---------------------------------------------------------------------------
# Each rule, on a loaded panel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "fields"), ADMINISTRATOR_ONLY.items(), ids=list(ADMINISTRATOR_ONLY)
)
async def test_an_administrator_only_command_refuses_a_non_admin(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    panel_device_id: str,
    non_admin_user: MockUser,
    command: str,
    fields: CommandFields,
) -> None:
    """A non-admin is refused with Core's `unauthorized`, though the panel it names is loaded."""
    client = await _client_for(hass, hass_ws_client, non_admin_user)

    reply = await _send(client, command, fields(panel_device_id))

    assert reply["success"] is False
    assert _error_code(reply) == websocket_api.ERR_UNAUTHORIZED


@pytest.mark.parametrize(
    ("command", "fields"), OPEN_TO_EVERY_USER.items(), ids=list(OPEN_TO_EVERY_USER)
)
async def test_an_open_command_answers_a_non_admin_as_it_answers_an_administrator(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    panel_device_id: str,
    non_admin_user: MockUser,
    command: str,
    fields: CommandFields,
) -> None:
    """The answer does not depend on who asks: a non-admin gets exactly what an administrator gets."""
    as_admin = await _send(await hass_ws_client(hass), command, fields(panel_device_id))
    as_non_admin = await _send(
        await _client_for(hass, hass_ws_client, non_admin_user), command, fields(panel_device_id)
    )

    assert as_admin["success"] is True
    assert as_non_admin["success"] is True
    assert as_non_admin["result"] == as_admin["result"]


async def test_a_non_admin_reads_the_panels_topology(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    panel_device_id: str,
    non_admin_user: MockUser,
) -> None:
    """The topology a non-admin reads is the loaded panel's: its circuit, that circuit's entities and its battery."""
    client = await _client_for(hass, hass_ws_client, non_admin_user)

    reply = await _send(client, "span_panel/panel_topology", {"device_id": panel_device_id})

    assert reply["success"] is True
    topology = reply["result"]
    assert isinstance(topology, dict)
    assert topology["serial"] == _SERIAL
    circuit = topology["circuits"][_CIRCUIT_ID]
    assert circuit["name"] == "Synthetic Circuit"
    assert set(circuit["entities"]) == {"power", "switch"}
    assert [device["type"] for device in topology["sub_devices"].values()] == ["bess"]
