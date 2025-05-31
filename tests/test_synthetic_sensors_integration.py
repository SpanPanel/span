"""Integration tests for synthetic sensors (solar and battery)."""

from typing import Any
import pytest

from tests.factories import SpanPanelApiResponseFactory, SpanPanelCircuitFactory
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
async def test_solar_sensors_created_when_enabled(
    hass: Any, enable_custom_integrations: Any
):
    """Test that solar synthetic sensors are created when solar option is enabled."""
    # Create mock responses with circuits that could be solar legs
    circuits = [
        SpanPanelCircuitFactory.create_circuit(
            circuit_id="30",
            name="Solar Leg 1",
            instant_power=-1200.0,  # Negative indicates production
            produced_energy=12000.5,
            consumed_energy=0.0,
        ),
        SpanPanelCircuitFactory.create_circuit(
            circuit_id="32",
            name="Solar Leg 2",
            instant_power=-800.0,  # Negative indicates production
            produced_energy=8000.2,
            consumed_energy=0.0,
        ),
    ]

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=circuits
    )

    # Configure entry with solar sensors enabled
    options = {
        "enable_solar_circuit": True,
        "leg1": 30,
        "leg2": 32,
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update to get proper data
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify solar synthetic sensors are created
        # Solar sensors should use friendly names like "solar_inverter" when enabled
        assert_entity_state(hass, "sensor.solar_inverter_instant_power", "-2000.0")
        assert_entity_state(hass, "sensor.solar_inverter_energy_produced", "20000.7")
        assert_entity_state(hass, "sensor.solar_inverter_energy_consumed", "0.0")

        # Verify sensor attributes
        power_state = hass.states.get("sensor.solar_inverter_instant_power")
        assert power_state is not None
        assert power_state.attributes.get("device_class") == "power"
        assert power_state.attributes.get("unit_of_measurement") == "W"

        energy_state = hass.states.get("sensor.solar_inverter_energy_produced")
        assert energy_state is not None
        assert energy_state.attributes.get("device_class") == "energy"
        assert energy_state.attributes.get("unit_of_measurement") == "Wh"


@pytest.mark.asyncio
async def test_solar_sensors_not_created_when_disabled(
    hass: Any, enable_custom_integrations: Any
):
    """Test that solar synthetic sensors are NOT created when solar option is disabled."""
    # Create mock responses with the same circuits
    circuits = [
        SpanPanelCircuitFactory.create_circuit(circuit_id="30", name="Solar Leg 1"),
        SpanPanelCircuitFactory.create_circuit(circuit_id="32", name="Solar Leg 2"),
    ]

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=circuits
    )

    # Configure entry with solar sensors DISABLED (default)
    options = {
        "enable_solar_circuit": False,
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify solar synthetic sensors are NOT created
        assert hass.states.get("sensor.solar_inverter_instant_power") is None
        assert hass.states.get("sensor.solar_inverter_energy_produced") is None
        assert hass.states.get("sensor.solar_inverter_energy_consumed") is None

        # Verify regular circuit sensors are still created
        assert hass.states.get("sensor.solar_leg_1_power") is not None
        assert hass.states.get("sensor.solar_leg_2_power") is not None


@pytest.mark.asyncio
async def test_battery_sensors_created_when_enabled(
    hass: Any, enable_custom_integrations: Any
):
    """Test that battery sensors are created when battery option is enabled."""
    # Create mock responses with battery data
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Configure entry with battery sensors enabled
    options = {
        "enable_battery_percentage": True,
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify battery sensor is created
        assert_entity_state(hass, "sensor.span_storage_battery_percentage", "85.0")

        # Verify sensor attributes
        battery_state = hass.states.get("sensor.span_storage_battery_percentage")
        assert battery_state is not None
        assert battery_state.attributes.get("device_class") == "battery"
        assert battery_state.attributes.get("unit_of_measurement") == "%"
        assert battery_state.attributes.get("state_class") == "measurement"


@pytest.mark.asyncio
async def test_battery_sensors_not_created_when_disabled(
    hass: Any, enable_custom_integrations: Any
):
    """Test that battery sensors are NOT created when battery option is disabled."""
    # Create mock responses with battery data
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Configure entry with battery sensors DISABLED (default)
    options = {
        "enable_battery_percentage": False,
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify battery sensor is NOT created
        assert hass.states.get("sensor.span_storage_battery_percentage") is None


@pytest.mark.asyncio
async def test_both_solar_and_battery_sensors_enabled(
    hass: Any, enable_custom_integrations: Any
):
    """Test that both solar and battery sensors work together when both are enabled."""
    # Create mock responses with solar circuits and battery data
    circuits = [
        SpanPanelCircuitFactory.create_kitchen_outlet_circuit(),
        SpanPanelCircuitFactory.create_circuit(
            circuit_id="30",
            name="Solar Leg 1",
            instant_power=-1500.0,
            produced_energy=15000.0,
        ),
        SpanPanelCircuitFactory.create_circuit(
            circuit_id="32",
            name="Solar Leg 2",
            instant_power=-900.0,
            produced_energy=9000.0,
        ),
    ]

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=circuits
    )

    # Configure entry with BOTH solar and battery sensors enabled
    options = {
        "enable_solar_circuit": True,
        "leg1": 30,
        "leg2": 32,
        "enable_battery_percentage": True,
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify solar synthetic sensors are created
        assert_entity_state(hass, "sensor.solar_inverter_instant_power", "-2400.0")
        assert_entity_state(hass, "sensor.solar_inverter_energy_produced", "24000.0")

        # Verify battery sensor is created
        assert_entity_state(hass, "sensor.span_storage_battery_percentage", "85.0")

        # Verify regular circuit sensors are still created
        assert hass.states.get("sensor.kitchen_outlets_power") is not None


@pytest.mark.asyncio
async def test_solar_sensors_with_single_leg_configuration(
    hass: Any, enable_custom_integrations: Any
):
    """Test that solar sensors work with single leg configuration (leg2 = 0)."""
    # Create mock responses with only one solar circuit
    circuits = [
        SpanPanelCircuitFactory.create_circuit(
            circuit_id="30",
            name="Solar Single Leg",
            instant_power=-2000.0,
            produced_energy=20000.0,
        ),
    ]

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=circuits
    )

    # Configure entry with single solar leg (leg2 = 0 means only leg1)
    options = {
        "enable_solar_circuit": True,
        "leg1": 30,
        "leg2": 0,  # 0 means not used
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Verify solar synthetic sensors are created with single leg values
        assert_entity_state(hass, "sensor.solar_inverter_instant_power", "-2000.0")
        assert_entity_state(hass, "sensor.solar_inverter_energy_produced", "20000.0")


@pytest.mark.asyncio
async def test_synthetic_sensor_friendly_naming(
    hass: Any, enable_custom_integrations: Any
):
    """Test that synthetic sensors use proper friendly naming."""
    circuits = [
        SpanPanelCircuitFactory.create_circuit(circuit_id="30", name="Solar Leg 1"),
        SpanPanelCircuitFactory.create_circuit(circuit_id="32", name="Solar Leg 2"),
    ]

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=circuits
    )

    # Test with device prefix enabled and battery enabled (default for modern installations)
    options = {
        "enable_solar_circuit": True,
        "leg1": 30,
        "leg2": 32,
        "use_device_prefix": True,
        "enable_battery_percentage": True,
    }

    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options):
        # Setup the integration
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Trigger coordinator update
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Check entity friendly names include device prefix for solar sensor
        power_state = hass.states.get("sensor.span_panel_solar_inverter_instant_power")
        assert power_state is not None, "Solar power sensor should exist"
        # The friendly name should include "Solar Inverter"
        assert "Solar Inverter" in power_state.attributes.get("friendly_name", "")

        # Test the battery sensor with device prefix friendly name
        battery_state = hass.states.get(
            "sensor.span_panel_span_storage_battery_percentage"
        )
        assert battery_state is not None, "Battery sensor should exist"
        assert "Storage" in battery_state.attributes.get("friendly_name", "")
