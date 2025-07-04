"""Tests for helper functions in the Span Panel integration."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.span_panel.const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from custom_components.span_panel.helpers import (
    construct_entity_id,
    construct_synthetic_entity_id,
    construct_synthetic_friendly_name,
    get_user_friendly_suffix,
    sanitize_name_for_entity_id,
)


class TestHelperFunctions:
    """Test the helper functions."""

    def test_sanitize_name_for_entity_id(self):
        """Test name sanitization for entity IDs."""
        assert sanitize_name_for_entity_id("Kitchen Outlets") == "kitchen_outlets"
        assert sanitize_name_for_entity_id("Main-Panel") == "main_panel"
        assert sanitize_name_for_entity_id("Test Name") == "test_name"
        assert sanitize_name_for_entity_id("UPPER CASE") == "upper_case"

    def test_get_user_friendly_suffix(self):
        """Test suffix mapping conversion."""
        assert get_user_friendly_suffix("instantPowerW") == "power"
        assert get_user_friendly_suffix("producedEnergyWh") == "energy_produced"
        assert get_user_friendly_suffix("circuit_priority") == "priority"
        assert get_user_friendly_suffix("unknown_field") == "unknown_field"

    def test_construct_entity_id_config_entry_none(self):
        """Test construct_entity_id raises error when config_entry is None."""
        coordinator = MagicMock()
        coordinator.config_entry = None
        span_panel = MagicMock()

        with pytest.raises(RuntimeError, match="Config entry missing from coordinator"):
            construct_entity_id(coordinator, span_panel, "sensor", "Kitchen", 1, "power")

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_entity_id_empty_options_legacy(self, mock_device_info):
        """Test construct_entity_id with empty options (legacy installation)."""
        mock_device_info.return_value = {"name": "Span Panel"}

        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        span_panel = MagicMock()

        result = construct_entity_id(
            coordinator, span_panel, "sensor", "Kitchen Outlets", 1, "power"
        )
        assert result == "sensor.kitchen_outlets_power"

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_entity_id_circuit_numbers_no_device_name(self, mock_device_info):
        """Test construct_entity_id with circuit numbers but no device name."""
        mock_device_info.return_value = {"name": None}

        coordinator = MagicMock()
        coordinator.config_entry.options = {USE_CIRCUIT_NUMBERS: True}
        span_panel = MagicMock()

        result = construct_entity_id(coordinator, span_panel, "sensor", "Kitchen", 1, "power")
        assert result is None

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_entity_id_device_prefix_no_device_name(self, mock_device_info):
        """Test construct_entity_id with device prefix but no device name."""
        mock_device_info.return_value = {"name": None}

        coordinator = MagicMock()
        coordinator.config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: True,
        }
        span_panel = MagicMock()

        result = construct_entity_id(coordinator, span_panel, "sensor", "Kitchen", 1, "power")
        assert result is None

    def test_construct_synthetic_entity_id_config_entry_none(self):
        """Test construct_synthetic_entity_id raises error when config_entry is None."""
        coordinator = MagicMock()
        coordinator.config_entry = None
        span_panel = MagicMock()

        with pytest.raises(RuntimeError, match="Config entry missing from coordinator"):
            construct_synthetic_entity_id(coordinator, span_panel, "sensor", [30, 32], "power")

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_synthetic_entity_id_empty_options(self, mock_device_info):
        """Test construct_synthetic_entity_id with stable naming (synthetic sensors are always stable)."""
        mock_device_info.return_value = {"name": "Span Panel"}

        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        span_panel = MagicMock()

        # Test with friendly name - legacy installation should not use device prefix
        result = construct_synthetic_entity_id(
            coordinator, span_panel, "sensor", [30, 32], "power", "Solar Production Power"
        )
        assert result == "sensor.solar_production_power_power"

        # Test without friendly name - legacy installation should not use device prefix
        result = construct_synthetic_entity_id(coordinator, span_panel, "sensor", [30, 32], "power")
        assert result == "sensor.synthetic_sensor_30_32_power"

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_synthetic_entity_id_no_device_name(self, mock_device_info):
        """Test construct_synthetic_entity_id with no device name - should still work with stable naming."""
        mock_device_info.return_value = {"name": None}

        coordinator = MagicMock()
        coordinator.config_entry.options = {USE_CIRCUIT_NUMBERS: True}
        span_panel = MagicMock()

        # Synthetic sensors should always use stable naming, even without device name
        result = construct_synthetic_entity_id(coordinator, span_panel, "sensor", [30, 32], "power")
        assert result == "sensor.synthetic_sensor_30_32_power"

    def test_construct_synthetic_friendly_name_with_user_name(self):
        """Test construct_synthetic_friendly_name with user-provided name."""
        result = construct_synthetic_friendly_name([30, 32], "Instant Power", "Solar Production")
        assert result == "Solar Production Instant Power"

    def test_construct_synthetic_friendly_name_multiple_circuits(self):
        """Test construct_synthetic_friendly_name with multiple circuits."""
        result = construct_synthetic_friendly_name([30, 32], "Instant Power")
        assert result == "Circuit 30-32 Instant Power"

    def test_construct_synthetic_friendly_name_single_circuit(self):
        """Test construct_synthetic_friendly_name with single circuit."""
        result = construct_synthetic_friendly_name([30], "Instant Power")
        assert result == "Circuit 30 Instant Power"

    def test_construct_synthetic_friendly_name_no_valid_circuits(self):
        """Test construct_synthetic_friendly_name with no valid circuits."""
        result = construct_synthetic_friendly_name([0, -1], "Instant Power")
        assert result == "Unknown Circuit Instant Power"

    def test_construct_synthetic_friendly_name_empty_circuits(self):
        """Test construct_synthetic_friendly_name with empty circuit list."""
        result = construct_synthetic_friendly_name([], "Instant Power")
        assert result == "Unknown Circuit Instant Power"
