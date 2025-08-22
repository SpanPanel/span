#!/usr/bin/env python3
"""Generate YAML from templates to demonstrate the template system.

This script shows how to use the YAML templates directly to generate
sensor configurations without needing the full integration setup.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add the span project to the path
project_root = Path(__file__).resolve().parents[1]  # Go up 1 level to project root
sys.path.insert(0, str(project_root))


async def generate_yaml_from_templates():
    """Generate YAML using the template system directly."""

    print("🚀 Starting YAML generation from templates...")

    # Create mock hass for the functions that need it
    mock_hass = MagicMock()

    # Mock the async_add_executor_job method to handle file reading
    async def mock_async_add_executor_job(func, *args, **kwargs):
        """Mock async_add_executor_job to handle file reading synchronously."""
        if func.__name__ == 'read_text':
            # Handle file reading synchronously
            return func(*args, **kwargs)
        return func(*args, **kwargs)

    mock_hass.async_add_executor_job = mock_async_add_executor_job

    # Example placeholders for a circuit sensor
    placeholders = {
        "device_identifier": "span-demo-001",
        "sensor_key": "span_span-demo-001_kitchen_lights_energy_consumed",
        "sensor_name": "Kitchen Lights Energy Consumed",
        "entity_id": "sensor.span-demo-001_kitchen_lights_energy_consumed",
        "tabs_attribute": "kitchen",
        "voltage_attribute": "120",
        "energy_display_precision": "2",
        "energy_grace_period_minutes": "15"
    }

    try:
        # Import the template utilities
        from custom_components.span_panel.synthetic_utils import combine_yaml_templates

        # Generate YAML using the circuit energy consumed template
        print("📋 Generating YAML from circuit_energy_consumed template...")
        result = await combine_yaml_templates(
            mock_hass,
            ["circuit_energy_consumed"],
            placeholders
        )

        print(f"✅ Generated YAML with {len(result['sensor_configs'])} sensors")
        print(f"📏 YAML size: {len(result['filled_template'])} characters")

        # Write the YAML to disk
        output_file = '/tmp/span_template_demo.yaml'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result['filled_template'])

        print(f"✅ YAML saved to: {output_file}")

        # Show the generated YAML
        print("\n" + "=" * 60)
        print("GENERATED YAML:")
        print("=" * 60)
        print(result['filled_template'])
        print("=" * 60)

        return result['filled_template']

    except Exception as e:
        print(f"❌ Error generating YAML: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    yaml_content = asyncio.run(generate_yaml_from_templates())
    if yaml_content:
        print("\n🎉 Template generation completed successfully!")
        print("📁 Check /tmp/span_template_demo.yaml for the generated configuration")
    else:
        print("\n💥 Template generation failed!")
        sys.exit(1)
