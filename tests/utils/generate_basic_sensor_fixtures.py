#!/usr/bin/env python3
"""Generate YAML fixtures using simulation data with minimal HA mocking.

This approach uses the simulation factory to get real circuit data,
then mocks just the minimal HA parts needed to run the synthetic sensor
configuration generation.
"""

import asyncio
from pathlib import Path
import sys
from unittest.mock import MagicMock

# Add the project root to Python path so imports work
project_root = Path(__file__).resolve().parents[2]  # Go up 2 levels: utils -> tests -> project_root
sys.path.insert(0, str(project_root))

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)


def post_process_yaml_for_legacy(yaml_content: str, pattern_name: str, options: dict) -> str:
    """Post-process YAML to strip device prefixes for legacy naming patterns.

    This function handles the testing requirement for legacy naming patterns by
    stripping device prefixes from sensor keys and entity_ids when use_device_prefix=False.
    This is only needed for testing since production never installs legacy mode from scratch.
    """
    if not options.get(USE_DEVICE_PREFIX, True):
        print(f"  🔧 Post-processing {pattern_name} to remove device prefixes for legacy testing...")

        # Parse the YAML content
        import yaml
        yaml_data = yaml.safe_load(yaml_content)

        # Get the device identifier to strip
        device_identifier = yaml_data.get("global_settings", {}).get("device_identifier", "")

        # Process each sensor
        new_sensors = {}
        for sensor_key, sensor_config in yaml_data.get("sensors", {}).items():
            # Keep the original sensor key - don't strip device prefix from it
            new_sensor_key = sensor_key

            # Only strip device prefix from entity_id if it has one
            entity_id = sensor_config.get("entity_id", "")
            if entity_id.startswith(f"sensor.span_{device_identifier}_"):
                # Remove device prefix but keep "sensor.span_panel_" for legacy format
                base_entity = entity_id.replace(f"sensor.span_{device_identifier}_", "")
                sensor_config["entity_id"] = f"sensor.span_panel_{base_entity}"

            # Strip device prefix from source_value if it has one
            variables = sensor_config.get("variables", {})
            if "source_value" in variables:
                source_value = variables["source_value"]
                if source_value.startswith(f"sensor.span_{device_identifier}_"):
                    # Remove device prefix but keep "sensor.span_panel_" for legacy format
                    base_source = source_value.replace(f"sensor.span_{device_identifier}_", "")
                    variables["source_value"] = f"sensor.span_panel_{base_source}"

            new_sensors[new_sensor_key] = sensor_config

        # Update the YAML data
        yaml_data["sensors"] = new_sensors

        # Convert back to YAML string
        processed_content = yaml.dump(yaml_data, default_flow_style=False, sort_keys=False)

        print(f"  ✅ Stripped device prefixes from {len(new_sensors)} sensors")
        return processed_content

    return yaml_content


async def generate_single_fixture(pattern_name: str, options: dict) -> str:
    """Generate YAML fixture for a single naming pattern."""

    try:
        print(f"  📝 Generating {pattern_name}...")
        print(f"  🔍 Getting simulation data for {pattern_name}...")

        # Use simulation factory to get real data
        from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory
        simulation_factory = SpanPanelSimulationFactory()
        mock_responses = await simulation_factory.get_realistic_panel_data()

        print(f"  🏗️ Creating synthetic sensor config for {pattern_name}...")

        # Create minimal mock coordinator (like working tests do)
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = options
        mock_coordinator.config_entry.data = {"device_name": "Span Panel"}
        mock_coordinator.config_entry.title = "Span Panel"

        # Create mock span panel with simulation data
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "sp3-simulation-001"

        # Convert simulation panel_state to mock panel data (like working tests)
        panel_state = mock_responses["panel_state"]
        mock_panel_data = MagicMock()
        mock_panel_data.instantGridPowerW = panel_state.instant_grid_power_w
        mock_panel_data.feedthroughPowerW = panel_state.feedthrough_power_w

        # Add energy data (simulation has this in panel_state)
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

        print(f"  ⚙️ Generating panel sensors for {pattern_name}...")

        # Create minimal mock hass
        mock_hass = MagicMock()

        # Generate panel sensors
        from custom_components.span_panel.synthetic_panel_circuits import generate_panel_sensors
        device_name = "Span Panel"  # Default device name for testing
        panel_sensor_configs, panel_backing_entities, global_settings, panel_mapping = await generate_panel_sensors(
            mock_hass, mock_coordinator, mock_span_panel, device_name
        )

        print(f"  ⚙️ Generating circuit sensors for {pattern_name}...")

        from custom_components.span_panel.synthetic_named_circuits import (
            generate_named_circuit_sensors,
        )

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
            circuit_mock.tabs = list(circuit.tabs) if circuit.tabs else []

            circuit_dict[circuit_id] = circuit_mock

        print(f"  🔌 Found {len(circuit_dict)} circuits from simulation")

        # Set the circuits as a dict on the mock span panel
        mock_span_panel.circuits = circuit_dict

        # Generate circuit sensors
        circuit_sensor_configs, circuit_backing_entities, circuit_global_settings, circuit_mapping = await generate_named_circuit_sensors(
            mock_hass, mock_coordinator, mock_span_panel, device_name
        )

        print(f"  DEBUG: Panel sensors: {len(panel_sensor_configs)}")
        print(f"  DEBUG: Circuit sensors: {len(circuit_sensor_configs)}")

        print(f"  📊 Creating YAML structure for {pattern_name}...")

        # Combine all sensor configs
        all_sensor_configs = {**panel_sensor_configs, **circuit_sensor_configs}

        print(f"  DEBUG: Combined sensors: {len(all_sensor_configs)}")

        # Create YAML structure (like test_yaml_generator_validation.py does)
        synthetic_yaml = {
            "version": "1.0",
            "global_settings": global_settings,
            "sensors": all_sensor_configs,
        }

        # Convert to YAML string (like the working tests do)
        import yaml
        yaml_content = yaml.dump(synthetic_yaml, default_flow_style=False, sort_keys=False)

        # Post-process for legacy naming patterns (test-only)
        # Note: Only legacy_no_prefix needs post-processing to strip device prefixes
        if pattern_name == "legacy_no_prefix":
            yaml_content = post_process_yaml_for_legacy(yaml_content, pattern_name, options)

        print(f"  ✅ Generated YAML for {pattern_name} ({len(yaml_content)} chars)")
        return yaml_content

    except Exception as e:
        print(f"  ❌ Error generating {pattern_name}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def generate_all_fixtures():
    """Generate all YAML fixtures using simulation-based approach."""

    fixtures_dir = project_root / "tests" / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)

    print("🚀 Starting YAML fixture generation using simulation data...")
    print("📝 Generating naming pattern fixtures...")

    # Define the 3 patterns to generate: legacy, circuit_naming, friendly_naming
    patterns = {
        "legacy_no_prefix": {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False},
        "circuit_numbers": {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: True},
        "friendly_names": {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False},
    }

    success_count = 0

    for pattern_name, options in patterns.items():
        yaml_content = await generate_single_fixture(pattern_name, options)
        if yaml_content:
            # Write to file (post-processing already done in generate_single_fixture)
            fixture_path = fixtures_dir / f"{pattern_name}.yaml"
            fixture_path.write_text(yaml_content)
            print(f"  ✅ Generated {fixture_path}")
            success_count += 1
        else:
            print(f"  ❌ Failed to generate {pattern_name}")

    print("\n🎉 YAML fixture generation complete!")
    print(f"📊 Generated {success_count}/{len(patterns)} fixtures")
    print(f"📁 Fixtures saved to: {fixtures_dir}")


if __name__ == "__main__":
    asyncio.run(generate_all_fixtures())
