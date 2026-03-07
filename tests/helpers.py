"""Helper functions for testing the Span Panel integration."""

import datetime
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry


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
