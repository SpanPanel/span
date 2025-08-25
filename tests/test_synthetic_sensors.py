"""Tests for synthetic_sensors module.

This module tests the synthetic sensor management functionality.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry

from custom_components.span_panel.synthetic_sensors import (
    force_quotes_representer,
    SyntheticSensorCoordinator,
    setup_synthetic_configuration,
    async_setup_synthetic_sensors,
    extract_circuit_id_from_entity_id,
    get_existing_battery_sensor_ids,
    cleanup_synthetic_sensors,
    handle_battery_options_change,
    async_export_synthetic_config_service,
    _get_stored_battery_sensor_ids,
    _synthetic_coordinators,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit
from ha_synthetic_sensors import SensorManager


class TestForceQuotesRepresenter:
    """Test the force quotes representer for YAML."""

    def test_force_quotes_on_brackets(self):
        """Test that strings with brackets get quoted."""
        dumper = MagicMock()
        dumper.represent_scalar.return_value = "quoted_result"

        result = force_quotes_representer(dumper, "tabs [1:2]")

        dumper.represent_scalar.assert_called_with("tag:yaml.org,2002:str", "tabs [1:2]", style='"')
        assert result == "quoted_result"

    def test_force_quotes_on_tabs_prefix(self):
        """Test that strings starting with 'tabs ' get quoted."""
        dumper = MagicMock()
        dumper.represent_scalar.return_value = "quoted_result"

        result = force_quotes_representer(dumper, "tabs 1")

        dumper.represent_scalar.assert_called_with("tag:yaml.org,2002:str", "tabs 1", style='"')
        assert result == "quoted_result"

    def test_force_quotes_on_dash(self):
        """Test that strings with dashes get quoted."""
        dumper = MagicMock()
        dumper.represent_scalar.return_value = "quoted_result"

        result = force_quotes_representer(dumper, "test-value")

        dumper.represent_scalar.assert_called_with("tag:yaml.org,2002:str", "test-value", style='"')
        assert result == "quoted_result"

    def test_no_quotes_on_normal_string(self):
        """Test that normal strings don't get quoted."""
        dumper = MagicMock()
        dumper.represent_scalar.return_value = "normal_result"

        result = force_quotes_representer(dumper, "normal_string")

        dumper.represent_scalar.assert_called_with("tag:yaml.org,2002:str", "normal_string")
        assert result == "normal_result"

    def test_non_string_input(self):
        """Test that non-string inputs are handled correctly."""
        dumper = MagicMock()
        dumper.represent_scalar.return_value = "number_result"

        # Should not be called with non-string, but if it is, should handle gracefully
        result = force_quotes_representer(dumper, 123)

        dumper.represent_scalar.assert_called_with("tag:yaml.org,2002:str", 123)
        assert result == "number_result"


class TestSyntheticSensorCoordinator:
    """Test the SyntheticSensorCoordinator class."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.loop = MagicMock()
        hass.data = {}
        hass.config = MagicMock()
        hass.config.config_dir = "/test/config"
        return hass

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock SPAN coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.last_update_success = True
        coordinator.panel_offline = False  # Add the new panel_offline property
        coordinator.data = MagicMock(spec=SpanPanel)
        coordinator.data.panel = MagicMock()
        coordinator.data.circuits = {}  # Add circuits dict
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())
        return coordinator

    @pytest.fixture
    def synthetic_coordinator(self, mock_hass, mock_coordinator):
        """Create a synthetic sensor coordinator."""
        with patch('homeassistant.helpers.dispatcher.async_dispatcher_connect'):
            return SyntheticSensorCoordinator(mock_hass, mock_coordinator, "Test Device")



    def test_handle_coordinator_update_no_success(self, synthetic_coordinator):
        """Test coordinator update handling when last update was not successful."""
        synthetic_coordinator.coordinator.last_update_success = False

        # Should not raise any exceptions
        synthetic_coordinator._handle_coordinator_update()

    def test_handle_coordinator_update_no_data(self, synthetic_coordinator):
        """Test coordinator update handling with no data."""
        synthetic_coordinator.coordinator.data = None

        # Should not raise any exceptions
        synthetic_coordinator._handle_coordinator_update()

    def test_handle_coordinator_update_with_changes(self, synthetic_coordinator):
        """Test coordinator update handling with actual changes."""
        # Ensure panel is online
        synthetic_coordinator.coordinator.panel_offline = False

        # Setup backing entity metadata with complete structure
        synthetic_coordinator.backing_entity_metadata = {
            "test_entity": {
                "data_path": "circuits.test.instant_power",
                "api_key": "instant_power",
                "circuit_id": "test",
                "friendly_name": None
            }
        }
        synthetic_coordinator.change_notifier = MagicMock()
        synthetic_coordinator._last_values = {"test_entity": 100.0}

        # Mock the value extraction to return a different value
        with patch.object(synthetic_coordinator, '_extract_value_from_panel', return_value=200.0):
            synthetic_coordinator._handle_coordinator_update()

            # Should notify of changes
            synthetic_coordinator.change_notifier.assert_called_once_with({"test_entity"})
            assert synthetic_coordinator._last_values["test_entity"] == 200.0

    def test_handle_coordinator_update_no_changes(self, synthetic_coordinator):
        """Test coordinator update handling with no changes."""
        # Ensure panel is online
        synthetic_coordinator.coordinator.panel_offline = False

        # Setup backing entity metadata with complete structure
        synthetic_coordinator.backing_entity_metadata = {
            "test_entity": {
                "data_path": "circuits.test.instant_power",
                "api_key": "instant_power",
                "circuit_id": "test",
                "friendly_name": None
            }
        }
        synthetic_coordinator.change_notifier = MagicMock()
        synthetic_coordinator._last_values = {"test_entity": 100.0}

        # Mock the value extraction to return the same value
        with patch.object(synthetic_coordinator, '_extract_value_from_panel', return_value=100.0):
            synthetic_coordinator._handle_coordinator_update()

            # Should not notify of changes
            synthetic_coordinator.change_notifier.assert_not_called()

    def test_get_backing_value_no_metadata(self, synthetic_coordinator):
        """Test getting backing value with no metadata."""
        result = synthetic_coordinator.get_backing_value("nonexistent_entity")

        assert result is None

    def test_get_backing_value_coordinator_not_ready(self, synthetic_coordinator):
        """Test getting backing value when coordinator is not ready."""
        synthetic_coordinator.backing_entity_metadata = {
            "test_entity": {
                "data_path": "circuits.test.instant_power",
                "api_key": "instant_power",
                "circuit_id": "test",
                "friendly_name": None
            }
        }
        synthetic_coordinator.coordinator.panel_offline = True  # Use panel_offline instead of last_update_success

        result = synthetic_coordinator.get_backing_value("test_entity")

        assert result is None  # Changed from 0.0 to None

    def test_handle_coordinator_update_panel_offline(self, synthetic_coordinator):
        """Test coordinator update handling when panel is offline."""
        # Set panel as offline
        synthetic_coordinator.coordinator.panel_offline = True

        # Setup backing entity metadata
        synthetic_coordinator.backing_entity_metadata = {
            "test_entity": {
                "data_path": "circuits.test.instant_power",
                "api_key": "instant_power",
                "circuit_id": "test",
                "friendly_name": None
            }
        }
        synthetic_coordinator.change_notifier = MagicMock()
        synthetic_coordinator._last_values = {"test_entity": 100.0}

        # Should clear values and notify when panel goes offline
        synthetic_coordinator._handle_coordinator_update()

        # Should notify of changes (clearing to None)
        synthetic_coordinator.change_notifier.assert_called_once_with({"test_entity"})
        assert synthetic_coordinator._last_values["test_entity"] is None

    def test_handle_coordinator_update_with_none_coordinator(self):
        """Test coordinator update handling when coordinator is None (manual mode)."""
        mock_hass = MagicMock()

        # Create synthetic coordinator with None coordinator
        synthetic_coordinator = SyntheticSensorCoordinator(
            mock_hass, None, "Test Panel", manual_update_mode=False
        )

        # Set up backing entity metadata and change notifier
        synthetic_coordinator.backing_entity_metadata = {
            "sensor.test_entity": {"type": "circuit", "circuit_id": "1", "attribute": "power"}
        }
        synthetic_coordinator.change_notifier = MagicMock()

        # Call the update handler
        synthetic_coordinator._handle_coordinator_update()

        # Should trigger manual update for all backing entities
        synthetic_coordinator.change_notifier.assert_called_once_with({"sensor.test_entity"})

    def test_synthetic_coordinator_direct_access(self):
        """Test direct access to synthetic coordinator from global registry."""
        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry_id"

        # Create synthetic coordinator with None coordinator
        synthetic_coordinator = SyntheticSensorCoordinator(
            mock_hass, None, "Test Panel", manual_update_mode=False
        )

        # Add to global registry
        _synthetic_coordinators["test_entry_id"] = synthetic_coordinator

        try:
            # Should find the coordinator by config entry ID directly
            result = _synthetic_coordinators.get("test_entry_id")
            assert result is synthetic_coordinator
        finally:
            # Clean up
            _synthetic_coordinators.pop("test_entry_id", None)




class TestSetupSyntheticConfiguration:
    """Test synthetic configuration setup."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.data = {}
        hass.config = MagicMock()
        hass.config.config_dir = "/test/config"
        return hass

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.entry_id = "test_entry_id"
        coordinator.config_entry.data = {"device_name": "Test Device"}
        coordinator.config_entry.title = "Test Panel"
        coordinator.data = MagicMock()
        coordinator.data.status = MagicMock()
        coordinator.data.status.serial_number = "TEST123"
        return coordinator















class TestUtilityFunctions:
    """Test utility functions."""



    def test_synthetic_coordinator_not_exists(self):
        """Test accessing non-existent synthetic coordinator."""
        result = _synthetic_coordinators.get("nonexistent_entry_id")
        assert result is None

    def test_extract_circuit_id_from_entity_id_backing(self):
        """Test extracting circuit ID from backing entity ID."""
        entity_id = "span_panel_test_device_circuit_1_power_backing"

        result = extract_circuit_id_from_entity_id(entity_id)

        assert result == "power"

    def test_extract_circuit_id_from_entity_id_normal(self):
        """Test extracting circuit ID from normal entity ID."""
        entity_id = "sensor.span_panel_circuit_1_power"

        result = extract_circuit_id_from_entity_id(entity_id)

        assert result == "0"

    def test_extract_circuit_id_from_entity_id_no_match(self):
        """Test extracting circuit ID when no pattern matches."""
        entity_id = "sensor.unrelated_entity"

        result = extract_circuit_id_from_entity_id(entity_id)

        assert result == "0"

    def test_get_existing_battery_sensor_ids(self):
        """Test getting existing battery sensor IDs - deprecated function always returns empty."""
        mock_sensor_manager = MagicMock(spec=SensorManager)

        result = get_existing_battery_sensor_ids(mock_sensor_manager)

        assert result == []  # Function is deprecated and always returns empty list

    def test_get_existing_battery_sensor_ids_no_battery(self):
        """Test getting battery sensor IDs when none exist."""
        mock_sensor_manager = MagicMock(spec=SensorManager)
        mock_sensor_manager.list_sensors = MagicMock()
        mock_sensor_config = MagicMock()
        mock_sensor_config.unique_id = "regular_sensor"
        mock_sensor_config.entity_id = "sensor.span_panel_power"

        mock_sensor_manager.list_sensors.return_value = [mock_sensor_config]

        result = get_existing_battery_sensor_ids(mock_sensor_manager)

        assert result == []






    async def test_cleanup_synthetic_sensors_not_exists(self):
        """Test cleanup when synthetic coordinator doesn't exist."""
        mock_config_entry = MagicMock(spec=ConfigEntry)
        mock_config_entry.entry_id = "nonexistent_entry_id"

        # Should not raise exception
        await cleanup_synthetic_sensors(mock_config_entry)


class TestBatteryOptionsChange:
    """Test battery options change handling."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.data = {
            "ha_synthetic_sensors": {
                "sensor_managers": {
                    "test_entry_id": MagicMock(spec=SensorManager)
                }
            }
        }
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        return MagicMock(spec=SpanPanelCoordinator)










class TestServiceHandlers:
    """Test service handlers."""
