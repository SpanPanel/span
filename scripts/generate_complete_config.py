#!/usr/bin/env python3
"""Generate complete sensor YAML configuration using the span integration's actual code path.

This script generates the full sensor configuration that would be created by the span
integration, including all panel sensors and circuit sensors. This helps test the complete
YAML generation pipeline and identify any formatting issues.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add the span project to the path
project_root = Path(__file__).resolve().parents[1]  # Go up 1 level to project root
sys.path.insert(0, str(project_root))

async def generate_complete_yaml():
    """Generate the complete YAML configuration using the integration's code path."""

    print("🚀 Starting complete YAML generation...")

    # Import what we need step by step to avoid import errors
    try:
        from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory
        print("✅ Imported SpanPanelSimulationFactory")
    except ImportError as e:
        print(f"❌ Failed to import SpanPanelSimulationFactory: {e}")
        return None, None

    # Use the same pattern as generate_basic_sensor_fixtures.py
    simulation_config = "simulation_config_32_circuit"
    print(f"📋 Using simulation config: {simulation_config}")

    try:
        # Create mock responses using the simulation factory (like generate_basic_sensor_fixtures.py)
        print("🏗️ Creating mock panel data...")
        simulation_factory = SpanPanelSimulationFactory()
        mock_responses = await simulation_factory.get_realistic_panel_data(config_name=simulation_config)

        # Create mock span panel with simulation data
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "span-sim-001"  # Use consistent test serial

        # Convert simulation panel_state to mock panel data (like working tests)
        panel_state = mock_responses["panel_state"]
        mock_panel_data = MagicMock()
        mock_panel_data.instantGridPowerW = panel_state.instant_grid_power_w
        mock_panel_data.feedthroughPowerW = panel_state.feedthrough_power_w

        # Add energy data
        if hasattr(panel_state, 'main_meter_energy'):
            mock_panel_data.mainMeterEnergyProducedWh = panel_state.main_meter_energy.produced_energy_wh
            mock_panel_data.mainMeterEnergyConsumedWh = panel_state.main_meter_energy.consumed_energy_wh
        else:
            mock_panel_data.mainMeterEnergyProducedWh = 1000.0
            mock_panel_data.mainMeterEnergyConsumedWh = 2000.0

        # Add feedthrough energy data
        if hasattr(panel_state, 'feedthrough_energy'):
            mock_panel_data.feedthroughEnergyProducedWh = panel_state.feedthrough_energy.produced_energy_wh
            mock_panel_data.feedthroughEnergyConsumedWh = panel_state.feedthrough_energy.consumed_energy_wh
        else:
            mock_panel_data.feedthroughEnergyProducedWh = 500.0
            mock_panel_data.feedthroughEnergyConsumedWh = 750.0

        # Attach panel data to mock span panel
        mock_span_panel.panel = mock_panel_data

        # Convert simulation circuits to the format expected by circuit sensor generator
        circuits_data = mock_responses["circuits"]
        circuit_dict = {}

        # Extract circuits from the simulation data structure and create dict by ID
        for circuit_id, circuit in circuits_data.circuits.additional_properties.items():
            circuit_mock = MagicMock()
            circuit_mock.id = circuit.id
            circuit_mock.name = circuit.name
            circuit_mock.instantPowerW = circuit.instant_power_w
            circuit_mock.producedEnergyWh = circuit.produced_energy_wh
            circuit_mock.consumedEnergyWh = circuit.consumed_energy_wh
            circuit_mock.relayState = circuit.relay_state.value
            circuit_mock.priority = circuit.priority.value
            circuit_mock.isUserControllable = circuit.is_user_controllable
            circuit_mock.tabs = [tab for tab in circuit.tabs] if circuit.tabs else []

            circuit_dict[circuit_id] = circuit_mock

        # Set the circuits as a dict on the mock span panel
        mock_span_panel.circuits = circuit_dict

        print(f"📡 Panel serial: {mock_span_panel.status.serial_number}")
        print(f"🔌 Found {len(circuit_dict)} circuits")

        # Create minimal mock coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.data = mock_span_panel
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {
            "energy_reporting_grace_period": 15,
        }
        mock_coordinator.config_entry.entry_id = "test_entry_id"

        # Use the device identifier from global settings for proper entity_id prefixes
        device_identifier = mock_span_panel.status.serial_number  # "span-sim-001"
        mock_coordinator.config_entry.title = device_identifier  # Used by entity_id construction functions
        mock_coordinator.config_entry.data = {"device_name": device_identifier}
        device_name = device_identifier  # Use device identifier as device name for entity_id generation

        # Generate panel sensors first
        print("⚙️ Generating panel sensors...")
        try:
            from custom_components.span_panel.synthetic_panel_circuits import generate_panel_sensors
            panel_sensor_configs, panel_backing_entities, global_settings, panel_mapping = await generate_panel_sensors(
                mock_coordinator, mock_span_panel, device_name
            )
            print(f"   ✅ Generated {len(panel_sensor_configs)} panel sensors")
        except Exception as e:
            print(f"   ❌ Failed to generate panel sensors: {e}")
            panel_sensor_configs = {}
            global_settings = {
                "device_identifier": mock_span_panel.status.serial_number,
                "variables": {"energy_grace_period_minutes": "15"}
            }

        # Generate circuit sensors
        print("🔌 Generating circuit sensors...")
        try:
            from custom_components.span_panel.synthetic_named_circuits import generate_named_circuit_sensors
            circuit_sensor_configs, circuit_backing_entities, circuit_global_settings, circuit_mapping = await generate_named_circuit_sensors(
                mock_coordinator, mock_span_panel, device_name
            )
            print(f"   ✅ Generated {len(circuit_sensor_configs)} circuit sensors")
        except Exception as e:
            print(f"   ❌ Failed to generate circuit sensors: {e}")
            circuit_sensor_configs = {}

        # Combine all sensor configs
        print("📊 Combining sensor configurations...")
        all_sensor_configs = {**panel_sensor_configs, **circuit_sensor_configs}

        print(f"📈 Total sensors generated: {len(all_sensor_configs)}")
        print(f"   • Panel sensors: {len(panel_sensor_configs)}")
        print(f"   • Circuit sensors: {len(circuit_sensor_configs)}")

        # Generate YAML using the actual integration code
        print("🔨 Generating YAML using integration's code path...")
        try:
            from custom_components.span_panel.synthetic_sensors import _construct_complete_yaml_config
            yaml_content = await _construct_complete_yaml_config(all_sensor_configs, global_settings)
        except Exception as e:
            print(f"   ❌ Failed to use integration YAML generation, falling back to simple dump: {e}")
            import yaml
            complete_yaml_dict = {
                "version": "1.0",
                "global_settings": global_settings,
                "sensors": all_sensor_configs,
            }
            yaml_content = yaml.dump(complete_yaml_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

        # Write the YAML to disk
        output_file = '/tmp/span_simulator_complete_config.yaml'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(yaml_content)

        print(f"✅ Complete YAML configuration saved to: {output_file}")
        print(f"📏 YAML size: {len(yaml_content)} characters")

        # Also save a summary
        summary_file = '/tmp/span_simulator_config_summary.txt'
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(f"SPAN Panel Synthetic Sensor Configuration Summary\n")
            f.write(f"=" * 50 + "\n\n")
            f.write(f"Simulation Config: {simulation_config}\n")
            f.write(f"Device Name: {device_name}\n")
            f.write(f"Serial Number: {mock_span_panel.status.serial_number}\n")
            f.write(f"Grace Period: {global_settings.get('variables', {}).get('energy_grace_period_minutes', 'N/A')} minutes\n\n")

            f.write(f"Sensor Counts:\n")
            f.write(f"  Panel sensors: {len(panel_sensor_configs)}\n")
            f.write(f"  Circuit sensors: {len(circuit_sensor_configs)}\n")
            f.write(f"  Total sensors: {len(all_sensor_configs)}\n\n")

            f.write("Panel Sensors:\n")
            for key in panel_sensor_configs.keys():
                f.write(f"  - {key}\n")

            f.write(f"\nCircuit Sensors ({len(circuit_sensor_configs)} total):\n")
            circuit_names = list(circuit_sensor_configs.keys())[:10]  # Show first 10
            for key in circuit_names:
                f.write(f"  - {key}\n")
            if len(circuit_sensor_configs) > 10:
                f.write(f"  ... and {len(circuit_sensor_configs) - 10} more\n")

        print(f"📋 Configuration summary saved to: {summary_file}")

        # Show first part of generated YAML
        print("\n" + "=" * 60)
        print("GENERATED YAML (first 1000 characters):")
        print("=" * 60)
        print(yaml_content[:1000])
        if len(yaml_content) > 1000:
            print("...")
        print("=" * 60)

        return yaml_content, all_sensor_configs

    except Exception as e:
        print(f"❌ Error generating YAML: {e}")
        import traceback
        traceback.print_exc()
        return None, None

if __name__ == "__main__":
    yaml_content, yaml_dict = asyncio.run(generate_complete_yaml())
    if yaml_content:
        print("\n🎉 Script completed successfully!")
        print("📁 Check /tmp/span_simulator_complete_config.yaml for the full configuration")
        print("📄 Check /tmp/span_simulator_config_summary.txt for a summary")
    else:
        print("\n💥 Script failed!")
        sys.exit(1)
