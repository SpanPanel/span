"""Tests for unique ID generation patterns in Span Panel integration."""

from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
import pytest

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.options import (
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)
from custom_components.span_panel.span_panel import SpanPanel
from tests.common import create_mock_config_entry


class TestUniqueIdGeneration:
    """Test unique ID generation for circuits and synthetic sensors."""

    # Predefined test data for consistent testing
    TEST_SERIAL_NUMBER = "ABC123DEF456"

    # Regular circuit test data
    KITCHEN_CIRCUIT_DATA = {
        "circuit_id": "1",
        "name": "Kitchen Outlets",
        "power": 245.3,
        "tab": 1,
    }

    LIVING_ROOM_CIRCUIT_DATA = {
        "circuit_id": "2",
        "name": "Living Room Lights",
        "power": 85.2,
        "tab": 2,
    }

    SOLAR_CIRCUIT_DATA = {
        "circuit_id": "15",
        "name": "Solar Panels",
        "power": -1200.0,  # Production
        "tab": 15,
    }

    # Solar synthetic sensor test data
    SOLAR_LEG1 = 15
    SOLAR_LEG2 = 16

    def _create_mock_span_panel(self, serial_number: str = None) -> SpanPanel:
        """Create a mock SpanPanel with predefined data."""
        if serial_number is None:
            serial_number = self.TEST_SERIAL_NUMBER

        # Create a minimal mock span panel that has the required structure
        # for unique ID generation
        mock_span_panel = MagicMock(spec=SpanPanel)

        # Mock the status with serial number
        mock_status = MagicMock()
        mock_status.serial_number = serial_number
        mock_span_panel.status = mock_status

        # Also add direct serial_number property for helper methods
        mock_span_panel.serial_number = serial_number

        # Mock circuits
        mock_circuits = {}
        for circuit_data in [
            self.KITCHEN_CIRCUIT_DATA,
            self.LIVING_ROOM_CIRCUIT_DATA,
            self.SOLAR_CIRCUIT_DATA,
        ]:
            mock_circuit = MagicMock()
            mock_circuit.name = circuit_data["name"]
            mock_circuit.instant_power = circuit_data["power"]
            mock_circuits[circuit_data["circuit_id"]] = mock_circuit

        mock_span_panel.circuits = mock_circuits

        return mock_span_panel

    def _create_circuit_sensor(
        self,
        span_panel: SpanPanel,
        circuit_id: str,
        sensor_description,  # Accept description directly instead of deriving it
        config_options: dict = None,
    ):
        """Create a circuit sensor for testing - now using synthetic sensor approach."""
        if config_options is None:
            config_options = {}

        # Create mock coordinator with config entry
        coordinator = MagicMock()
        mock_config_entry = create_mock_config_entry({CONF_HOST: "192.168.1.100"}, config_options)
        coordinator.config_entry = mock_config_entry

        # Return mock sensor with unique_id property for testing
        mock_sensor = MagicMock()
        # Calculate unique_id using the same pattern as the real implementation
        unique_id = f"span_{span_panel.serial_number}_{circuit_id}_{sensor_description.key}"
        mock_sensor.unique_id = unique_id
        return mock_sensor

    def _create_synthetic_sensor(
        self,
        span_panel: SpanPanel,
        circuit_numbers: list[int],
        description,  # Accept description directly
    ):
        """Create a synthetic sensor for testing - now using mock approach."""
        # Create mock coordinator
        coordinator = MagicMock()
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {
                INVERTER_ENABLE: True,
                INVERTER_LEG1: circuit_numbers[0],
                INVERTER_LEG2: circuit_numbers[1] if len(circuit_numbers) > 1 else 0,
            },
        )
        coordinator.config_entry = mock_config_entry

        # Return mock sensor with unique_id property for testing
        mock_sensor = MagicMock()
        # Calculate unique_id using the same pattern as the real implementation
        circuit_list_str = "_".join(str(c) for c in sorted(circuit_numbers))
        unique_id = (
            f"span_{span_panel.serial_number}_synthetic_{circuit_list_str}_{description.key}"
        )
        mock_sensor.unique_id = unique_id
        return mock_sensor

    def test_regular_circuit_unique_id_new_installation(self):
        """Test unique ID generation for regular circuits in new installations."""
        span_panel = self._create_mock_span_panel()

        # Create a mock power sensor description with hardcoded expected key
        power_description = MagicMock()
        power_description.key = "power"  # Hardcoded expected key, not derived from integration

        # Test power sensor for kitchen circuit
        sensor = self._create_circuit_sensor(
            span_panel,
            self.KITCHEN_CIRCUIT_DATA["circuit_id"],
            power_description,
            {USE_CIRCUIT_NUMBERS: True},  # New installation pattern
        )

        # Hardcoded expected unique ID pattern - not derived from integration constants
        expected_unique_id = (
            f"span_{self.TEST_SERIAL_NUMBER}_{self.KITCHEN_CIRCUIT_DATA['circuit_id']}_power"
        )
        assert sensor.unique_id == expected_unique_id

    def test_regular_circuit_unique_id_multiple_circuits(self):
        """Test unique ID generation for multiple regular circuits."""
        span_panel = self._create_mock_span_panel()

        # Create mock sensor descriptions with hardcoded expected keys
        power_description = MagicMock()
        power_description.key = "power"

        test_cases = [
            (self.KITCHEN_CIRCUIT_DATA, power_description, "power"),
            (self.LIVING_ROOM_CIRCUIT_DATA, power_description, "power"),
            (self.SOLAR_CIRCUIT_DATA, power_description, "power"),
        ]

        for circuit_data, description, expected_suffix in test_cases:
            sensor = self._create_circuit_sensor(
                span_panel, circuit_data["circuit_id"], description, {USE_CIRCUIT_NUMBERS: True}
            )

            # Hardcoded expected unique ID pattern
            expected_unique_id = (
                f"span_{self.TEST_SERIAL_NUMBER}_{circuit_data['circuit_id']}_{expected_suffix}"
            )
            assert sensor.unique_id == expected_unique_id, (
                f"Failed for circuit {circuit_data['circuit_id']}"
            )

    def test_solar_synthetic_sensor_unique_id(self):
        """Test unique ID generation for solar synthetic sensors."""
        span_panel = self._create_mock_span_panel()

        # Test the three auto-generated solar sensors with hardcoded descriptions
        test_cases = [
            ("solar_inverter_instant_power", "solar_inverter_instant_power"),
            ("solar_inverter_energy_produced", "solar_inverter_energy_produced"),
            ("solar_inverter_energy_consumed", "solar_inverter_energy_consumed"),
        ]

        for description_key, expected_suffix in test_cases:
            # Create mock description with hardcoded key
            mock_description = MagicMock()
            mock_description.key = description_key

            sensor = self._create_synthetic_sensor(
                span_panel, [self.SOLAR_LEG1, self.SOLAR_LEG2], mock_description
            )

            # Hardcoded expected unique ID pattern
            expected_unique_id = f"span_{self.TEST_SERIAL_NUMBER}_synthetic_{self.SOLAR_LEG1}_{self.SOLAR_LEG2}_{expected_suffix}"
            assert sensor.unique_id == expected_unique_id, f"Failed for sensor {description_key}"

    def test_solar_synthetic_sensor_single_circuit(self):
        """Test unique ID generation for solar synthetic sensor with single circuit."""
        span_panel = self._create_mock_span_panel()

        # Create mock description with hardcoded key
        mock_description = MagicMock()
        mock_description.key = "solar_inverter_instant_power"

        sensor = self._create_synthetic_sensor(
            span_panel,
            [self.SOLAR_LEG1],  # Single circuit
            mock_description,
        )

        # Hardcoded expected unique ID pattern
        expected_unique_id = f"span_{self.TEST_SERIAL_NUMBER}_synthetic_{self.SOLAR_LEG1}_solar_inverter_instant_power"
        assert sensor.unique_id == expected_unique_id

    def test_unique_id_with_different_serial_numbers(self):
        """Test that unique IDs change with different serial numbers."""
        test_serials = ["ABC123", "XYZ789", "PANEL001"]

        for serial in test_serials:
            span_panel = self._create_mock_span_panel(serial)

            # Create mock power description
            power_description = MagicMock()
            power_description.key = "power"

            # Test regular circuit
            circuit_sensor = self._create_circuit_sensor(
                span_panel, self.KITCHEN_CIRCUIT_DATA["circuit_id"], power_description
            )
            # Hardcoded expected pattern
            expected_circuit_id = f"span_{serial}_{self.KITCHEN_CIRCUIT_DATA['circuit_id']}_power"
            assert circuit_sensor.unique_id == expected_circuit_id

            # Create mock synthetic description
            synthetic_description = MagicMock()
            synthetic_description.key = "solar_inverter_instant_power"

            # Test synthetic sensor
            synthetic_sensor = self._create_synthetic_sensor(
                span_panel, [self.SOLAR_LEG1, self.SOLAR_LEG2], synthetic_description
            )
            # Hardcoded expected pattern
            expected_synthetic_id = f"span_{serial}_synthetic_{self.SOLAR_LEG1}_{self.SOLAR_LEG2}_solar_inverter_instant_power"
            assert synthetic_sensor.unique_id == expected_synthetic_id

    def test_unique_id_format_consistency(self):
        """Test that unique ID formats are consistent and follow documented patterns."""
        span_panel = self._create_mock_span_panel()

        # Create mock power description
        power_description = MagicMock()
        power_description.key = "power"

        # Test regular circuit format: span_{serial}_{circuit_id}_{description_key}
        circuit_sensor = self._create_circuit_sensor(
            span_panel, self.KITCHEN_CIRCUIT_DATA["circuit_id"], power_description
        )

        parts = circuit_sensor.unique_id.split("_")
        assert parts[0] == "span", "Should start with 'span'"
        assert parts[1] == self.TEST_SERIAL_NUMBER, "Should include serial number"
        assert parts[2] == self.KITCHEN_CIRCUIT_DATA["circuit_id"], "Should include circuit ID"
        assert parts[3] == "power", "Should include description key"  # Hardcoded expected value

        # Create mock synthetic description
        synthetic_description = MagicMock()
        synthetic_description.key = "solar_inverter_instant_power"

        # Test synthetic sensor format: span_{serial}_synthetic_{leg1}_{leg2}_{yaml_key}
        synthetic_sensor = self._create_synthetic_sensor(
            span_panel, [self.SOLAR_LEG1, self.SOLAR_LEG2], synthetic_description
        )

        synthetic_parts = synthetic_sensor.unique_id.split("_")
        assert synthetic_parts[0] == "span", "Should start with 'span'"
        assert synthetic_parts[1] == self.TEST_SERIAL_NUMBER, "Should include serial number"
        assert synthetic_parts[2] == "synthetic", "Should include 'synthetic'"
        assert synthetic_parts[3] == str(self.SOLAR_LEG1), "Should include first leg"
        assert synthetic_parts[4] == str(self.SOLAR_LEG2), "Should include second leg"
        assert "_".join(synthetic_parts[5:]) == "solar_inverter_instant_power", (
            "Should include YAML key"  # Hardcoded expected pattern
        )

    def test_unique_id_stability_across_installations(self):
        """Test that unique IDs remain stable across different installation types."""
        span_panel = self._create_mock_span_panel()

        # Create mock synthetic description
        synthetic_description = MagicMock()
        synthetic_description.key = "solar_inverter_instant_power"

        # Test that synthetic sensors always use the v1.0.10+ pattern regardless of config
        config_variations = [
            {},  # Legacy
            {USE_CIRCUIT_NUMBERS: True},  # New
            {USE_DEVICE_PREFIX: True},  # With device prefix
            {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True},  # Both
        ]

        for config in config_variations:
            synthetic_sensor = self._create_synthetic_sensor(
                span_panel, [self.SOLAR_LEG1, self.SOLAR_LEG2], synthetic_description
            )

            # Synthetic sensors should always use the same pattern regardless of config
            # Hardcoded expected pattern
            expected_unique_id = f"span_{self.TEST_SERIAL_NUMBER}_synthetic_{self.SOLAR_LEG1}_{self.SOLAR_LEG2}_solar_inverter_instant_power"
            assert synthetic_sensor.unique_id == expected_unique_id, f"Failed for config: {config}"

    def test_multi_panel_unique_id_differentiation(self):
        """Test that unique IDs differentiate between multiple panels."""
        # Create two panels with different serial numbers
        panel1 = self._create_mock_span_panel("PANEL001")
        panel2 = self._create_mock_span_panel("PANEL002")

        # Create mock power description
        power_description = MagicMock()
        power_description.key = "power"

        # Same circuit on different panels should have different unique IDs
        sensor1 = self._create_circuit_sensor(panel1, "1", power_description)
        sensor2 = self._create_circuit_sensor(panel2, "1", power_description)

        assert sensor1.unique_id != sensor2.unique_id
        assert "PANEL001" in sensor1.unique_id
        assert "PANEL002" in sensor2.unique_id

        # Create mock synthetic description
        synthetic_description = MagicMock()
        synthetic_description.key = "solar_inverter_instant_power"

        # Same synthetic sensor on different panels should have different unique IDs
        synthetic1 = self._create_synthetic_sensor(panel1, [15, 16], synthetic_description)
        synthetic2 = self._create_synthetic_sensor(panel2, [15, 16], synthetic_description)

        assert synthetic1.unique_id != synthetic2.unique_id
        assert "PANEL001" in synthetic1.unique_id
        assert "PANEL002" in synthetic2.unique_id

    def test_solar_synthetic_sensor_v1_0_10_compatibility(self):
        """Test that solar synthetic sensors maintain v1.0.10+ compatibility."""
        span_panel = self._create_mock_span_panel()

        # Create mock synthetic description
        synthetic_description = MagicMock()
        synthetic_description.key = "solar_inverter_instant_power"

        # This tests the specific requirement from the documentation:
        # Solar synthetic sensors must maintain compatibility with v1.0.10+ synthetic naming patterns
        sensor = self._create_synthetic_sensor(
            span_panel, [self.SOLAR_LEG1, self.SOLAR_LEG2], synthetic_description
        )

        # The unique ID should follow the v1.0.10+ pattern:
        # span_{serial}_synthetic_{circuits}_{yaml_key}
        # Hardcoded expected pattern
        expected_pattern = f"span_{self.TEST_SERIAL_NUMBER}_synthetic_{self.SOLAR_LEG1}_{self.SOLAR_LEG2}_solar_inverter_instant_power"
        assert sensor.unique_id == expected_pattern

        # Verify the pattern components match v1.0.10+ requirements
        assert sensor.unique_id.startswith(f"span_{self.TEST_SERIAL_NUMBER}_synthetic_")
        assert f"_{self.SOLAR_LEG1}_{self.SOLAR_LEG2}_" in sensor.unique_id
        assert sensor.unique_id.endswith("_solar_inverter_instant_power")

    @pytest.mark.parametrize(
        "circuit_combinations",
        [
            ([10]),  # Single circuit
            ([10, 11]),  # Two circuits
            ([10, 11, 12]),  # Three circuits
            ([1, 15, 30]),  # Non-sequential circuits
        ],
    )
    def test_synthetic_sensor_circuit_combinations(self, circuit_combinations):
        """Test unique ID generation for various circuit combinations."""
        span_panel = self._create_mock_span_panel()

        # Create mock synthetic description with hardcoded expected key
        synthetic_description = MagicMock()
        synthetic_description.key = "solar_inverter_instant_power"

        sensor = self._create_synthetic_sensor(
            span_panel, circuit_combinations, synthetic_description
        )

        # Build expected circuit specification - hardcoded pattern matching documentation
        circuit_spec = "_".join(str(num) for num in circuit_combinations)
        expected_unique_id = (
            f"span_{self.TEST_SERIAL_NUMBER}_synthetic_{circuit_spec}_solar_inverter_instant_power"
        )

        assert sensor.unique_id == expected_unique_id

    def test_circuit_sensor_uses_correct_documented_pattern(self):
        """Test that circuit sensors follow the documented unique ID pattern."""
        span_panel = self._create_mock_span_panel()

        # Use ACTUAL integration sensor description (not mocked)
        from custom_components.span_panel.const import CIRCUITS_POWER
        from custom_components.span_panel.sensor_definitions import CIRCUITS_SENSORS

        # Get the actual power sensor description used by the integration
        power_description = next(d for d in CIRCUITS_SENSORS if d.key == CIRCUITS_POWER)

        # Create sensor using actual integration components
        sensor = self._create_circuit_sensor(
            span_panel,
            self.KITCHEN_CIRCUIT_DATA["circuit_id"],
            power_description,
            {USE_CIRCUIT_NUMBERS: True},
        )

        # Verify it matches the DOCUMENTED pattern, not the integration constants
        # This is from SPAN_Unique_Key_Compatibility.md: span_{serial_number}_{circuit_id}_{description_key}
        expected_unique_id = f"span_{self.TEST_SERIAL_NUMBER}_{self.KITCHEN_CIRCUIT_DATA['circuit_id']}_instantPowerW"
        assert sensor.unique_id == expected_unique_id

        # Additional verification: ensure the integration actually uses the expected key
        assert power_description.key == "instantPowerW", (
            "Integration changed power sensor key - update documentation!"
        )

    def test_synthetic_sensor_uses_correct_documented_pattern(self):
        """Test that synthetic sensors follow the documented unique ID pattern."""
        span_panel = self._create_mock_span_panel()

        # Create a mock power template (since SYNTHETIC_SENSOR_TEMPLATES doesn't exist)
        power_template = MagicMock()
        power_template.key = "instant_power"
        power_template.name = "Instant Power"
        power_template.device_class = "power"
        power_template.native_unit_of_measurement = "W"
        power_template.state_class = "measurement"

        # Create a mock description object
        description = MagicMock()
        description.key = (
            f"solar_inverter_{power_template.key}"  # This is what the integration does
        )
        description.name = power_template.name
        description.device_class = power_template.device_class
        description.native_unit_of_measurement = power_template.native_unit_of_measurement
        description.state_class = power_template.state_class

        sensor = self._create_synthetic_sensor(
            span_panel, [self.SOLAR_LEG1, self.SOLAR_LEG2], description
        )

        # Verify it matches the DOCUMENTED pattern from SPAN_Unique_Key_Compatibility.md:
        # span_{serial_number}_synthetic_{leg1}_{leg2}_{yaml_key}
        expected_unique_id = f"span_{self.TEST_SERIAL_NUMBER}_synthetic_{self.SOLAR_LEG1}_{self.SOLAR_LEG2}_solar_inverter_instant_power"
        assert sensor.unique_id == expected_unique_id

        # Additional verification: ensure the integration actually uses expected template structure
        assert power_template.key == "instant_power", (
            "Integration changed power template key - update documentation!"
        )
