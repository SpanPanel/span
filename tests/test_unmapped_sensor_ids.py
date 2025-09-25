"""Tests for unmapped circuit sensor ID generation."""

from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST

from custom_components.span_panel.const import USE_DEVICE_PREFIX
from custom_components.span_panel.sensor import SpanUnmappedCircuitSensor
from custom_components.span_panel.sensor_definitions import UNMAPPED_SENSORS
from custom_components.span_panel.span_panel import SpanPanel
from tests.common import create_mock_config_entry


class TestUnmappedSensorIds:
    """Test unique ID and entity ID generation for unmapped circuit sensors."""

    TEST_SERIAL_NUMBER = "NJ-2316-005K6"

    def _create_mock_span_panel(self, serial_number: str = None) -> SpanPanel:
        """Create a mock SpanPanel with predefined data."""
        if serial_number is None:
            serial_number = self.TEST_SERIAL_NUMBER

        mock_span_panel = MagicMock(spec=SpanPanel)
        mock_status = MagicMock()
        mock_status.serial_number = serial_number
        mock_span_panel.status = mock_status

        # Add api attribute that some tests might access
        mock_api = MagicMock()
        mock_span_panel.api = mock_api

        return mock_span_panel

    def _create_mock_coordinator(self, config_options: dict = None):
        """Create a mock coordinator with config entry."""
        if config_options is None:
            config_options = {}

        coordinator = MagicMock()
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100", "device_name": "SPAN Panel"},
            config_options
        )
        coordinator.config_entry = mock_config_entry
        return coordinator

    def test_unmapped_sensor_unique_id_generation(self):
        """Test that unmapped circuit sensors generate correct unique IDs."""
        span_panel = self._create_mock_span_panel()
        coordinator = self._create_mock_coordinator()
        circuit_id = "unmapped_tab_32"

        expected_patterns = {
            "instantPowerW": f"span_{self.TEST_SERIAL_NUMBER.lower()}_unmapped_tab_32_power",
            "producedEnergyWh": f"span_{self.TEST_SERIAL_NUMBER.lower()}_unmapped_tab_32_energy_produced",
            "consumedEnergyWh": f"span_{self.TEST_SERIAL_NUMBER.lower()}_unmapped_tab_32_energy_consumed",
        }

        for description in UNMAPPED_SENSORS:
            # Test the full sensor initialization process (this is what actually happens at runtime)
            sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)

            # Check the unique ID that gets set during initialization
            actual_unique_id = sensor._attr_unique_id
            expected_unique_id = expected_patterns[description.key]

            assert actual_unique_id == expected_unique_id, (
                f"Unique ID mismatch for {description.key}: "
                f"expected {expected_unique_id}, got {actual_unique_id}"
            )

            # Verify no duplication in unique ID (this is the critical test)
            assert "unmapped_tab_32_unmapped_tab_32" not in actual_unique_id, (
                f"Duplicate unmapped_tab in unique_id: {actual_unique_id}"
            )

            # Also test the method directly for completeness
            method_unique_id = sensor._generate_unique_id(span_panel, description)
            assert method_unique_id == expected_unique_id, (
                f"Method unique ID mismatch for {description.key}: "
                f"expected {expected_unique_id}, got {method_unique_id}"
            )

    def test_unmapped_sensor_entity_id_generation(self):
        """Test that unmapped circuit sensors have proper unique IDs (entity_id is set by HA framework)."""
        span_panel = self._create_mock_span_panel()
        coordinator = self._create_mock_coordinator()
        circuit_id = "unmapped_tab_32"

        for description in UNMAPPED_SENSORS:
            # Test the full sensor initialization process (this is what actually happens at runtime)
            sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)

            # Test that the sensor has a unique_id (this is what the sensor actually sets)
            assert sensor.unique_id is not None, f"Unique ID should be set for {description.key}"

            # Test that the unique_id follows the expected pattern
            assert "unmapped_tab_32" in sensor.unique_id, f"Unique ID should contain circuit ID: {sensor.unique_id}"
            # The unique_id contains a transformed version of the key, not the original key
            assert "power" in sensor.unique_id or "energy" in sensor.unique_id, f"Unique ID should contain transformed key: {sensor.unique_id}"

    def test_unmapped_sensor_always_uses_device_prefix(self):
        """Test that unmapped sensors always use device prefix regardless of config."""
        span_panel = self._create_mock_span_panel()
        circuit_id = "unmapped_tab_27"

        # Test with device prefix disabled
        coordinator_no_prefix = self._create_mock_coordinator({USE_DEVICE_PREFIX: False})

        # Test with device prefix enabled
        coordinator_with_prefix = self._create_mock_coordinator({USE_DEVICE_PREFIX: True})

        for description in UNMAPPED_SENSORS:
            # Create sensors with both configurations
            sensor_no_prefix = SpanUnmappedCircuitSensor(
                coordinator_no_prefix, description, span_panel, circuit_id
            )
            sensor_with_prefix = SpanUnmappedCircuitSensor(
                coordinator_with_prefix, description, span_panel, circuit_id
            )

            # Both should have the same unique_id (unmapped sensors always use device prefix)
            assert sensor_no_prefix.unique_id == sensor_with_prefix.unique_id, (
                f"Unmapped sensors should always use device prefix in unique_id: "
                f"no_prefix={sensor_no_prefix.unique_id}, with_prefix={sensor_with_prefix.unique_id}"
            )

            # Both should contain the device name in unique_id (actual device name, not "span_panel")
            assert "span_" in sensor_no_prefix.unique_id, (
                f"Unmapped sensor unique_id should contain device prefix: {sensor_no_prefix.unique_id}"
            )

    def test_unmapped_sensor_different_circuit_numbers(self):
        """Test unmapped sensors with different circuit numbers."""
        span_panel = self._create_mock_span_panel()
        coordinator = self._create_mock_coordinator()

        test_circuits = ["unmapped_tab_15", "unmapped_tab_30", "unmapped_tab_28"]

        for circuit_id in test_circuits:
            circuit_id.split("_")[-1]  # Extract number from circuit_id

            for description in UNMAPPED_SENSORS:
                sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)

                # Test unique ID
                unique_id = sensor._generate_unique_id(span_panel, description)
                assert circuit_id in unique_id, (
                    f"Circuit ID {circuit_id} not found in unique_id: {unique_id}"
                )

                # Verify no duplication in unique_id
                assert f"{circuit_id}_{circuit_id}" not in unique_id

    def test_unmapped_sensor_different_serial_numbers(self):
        """Test unmapped sensors with different panel serial numbers."""
        coordinator = self._create_mock_coordinator()
        circuit_id = "unmapped_tab_32"

        test_serials = ["ABC123DEF456", "XYZ789GHI012", "TEST-SERIAL-001"]

        for serial_number in test_serials:
            span_panel = self._create_mock_span_panel(serial_number)

            for description in UNMAPPED_SENSORS:
                sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)
                unique_id = sensor._generate_unique_id(span_panel, description)

                # Unique ID should contain the serial number (lowercase)
                assert serial_number.lower() in unique_id, (
                    f"Serial number {serial_number} not found in unique_id: {unique_id}"
                )

                # Should start with "span_" followed by serial
                assert unique_id.startswith(f"span_{serial_number.lower()}_"), (
                    f"Unique ID should start with span_{serial_number.lower()}_: {unique_id}"
                )

    def test_unmapped_sensor_key_mapping(self):
        """Test that description keys are correctly mapped to user-friendly suffixes in generated IDs."""
        span_panel = self._create_mock_span_panel()
        coordinator = self._create_mock_coordinator()
        circuit_id = "unmapped_tab_32"

        # Test that the correct suffixes appear in the generated IDs
        key_to_suffix_mapping = {
            "instantPowerW": "power",
            "producedEnergyWh": "energy_produced",
            "consumedEnergyWh": "energy_consumed",
        }

        for description in UNMAPPED_SENSORS:
            sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)

            # Test that the expected suffix appears in unique ID
            unique_id = sensor._generate_unique_id(span_panel, description)
            expected_suffix = key_to_suffix_mapping[description.key]

            assert expected_suffix in unique_id, (
                f"Expected suffix '{expected_suffix}' not found in unique_id for {description.key}: {unique_id}"
            )

    def test_unmapped_sensor_entity_registry_defaults(self):
        """Test that unmapped sensors have correct entity registry defaults."""
        span_panel = self._create_mock_span_panel()
        coordinator = self._create_mock_coordinator()
        circuit_id = "unmapped_tab_32"

        for description in UNMAPPED_SENSORS:
            sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)

            # Unmapped sensors should be enabled but hidden
            assert sensor._attr_entity_registry_enabled_default is True, (
                "Unmapped sensor should be enabled by default"
            )
            assert sensor._attr_entity_registry_visible_default is False, (
                "Unmapped sensor should be hidden by default"
            )

    def test_unmapped_sensor_initialization_bug_prevention(self):
        """Test that prevents the specific bug where description.key gets overridden during initialization.

        This test specifically catches the bug where the __init__ method was overriding
        description.key with circuit_id, causing duplicate unmapped_tab_32 in IDs.
        """
        span_panel = self._create_mock_span_panel()
        coordinator = self._create_mock_coordinator()
        circuit_id = "unmapped_tab_32"

        for description in UNMAPPED_SENSORS:
            # Create sensor (this is where the bug would manifest)
            sensor = SpanUnmappedCircuitSensor(coordinator, description, span_panel, circuit_id)

            # Verify the original key is preserved
            assert hasattr(sensor, "original_key"), "Sensor should store original_key"
            assert sensor.original_key == description.key, (
                f"original_key should match description.key: {sensor.original_key} != {description.key}"
            )

            # Verify no duplication in the actual attributes set during initialization
            unique_id = sensor._attr_unique_id

            # The bug would create IDs like: span_serial_unmapped_tab_32_unmapped_tab_32
            assert "unmapped_tab_32_unmapped_tab_32" not in unique_id, (
                f"Bug detected: duplicate circuit_id in unique_id: {unique_id}"
            )

            # Verify the IDs contain the expected suffix based on original key
            expected_suffix = {
                "instantPowerW": "power",
                "producedEnergyWh": "energy_produced",
                "consumedEnergyWh": "energy_consumed",
            }[description.key]

            assert expected_suffix in unique_id, (
                f"Expected suffix '{expected_suffix}' not found in unique_id: {unique_id}"
            )

            # Only test unique_id since entity_id is not set by the sensor
