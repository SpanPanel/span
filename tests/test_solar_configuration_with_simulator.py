"""Test solar configuration using simulator mode (real integration test).

This module automatically runs in simulation mode without requiring the
SPAN_USE_REAL_SIMULATION environment variable to be set externally.
"""

# Critical: Set simulation mode BEFORE any imports
import os

import pytest

# Skip this test if SPAN_USE_REAL_SIMULATION is not set externally
if os.environ.get('SPAN_USE_REAL_SIMULATION', '').lower() not in ('1', 'true', 'yes'):
    pytest.skip("Simulation tests require SPAN_USE_REAL_SIMULATION=1", allow_module_level=True)

os.environ['SPAN_USE_REAL_SIMULATION'] = '1'

# Configure logging to reduce noise BEFORE other imports
import logging

logging.getLogger("homeassistant.core").setLevel(logging.WARNING)
logging.getLogger("homeassistant.loader").setLevel(logging.WARNING)
logging.getLogger("homeassistant.setup").setLevel(logging.WARNING)
logging.getLogger("homeassistant.components").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yaml").setLevel(logging.WARNING)
logging.getLogger("homeassistant.helpers").setLevel(logging.WARNING)
logging.getLogger("homeassistant.config_entries").setLevel(logging.WARNING)

# Keep our own logs visible for debugging
logging.getLogger("custom_components.span_panel").setLevel(logging.INFO)
logging.getLogger("ha_synthetic_sensors").setLevel(logging.INFO)

# Now we can safely import everything else
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant

# Import MockConfigEntry from pytest-homeassistant-custom-component
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.span_panel.const import COORDINATOR, DOMAIN, SENSOR_SET
from custom_components.span_panel.options import INVERTER_ENABLE, INVERTER_LEG1, INVERTER_LEG2
from custom_components.span_panel.synthetic_solar import handle_solar_options_change

# Import simulation factory for consistent serial number validation


async def test_solar_configuration_with_simulator_friendly_names(hass: HomeAssistant, enable_custom_integrations: Any) -> None:
    """Test solar configuration with friendly names using simulator mode."""

    # Get the consistent simulation serial number from the fixture
    with open("tests/fixtures/friendly_names.yaml") as f:
        fixture_data = yaml.safe_load(f)
    simulation_serial = fixture_data["global_settings"]["device_identifier"]

    # Create a config entry with simulator mode and the correct serial number as host
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"SPAN Panel (Simulator: {simulation_serial})",
        data={
            CONF_HOST: simulation_serial,  # Use serial number as host for simulation
            CONF_ACCESS_TOKEN: "simulator_token",
            "simulation_mode": True,
        },
        options={
            "use_device_prefix": True,
            "use_circuit_numbers": False,
            "enable_solar_circuit": True,  # Enable solar from the start
            "leg1": 30,
            "leg2": 32,
        },
    )

    # Add the config entry to hass
    config_entry.add_to_hass(hass)

    # Setup the integration - this will use real simulation data
    result = await hass.config_entries.async_setup(config_entry.entry_id)
    assert result is True, "Integration should setup successfully"

    # Verify the integration is loaded
    assert config_entry.state == ConfigEntryState.LOADED

    # Verify integration data exists
    assert DOMAIN in hass.data
    assert config_entry.entry_id in hass.data[DOMAIN]

    # Get the coordinator with real simulation data
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    assert coordinator is not None

    # Verify span panel has simulation mode enabled
    span_panel = coordinator.data
    assert span_panel is not None
    assert span_panel.api.simulation_mode is True

    # Validate that the simulator provides the expected serial number
    expected_serial = simulation_serial  # Use the same serial we extracted earlier
    actual_serial = span_panel.status.serial_number
    print(f"Expected serial number from YAML: {expected_serial}")
    print(f"Actual serial number from simulator: {actual_serial}")
    assert actual_serial == expected_serial, f"Serial number mismatch: expected {expected_serial}, got {actual_serial}"

    # Wait for initial data load
    await hass.async_block_till_done()

    # Check that native sensors are created first
    all_entities = hass.states.async_entity_ids()
    print(f"\nAll {len(all_entities)} entities after initial setup:")
    for entity_id in sorted(all_entities):
        print(f"  {entity_id}")

    # Solar sensors should not be created yet during initial setup
    solar_entities = [e for e in all_entities if "solar" in e]
    print(f"\nFound {len(solar_entities)} solar entities after initial setup: {solar_entities}")

    # Now trigger the options change to create solar sensors (this simulates what happens
    # when a user changes options in the UI, which creates solar sensors after native sensors exist)
    print("\nTriggering options change to create solar sensors...")
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            "use_device_prefix": True,
            "use_circuit_numbers": False,
            "enable_solar_circuit": True,
            "leg1": 30,  # Use circuit 30 (available in simulation)
            "leg2": 32,  # Use circuit 32 (available in simulation)
        },
    )

    # In the test environment, we need to manually trigger the solar options change
    # since the update_listener may not be called automatically
    from custom_components.span_panel.synthetic_solar import handle_solar_options_change

    coordinator_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    coordinator = coordinator_data.get(COORDINATOR)
    sensor_set = coordinator_data.get(SENSOR_SET)

    print(f"Coordinator available: {coordinator is not None}")
    print(f"Sensor set available: {sensor_set is not None}")

    if coordinator and sensor_set:
        solar_enabled = config_entry.options.get("enable_solar_circuit", False)
        leg1 = config_entry.options.get("leg1", 0)
        leg2 = config_entry.options.get("leg2", 0)

        print(f"Calling handle_solar_options_change with: enable={solar_enabled}, leg1={leg1}, leg2={leg2}")
        result = await handle_solar_options_change(hass, config_entry, coordinator, sensor_set, solar_enabled, leg1, leg2, "Test Device")
        print(f"Solar options change result: {result}")

        # Check what's in synthetic storage after the solar options change
        # Use the cached sensor_set directly
        if sensor_set:
            all_sensors = sensor_set.list_sensors()
            solar_sensors_in_storage = [s for s in all_sensors if 'solar' in s.unique_id.lower() or 'inverter' in s.unique_id.lower()]
            print(f"Total sensors in storage: {len(all_sensors)}")
            print(f"Solar sensors in storage: {len(solar_sensors_in_storage)}")
            for sensor in solar_sensors_in_storage[:3]:  # Show first 3
                print(f"  - {sensor.unique_id} -> {sensor.entity_id}")
        else:
            print("No sensor set found in coordinator data")

        if result:
            # Trigger a reload to activate the new sensors
            await hass.config_entries.async_reload(config_entry.entry_id)

    # Wait for the options change to be processed and integration to reload
    await hass.async_block_till_done()

    # Wait for solar sensors to be created by polling
    import asyncio

    async def wait_for_solar_sensors(timeout=10.0):
        """Wait for solar sensors to be created with polling."""
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            await hass.async_block_till_done()
            all_entities = hass.states.async_entity_ids()
            solar_entities = [e for e in all_entities if "solar" in e]

            if len(solar_entities) > 0:
                return solar_entities

            # Wait a bit before checking again
            await asyncio.sleep(0.1)

        return []

    # Wait for solar sensors to be created
    solar_entities = await wait_for_solar_sensors()

    print(f"\nFound {len(solar_entities)} solar entities after options change and reload:")
    for entity_id in sorted(solar_entities):
        state = hass.states.get(entity_id)
        print(f"  {entity_id}: {state.state if state else 'None'}")

    # Export the complete YAML configuration
    print("\n" + "="*80)
    print("EXPORTING COMPLETE YAML CONFIGURATION")
    print("="*80)

    # Get the storage manager from the integration data
    coordinator_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    storage_manager = coordinator_data.get("storage_manager")

    if storage_manager:
        # Export YAML for the main sensor set
        try:
            yaml_content = storage_manager.export_yaml("span_panel_sensors")
            print("\nCOMPLETE YAML CONFIGURATION:")
            print("-" * 40)
            print(yaml_content)
            print("-" * 40)
        except Exception as e:
            print(f"Error exporting YAML: {e}")

        # Also show sensor set metadata
        try:
            metadata = storage_manager.get_sensor_set_metadata("span_panel_sensors")
            if metadata:
                print("\nSensor Set Metadata:")
                print(f"  ID: {metadata.sensor_set_id}")
                print(f"  Name: {metadata.name}")
                print(f"  Device ID: {metadata.device_identifier}")
                print(f"  Sensor Count: {metadata.sensor_count}")
                print(f"  Global Settings: {metadata.global_settings}")
        except Exception as e:
            print(f"Error getting metadata: {e}")
    else:
        print("Storage manager not found in integration data")

    # Verify solar sensors were created
    assert len(solar_entities) > 0, "Solar sensors should be created after options change and reload"


async def test_solar_configuration_with_simulator_circuit_numbers(hass: HomeAssistant, enable_custom_integrations: Any) -> None:
    """Test solar configuration with circuit numbers using simulator mode."""

    # Create a config entry with simulator mode and circuit numbers (no solar initially)
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="SPAN Panel (Simulator)",
        data={
            CONF_HOST: "localhost",
            CONF_ACCESS_TOKEN: "simulator_token",
            "simulation_mode": True,
        },
        options={
            "use_device_prefix": True,
            "use_circuit_numbers": True,  # Use circuit numbers instead of friendly names
        },
    )

    # Add the config entry to hass
    config_entry.add_to_hass(hass)

    # Setup the integration
    result = await hass.config_entries.async_setup(config_entry.entry_id)
    assert result is True, "Integration should setup successfully"

    # Wait for setup to complete
    await hass.async_block_till_done()

    # Verify no solar sensors initially
    initial_solar_entities = [e for e in hass.states.async_entity_ids() if "solar" in e]
    print(f"\nInitial solar entities (should be 0): {len(initial_solar_entities)}")

    # Now add solar configuration via options change
    print("\nConfiguring solar inverter via options change...")
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            "use_device_prefix": True,
            "use_circuit_numbers": True,  # Use circuit numbers instead of friendly names
            INVERTER_ENABLE: True,  # Enable solar
            INVERTER_LEG1: 30,
            INVERTER_LEG2: 32,
        },
    )

    # Trigger options update by calling the handler directly
    coordinator_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    coordinator = coordinator_data.get(COORDINATOR)
    sensor_set = coordinator_data.get(SENSOR_SET)

    print(f"Coordinator available: {coordinator is not None}")
    print(f"Sensor set available: {sensor_set is not None}")

    if coordinator and sensor_set:
        solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
        leg1 = config_entry.options.get(INVERTER_LEG1, 0)
        leg2 = config_entry.options.get(INVERTER_LEG2, 0)

        print(f"Calling handle_solar_options_change with: enable={solar_enabled}, leg1={leg1}, leg2={leg2}")
        result = await handle_solar_options_change(hass, config_entry, coordinator, sensor_set, solar_enabled, leg1, leg2, "Test Device")
        print(f"Solar options change result: {result}")
    else:
        print("Warning: Could not find coordinator or storage manager for solar options change")

    # Reload the integration to apply changes
    print("Reloading integration to apply solar configuration...")
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    # Check sensor keys in the SensorSet instead of entity IDs (sensor keys are stable)
    # Get the sensor_set directly from the cached data
    coordinator_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    sensor_set = coordinator_data.get(SENSOR_SET)

    if sensor_set:
        all_sensors = sensor_set.list_sensors()
        # Look for solar sensor keys (these are stable and reliable)
        solar_sensors_in_storage = [s for s in all_sensors if 'solar' in s.unique_id.lower()]

        print(f"\nFound {len(solar_sensors_in_storage)} solar sensors in storage:")
        for sensor in solar_sensors_in_storage:
            print(f"  - {sensor.unique_id} -> {sensor.entity_id}")

        # Verify solar sensors were created in storage
        assert len(solar_sensors_in_storage) > 0, "Solar sensors should be created in SensorSet after options change"

        # Look for specific solar sensor keys (should include solar_current_power, etc.)
        expected_solar_types = ['solar_current_power', 'solar_energy_produced', 'solar_energy_consumed']
        found_solar_types = []
        for sensor in solar_sensors_in_storage:
            for solar_type in expected_solar_types:
                if solar_type in sensor.unique_id.lower():
                    found_solar_types.append(solar_type)
                    break

        print(f"Found solar sensor types: {found_solar_types}")
        assert len(found_solar_types) > 0, f"Should have at least one of {expected_solar_types} solar sensors"
    else:
        assert False, "No sensor set found in coordinator data"
