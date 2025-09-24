#!/usr/bin/env python3
"""Generate solar sensor fixtures by adding solar configuration to existing base fixtures.

This utility loads existing base fixtures (like friendly_names.yaml) and adds
solar sensors for specific leg configurations to create expected test outputs.
"""

import asyncio
from pathlib import Path
import sys
from unittest.mock import MagicMock

import yaml

# Add the project root to Python path so imports work
project_root = Path(__file__).resolve().parents[2]  # Go up 2 levels: utils -> tests -> project_root
sys.path.insert(0, str(project_root))

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)


async def generate_solar_fixture_with_legs(
    base_fixture_name: str,
    leg1_circuit: str,
    leg2_circuit: str,
    output_name: str = None
) -> str:
    """Generate solar fixture by adding solar sensors to a base fixture.

    Args:
        base_fixture_name: Name of base fixture file (e.g., "friendly_names")
        leg1_circuit: First solar leg circuit ID (e.g., "unmapped_tab_30")
        leg2_circuit: Second solar leg circuit ID (e.g., "unmapped_tab_32")
        output_name: Output filename (defaults to "{base_fixture_name}_with_solar")

    Returns:
        Generated YAML content as string

    """
    try:
        # Load the base fixture
        fixtures_dir = project_root / "tests" / "fixtures"
        base_fixture_path = fixtures_dir / f"{base_fixture_name}.yaml"

        if not base_fixture_path.exists():
            raise FileNotFoundError(f"Base fixture not found: {base_fixture_path}")

        print(f"📝 Loading base fixture: {base_fixture_path}")
        with open(base_fixture_path) as f:
            base_yaml_data = yaml.safe_load(f)

        print(f"🔍 Base fixture has {len(base_yaml_data.get('sensors', {}))} sensors")

        # Extract configuration from base fixture
        global_settings = base_yaml_data.get("global_settings", {})
        device_identifier = global_settings.get("device_identifier", "sp3-simulation-001")

        # Use explicit config flags based on fixture name - NO INFERENCE!
        if base_fixture_name == "friendly_names":
            use_device_prefix = True
            use_circuit_numbers = False
        elif base_fixture_name == "circuit_numbers":
            use_device_prefix = True
            use_circuit_numbers = True
        elif base_fixture_name == "legacy_no_prefix":
            use_device_prefix = False
            use_circuit_numbers = False
        else:
            raise ValueError(f"Unknown fixture type: {base_fixture_name}")

        print(f"🔍 Using explicit config: use_device_prefix={use_device_prefix}, use_circuit_numbers={use_circuit_numbers}")

        # Create mock coordinator with explicit flags
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {
            USE_CIRCUIT_NUMBERS: use_circuit_numbers,
            USE_DEVICE_PREFIX: use_device_prefix,
        }
        mock_coordinator.config_entry.data = {"device_name": "Span Panel"}
        mock_coordinator.config_entry.title = "Span Panel"

        # Create mock span panel with solar circuits
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = device_identifier

        # Create mock circuits for the solar legs
        mock_circuit_30 = MagicMock()
        mock_circuit_30.name = "Solar East"
        mock_circuit_30.circuit_id = leg1_circuit
        mock_circuit_30.tabs = [30]

        mock_circuit_32 = MagicMock()
        mock_circuit_32.name = "Solar West"
        mock_circuit_32.circuit_id = leg2_circuit
        mock_circuit_32.tabs = [32]

        # Add existing circuits from base fixture (needed for backing entity validation)
        mock_span_panel.circuits = {
            leg1_circuit: mock_circuit_30,
            leg2_circuit: mock_circuit_32,
        }

        print(f"⚙️ Generating solar sensors for legs {leg1_circuit} and {leg2_circuit}...")

        # Prefer using the integration's solar generator to ensure consistency
        from custom_components.span_panel.synthetic_solar import (
            generate_solar_sensors_with_entity_ids,
        )

        # Build mock coordinator/span_panel similarly to other generators
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {
            "energy_reporting_grace_period": 15,
            "power_display_precision": 0,
            "energy_display_precision": 2,
        }
        mock_coordinator.config_entry.data = {"device_name": "Span Panel"}

        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = device_identifier

        # Compose leg entity IDs based on unmapped tab convention
        leg1_number = int(leg1_circuit.split("_")[-1])
        leg2_number = int(leg2_circuit.split("_")[-1])
        leg1_power = f"sensor.span_panel_unmapped_tab_{leg1_number}_power"
        leg2_power = f"sensor.span_panel_unmapped_tab_{leg2_number}_power"

        # Use integration function to get solar sensor configs
        solar_sensor_configs = await generate_solar_sensors_with_entity_ids(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            leg1_entity_id=leg1_power,
            leg2_entity_id=leg2_power,
            device_name="Span Panel",
        )

        print(f"☀️ Generated {len(solar_sensor_configs)} solar sensors")

        # Combine base sensors with solar sensors
        combined_sensors = {**base_yaml_data.get("sensors", {}), **solar_sensor_configs}

        # Create the combined YAML structure (no backing entities or global settings for solar)
        combined_yaml = {
            "version": base_yaml_data.get("version", "1.0"),
            "global_settings": base_yaml_data.get("global_settings", {}),
            "sensors": combined_sensors,
        }

        # Convert to YAML string
        yaml_content = yaml.dump(combined_yaml, default_flow_style=False, sort_keys=False)

        print(f"✅ Combined fixture has {len(combined_sensors)} total sensors")
        return yaml_content

    except Exception as e:
        print(f"❌ Error generating solar fixture: {e}")
        import traceback
        traceback.print_exc()
        return None


async def generate_all_solar_fixtures():
    """Generate solar fixtures for different base patterns."""

    fixtures_dir = project_root / "tests" / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)

    print("🚀 Starting solar fixture generation...")

    # Define solar configurations to generate
    solar_configs = [
        {
            "base": "friendly_names",
            "leg1": "unmapped_tab_30",
            "leg2": "unmapped_tab_32",
            "output": "expected_solar_friendly"
        },
        {
            "base": "circuit_numbers",
            "leg1": "unmapped_tab_30",
            "leg2": "unmapped_tab_32",
            "output": "expected_solar_circuit_numbers"
        },
    ]

    success_count = 0

    for config in solar_configs:
        print(f"\n📝 Generating {config['output']}.yaml...")

        yaml_content = await generate_solar_fixture_with_legs(
            base_fixture_name=config["base"],
            leg1_circuit=config["leg1"],
            leg2_circuit=config["leg2"],
            output_name=config["output"]
        )

        if yaml_content:
            # Write to file
            output_path = fixtures_dir / f"{config['output']}.yaml"
            output_path.write_text(yaml_content)
            print(f"✅ Generated {output_path}")
            success_count += 1
        else:
            print(f"❌ Failed to generate {config['output']}")

    print("\n🎉 Solar fixture generation complete!")
    print(f"📊 Generated {success_count}/{len(solar_configs)} solar fixtures")
    print(f"📁 Fixtures saved to: {fixtures_dir}")


if __name__ == "__main__":
    asyncio.run(generate_all_solar_fixtures())
