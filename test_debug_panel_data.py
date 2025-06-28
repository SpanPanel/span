"""Debug panel sensor test to see what's happening with panel data."""

from typing import Any

import pytest

from custom_components.span_panel.span_panel_data import SpanPanelData
from tests.factories import (
    SpanPanelApiResponseFactory,
    SpanPanelDataFactory,
)
from tests.helpers import (
    patch_span_panel_dependencies,
    setup_span_panel_entry,
    trigger_coordinator_update,
)


@pytest.mark.asyncio
async def test_debug_panel_sensor_data(hass: Any, enable_custom_integrations: Any) -> None:
    """Debug test to understand panel sensor data structure."""

    # Create panel data with known values
    panel_data = SpanPanelDataFactory.create_panel_data(
        grid_power=1850.5,
        dsm_grid_state="DSM_GRID_UP",
        dsm_state="DSM_ON_GRID",
    )

    print(f"Raw panel factory data: {panel_data}")

    # Create a SpanPanelData object directly to test our properties
    span_panel_data = SpanPanelData.from_dict(panel_data)
    print("SpanPanelData object created")
    print(f"instantGridPowerW: {span_panel_data.instantGridPowerW}")
    print(f"mainMeterEnergyProducedWh: {span_panel_data.mainMeterEnergyProducedWh}")
    print(f"mainMeterEnergyConsumedWh: {span_panel_data.mainMeterEnergyConsumedWh}")

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        panel_data=panel_data
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and check the panel data object
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        print(f"Coordinator panel data type: {type(coordinator.span_panel_api.panel)}")
        print(f"Coordinator panel data: {coordinator.span_panel_api.panel}")

        if hasattr(coordinator.span_panel_api.panel, "instantGridPowerW"):
            print(
                f"Panel has instantGridPowerW: {coordinator.span_panel_api.panel.instantGridPowerW}"
            )
        else:
            print("Panel does NOT have instantGridPowerW attribute")

        # Check if it's a MagicMock
        from unittest.mock import MagicMock

        if isinstance(coordinator.span_panel_api.panel, MagicMock):
            print("WARNING: Panel object is a MagicMock!")
        else:
            print("Panel object is NOT a MagicMock")

        await trigger_coordinator_update(coordinator)

        # Check all panel sensor entity states
        sensor_states = []
        for entity_id in hass.states.async_entity_ids("sensor"):
            if "span_panel" in entity_id and "circuit" not in entity_id:
                state = hass.states.get(entity_id)
                sensor_states.append(f"{entity_id}: {state.state}")

        print(f"Panel sensor states: {sensor_states}")

        # Look for current_power specifically
        current_power_state = hass.states.get("sensor.span_panel_current_power")
        if current_power_state:
            print(f"Current power sensor state: {current_power_state.state}")
            print(f"Current power sensor attributes: {current_power_state.attributes}")
        else:
            print("Current power sensor not found")
