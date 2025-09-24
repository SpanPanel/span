#!/usr/bin/env python3
"""Generate actual sensor YAML using the same code path as the test."""

import asyncio
from pathlib import Path
import sys

# Add the span project to the path
project_root = Path(__file__).resolve().parents[1]  # Go up 1 level to project root
sys.path.insert(0, str(project_root))

async def generate_actual_yaml():
    """Generate the actual YAML using the test's code path."""

    # Import the test functions
    from tests.test_energy_sensor_integration import MockDeviceData, generate_energy_sensor_yaml

    # Use the same test data as the test
    device_data = MockDeviceData(serial_number="test_span_001")

    # Generate the YAML using the same function as the test
    sensor_yaml, sensor_mapping = await generate_energy_sensor_yaml(device_data)

    # Write to /tmp
    with open('/tmp/actual_sensor_from_test.yaml', 'w') as f:
        f.write(sensor_yaml)


if __name__ == "__main__":
    asyncio.run(generate_actual_yaml())
