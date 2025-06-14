"""Integration tests for the door state tamper sensor."""

from typing import Any
import pytest

from custom_components.span_panel.const import (
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
)
from tests.factories import SpanPanelApiResponseFactory, SpanPanelStatusFactory
from tests.helpers import (
    assert_entity_state,
    patch_span_panel_dependencies,
    setup_span_panel_entry,
    trigger_coordinator_update,
)


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


@pytest.mark.asyncio
async def test_door_state_tamper_sensor_closed(hass: Any, enable_custom_integrations: Any):
    """Test that door state tamper sensor reports clear when door is closed."""
    # Create mock responses with door closed
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        status_data=SpanPanelStatusFactory.create_status(door_state=SYSTEM_DOOR_STATE_CLOSED)
    )

    entry, _ = setup_span_panel_entry(hass, mock_responses)

    with patch_span_panel_dependencies(mock_responses):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update to get proper data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check that door state tamper sensor is clear (OFF) when door is closed
        assert_entity_state(hass, "binary_sensor.door_state", "off")

        # Verify the sensor has the correct device class
        state = hass.states.get("binary_sensor.door_state")
        assert state is not None
        assert state.attributes.get("device_class") == "tamper"


@pytest.mark.asyncio
async def test_door_state_tamper_sensor_open(hass: Any, enable_custom_integrations: Any):
    """Test that door state tamper sensor reports tampered when door is open."""
    # Create mock responses with door open
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        status_data=SpanPanelStatusFactory.create_status(door_state=SYSTEM_DOOR_STATE_OPEN)
    )

    entry, _ = setup_span_panel_entry(hass, mock_responses)

    with patch_span_panel_dependencies(mock_responses):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update to get proper data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check that door state tamper sensor is tampered (ON) when door is open
        assert_entity_state(hass, "binary_sensor.door_state", "on")

        # Verify the sensor has the correct device class
        state = hass.states.get("binary_sensor.door_state")
        assert state is not None
        assert state.attributes.get("device_class") == "tamper"


@pytest.mark.asyncio
async def test_door_state_tamper_sensor_unknown(hass: Any, enable_custom_integrations: Any):
    """Test that door state tamper sensor remains unknown when door state is unknown."""
    # Create mock responses with unknown door state
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        status_data=SpanPanelStatusFactory.create_status(door_state="UNKNOWN")
    )

    entry, _ = setup_span_panel_entry(hass, mock_responses)

    with patch_span_panel_dependencies(mock_responses):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update to get proper data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check that door state tamper sensor remains unknown when state is unknown
        state = hass.states.get("binary_sensor.door_state")
        assert state is not None
        assert state.state == "unknown"

        # Verify the sensor has the correct device class
        assert state.attributes.get("device_class") == "tamper"
