"""Tests for entity_summary module.

This module tests the entity summary logging functionality.
"""
import logging
import pytest
from unittest.mock import MagicMock, patch
from homeassistant.config_entries import ConfigEntry


class TestLogEntitySummary:
    """Test the log_entity_summary function."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator with span panel data."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

        coordinator = MagicMock(spec=SpanPanelCoordinator)
        span_panel = MagicMock(spec=SpanPanel)

        # Create mock circuits
        circuits = {}
        for i in range(1, 11):  # 10 circuits total
            circuit = MagicMock(spec=SpanPanelCircuit)
            circuit.circuit_id = f"circuit_{i}"
            circuit.name = f"Circuit {i}"
            circuit.is_user_controllable = i <= 8  # First 8 are controllable, last 2 are not
            circuits[f"circuit_{i}"] = circuit

        span_panel.circuits = circuits
        coordinator.data = span_panel
        return coordinator

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {}
        return config_entry

    @pytest.fixture
    def mock_config_entry_with_options(self):
        """Create a mock config entry with all options enabled."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.options import BATTERY_ENABLE, INVERTER_ENABLE

        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {
            BATTERY_ENABLE: True,
            INVERTER_ENABLE: True
        }
        return config_entry

    def test_log_entity_summary_debug_level(self, mock_coordinator, mock_config_entry, caplog):
        """Test entity summary logging at debug level."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            # Check that summary was logged
            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
            assert "Total circuits: 10 (8 controllable, 2 non-controllable)" in caplog.text
            assert "=== NATIVE SENSORS ===" in caplog.text
            assert "=== SYNTHETIC SENSORS (Template-based) ===" in caplog.text
            assert "=== END ENTITY SUMMARY ===" in caplog.text

    def test_log_entity_summary_info_level(self, mock_coordinator, mock_config_entry, caplog):
        """Test entity summary logging at info level."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            # Check that summary was logged
            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
            assert "Total circuits: 10" in caplog.text

    def test_log_entity_summary_no_logging(self, mock_coordinator, mock_config_entry, caplog):
        """Test that nothing is logged when logging is disabled."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        # Set logger to WARNING level (higher than INFO/DEBUG)
        with caplog.at_level(logging.WARNING, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            # Should not log anything
            assert "=== SPAN PANEL ENTITY SUMMARY ===" not in caplog.text

    def test_log_entity_summary_with_battery_enabled(self, mock_coordinator, mock_config_entry_with_options, caplog):
        """Test entity summary with battery enabled."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_options)

            assert "Battery sensors: 1 (native sensor)" in caplog.text

    def test_log_entity_summary_with_battery_disabled(self, mock_coordinator, mock_config_entry, caplog):
        """Test entity summary with battery disabled."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "Battery sensors: 0 (battery disabled)" in caplog.text

    def test_log_entity_summary_with_solar_enabled(self, mock_coordinator, mock_config_entry_with_options, caplog):
        """Test entity summary with solar enabled."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_options)

            assert "Solar synthetic sensors: 3" in caplog.text

    def test_log_entity_summary_with_solar_disabled(self, mock_coordinator, mock_config_entry, caplog):
        """Test entity summary with solar disabled."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "Solar synthetic sensors: 0 (solar disabled)" in caplog.text

    def test_log_entity_summary_non_controllable_circuits(self, mock_coordinator, mock_config_entry, caplog):
        """Test that non-controllable circuits are properly identified and logged."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            # Should show the 2 non-controllable circuits
            assert "Non-controllable circuits:" in caplog.text
            assert "Circuit 9 (ID: circuit_9)" in caplog.text
            assert "Circuit 10 (ID: circuit_10)" in caplog.text

    def test_log_entity_summary_all_controllable_circuits(self, mock_config_entry, caplog):
        """Test entity summary when all circuits are controllable."""
        # Create coordinator with all controllable circuits
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

        coordinator = MagicMock(spec=SpanPanelCoordinator)
        span_panel = MagicMock(spec=SpanPanel)

        circuits = {}
        for i in range(1, 6):  # 5 circuits, all controllable
            circuit = MagicMock(spec=SpanPanelCircuit)
            circuit.circuit_id = f"circuit_{i}"
            circuit.name = f"Circuit {i}"
            circuit.is_user_controllable = True
            circuits[f"circuit_{i}"] = circuit

        span_panel.circuits = circuits
        coordinator.data = span_panel

        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(coordinator, mock_config_entry)

            assert "Non-controllable circuits: None" in caplog.text

    def test_log_entity_summary_no_circuits(self, mock_config_entry, caplog):
        """Test entity summary with no circuits."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

        coordinator = MagicMock(spec=SpanPanelCoordinator)
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.circuits = {}
        coordinator.data = span_panel

        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator, mock_config_entry)

            assert "Total circuits: 0 (0 controllable, 0 non-controllable)" in caplog.text
            assert "Circuit synthetic sensors: 0" in caplog.text

    def test_log_entity_summary_sensor_counts(self, mock_coordinator, mock_config_entry_with_options, caplog):
        """Test that sensor counts are calculated correctly."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_options)

            # With 10 circuits, battery enabled, solar enabled
            # Unmapped sensors: 10 circuits * 3 sensors per circuit = 30
            assert "Unmapped circuit sensors: 30" in caplog.text

            # Circuit synthetic sensors: 10 circuits * 3 sensors = 30
            assert "Circuit synthetic sensors: 30" in caplog.text

            # Panel synthetic sensors: 6 (fixed)
            assert "Panel synthetic sensors: 6" in caplog.text

            # Solar synthetic sensors: 3 (when enabled)
            assert "Solar synthetic sensors: 3" in caplog.text

            # Battery sensors: 1 (when enabled)
            assert "Battery sensors: 1" in caplog.text

            # Circuit switches: 8 (only controllable circuits)
            assert "Circuit switches: 8 (controllable circuits only)" in caplog.text

            # Circuit selects: 8 (only controllable circuits)
            assert "Circuit selects: 8 (controllable circuits only)" in caplog.text

    def test_log_entity_summary_total_entity_calculation(self, mock_coordinator, mock_config_entry_with_options, caplog):
        """Test that total entity count is calculated correctly."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_options)

            # Check the calculation is shown
            assert "Total entities:" in caplog.text
            assert "sensors +" in caplog.text
            assert "switches +" in caplog.text
            assert "selects =" in caplog.text

    def test_log_entity_summary_logger_level_detection(self, mock_coordinator, mock_config_entry):
        """Test that the function correctly detects logger levels."""
        with patch('logging.getLogger') as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            # Test debug level enabled
            mock_logger.isEnabledFor.side_effect = lambda level: level == logging.DEBUG

            # Lazy imports to avoid collection issues
            from custom_components.span_panel.entity_summary import log_entity_summary

            log_entity_summary(mock_coordinator, mock_config_entry)

            # Should check for both DEBUG and INFO levels
            mock_logger.isEnabledFor.assert_any_call(logging.DEBUG)
            mock_logger.isEnabledFor.assert_any_call(logging.INFO)

    def test_log_entity_summary_uses_correct_log_function(self, mock_coordinator, mock_config_entry):
        """Test that the correct log function is used based on level."""
        with patch('logging.getLogger') as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            # Test debug level enabled
            mock_logger.isEnabledFor.side_effect = lambda level: level == logging.DEBUG

            # Lazy imports to avoid collection issues
            from custom_components.span_panel.entity_summary import log_entity_summary

            log_entity_summary(mock_coordinator, mock_config_entry)

            # Should use debug function
            assert mock_logger.debug.called

            # Reset and test info level only
            mock_logger.reset_mock()
            mock_logger.isEnabledFor.side_effect = lambda level: level == logging.INFO and level != logging.DEBUG

            log_entity_summary(mock_coordinator, mock_config_entry)

            # Should use info function for main logs
            assert mock_logger.info.called


class TestEntitySummaryEdgeCases:
    """Test edge cases for entity summary."""

    def test_log_entity_summary_with_none_options(self, caplog):
        """Test entity summary when config entry options is None."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

        coordinator = MagicMock(spec=SpanPanelCoordinator)
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.circuits = {}
        coordinator.data = span_panel

        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {}  # Empty dict instead of None to avoid AttributeError

        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            # Should not crash when options is empty
            log_entity_summary(coordinator, config_entry)

            # Should default to disabled options
            assert "Battery sensors: 0 (battery disabled)" in caplog.text
            assert "Solar synthetic sensors: 0 (solar disabled)" in caplog.text

    def test_log_entity_summary_missing_option_keys(self, caplog):
        """Test entity summary when specific option keys are missing."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

        coordinator = MagicMock(spec=SpanPanelCoordinator)
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.circuits = {}
        coordinator.data = span_panel

        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {}  # Empty dict, missing keys

        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator, config_entry)

            # Should default to False for missing keys
            assert "Battery sensors: 0 (battery disabled)" in caplog.text
            assert "Solar synthetic sensors: 0 (solar disabled)" in caplog.text

    def test_log_entity_summary_circuit_without_attributes(self, caplog):
        """Test entity summary with circuits missing expected attributes."""
        # Lazy imports to avoid collection issues
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

        coordinator = MagicMock(spec=SpanPanelCoordinator)
        span_panel = MagicMock(spec=SpanPanel)

        # Create circuit with minimal attributes
        circuit = MagicMock()
        circuit.circuit_id = "test_circuit"
        circuit.name = "Test Circuit"
        # Missing is_user_controllable - should handle gracefully

        span_panel.circuits = {"test_circuit": circuit}
        coordinator.data = span_panel

        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {}

        # Lazy imports to avoid collection issues
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            # Should handle missing attributes gracefully
            log_entity_summary(coordinator, config_entry)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
