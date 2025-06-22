"""Additional tests for edge cases in solar sensor features."""

import tempfile
from pathlib import Path

import pytest
from unittest.mock import MagicMock
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST

from custom_components.span_panel.options import (
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)
from custom_components.span_panel.solar_tab_manager import SolarTabManager
from custom_components.span_panel.solar_synthetic_sensors import SolarSyntheticSensors

from tests.common import create_mock_config_entry


class TestSolarEdgeCases:
    """Test edge cases for solar sensor features."""

    @pytest.mark.asyncio
    async def test_solar_tab_manager_with_zero_legs(self, hass: HomeAssistant):
        """Test SolarTabManager when both legs are set to 0 (disabled)."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 0, INVERTER_LEG2: 0},
        )

        manager = SolarTabManager(hass, mock_config_entry)

        # This should not raise an exception
        await manager.enable_solar_tabs(0, 0)

        # Should still disable properly
        await manager.disable_solar_tabs()

    @pytest.mark.asyncio
    async def test_solar_synthetic_sensors_with_empty_hass_config(self, hass: HomeAssistant):
        """Test SolarSyntheticSensors when Home Assistant config path is None."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )

        # Mock hass.config.config_dir to return None
        hass.config.config_dir = None

        # Should raise ValueError when config_dir is None and no config_dir provided
        with pytest.raises(ValueError, match="Home Assistant config directory is not available"):
            SolarSyntheticSensors(hass, mock_config_entry)

        # But should work when providing an explicit config directory
        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_dir)
            await solar_sensors.generate_config(15, 16)
            await solar_sensors.remove_config()

    @pytest.mark.asyncio
    async def test_solar_disabled_no_operation(self, hass: HomeAssistant):
        """Test that when solar is disabled, no operations are performed."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: False},  # Solar disabled
        )

        manager = SolarTabManager(hass, mock_config_entry)
        solar_sensors = SolarSyntheticSensors(hass, mock_config_entry)

        # These should not perform any operations when solar is disabled
        await manager.enable_solar_tabs(15, 16)
        await solar_sensors.generate_config(15, 16)

        # Disable operations should still work
        await manager.disable_solar_tabs()
        await solar_sensors.remove_config()

    @pytest.mark.asyncio
    async def test_concurrent_solar_operations(self, hass: HomeAssistant):
        """Test concurrent solar enable/disable operations."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )

        manager = SolarTabManager(hass, mock_config_entry)
        solar_sensors = SolarSyntheticSensors(hass, mock_config_entry)

        # Run operations concurrently (no need to patch since SolarTabManager is simplified)
        import asyncio

        await asyncio.gather(
            manager.enable_solar_tabs(15, 16),
            solar_sensors.generate_config(15, 16),
            manager.disable_solar_tabs(),
            solar_sensors.remove_config(),
        )

    @pytest.mark.asyncio
    async def test_cache_window_boundary_conditions(self, hass: HomeAssistant):
        """Test cache window calculation with boundary conditions."""
        from custom_components.span_panel.span_panel_api import SpanPanelApi

        # Test very small scan intervals
        api = SpanPanelApi("192.168.1.100", scan_interval=0.5)  # 500ms
        cache_window = api._calculate_cache_window()
        assert cache_window == 1.0  # Should enforce minimum of 1 second

        # Test very large scan intervals
        api = SpanPanelApi("192.168.1.100", scan_interval=300)  # 5 minutes
        cache_window = api._calculate_cache_window()
        assert cache_window == 180.0  # 60% of 300 seconds

        # Test exact minimum boundary
        api = SpanPanelApi("192.168.1.100", scan_interval=1)
        cache_window = api._calculate_cache_window()
        assert cache_window == 1.0  # Minimum enforced

    @pytest.mark.asyncio
    async def test_yaml_generation_with_special_characters(self, hass: HomeAssistant):
        """Test YAML generation with special characters in entity names."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )

        # Set up coordinator data structure
        mock_coordinator = MagicMock()
        span_panel = MagicMock()
        span_panel.circuits = {
            "unmapped_tab_15": MagicMock(name="Unmapped Tab 15"),
            "unmapped_tab_16": MagicMock(name="Unmapped Tab 16"),
        }
        mock_coordinator.data = span_panel

        # Mock async_add_executor_job
        async def mock_async_add_executor_job(func, *args, **kwargs):
            return func(*args, **kwargs)

        hass.async_add_executor_job = mock_async_add_executor_job

        from custom_components.span_panel.const import DOMAIN

        hass.data = {DOMAIN: {mock_config_entry.entry_id: {"coordinator": mock_coordinator}}}

        # Use temporary directory instead of mocking file operations
        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_dir)
            await solar_sensors.generate_config(15, 16)

            # Verify file was created
            config_file = Path(temp_dir) / "span-ha-synthetic.yaml"
            assert config_file.exists()

    @pytest.mark.asyncio
    async def test_error_recovery_in_lifecycle(self, hass: HomeAssistant):
        """Test error recovery during solar lifecycle operations."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )

        manager = SolarTabManager(hass, mock_config_entry)
        solar_sensors = SolarSyntheticSensors(hass, mock_config_entry)

        # Test that operations work without error (simplified - no entity registry operations)
        await manager.enable_solar_tabs(15, 16)

        # Cleanup operations should still work
        await solar_sensors.remove_config()
        await solar_sensors.remove_config()

    @pytest.mark.asyncio
    async def test_multiple_config_entries(self, hass: HomeAssistant):
        """Test handling multiple SPAN panel config entries."""
        config_entry_1 = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )
        config_entry_2 = create_mock_config_entry(
            {CONF_HOST: "192.168.1.101"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 17, INVERTER_LEG2: 18},
        )

        manager_1 = SolarTabManager(hass, config_entry_1)
        manager_2 = SolarTabManager(hass, config_entry_2)

        solar_sensors_1 = SolarSyntheticSensors(hass, config_entry_1)
        solar_sensors_2 = SolarSyntheticSensors(hass, config_entry_2)

        # Both should operate independently (simplified - no entity registry manipulation)
        await manager_1.enable_solar_tabs(15, 16)
        await manager_2.enable_solar_tabs(17, 18)

        await solar_sensors_1.generate_config(15, 16)
        await solar_sensors_2.generate_config(17, 18)

        # Cleanup
        await manager_1.disable_solar_tabs()
        await manager_2.disable_solar_tabs()
        await solar_sensors_1.remove_config()
        await solar_sensors_2.remove_config()
