"""test_basic_features.

General feature tests for Span Panel integration (setup, coordinator, basic entity functionality).
"""

from typing import Any

import pytest

from tests.factories import (
    SpanPanelApiResponseFactory,
    SpanPanelCircuitFactory,
    SpanPanelDataFactory,
    SpanPanelStatusFactory,
)
from tests.helpers import (
    assert_entity_state,
    assert_entity_attribute,
    get_circuit_entity_id,
    get_panel_entity_id,
    patch_span_panel_dependencies,
    setup_span_panel_entry,
    trigger_coordinator_update,
)


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


@pytest.mark.asyncio
async def test_integration_setup_and_unload(hass: Any, enable_custom_integrations: Any):
    """Test that the integration sets up and unloads correctly."""

    # Create test data
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()
    entry, _ = setup_span_panel_entry(hass, mock_responses)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Verify entry is loaded
        assert entry.state.name == "LOADED"

        # Verify coordinator data
        assert "span_panel" in hass.data
        assert entry.entry_id in hass.data["span_panel"]

        # Test unload
        result = await hass.config_entries.async_unload(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Verify cleanup
        assert entry.entry_id not in hass.data.get("span_panel", {})


@pytest.mark.asyncio
async def test_coordinator_update_cycle(hass: Any, enable_custom_integrations: Any):
    """Test that the coordinator updates data correctly."""

    # Create test data with known values
    circuit_data = SpanPanelCircuitFactory.create_kitchen_outlet_circuit()
    panel_data = SpanPanelDataFactory.create_on_grid_panel_data()
    status_data = SpanPanelStatusFactory.create_status()

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data],
        panel_data=panel_data,
        status_data=status_data,
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses) as (mock_panel, mock_api):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and trigger update manually
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify API calls were made during the coordinator update
        mock_api.get_status_data.assert_called()
        mock_api.get_panel_data.assert_called()
        mock_api.get_circuits_data.assert_called()


@pytest.mark.asyncio
async def test_circuit_sensor_creation_and_values(hass: Any, enable_custom_integrations: Any):
    """Test that circuit sensors are created with correct values."""

    # Create test circuit with known values
    circuit_data = SpanPanelCircuitFactory.create_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        instant_power=245.3,
        consumed_energy=2450.8,
        produced_energy=0.0,
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data]
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and trigger manual update to ensure sensors get data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check power sensor
        power_entity_id = get_circuit_entity_id(
            "1", "Kitchen Outlets", "sensor", "power", use_device_prefix=True
        )
        assert_entity_state(hass, power_entity_id, "245.3")
        assert_entity_attribute(hass, power_entity_id, "unit_of_measurement", "W")

        # Check consumed energy sensor
        consumed_entity_id = get_circuit_entity_id(
            "1", "Kitchen Outlets", "sensor", "energy_consumed", use_device_prefix=True
        )
        assert_entity_state(hass, consumed_entity_id, "2450.8")
        assert_entity_attribute(hass, consumed_entity_id, "unit_of_measurement", "Wh")


@pytest.mark.asyncio
async def test_circuit_switch_creation_and_control(hass: Any, enable_custom_integrations: Any):
    """Test that circuit switches are created and can be controlled."""

    # Create controllable circuit
    circuit_data = SpanPanelCircuitFactory.create_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state="CLOSED",
        is_user_controllable=True,
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data]
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses) as (mock_panel, mock_api):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Check switch entity exists and is on (CLOSED relay)
        switch_entity_id = get_circuit_entity_id(
            "1", "Kitchen Outlets", "switch", "breaker", use_device_prefix=True
        )
        assert_entity_state(hass, switch_entity_id, "on")

        # Test turning off the switch
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": switch_entity_id},
            blocking=True,
        )

        # Verify API was called to set relay to OPEN
        mock_api.set_relay.assert_called()


@pytest.mark.asyncio
async def test_non_controllable_circuit_no_switch(hass: Any, enable_custom_integrations: Any):
    """Test that non-controllable circuits don't create switch entities."""

    # Create non-controllable circuit
    circuit_data = SpanPanelCircuitFactory.create_circuit(
        circuit_id="1",
        name="Main Panel Feed",
        is_user_controllable=False,
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data]
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Check that switch entity does NOT exist
        switch_entity_id = get_circuit_entity_id(
            "1", "Main Panel Feed", "switch", "breaker", use_device_prefix=True
        )
        switch_state = hass.states.get(switch_entity_id)
        assert switch_state is None, (
            f"Switch {switch_entity_id} should not exist for non-controllable circuit"
        )


@pytest.mark.asyncio
async def test_panel_level_sensors(hass: Any, enable_custom_integrations: Any):
    """Test that panel-level sensors are created with correct values."""

    # Create panel data with known values
    panel_data = SpanPanelDataFactory.create_panel_data(
        grid_power=1850.5,
        dsm_grid_state="DSM_GRID_UP",
        dsm_state="DSM_ON_GRID",
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        panel_data=panel_data
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and trigger manual update to ensure sensors get data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check grid power sensor (entity name is "Current Power")
        grid_power_entity_id = get_panel_entity_id("current_power", use_device_prefix=True)
        assert_entity_state(hass, grid_power_entity_id, "1850.5")

        # Check DSM state sensor
        dsm_state_entity_id = get_panel_entity_id("dsm_state", use_device_prefix=True)
        assert_entity_state(hass, dsm_state_entity_id, "DSM_ON_GRID")


@pytest.mark.asyncio
async def test_producing_circuit_power_values(hass: Any, enable_custom_integrations: Any):
    """Test that producing circuits (like solar) show correct power values."""

    # Create solar circuit with negative power (producing)
    circuit_data = SpanPanelCircuitFactory.create_circuit(
        circuit_id="15",
        name="Solar Panels",
        instant_power=-1200.0,  # Negative indicates production
        consumed_energy=0.0,
        produced_energy=12000.5,
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data]
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and trigger manual update to ensure sensors get data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check power sensor shows absolute value (no negative display)
        power_entity_id = get_circuit_entity_id(
            "15", "Solar Panels", "sensor", "power", use_device_prefix=True
        )
        assert_entity_state(hass, power_entity_id, "1200.0")

        # Check produced energy sensor
        produced_entity_id = get_circuit_entity_id(
            "15", "Solar Panels", "sensor", "energy_produced", use_device_prefix=True
        )
        assert_entity_state(hass, produced_entity_id, "12000.5")


@pytest.mark.asyncio
async def test_panel_on_grid_state(hass: Any, enable_custom_integrations: Any):
    """Test panel on-grid operational state detection."""

    # Test on-grid state
    on_grid_data = SpanPanelDataFactory.create_on_grid_panel_data()
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        panel_data=on_grid_data
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and trigger manual update to ensure sensors get data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check DSM state shows on-grid
        dsm_state_entity_id = get_panel_entity_id("dsm_state", use_device_prefix=True)
        assert_entity_state(hass, dsm_state_entity_id, "DSM_ON_GRID")


@pytest.mark.asyncio
async def test_panel_backup_state(hass: Any, enable_custom_integrations: Any):
    """Test panel backup operational state detection."""

    # Test backup state
    backup_data = SpanPanelDataFactory.create_backup_panel_data()
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        panel_data=backup_data
    )

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and trigger manual update to ensure sensors get data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check DSM state shows backup
        dsm_state_entity_id = get_panel_entity_id("dsm_state", use_device_prefix=True)
        assert_entity_state(hass, dsm_state_entity_id, "DSM_ON_BACKUP")
