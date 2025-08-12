#!/usr/bin/env python3
"""Generate actual sensor YAML using the same code path as the test."""

import asyncio
import sys
import os

# Add the span project to the path
sys.path.insert(0, '/Users/bflood/projects/HA/span')

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

    print("Generated YAML saved to /tmp/actual_sensor_from_test.yaml")
    print("\nGenerated YAML content:")
    print("=" * 50)
    print(sensor_yaml)
    print("=" * 50)
    print(f"\nSensor mapping: {sensor_mapping}")

if __name__ == "__main__":
    asyncio.run(generate_actual_yaml())
