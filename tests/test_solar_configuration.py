"""Test solar configuration integration with base fixtures and expected outputs."""

import pytest
import yaml
from typing import Any, Dict

# Direct import of the factories.py file specifically
import sys
import os
import importlib.util
import re

# Load the factories.py file directly, bypassing Python's module resolution
factories_path = os.path.join(os.path.dirname(__file__), "factories.py")
spec = importlib.util.spec_from_file_location("factories_direct", factories_path)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load factories.py from {factories_path}")
factories_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factories_module)
SpanPanelApiResponseFactory = factories_module.SpanPanelApiResponseFactory
SpanPanelDataFactory = factories_module.SpanPanelDataFactory
SpanPanelStatusFactory = factories_module.SpanPanelStatusFactory
SpanPanelStorageBatteryFactory = factories_module.SpanPanelStorageBatteryFactory

from tests.helpers import (
    patch_span_panel_dependencies,
    setup_span_panel_entry_with_cleanup,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.options import INVERTER_ENABLE, INVERTER_LEG1, INVERTER_LEG2


DUMP_MODE = os.environ.get('DUMP_MODE', '').lower() in ('1', 'true', 'yes')

DUMP_MODE = False

def sensor_key_sort_tuple(sensor_key: str):
    # Sort by the trimmed sensor key directly
    return sensor_key.strip()

def detailed_key_diff(expected, actual):
    ek, ak = expected, actual
    ek_repr, ak_repr = repr(ek), repr(ak)
    ek_ord = [ord(c) for c in ek]
    ak_ord = [ord(c) for c in ak]
    maxlen = max(len(ek), len(ak))
    lines = []
    caret_line = []
    for j in range(maxlen):
        e_c = ek[j] if j < len(ek) else ''
        a_c = ak[j] if j < len(ak) else ''
        e_o = ord(e_c) if j < len(ek) else ''
        a_o = ord(a_c) if j < len(ak) else ''
        lines.append(
            f"{j:2}: expected {repr(e_c):>4} ({e_o!s:>3}) | actual {repr(a_c):>4} ({a_o!s:>3})"
        )
        if e_c != a_c:
            caret_line.append('^')
        else:
            caret_line.append(' ')
    caret_str = ' ' * 32 + ''.join(caret_line)
    # Summarize the type of difference
    summary = []
    if len(ek) != len(ak):
        summary.append(f"Length differs: expected {len(ek)}, actual {len(ak)}")
    if ek.strip() == ak.strip() and ek != ak:
        summary.append("Difference is only in leading/trailing whitespace.")
    if any(ord(c) < 32 or ord(c) == 127 for c in ek + ak):
        summary.append("One or both keys contain non-printable characters.")
    return (
        f"  expected: {ek_repr}\n"
        f"  actual:   {ak_repr}\n"
        f"  ordinals (all differences marked):\n"
        + '\n'.join(lines) + '\n' + caret_str +
        ("\n" + "; ".join(summary) if summary else "")
    )

def compare_yaml_structures(actual: Dict, expected: Dict, path: str = "") -> None:
    """Compare two YAML structures, iterating by sorted sensor key and property. On first difference, output the YAML path and both values using repr()."""
    # Compare global settings
    if "global_settings" in expected:
        assert "global_settings" in actual, f"Missing global_settings in actual YAML"
        for key, expected_value in expected["global_settings"].items():
            assert key in actual["global_settings"], f"Missing global_settings.{key}"
            if actual["global_settings"][key] != expected_value:
                raise AssertionError(
                    f"global_settings.{key}:\n  expected: {repr(expected_value)}\n  actual:   {repr(actual['global_settings'][key])}"
                )

    # Compare sensors (sort keys to ensure order doesn't matter)
    expected_sensors = expected.get("sensors", {})
    actual_sensors = actual.get("sensors", {})
    expected_keys = sorted(expected_sensors.keys(), key=sensor_key_sort_tuple)
    actual_keys = sorted(actual_sensors.keys(), key=sensor_key_sort_tuple)
    if expected_keys != actual_keys:
        # Find the first index where they differ
        min_len = min(len(expected_keys), len(actual_keys))
        for i in range(min_len):
            if expected_keys[i] != actual_keys[i]:
                msg = (
                    f"Sensor key mismatch at index {i}:\n"
                    + detailed_key_diff(expected_keys[i], actual_keys[i])
                )
                if DUMP_MODE:
                    msg += f"\nFull expected: {expected_keys}\nFull actual:   {actual_keys}"
                raise AssertionError(msg)
        # If one list is longer
        if len(expected_keys) != len(actual_keys):
            msg = f"Sensor key list length mismatch: expected {len(expected_keys)} keys, actual {len(actual_keys)} keys"
            if DUMP_MODE:
                msg += f"\nFull expected: {expected_keys}\nFull actual:   {actual_keys}"
            raise AssertionError(msg)
    for key in expected_keys:
        expected_sensor = expected_sensors[key]
        actual_sensor = actual_sensors[key]
        for prop, expected_value in expected_sensor.items():
            assert prop in actual_sensor, f"{path}{key}.{prop} missing in actual YAML"
            actual_value = actual_sensor[prop]
            current_path = f"{path}{key}.{prop}"
            if isinstance(expected_value, dict):
                assert isinstance(actual_value, dict), f"{current_path} expected dict, got {type(actual_value)}"
                for subkey, subval in expected_value.items():
                    assert subkey in actual_value, f"{current_path}.{subkey} missing in actual YAML"
                    if actual_value[subkey] != subval:
                        raise AssertionError(
                            f"{current_path}.{subkey}:\n  expected: {repr(subval)}\n  actual:   {repr(actual_value[subkey])}"
                        )
            elif prop == "name":
                # Normalize for case and whitespace
                if actual_value.strip().lower() != expected_value.strip().lower():
                    raise AssertionError(
                        f"{current_path}:\n  expected: {repr(expected_value)}\n  actual:   {repr(actual_value)}"
                    )
            else:
                if actual_value != expected_value:
                    raise AssertionError(
                        f"{current_path}:\n  expected: {repr(expected_value)}\n  actual:   {repr(actual_value)}"
                    )


@pytest.mark.asyncio
async def test_solar_configuration_friendly_names(
    hass: Any,
    enable_custom_integrations: Any,
    mock_ha_storage,
    mock_synthetic_sensor_manager,
    baseline_serial_number,
    device_registry,
    async_add_entities
):
    """Test solar configuration with friendly names naming convention."""

    # Import the simulation factory
    from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory

    # Get realistic data from simulation mode
    simulation_data = await SpanPanelSimulationFactory.get_realistic_panel_data()

    # Convert simulation data to the format expected by the integration
    circuits = {}
    for circuit_id, circuit in simulation_data['circuits'].circuits.additional_properties.items():
        circuits[circuit_id] = {
            "id": circuit_id,
            "name": circuit.name,
            "tabs": circuit.tabs,
            "relayState": circuit.relay_state,
            "instantPowerW": circuit.instant_power_w,
            "producedEnergyWh": circuit.produced_energy_wh,
            "consumedEnergyWh": circuit.consumed_energy_wh,
            "priority": circuit.priority,
            "isUserControllable": circuit.is_user_controllable,
        }

    # Create mock responses using simulation data
    # Convert simulation objects to dictionaries using factory methods as fallback
    panel_data = SpanPanelDataFactory.create_on_grid_panel_data()
    status_data = SpanPanelStatusFactory.create_status(serial_number=baseline_serial_number)
    storage_data = SpanPanelStorageBatteryFactory.create_battery_data()

    mock_responses = {
        "circuits": circuits,
        "panel": panel_data,
        "status": status_data,
        "battery": storage_data,
    }

    # Add unmapped circuits for tabs 30 and 32 to simulate solar legs
    mock_responses["circuits"]["unmapped_tab_30"] = {
        "id": "unmapped_tab_30",
        "name": "Solar East",
        "tabs": [30],
        "relayState": "OPEN",
        "instantPowerW": 2500.0,
        "producedEnergyWh": 15000.0,
        "consumedEnergyWh": 0.0
    }
    mock_responses["circuits"]["unmapped_tab_32"] = {
        "id": "unmapped_tab_32",
        "name": "Solar West",
        "tabs": [32],
        "relayState": "OPEN",
        "instantPowerW": 2300.0,
        "producedEnergyWh": 14000.0,
        "consumedEnergyWh": 0.0
    }

    # Configure entry to use friendly names (device prefix = True, circuit numbers = False)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
        INVERTER_ENABLE: False,  # Start with solar disabled
        INVERTER_LEG1: 0,
        INVERTER_LEG2: 0,
    }
    entry, _ = setup_span_panel_entry_with_cleanup(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration - this creates base sensor set
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Verify integration loaded properly and created the base sensors
        # Before solar configuration, we should have created the base sensors including the unmapped ones
        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id, {})
        assert entry_data, "Integration should have created entry data"

        coordinator = entry_data.get("coordinator")
        assert coordinator is not None, "Coordinator should exist"

        # Verify unmapped solar circuits exist in coordinator data
        # coordinator.data is the SpanPanel object, not a dictionary
        span_panel = coordinator.data
        assert span_panel is not None, "Coordinator should have span panel data"
        assert hasattr(span_panel, 'circuits'), "SpanPanel should have circuits attribute"
        assert "unmapped_tab_30" in span_panel.circuits, "Should have unmapped tab 30 for solar"
        assert "unmapped_tab_32" in span_panel.circuits, "Should have unmapped tab 32 for solar"

        # Set up storage manager properly using the public API pattern
        from ha_synthetic_sensors import StorageManager, async_setup_synthetic_sensors
        from unittest.mock import patch, AsyncMock

        with (
            patch("ha_synthetic_sensors.storage_manager.Store") as MockStore,
            patch("homeassistant.helpers.device_registry.async_get") as MockDeviceRegistry,
        ):
            # Standard mock setup
            mock_store = AsyncMock()
            mock_store.async_load.return_value = None
            MockStore.return_value = mock_store
            MockDeviceRegistry.return_value = device_registry

            # Create storage manager
            storage_manager = StorageManager(hass, "span_panel_synthetic", enable_entity_listener=False)
            storage_manager._store = mock_store
            await storage_manager.async_load()

            # Create sensor set
            sensor_set_id = "span_panel_sensors"
            await storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier=baseline_serial_number,
                name="SPAN Panel Sensors"
            )

            # Set up synthetic sensors using public API
            sensor_manager = await async_setup_synthetic_sensors(
                hass=hass,
                config_entry=entry,
                async_add_entities=async_add_entities,
                storage_manager=storage_manager,
            )
            assert sensor_manager is not None, "Sensor manager should be created"

        # Now enable solar with circuits 30 and 32
        from custom_components.span_panel.synthetic_solar import handle_solar_options_change

        # Enable solar configuration
        success = await handle_solar_options_change(
            hass=hass,
            config_entry=entry,
            coordinator=coordinator,
            storage_manager=storage_manager,
            enable_solar=True,
            leg1_circuit=30,
            leg2_circuit=32,
            device_name="Test Device",
        )
        assert success, "Solar configuration should succeed"

        # Wait for integration to process the change
        await hass.async_block_till_done()

        # Load the baseline fixture first before applying solar
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        baseline_path = os.path.join(fixtures_dir, "friendly_names.yaml")

        # Load baseline configuration into the sensor set
        with open(baseline_path, 'r') as f:
            baseline_yaml = f.read()

        # Get the sensor manager and load the baseline configuration
        from ha_synthetic_sensors import StorageManager
        storage_manager = StorageManager(hass, f"{DOMAIN}_synthetic")
        await storage_manager.async_load()

        # Get the sensor set and import the baseline YAML
        span_panel = coordinator.data
        device_identifier = span_panel.status.serial_number
        sensor_set_id = f"{device_identifier}_sensors"

        # Create the sensor set with baseline configuration
        if not storage_manager.sensor_set_exists(sensor_set_id):
            await storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier=device_identifier,
                name=f"SPAN Panel {device_identifier} Sensors",
            )

        sensor_set = storage_manager.get_sensor_set(sensor_set_id)

        # Import the baseline YAML configuration
        await sensor_set.async_import_yaml(baseline_yaml)
        await storage_manager.async_save()

        # Now enable solar with circuits 30 and 32 on top of the baseline
        from custom_components.span_panel.synthetic_solar import handle_solar_options_change

        # Enable solar configuration
        success = await handle_solar_options_change(
            hass=hass,
            config_entry=entry,
            coordinator=coordinator,
            storage_manager=storage_manager,
            enable_solar=True,
            leg1_circuit=30,
            leg2_circuit=32,
            device_name="Test Device",
        )
        assert success, "Solar configuration should succeed"

        # Wait for integration to process the solar change
        await hass.async_block_till_done()

        # Export YAML after solar configuration - this is what the integration actually generated
        integration_generated_yaml = sensor_set.export_yaml()

        # DEBUG: Check for whitespace in the raw YAML
        print("=== DEBUG: Raw YAML from integration (friendly names) ===")
        for i, line in enumerate(integration_generated_yaml.splitlines()[:10]):  # First 10 lines
            print(f"Line {i}: {repr(line)}")

        integration_config = yaml.safe_load(integration_generated_yaml)

        # Load expected solar output fixture for friendly names naming (what we expect the integration to generate)
        expected_path = os.path.join(fixtures_dir, "expected_solar_friendly.yaml")

        # Compare integration output with expected fixture
        if os.path.exists(expected_path):
            with open(expected_path, 'r') as f:
                expected_yaml = f.read()

                # DEBUG: Check for whitespace in the expected fixture
                print("=== DEBUG: Raw YAML from expected fixture (friendly names) ===")
                for i, line in enumerate(expected_yaml.splitlines()[:10]):  # First 10 lines
                    print(f"Line {i}: {repr(line)}")

                expected_config = yaml.safe_load(expected_yaml)

            # This is the real test - does the integration generate the expected configuration?
            compare_yaml_structures(integration_config, expected_config, "friendly_names_solar")
        else:
            # Save integration output for debugging if expected fixture doesn't exist
            debug_path = os.path.join(fixtures_dir, "debug_integration_generated_friendly_names.yaml")
            with open(debug_path, 'w') as f:
                f.write(integration_generated_yaml)
            assert False, f"Expected fixture {expected_path} not found. Integration output saved to {debug_path}"


@pytest.mark.asyncio
async def test_solar_configuration_circuit_numbers(
    hass: Any,
    enable_custom_integrations: Any,
    mock_ha_storage,
    mock_synthetic_sensor_manager,
    baseline_serial_number,
    device_registry,
    async_add_entities
):
    """Test solar configuration with circuit numbers naming convention."""

    # Import the simulation factory
    from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory

    # Get realistic data from simulation mode
    simulation_data = await SpanPanelSimulationFactory.get_realistic_panel_data()

    # Convert simulation data to the format expected by the integration
    circuits = {}
    for circuit_id, circuit in simulation_data['circuits'].circuits.additional_properties.items():
        circuits[circuit_id] = {
            "id": circuit_id,
            "name": circuit.name,
            "tabs": circuit.tabs,
            "relayState": circuit.relay_state,
            "instantPowerW": circuit.instant_power_w,
            "producedEnergyWh": circuit.produced_energy_wh,
            "consumedEnergyWh": circuit.consumed_energy_wh,
            "priority": circuit.priority,
            "isUserControllable": circuit.is_user_controllable,
        }

    # Create mock responses using simulation data
    # Convert simulation objects to dictionaries using factory methods as fallback
    panel_data = SpanPanelDataFactory.create_on_grid_panel_data()
    status_data = SpanPanelStatusFactory.create_status(serial_number=baseline_serial_number)
    storage_data = SpanPanelStorageBatteryFactory.create_battery_data()

    mock_responses = {
        "circuits": circuits,
        "panel": panel_data,
        "status": status_data,
        "battery": storage_data,
    }

    # Add unmapped circuits for tabs 30 and 32 to simulate solar legs
    mock_responses["circuits"]["unmapped_tab_30"] = {
        "id": "unmapped_tab_30",
        "name": "Solar East",
        "tabs": [30],
        "relayState": "OPEN",
        "instantPowerW": 2500.0,
        "producedEnergyWh": 15000.0,
        "consumedEnergyWh": 0.0
    }
    mock_responses["circuits"]["unmapped_tab_32"] = {
        "id": "unmapped_tab_32",
        "name": "Solar West",
        "tabs": [32],
        "relayState": "OPEN",
        "instantPowerW": 2300.0,
        "producedEnergyWh": 14000.0,
        "consumedEnergyWh": 0.0
    }

    # Configure entry to use circuit numbers (device prefix = False, circuit numbers = True)
    options = {
        "use_device_prefix": False,
        "use_circuit_numbers": True,
        INVERTER_ENABLE: False,  # Start with solar disabled
        INVERTER_LEG1: 0,
        INVERTER_LEG2: 0,
    }
    entry, _ = setup_span_panel_entry_with_cleanup(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration - this creates base sensor set
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Verify integration loaded properly and created the base sensors
        # Before solar configuration, we should have created the base sensors including the unmapped ones
        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id, {})
        assert entry_data, "Integration should have created entry data"

        coordinator = entry_data.get("coordinator")
        assert coordinator is not None, "Coordinator should exist"

        # Verify unmapped solar circuits exist in coordinator data
        # coordinator.data is the SpanPanel object, not a dictionary
        span_panel = coordinator.data
        assert span_panel is not None, "Coordinator should have span panel data"
        assert hasattr(span_panel, 'circuits'), "SpanPanel should have circuits attribute"
        assert "unmapped_tab_30" in span_panel.circuits, "Should have unmapped tab 30 for solar"
        assert "unmapped_tab_32" in span_panel.circuits, "Should have unmapped tab 32 for solar"

        # Set up storage manager properly using the public API pattern
        from ha_synthetic_sensors import StorageManager, async_setup_synthetic_sensors
        from unittest.mock import patch, AsyncMock

        with (
            patch("ha_synthetic_sensors.storage_manager.Store") as MockStore,
            patch("homeassistant.helpers.device_registry.async_get") as MockDeviceRegistry,
        ):
            # Standard mock setup
            mock_store = AsyncMock()
            mock_store.async_load.return_value = None
            MockStore.return_value = mock_store
            MockDeviceRegistry.return_value = device_registry

            # Create storage manager
            storage_manager = StorageManager(hass, "span_panel_synthetic", enable_entity_listener=False)
            storage_manager._store = mock_store
            await storage_manager.async_load()

            # Create sensor set
            sensor_set_id = "span_panel_sensors"
            await storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier=baseline_serial_number,
                name="SPAN Panel Sensors"
            )

            # Set up synthetic sensors using public API
            sensor_manager = await async_setup_synthetic_sensors(
                hass=hass,
                config_entry=entry,
                async_add_entities=async_add_entities,
                storage_manager=storage_manager,
            )
            assert sensor_manager is not None, "Sensor manager should be created"

        # Now enable solar with circuits 30 and 32
        from custom_components.span_panel.synthetic_solar import handle_solar_options_change

        # Enable solar configuration
        success = await handle_solar_options_change(
            hass=hass,
            config_entry=entry,
            coordinator=coordinator,
            storage_manager=storage_manager,
            enable_solar=True,
            leg1_circuit=30,
            leg2_circuit=32,
            device_name="Test Device",
        )
        assert success, "Solar configuration should succeed"

        # Wait for integration to process the change
        await hass.async_block_till_done()

        # Load the baseline fixture first before applying solar
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        baseline_path = os.path.join(fixtures_dir, "circuit_numbers.yaml")

        # Load baseline configuration into the sensor set
        with open(baseline_path, 'r') as f:
            baseline_yaml = f.read()

        # Get the sensor manager and load the baseline configuration
        from ha_synthetic_sensors import StorageManager
        storage_manager = StorageManager(hass, f"{DOMAIN}_synthetic")
        await storage_manager.async_load()

        # Get the sensor set and import the baseline YAML
        span_panel = coordinator.data
        device_identifier = span_panel.status.serial_number
        sensor_set_id = f"{device_identifier}_sensors"

        # Create the sensor set with baseline configuration
        if not storage_manager.sensor_set_exists(sensor_set_id):
            await storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier=device_identifier,
                name=f"SPAN Panel {device_identifier} Sensors",
            )

        sensor_set = storage_manager.get_sensor_set(sensor_set_id)

        # Import the baseline YAML configuration
        await sensor_set.async_import_yaml(baseline_yaml)
        await storage_manager.async_save()

        # Now enable solar with circuits 30 and 32 on top of the baseline
        from custom_components.span_panel.synthetic_solar import handle_solar_options_change

        # Enable solar configuration
        success = await handle_solar_options_change(
            hass=hass,
            config_entry=entry,
            coordinator=coordinator,
            storage_manager=storage_manager,
            enable_solar=True,
            leg1_circuit=30,
            leg2_circuit=32,
            device_name="Test Device",
        )
        assert success, "Solar configuration should succeed"

        # Wait for integration to process the solar change
        await hass.async_block_till_done()

        # Export YAML after solar configuration - this is what the integration actually generated
        integration_generated_yaml = sensor_set.export_yaml()
        # Trim whitespace from each line before loading
        trimmed_yaml = '\n'.join(line.rstrip() for line in integration_generated_yaml.splitlines())
        integration_config = yaml.safe_load(trimmed_yaml)

        # Load expected solar output fixture for circuit numbers naming (what we expect the integration to generate)
        expected_path = os.path.join(fixtures_dir, "expected_solar_circuit_numbers.yaml")

        # Compare integration output with expected fixture
        if os.path.exists(expected_path):
            with open(expected_path, 'r') as f:
                expected_yaml = f.read()
                # Trim whitespace from each line before loading
                trimmed_expected_yaml = '\n'.join(line.rstrip() for line in expected_yaml.splitlines())
                expected_config = yaml.safe_load(trimmed_expected_yaml)

            # This is the real test - does the integration generate the expected configuration?
            compare_yaml_structures(integration_config, expected_config, "circuit_numbers_solar")
        else:
            # Save integration output for debugging if expected fixture doesn't exist
            debug_path = os.path.join(fixtures_dir, "debug_integration_generated_circuit_numbers.yaml")
            with open(debug_path, 'w') as f:
                f.write(integration_generated_yaml)
            assert False, f"Expected fixture {expected_path} not found. Integration output saved to {debug_path}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
