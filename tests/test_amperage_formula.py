#!/usr/bin/env python3
"""Test amperage formula with literal voltage attributes."""

import yaml
from unittest.mock import MagicMock

# Mock the synthetic sensors package
try:
    from ha_synthetic_sensors.config_manager import ConfigManager
    from ha_synthetic_sensors.evaluator import Evaluator
except ImportError:
    print("ha-synthetic-sensors package not available")
    exit(1)


def test_amperage_formula():
    """Test that amperage formula works with literal voltage attribute."""

    # Create YAML configuration similar to our templates
    yaml_content = """
version: "1.0"
sensors:
  test_circuit_power:
    name: "Test Circuit Power"
    formula: "source_value"
    variables:
      source_value: "sensor.test_power"
    attributes:
      tabs: "tabs [30:32]"
      voltage: 240
      amperage:
        formula: "state / voltage"
        metadata:
          unit_of_measurement: "A"
          device_class: "current"
          suggested_display_precision: 2
    metadata:
      unit_of_measurement: "W"
      device_class: "power"
      state_class: "measurement"
"""

    # Create mock Home Assistant
    mock_hass = MagicMock()

    # Mock the test power sensor state
    mock_state = MagicMock()
    mock_state.state = "1200.0"  # 1200W
    mock_hass.states.get.return_value = mock_state

    # Load configuration
    config_manager = ConfigManager(mock_hass)
    config = config_manager.load_from_yaml(yaml_content)

    print(f"Loaded {len(config.sensors)} sensors")

    # Get the test sensor
    test_sensor = config.sensors[0]
    print(f"Sensor: {test_sensor.unique_id}")
    print(f"Formulas: {len(test_sensor.formulas)}")

    # Check that we have the expected formulas
    formula_ids = [f.id for f in test_sensor.formulas]
    print(f"Formula IDs: {formula_ids}")

    # Find the amperage formula
    amperage_formula = None
    for formula in test_sensor.formulas:
        if formula.id == "test_circuit_power_amperage":
            amperage_formula = formula
            break

    if amperage_formula:
        print(f"Amperage formula: {amperage_formula.formula}")
        print(f"Amperage variables: {amperage_formula.variables}")

        # Test evaluation
        evaluator = Evaluator(mock_hass)
        result = evaluator.evaluate_formula(amperage_formula)

        # Expected: 1200W / 240V = 5A
        expected_amperage = 1200.0 / 240.0
        print(f"Expected amperage: {expected_amperage}A")
        print(f"Actual result: {result}")

        if abs(result - expected_amperage) < 0.01:
            print("✅ Amperage calculation works correctly!")
            return True
        else:
            print("❌ Amperage calculation failed!")
            return False
    else:
        print("❌ Amperage formula not found!")
        return False


if __name__ == "__main__":
    success = test_amperage_formula()
    exit(0 if success else 1)
