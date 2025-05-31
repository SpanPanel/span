"""test_circuit_control.

Tests for Span Panel circuit control functionality (switches, relay operations).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock


import pytest

from custom_components.span_panel.const import CircuitRelayState
from custom_components.span_panel.switch import async_setup_entry
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


def create_mock_circuit(
    circuit_id: str = "1",
    name: str = "Test Circuit",
    relay_state: str = CircuitRelayState.CLOSED.name,
    is_user_controllable: bool = True,
):
    """Create a mock circuit for testing."""
    circuit = MagicMock()
    circuit.id = circuit_id
    circuit.name = name
    circuit.relay_state = relay_state
    circuit.is_user_controllable = is_user_controllable
    circuit.tabs = [int(circuit_id)]
    circuit.copy.return_value = circuit
    return circuit


def create_mock_span_panel(circuits: dict[str, Any]):
    """Create a mock SpanPanel with circuits."""
    panel = MagicMock()
    panel.circuits = circuits
    panel.status.serial_number = "TEST123"
    return panel


@pytest.mark.asyncio
async def test_switch_creation_for_controllable_circuit(
    hass: Any, enable_custom_integrations: Any
):
    """Test that switches are created only for user-controllable circuits."""

    # Create controllable circuit
    controllable_circuit = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        is_user_controllable=True,
    )

    # Create non-controllable circuit
    non_controllable_circuit = create_mock_circuit(
        circuit_id="2",
        name="Main Feed",
        is_user_controllable=False,
    )

    circuits = {
        "1": controllable_circuit,
        "2": non_controllable_circuit,
    }

    mock_panel = create_mock_span_panel(circuits)
    mock_coordinator = MagicMock()
    mock_coordinator.data = mock_panel

    entities = []

    def mock_add_entities(new_entities, update_before_add: bool = False):
        entities.extend(new_entities)

    mock_config_entry = MagicMock()
    mock_config_entry.entry_id = "test_entry"

    # Mock hass data
    hass.data = {
        "span_panel": {
            "test_entry": {
                "coordinator": mock_coordinator,
            }
        }
    }

    await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    # Should only create one switch for the controllable circuit
    assert len(entities) == 1
    assert entities[0].circuit_id == "1"


@pytest.mark.asyncio
async def test_switch_turn_on_operation(hass: Any, enable_custom_integrations: Any):
    """Test turning on a circuit switch."""

    circuit = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state=CircuitRelayState.OPEN.name,
    )

    circuits = {"1": circuit}
    mock_panel = create_mock_span_panel(circuits)
    mock_panel.api.set_relay = AsyncMock()

    mock_coordinator = MagicMock()
    mock_coordinator.data = mock_panel
    mock_coordinator.async_request_refresh = AsyncMock()

    switch = SpanPanelCircuitsSwitch(mock_coordinator, "1", "Kitchen Outlets")

    # Turn on the switch
    await switch.async_turn_on()

    # Verify API was called with correct parameters
    mock_panel.api.set_relay.assert_called_once()
    call_args = mock_panel.api.set_relay.call_args
    assert (
        call_args[0][1] == CircuitRelayState.CLOSED
    )  # Second argument should be CLOSED

    # Verify refresh was requested
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_switch_turn_off_operation(hass: Any, enable_custom_integrations: Any):
    """Test turning off a circuit switch."""

    circuit = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state=CircuitRelayState.CLOSED.name,
    )

    circuits = {"1": circuit}
    mock_panel = create_mock_span_panel(circuits)
    mock_panel.api.set_relay = AsyncMock()

    mock_coordinator = MagicMock()
    mock_coordinator.data = mock_panel
    mock_coordinator.async_request_refresh = AsyncMock()

    switch = SpanPanelCircuitsSwitch(mock_coordinator, "1", "Kitchen Outlets")

    # Turn off the switch
    await switch.async_turn_off()

    # Verify API was called with correct parameters
    mock_panel.api.set_relay.assert_called_once()
    call_args = mock_panel.api.set_relay.call_args
    assert call_args[0][1] == CircuitRelayState.OPEN  # Second argument should be OPEN

    # Verify refresh was requested
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_switch_state_reflects_relay_state(
    hass: Any, enable_custom_integrations: Any
):
    """Test that switch state correctly reflects circuit relay state."""

    # Test CLOSED relay -> switch ON
    circuit_closed = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state=CircuitRelayState.CLOSED.name,
    )

    circuits_closed = {"1": circuit_closed}
    mock_panel_closed = create_mock_span_panel(circuits_closed)

    mock_coordinator_closed = MagicMock()
    mock_coordinator_closed.data = mock_panel_closed

    switch_closed = SpanPanelCircuitsSwitch(
        mock_coordinator_closed, "1", "Kitchen Outlets"
    )

    # Check that switch is on for CLOSED relay
    assert switch_closed.is_on is True

    # Test OPEN relay -> switch OFF
    circuit_open = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state=CircuitRelayState.OPEN.name,
    )

    circuits_open = {"1": circuit_open}
    mock_panel_open = create_mock_span_panel(circuits_open)

    mock_coordinator_open = MagicMock()
    mock_coordinator_open.data = mock_panel_open

    switch_open = SpanPanelCircuitsSwitch(mock_coordinator_open, "1", "Kitchen Outlets")

    # Check that switch is off for OPEN relay
    assert switch_open.is_on is False


@pytest.mark.asyncio
async def test_switch_handles_missing_circuit(
    hass: Any, enable_custom_integrations: Any
):
    """Test that switch handles gracefully when circuit is missing."""

    # Empty circuits dict
    circuits = {}
    mock_panel = create_mock_span_panel(circuits)

    mock_coordinator = MagicMock()
    mock_coordinator.data = mock_panel

    # Should raise ValueError for missing circuit
    with pytest.raises(ValueError, match="Circuit 1 not found"):
        SpanPanelCircuitsSwitch(mock_coordinator, "1", "Missing Circuit")


@pytest.mark.asyncio
async def test_switch_coordinator_update_handling(
    hass: Any, enable_custom_integrations: Any
):
    """Test switch updates correctly when coordinator data changes."""

    circuit = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state=CircuitRelayState.CLOSED.name,
    )

    circuits = {"1": circuit}
    mock_panel = create_mock_span_panel(circuits)

    mock_coordinator = MagicMock()
    mock_coordinator.data = mock_panel

    switch = SpanPanelCircuitsSwitch(mock_coordinator, "1", "Kitchen Outlets")

    # Add mock hass and entity registry to prevent "hass is None" error
    switch.hass = hass
    switch.entity_id = "switch.span_panel_kitchen_outlets_breaker"
    switch.registry_entry = MagicMock()

    # Add mock platform to prevent platform_name error
    mock_platform = MagicMock()
    mock_platform.platform_name = "switch"
    switch.platform = mock_platform

    # Initially should be on (CLOSED)
    assert switch.is_on is True

    # Change circuit state to OPEN
    circuit.relay_state = CircuitRelayState.OPEN.name

    # Trigger coordinator update (now won't fail due to hass being None)
    switch._handle_coordinator_update()

    # Switch should now be off
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_circuit_name_change_triggers_reload_request(
    hass: Any, enable_custom_integrations: Any
):
    """Test that changing circuit name triggers integration reload."""

    circuit = create_mock_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state=CircuitRelayState.CLOSED.name,
    )

    circuits = {"1": circuit}
    mock_panel = create_mock_span_panel(circuits)

    mock_coordinator = MagicMock()
    mock_coordinator.data = mock_panel
    mock_coordinator.request_reload = MagicMock()

    switch = SpanPanelCircuitsSwitch(mock_coordinator, "1", "Kitchen Outlets")

    # Add mock hass and entity registry to prevent "hass is None" error
    switch.hass = hass
    switch.entity_id = "switch.span_panel_kitchen_outlets_breaker"
    switch.registry_entry = MagicMock()

    # Add mock platform to prevent platform_name error
    mock_platform = MagicMock()
    mock_platform.platform_name = "switch"
    switch.platform = mock_platform

    # Change circuit name
    circuit.name = "New Kitchen Outlets"

    # Trigger coordinator update (now won't fail due to hass being None)
    switch._handle_coordinator_update()

    # Should request reload due to name change
    mock_coordinator.request_reload.assert_called_once()
