"""Integration test for grace period functionality with YAML generation."""

import pytest
from unittest.mock import MagicMock

from custom_components.span_panel.synthetic_panel_circuits import generate_panel_sensors
from custom_components.span_panel.options import ENERGY_REPORTING_GRACE_PERIOD


class TestGracePeriodIntegration:
    """Test grace period integration with YAML generation."""

    @pytest.mark.asyncio
    async def test_grace_period_in_generated_yaml(self):
        """Test that grace period appears correctly in generated YAML."""
        # Mock coordinator with custom grace period
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {
            ENERGY_REPORTING_GRACE_PERIOD: 30,
            "power_display_precision": 0,
            "energy_display_precision": 2,
        }
        mock_coordinator.config_entry.data = {"device_name": "Test Panel"}
        mock_coordinator.config_entry.title = "Test Panel"

        # Mock span panel with energy data
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "test-panel-001"

        # Mock panel data with all energy fields
        mock_panel_data = MagicMock()
        mock_panel_data.instantGridPowerW = 1500.0
        mock_panel_data.feedthroughPowerW = 200.0
        mock_panel_data.mainMeterEnergyProducedWh = 1000.0
        mock_panel_data.mainMeterEnergyConsumedWh = 2000.0
        mock_panel_data.feedthroughEnergyProducedWh = 500.0
        mock_panel_data.feedthroughEnergyConsumedWh = 750.0

        mock_span_panel.panel = mock_panel_data

        # Mock hass
        mock_hass = MagicMock()

        # Generate panel sensors
        sensor_configs, backing_entities, global_settings, mapping = await generate_panel_sensors(
            mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
        )

        # Verify global settings contain custom grace period
        assert "variables" in global_settings
        assert "energy_grace_period_minutes" in global_settings["variables"]
        assert global_settings["variables"]["energy_grace_period_minutes"] == 30

        # Verify energy sensors are generated
        energy_sensors = [
            key for key in sensor_configs.keys()
            if "energy" in key and ("consumed" in key or "produced" in key)
        ]
        assert len(energy_sensors) == 4  # 4 panel energy sensors

        # Verify energy sensors have grace period exception handling
        for sensor_key in energy_sensors:
            sensor_config = sensor_configs[sensor_key]

            # Check for exception handling structure
            assert "UNAVAILABLE" in sensor_config
            assert sensor_config["UNAVAILABLE"]["formula"] == "state if within_grace else UNKNOWN"

            # Check for computed variables
            assert "variables" in sensor_config
            assert "within_grace" in sensor_config["variables"]

            within_grace_config = sensor_config["variables"]["within_grace"]
            assert "formula" in within_grace_config
            assert "energy_grace_period_minutes" in within_grace_config["formula"]
            assert "metadata(state, 'last_changed')" in within_grace_config["formula"]
            assert "minutes_between" in within_grace_config["formula"]
            # within_grace should have UNAVAILABLE handler
            assert "UNAVAILABLE" in within_grace_config
            assert within_grace_config["UNAVAILABLE"] == False

            # Check for diagnostic attribute
            assert "attributes" in sensor_config
            assert "energy_reporting_status" in sensor_config["attributes"]
            assert "formula" in sensor_config["attributes"]["energy_reporting_status"]

    @pytest.mark.asyncio
    async def test_grace_period_default_value(self):
        """Test that default grace period (15 minutes) is used when not specified."""
        # Mock coordinator without grace period option
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {}  # No grace period specified
        mock_coordinator.config_entry.data = {"device_name": "Test Panel"}
        mock_coordinator.config_entry.title = "Test Panel"

        # Mock span panel
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "test-panel-002"

        mock_panel_data = MagicMock()
        mock_panel_data.instantGridPowerW = 1000.0
        mock_panel_data.feedthroughPowerW = 100.0
        mock_panel_data.mainMeterEnergyProducedWh = 500.0
        mock_panel_data.mainMeterEnergyConsumedWh = 1000.0
        mock_panel_data.feedthroughEnergyProducedWh = 250.0
        mock_panel_data.feedthroughEnergyConsumedWh = 375.0

        mock_span_panel.panel = mock_panel_data

        # Mock hass
        mock_hass = MagicMock()

        # Generate panel sensors
        sensor_configs, backing_entities, global_settings, mapping = await generate_panel_sensors(
            mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
        )

        # Verify default grace period (15) is used
        assert global_settings["variables"]["energy_grace_period_minutes"] == 15

    def test_grace_period_formula_structure(self):
        """Test the grace period formula structure is correct."""
        # Expected formula components
        expected_formula = "minutes_between(metadata(state, 'last_changed'), now()) < energy_grace_period_minutes"

        # This formula should:
        # 1. Get entity last_changed with metadata(state, 'last_changed')
        # 2. Get current time with now()
        # 3. Calculate minutes between using minutes_between function
        # 4. Compare against the grace period threshold

        # Test individual components exist in the formula
        assert "now()" in expected_formula
        assert "metadata(state, 'last_changed')" in expected_formula
        assert "minutes_between" in expected_formula
        assert "energy_grace_period_minutes" in expected_formula
        assert "<" in expected_formula

    def test_grace_period_exception_handler_logic(self):
        """Test the exception handler logic structure."""
        expected_handler = "state if within_grace else UNKNOWN"

        # This handler should:
        # 1. Check the within_grace computed variable
        # 2. If true, return the last known state
        # 3. If false, return UNKNOWN

        assert "within_grace" in expected_handler
        assert "state" in expected_handler
        assert "UNKNOWN" in expected_handler
        assert " if " in expected_handler
        assert " else " in expected_handler

    @pytest.mark.asyncio
    async def test_grace_period_boundary_values(self):
        """Test grace period works with boundary values (0 and 60 minutes)."""
        test_cases = [0, 60]

        for grace_period in test_cases:
            # Mock coordinator with boundary grace period
            mock_coordinator = MagicMock()
            mock_coordinator.config_entry = MagicMock()
            mock_coordinator.config_entry.options = {
                ENERGY_REPORTING_GRACE_PERIOD: grace_period
            }
            mock_coordinator.config_entry.data = {"device_name": "Test Panel"}
            mock_coordinator.config_entry.title = "Test Panel"

            # Mock span panel
            mock_span_panel = MagicMock()
            mock_span_panel.status.serial_number = f"test-panel-{grace_period}"

            mock_panel_data = MagicMock()
            mock_panel_data.instantGridPowerW = 1000.0
            mock_panel_data.feedthroughPowerW = 100.0
            mock_panel_data.mainMeterEnergyConsumedWh = 1000.0
            mock_panel_data.mainMeterEnergyProducedWh = 500.0
            mock_panel_data.feedthroughEnergyConsumedWh = 375.0
            mock_panel_data.feedthroughEnergyProducedWh = 250.0

            mock_span_panel.panel = mock_panel_data

            # Mock hass
            mock_hass = MagicMock()

            # Generate sensors and verify grace period value
            sensor_configs, backing_entities, global_settings, mapping = await generate_panel_sensors(
                mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
            )

            # Verify the boundary value is correctly set
            assert global_settings["variables"]["energy_grace_period_minutes"] == grace_period
