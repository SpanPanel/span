#!/usr/bin/env python3
"""Generate YAML from templates to demonstrate the template system.

This script shows how to use the YAML templates directly to generate
sensor configurations without needing the full integration setup.
"""

import asyncio
from pathlib import Path
import sys
from unittest.mock import MagicMock

# Add the span project to the path
project_root = Path(__file__).resolve().parents[1]  # Go up 1 level to project root
sys.path.insert(0, str(project_root))


async def generate_yaml_from_templates():
    """Generate YAML using the template system directly."""


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
        result = await combine_yaml_templates(
            mock_hass,
            ["circuit_energy_consumed"],
            placeholders
        )


        # Write the YAML to disk
        output_file = '/tmp/span_template_demo.yaml'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result['filled_template'])


        # Show the generated YAML

        return result['filled_template']

    except Exception:
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    yaml_content = asyncio.run(generate_yaml_from_templates())
    if yaml_content:
        pass
    else:
        sys.exit(1)
