"""Test panel-level sensors functionality."""

from unittest.mock import MagicMock

import pytest
from span_panel_api import SpanPanelSnapshot

from tests.factories import SpanPanelSnapshotFactory
from tests.helpers import make_span_panel_entry


class TestPanelSensors:
    """Test panel-level sensors."""

    @pytest.fixture
    def mock_snapshot(self) -> SpanPanelSnapshot:
        """Create a snapshot for testing."""
        return SpanPanelSnapshotFactory.create_complete()

    @pytest.fixture
    def mock_coordinator(self, mock_snapshot: SpanPanelSnapshot) -> MagicMock:
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.data = mock_snapshot
        coordinator.config_entry = make_span_panel_entry()
        return coordinator

    def test_panel_data_status_sensors_creation(self, mock_coordinator: MagicMock, mock_snapshot: SpanPanelSnapshot) -> None:
        """Test that panel data status sensors are created correctly."""
        from custom_components.span_panel.sensor import SpanPanelPanelStatus
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        for description in PANEL_DATA_STATUS_SENSORS:
            sensor = SpanPanelPanelStatus(mock_coordinator, description, mock_snapshot)
            assert sensor.entity_description == description
            assert sensor.coordinator == mock_coordinator

            # Panel data sensors return the snapshot itself as data source
            data_source = sensor.get_data_source(mock_snapshot)
            assert data_source is mock_snapshot

    def test_hardware_status_sensors_creation(self, mock_coordinator: MagicMock, mock_snapshot: SpanPanelSnapshot) -> None:
        """Test that hardware status sensors are created correctly."""
        from custom_components.span_panel.sensor import SpanPanelStatus
        from custom_components.span_panel.sensor_definitions import STATUS_SENSORS

        for description in STATUS_SENSORS:
            sensor = SpanPanelStatus(mock_coordinator, description, mock_snapshot)
            assert sensor.entity_description == description
            assert sensor.coordinator == mock_coordinator

            # Status sensors also return the snapshot
            data_source = sensor.get_data_source(mock_snapshot)
            assert data_source is mock_snapshot

    def test_main_relay_state_sensor(self) -> None:
        """Test the main relay state sensor specifically."""
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        main_relay_description = next(
            d for d in PANEL_DATA_STATUS_SENSORS if d.key == "main_relay_state"
        )

        test_cases = [
            ("CLOSED", "CLOSED"),
            ("OPEN", "OPEN"),
            ("UNKNOWN", "UNKNOWN"),
        ]

        for input_state, expected_output in test_cases:
            snapshot = SpanPanelSnapshotFactory.create(main_relay_state=input_state)
            sensor_value = main_relay_description.value_fn(snapshot)
            assert sensor_value == expected_output, (
                f"Expected {expected_output} for input {input_state}, got {sensor_value}"
            )

    def test_dsm_state_sensor(self) -> None:
        """Test the DSM state sensor."""
        from custom_components.span_panel.const import DSM_GRID_UP, DSM_ON_GRID
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        dsm_description = next(d for d in PANEL_DATA_STATUS_SENSORS if d.key == "dsm_state")

        for state in [DSM_ON_GRID, DSM_GRID_UP]:
            snapshot = SpanPanelSnapshotFactory.create(dsm_state=state)
            assert dsm_description.value_fn(snapshot) == state

    def test_software_version_sensor(self) -> None:
        """Test the software version sensor."""
        from custom_components.span_panel.sensor_definitions import STATUS_SENSORS

        version_description = next(d for d in STATUS_SENSORS if d.key == "software_version")

        for version in ["1.2.3", "2.0.1", "1.5.0-beta"]:
            snapshot = SpanPanelSnapshotFactory.create(firmware_version=version)
            assert version_description.value_fn(snapshot) == version

    def test_current_run_config_sensor(self) -> None:
        """Test the current run config sensor."""
        from custom_components.span_panel.const import PANEL_ON_GRID
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        config_description = next(
            d for d in PANEL_DATA_STATUS_SENSORS if d.key == "current_run_config"
        )

        snapshot = SpanPanelSnapshotFactory.create(current_run_config=PANEL_ON_GRID)
        assert config_description.value_fn(snapshot) == PANEL_ON_GRID

    def test_sensor_unique_ids(self, mock_coordinator: MagicMock, mock_snapshot: SpanPanelSnapshot) -> None:
        """Test that sensors have correct unique IDs."""
        from custom_components.span_panel.sensor import SpanPanelPanelStatus
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        main_relay_description = next(
            d for d in PANEL_DATA_STATUS_SENSORS if d.key == "main_relay_state"
        )

        sensor = SpanPanelPanelStatus(mock_coordinator, main_relay_description, mock_snapshot)
        expected_unique_id = (
            f"span_{mock_snapshot.serial_number.lower()}_{main_relay_description.key}"
        )
        assert sensor.unique_id == expected_unique_id

    def test_sensor_names(self, mock_coordinator: MagicMock, mock_snapshot: SpanPanelSnapshot) -> None:
        """Test that sensors have correct names."""
        from custom_components.span_panel.sensor import SpanPanelPanelStatus, SpanPanelStatus
        from custom_components.span_panel.sensor_definitions import (
            PANEL_DATA_STATUS_SENSORS,
            STATUS_SENSORS,
        )

        for description in PANEL_DATA_STATUS_SENSORS:
            sensor = SpanPanelPanelStatus(mock_coordinator, description, mock_snapshot)
            assert str(description.name) in str(sensor.name)

        for description in STATUS_SENSORS:
            sensor = SpanPanelStatus(mock_coordinator, description, mock_snapshot)
            assert str(description.name) in str(sensor.name)

    def test_main_relay_state_with_real_data(self) -> None:
        """Test main relay state sensor with real snapshot data."""
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        main_relay_description = next(
            d for d in PANEL_DATA_STATUS_SENSORS if d.key == "main_relay_state"
        )

        snapshot_closed = SpanPanelSnapshotFactory.create(main_relay_state="CLOSED")
        assert main_relay_description.value_fn(snapshot_closed) == "CLOSED"

        snapshot_open = SpanPanelSnapshotFactory.create(main_relay_state="OPEN")
        assert main_relay_description.value_fn(snapshot_open) == "OPEN"

    def test_dsm_states_with_real_data(self) -> None:
        """Test DSM state sensors with real snapshot data."""
        from custom_components.span_panel.const import DSM_GRID_UP, DSM_ON_GRID
        from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

        dsm_description = next(d for d in PANEL_DATA_STATUS_SENSORS if d.key == "dsm_state")
        snapshot = SpanPanelSnapshotFactory.create(dsm_state=DSM_ON_GRID)
        assert dsm_description.value_fn(snapshot) == DSM_ON_GRID

        dsm_grid_description = next(
            d for d in PANEL_DATA_STATUS_SENSORS if d.key == "dsm_grid_state"
        )
        snapshot2 = SpanPanelSnapshotFactory.create(dsm_grid_state=DSM_GRID_UP)
        assert dsm_grid_description.value_fn(snapshot2) == DSM_GRID_UP


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
