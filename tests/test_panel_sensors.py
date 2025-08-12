"""Test panel-level sensors functionality."""

from unittest.mock import MagicMock

import pytest

from custom_components.span_panel.const import (
    DSM_GRID_UP,
    DSM_ON_GRID,
    PANEL_ON_GRID,
)
from custom_components.span_panel.sensor import (
    SpanPanelPanelStatus,
    SpanPanelStatus,
)
from custom_components.span_panel.sensor_definitions import (
    PANEL_DATA_STATUS_SENSORS,
    STATUS_SENSORS,
)
from custom_components.span_panel.span_panel_data import SpanPanelData
from custom_components.span_panel.span_panel_hardware_status import SpanPanelHardwareStatus
from tests.factories import SpanPanelApiResponseFactory
from tests.helpers import make_span_panel_entry


class MockSpanPanel:
    """Mock SpanPanel for testing."""

    def __init__(self):
        """Initialize mock span panel with test data."""
        # Create realistic test data
        api_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

        # Create real data objects from the factory data
        self.status = SpanPanelHardwareStatus.from_dict(api_responses["status"])
        self.panel = SpanPanelData.from_dict(api_responses["panel"])

        # Add circuits dict (empty for panel sensor tests)
        self.circuits = {}

        # Add host attribute that panel sensors expect
        self.host = "192.168.1.100"

        # Add host attribute that the sensors expect
        self.host = "192.168.1.100"


class TestPanelSensors:
    """Test panel-level sensors."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.data = MockSpanPanel()
        coordinator.config_entry = make_span_panel_entry()
        return coordinator

    @pytest.fixture
    def mock_span_panel(self):
        """Create a mock span panel for testing."""
        return MockSpanPanel()

    def test_panel_data_status_sensors_creation(self, mock_coordinator, mock_span_panel):
        """Test that panel data status sensors are created correctly."""
        # Test each panel data status sensor
        for description in PANEL_DATA_STATUS_SENSORS:
            sensor = SpanPanelPanelStatus(mock_coordinator, description, mock_span_panel)

            # Verify sensor properties
            assert sensor.entity_description == description
            assert sensor.coordinator == mock_coordinator

            # Verify data source is panel data
            data_source = sensor.get_data_source(mock_span_panel)
            assert data_source == mock_span_panel.panel

    def test_hardware_status_sensors_creation(self, mock_coordinator, mock_span_panel):
        """Test that hardware status sensors are created correctly."""
        # Test each hardware status sensor
        for description in STATUS_SENSORS:
            sensor = SpanPanelStatus(mock_coordinator, description, mock_span_panel)

            # Verify sensor properties
            assert sensor.entity_description == description
            assert sensor.coordinator == mock_coordinator

            # Verify data source is hardware status
            data_source = sensor.get_data_source(mock_span_panel)
            assert data_source == mock_span_panel.status

    def test_main_relay_state_sensor(self, mock_coordinator, mock_span_panel):
        """Test the main relay state sensor specifically."""
        # Find the main relay state sensor description
        main_relay_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "main_relay_state":
                main_relay_description = description
                break

        assert main_relay_description is not None, "Main relay state sensor not found"

        # Create the sensor
        SpanPanelPanelStatus(mock_coordinator, main_relay_description, mock_span_panel)

        # Test with different relay states
        test_cases = [
            ("CLOSED", "CLOSED"),
            ("OPEN", "OPEN"),
            ("UNKNOWN", "UNKNOWN"),
        ]

        for input_state, expected_output in test_cases:
            # Set the relay state in mock data
            mock_span_panel.panel.main_relay_state = input_state

            # Get the sensor value
            sensor_value = main_relay_description.value_fn(mock_span_panel.panel)

            assert sensor_value == expected_output, (
                f"Expected {expected_output} for input {input_state}, got {sensor_value}"
            )

    def test_dsm_state_sensor(self, mock_coordinator, mock_span_panel):
        """Test the DSM state sensor."""
        # Find the DSM state sensor description
        dsm_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "dsm_state":
                dsm_description = description
                break

        assert dsm_description is not None, "DSM state sensor not found"

        # Create the sensor
        SpanPanelPanelStatus(mock_coordinator, dsm_description, mock_span_panel)

        # Test with valid DSM states
        test_states = [DSM_ON_GRID, DSM_GRID_UP]

        for state in test_states:
            mock_span_panel.panel.dsm_state = state
            sensor_value = dsm_description.value_fn(mock_span_panel.panel)
            assert sensor_value == state

    def test_software_version_sensor(self, mock_coordinator, mock_span_panel):
        """Test the software version sensor."""
        # Find the software version sensor description
        version_description = None
        for description in STATUS_SENSORS:
            if description.key == "software_version":
                version_description = description
                break

        assert version_description is not None, "Software version sensor not found"

        # Create the sensor
        SpanPanelStatus(mock_coordinator, version_description, mock_span_panel)

        # Test with different firmware versions
        test_versions = ["1.2.3", "2.0.1", "1.5.0-beta"]

        for version in test_versions:
            mock_span_panel.status.firmware_version = version
            sensor_value = version_description.value_fn(mock_span_panel.status)
            assert sensor_value == version

    def test_current_run_config_sensor(self, mock_coordinator, mock_span_panel):
        """Test the current run config sensor."""
        # Find the current run config sensor description
        config_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "current_run_config":
                config_description = description
                break

        assert config_description is not None, "Current run config sensor not found"

        # Create the sensor
        SpanPanelPanelStatus(mock_coordinator, config_description, mock_span_panel)

        # Test with valid run configs
        test_configs = [PANEL_ON_GRID]

        for config in test_configs:
            mock_span_panel.panel.current_run_config = config
            sensor_value = config_description.value_fn(mock_span_panel.panel)
            assert sensor_value == config

    def test_sensor_unique_ids(self, mock_coordinator, mock_span_panel):
        """Test that sensors have correct unique IDs."""
        # Test panel data status sensor unique ID
        main_relay_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "main_relay_state":
                main_relay_description = description
                break

        sensor = SpanPanelPanelStatus(mock_coordinator, main_relay_description, mock_span_panel)

        # Check that unique ID includes the serial number and key
        expected_unique_id = (
            f"span_{mock_span_panel.status.serial_number}_{main_relay_description.key}"
        )
        assert sensor.unique_id == expected_unique_id

    def test_sensor_names(self, mock_coordinator, mock_span_panel):
        """Test that sensors have correct names."""
        # Test each panel data status sensor name
        for description in PANEL_DATA_STATUS_SENSORS:
            sensor = SpanPanelPanelStatus(mock_coordinator, description, mock_span_panel)

            # Name should include description name
            assert description.name in sensor.name

        # Test each hardware status sensor name
        for description in STATUS_SENSORS:
            sensor = SpanPanelStatus(mock_coordinator, description, mock_span_panel)

            # Name should include description name
            assert description.name in sensor.name

    def test_main_relay_state_with_real_data(self):
        """Test main relay state sensor with real factory data."""
        # Create test data using the factory
        api_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

        # Ensure main relay state is CLOSED in the test data
        api_responses["panel"]["mainRelayState"] = "CLOSED"

        # Create real panel data object
        panel_data = SpanPanelData.from_dict(api_responses["panel"])

        # Find the main relay state sensor description
        main_relay_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "main_relay_state":
                main_relay_description = description
                break

        # Test the value function
        sensor_value = main_relay_description.value_fn(panel_data)
        assert sensor_value == "CLOSED"

        # Test with OPEN state
        api_responses["panel"]["mainRelayState"] = "OPEN"
        panel_data = SpanPanelData.from_dict(api_responses["panel"])
        sensor_value = main_relay_description.value_fn(panel_data)
        assert sensor_value == "OPEN"

    def test_dsm_states_with_real_data(self):
        """Test DSM state sensors with real factory data."""
        # Create test data using the factory
        api_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

        # Test DSM state
        api_responses["panel"]["dsmState"] = DSM_ON_GRID
        panel_data = SpanPanelData.from_dict(api_responses["panel"])

        # Find the DSM state sensor description
        dsm_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "dsm_state":
                dsm_description = description
                break

        sensor_value = dsm_description.value_fn(panel_data)
        assert sensor_value == DSM_ON_GRID

        # Test DSM grid state
        api_responses["panel"]["dsmGridState"] = DSM_GRID_UP
        panel_data = SpanPanelData.from_dict(api_responses["panel"])

        # Find the DSM grid state sensor description
        dsm_grid_description = None
        for description in PANEL_DATA_STATUS_SENSORS:
            if description.key == "dsm_grid_state":
                dsm_grid_description = description
                break

        sensor_value = dsm_grid_description.value_fn(panel_data)
        assert sensor_value == DSM_GRID_UP


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
