"""Helper functions for testing the Span Panel integration."""

from collections.abc import Awaitable, Callable, Mapping
import datetime
import inspect
from types import ModuleType
from typing import Any, Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util.dt import utcnow
import probatio as vol
import pytest
from pytest_homeassistant.common import MockConfigEntry
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel import package_ratings
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.util import circuit_device_info

type WebSocketHandler = Callable[[HomeAssistant, MagicMock, dict[str, Any]], Awaitable[None]]
"""A websocket command's own coroutine, as a test awaits it with a mock connection."""


def unwrap_websocket_command(command: Callable[..., object]) -> WebSocketHandler:
    """Unwrap a websocket command to the coroutine its decorators wrap.

    `@websocket_command`, `@require_admin` and `@async_response` each wrap the
    handler, so a test that calls it directly with a mock connection has to get
    beneath them.
    """
    handler: WebSocketHandler = inspect.unwrap(command, stop=inspect.iscoroutinefunction)
    return handler


def shown_values(schema: vol.Schema) -> dict[str, object]:
    """Return the values a form shows: what saving it unchanged submits."""
    shown: dict[str, object] = {}
    for key in schema.schema:
        description = key.description
        if isinstance(description, Mapping) and "suggested_value" in description:
            shown[str(key)] = description["suggested_value"]
    return shown


def count_panel_describes_breakers(
    monkeypatch: pytest.MonkeyPatch, *modules: ModuleType
) -> list[SpanPanelSnapshot]:
    """Record each snapshot `panel_describes_breakers` is asked of, through `package_ratings` and each of `modules`.

    The helper is looked up where it is called, so it is replaced in its own
    module, whose other helpers ask through it, and in each module that imports
    it. One of `modules` that does not import it gains the counting helper,
    which nothing there calls.
    """
    asked: list[SpanPanelSnapshot] = []
    describes = package_ratings.panel_describes_breakers

    def counting(snapshot: SpanPanelSnapshot) -> bool:
        asked.append(snapshot)
        return describes(snapshot)

    for module in (package_ratings, *modules):
        monkeypatch.setattr(module, "panel_describes_breakers", counting, raising=False)
    return asked


def make_span_panel_entry(
    entry_id: str = "test_entry",
    host: str = "192.168.1.100",
    access_token: str = "test_token",
    scan_interval: int = 15,
    options: dict[str, Any] | None = None,
    version: int = 2,
    unique_id: str | None = None,
) -> MockConfigEntry:
    """Create a MockConfigEntry for Span Panel with common defaults."""
    return MockConfigEntry(
        domain="span_panel",
        data={
            CONF_HOST: host,
            CONF_ACCESS_TOKEN: access_token,
            CONF_SCAN_INTERVAL: scan_interval,
        },
        options=options or {},
        entry_id=entry_id,
        version=version,
        unique_id=unique_id or f"{host}_{entry_id}",
    )


def assert_entity_state(hass: HomeAssistant, entity_id: str, expected_state: Any) -> None:
    """Assert the state of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity {entity_id} not found in hass.states"
    assert state.state == str(expected_state), (
        f"Entity {entity_id} state is '{state.state}', expected '{expected_state}'"
    )


def assert_entity_attribute(
    hass: HomeAssistant, entity_id: str, attribute: str, expected_value: Any
) -> None:
    """Assert an attribute of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity {entity_id} not found in hass.states"
    actual_value = state.attributes.get(attribute)
    assert actual_value == expected_value, (
        f"Expected {entity_id}.{attribute} to be '{expected_value}', got '{actual_value}'"
    )


async def advance_time(hass: HomeAssistant, seconds: int) -> None:
    """Advance Home Assistant time by a given number of seconds and block till done."""
    now = utcnow()
    future = now + datetime.timedelta(seconds=seconds)
    from .common import async_fire_time_changed

    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()


async def trigger_coordinator_update(coordinator: Any) -> None:
    """Manually trigger a coordinator update."""
    await coordinator.async_request_refresh()
    await coordinator.hass.async_block_till_done()


def setup_span_panel_entry(
    hass: HomeAssistant,
    entry_id: str = "test_span_panel",
    host: str = "192.168.1.100",
    access_token: str = "test_token",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create and add a span panel entry for testing.

    Returns:
        The config entry.

    """
    entry = make_span_panel_entry(
        entry_id=entry_id,
        host=host,
        access_token=access_token,
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


PANEL_NAME: Final = "SPAN Panel"
"""The title `add_panel_entry` gives its entry, and its `device_name`."""


def add_panel_entry(hass: HomeAssistant, entry_id: str, serial: str) -> MockConfigEntry:
    """Add a config entry keyed on the panel's serial, as the config flow makes one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.40", "device_name": PANEL_NAME},
        options={},
        title=PANEL_NAME,
        entry_id=entry_id,
        unique_id=serial,
    )
    entry.add_to_hass(hass)
    return entry


def register_panel_device(hass: HomeAssistant, entry: MockConfigEntry, serial: str) -> str:
    """Register `entry`'s panel device under `serial` and return its device registry ID.

    Runtime data's `panel_device_id` has to name a registered device. Every
    sub-device names the panel device as its parent, and Core drops an entity
    whose device names an unregistered parent, logging an error rather than
    raising, so a made-up ID loses the entity without failing the test.

    `entry` must already be added to `hass`, because the device registry
    refuses a device for an unknown config entry.
    """
    device_info = DeviceInfo(identifiers={(DOMAIN, serial)}, name=PANEL_NAME)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **device_info)
    return device.id


def register_circuit_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    serial: str,
    circuit: SpanCircuitSnapshot,
    *,
    panel_device_id: str,
) -> dr.ChildDeviceEntry:
    """Register `circuit`'s own device under the panel device, as its first entity would.

    The device a circuit entity's `device_info` names: a child of the panel
    device `register_panel_device` answered with, under the circuit's
    identifier and name.
    """
    info = circuit_device_info(serial, circuit, circuit.circuit_id, panel_device_id=panel_device_id)
    return dr.async_get(hass).async_get_or_create_child(config_entry_id=entry.entry_id, **info)


async def ensure_device_registered_stand_in(
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot, device_name: str
) -> str:
    """Stand in for `ensure_device_registered`, registering the panel device as it does.

    A test that patches the real one out still gets runtime data naming a
    registered device, because setup carries the ID this returns into it.
    """
    return register_panel_device(hass, entry, snapshot.serial_number)


def get_circuit_entity_id_from_integration(
    coordinator: Any,
    snapshot: Any,
    circuit_data: Any,
    suffix: str,
) -> str | None:
    """Generate expected entity ID for a circuit entity using integration helpers."""
    from custom_components.span_panel.helpers import construct_entity_id, get_circuit_number

    circuit_number = get_circuit_number(circuit_data)

    return construct_entity_id(
        coordinator,
        snapshot,
        "sensor",
        circuit_data.name,
        circuit_number,
        suffix,
    )


async def mock_circuit_relay_operation(mock_client: AsyncMock) -> None:
    """Mock a circuit relay operation."""
    mock_client.set_circuit_relay.return_value = None
