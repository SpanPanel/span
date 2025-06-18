"""Tests for solar sensor features using synthetic sensors integration."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from custom_components.span_panel.options import (
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)
from custom_components.span_panel.solar_tab_manager import SolarTabManager
from custom_components.span_panel.synthetic_bridge import SyntheticSensorsBridge

from tests.common import create_mock_config_entry


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry with solar options."""
    return create_mock_config_entry(
        {
            CONF_HOST: "192.168.1.100",
        },
        {
            INVERTER_ENABLE: True,
            INVERTER_LEG1: 15,
            INVERTER_LEG2: 16,
        },
    )


@pytest.fixture
def mock_entity_registry():
    """Create a mock entity registry."""
    registry = MagicMock()
    registry.async_get = MagicMock(return_value=MagicMock())
    registry.async_update_entity = MagicMock()
    registry.entities = {
        "sensor.span_panel_unmapped_tab_15_power": MagicMock(),
        "sensor.span_panel_unmapped_tab_15_produced_energy": MagicMock(),
        "sensor.span_panel_unmapped_tab_15_consumed_energy": MagicMock(),
        "sensor.span_panel_unmapped_tab_16_power": MagicMock(),
        "sensor.span_panel_unmapped_tab_16_produced_energy": MagicMock(),
        "sensor.span_panel_unmapped_tab_16_consumed_energy": MagicMock(),
    }
    return registry


@pytest.fixture
def mock_coordinator_with_circuits():
    """Create a mock coordinator with circuit data for synthetic bridge tests."""
    coordinator = MagicMock()

    # Create mock span panel with unmapped tab circuits
    span_panel = MagicMock()
    span_panel.circuits = {
        "unmapped_tab_15": MagicMock(name="Unmapped Tab 15"),
        "unmapped_tab_16": MagicMock(name="Unmapped Tab 16"),
    }
    coordinator.data = span_panel
    return coordinator


@pytest.fixture
def hass_with_coordinator_data(mock_config_entry, mock_coordinator_with_circuits):
    """Create a Home Assistant instance with properly configured coordinator data."""
    hass = MagicMock()
    hass.config.config_dir = "/mock/config"

    # Mock async_add_executor_job to execute the function immediately
    async def mock_async_add_executor_job(func, *args, **kwargs):
        return func(*args, **kwargs)

    hass.async_add_executor_job = mock_async_add_executor_job

    # Set up the data structure that SyntheticSensorsBridge expects
    from custom_components.span_panel.const import DOMAIN

    hass.data = {
        DOMAIN: {mock_config_entry.entry_id: {"coordinator": mock_coordinator_with_circuits}}
    }
    return hass


class TestSolarTabManager:
    """Test the SolarTabManager class."""

    @pytest.mark.asyncio
    async def test_init(self, hass: HomeAssistant, mock_config_entry: ConfigEntry):
        """Test SolarTabManager initialization."""
        manager = SolarTabManager(hass, mock_config_entry)
        assert manager._hass == hass
        assert manager._config_entry == mock_config_entry

    @pytest.mark.asyncio
    async def test_enable_solar_tabs_dual_legs(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_entity_registry
    ):
        """Test enabling solar tabs for dual inverter legs."""
        manager = SolarTabManager(hass, mock_config_entry)

        # With simplified approach, this just logs and doesn't modify entities
        await manager.enable_solar_tabs(15, 16)

        # No entity registry operations should occur
        assert mock_entity_registry.async_update_entity.call_count == 0

    @pytest.mark.asyncio
    async def test_enable_solar_tabs_single_leg(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_entity_registry
    ):
        """Test enabling solar tabs for single inverter leg."""
        manager = SolarTabManager(hass, mock_config_entry)

        # With simplified approach, this just logs and doesn't modify entities
        await manager.enable_solar_tabs(15, 0)

        # No entity registry operations should occur
        assert mock_entity_registry.async_update_entity.call_count == 0

    @pytest.mark.asyncio
    async def test_enable_solar_tabs_entity_not_found(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry
    ):
        """Test enabling solar tabs when entities don't exist."""
        manager = SolarTabManager(hass, mock_config_entry)

        # With simplified approach, this should not raise any errors
        await manager.enable_solar_tabs(99, 100)  # Non-existent tabs

    @pytest.mark.asyncio
    async def test_disable_solar_tabs(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, mock_entity_registry
    ):
        """Test disabling all solar tab circuits."""
        manager = SolarTabManager(hass, mock_config_entry)

        # With simplified approach, this just logs and doesn't modify entities
        await manager.disable_solar_tabs()

        # No entity registry operations should occur
        assert mock_entity_registry.async_update_entity.call_count == 0


class TestSyntheticSensorsBridge:
    """Test the SyntheticSensorsBridge class."""

    @pytest.fixture
    def temp_config_dir(self, hass: HomeAssistant):
        """Create a temporary config directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            hass.config.config_dir = temp_dir
            yield temp_dir

    @pytest.mark.asyncio
    async def test_init(self, hass: HomeAssistant, mock_config_entry: ConfigEntry):
        """Test SyntheticSensorsBridge initialization."""
        bridge = SyntheticSensorsBridge(hass, mock_config_entry)
        assert bridge._hass == hass
        assert bridge._config_entry == mock_config_entry
        assert bridge._config_file.name == "span-ha-synthetic.yaml"

    @pytest.mark.asyncio
    async def test_generate_solar_config_dual_legs(
        self,
        hass_with_coordinator_data: HomeAssistant,
        mock_config_entry: ConfigEntry,
        temp_config_dir,
    ):
        """Test generating YAML config for dual inverter legs."""
        bridge = SyntheticSensorsBridge(
            hass_with_coordinator_data, mock_config_entry, temp_config_dir
        )
        await bridge.generate_solar_config(15, 16)

        # Check that config file was created
        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"
        assert config_file.exists()

        # Load and verify the YAML content
        with open(config_file) as f:
            config = yaml.safe_load(f)

        assert config["version"] == "1.0"
        assert "sensors" in config

        # Check solar inverter instant power sensor - using the actual entity ID as key
        power_sensor = config["sensors"]["span_panel_circuit_15_16_instant_power"]
        assert power_sensor["name"] == "Solar Inverter Instant Power"
        assert power_sensor["formula"] == "leg1_power + leg2_power"
        # The entity IDs should use the stable unmapped naming logic
        assert power_sensor["variables"]["leg1_power"] == "sensor.span_panel_unmapped_tab_15_power"
        assert power_sensor["variables"]["leg2_power"] == "sensor.span_panel_unmapped_tab_16_power"
        assert power_sensor["unit_of_measurement"] == "W"
        assert power_sensor["device_class"] == "power"
        assert power_sensor["state_class"] == "measurement"

        # Check energy produced sensor
        produced_sensor = config["sensors"]["span_panel_circuit_15_16_energy_produced"]
        assert produced_sensor["name"] == "Solar Inverter Energy Produced"
        assert produced_sensor["formula"] == "leg1_produced + leg2_produced"
        # The entity IDs should use the stable unmapped naming logic
        assert (
            produced_sensor["variables"]["leg1_produced"]
            == "sensor.span_panel_unmapped_tab_15_energy_produced"
        )
        assert (
            produced_sensor["variables"]["leg2_produced"]
            == "sensor.span_panel_unmapped_tab_16_energy_produced"
        )
        assert produced_sensor["unit_of_measurement"] == "Wh"
        assert produced_sensor["device_class"] == "energy"
        assert produced_sensor["state_class"] == "total_increasing"

        # Check energy consumed sensor
        consumed_sensor = config["sensors"]["span_panel_circuit_15_16_energy_consumed"]
        assert consumed_sensor["name"] == "Solar Inverter Energy Consumed"
        assert consumed_sensor["formula"] == "leg1_consumed + leg2_consumed"
        # The entity IDs should use the stable unmapped naming logic
        assert (
            consumed_sensor["variables"]["leg1_consumed"]
            == "sensor.span_panel_unmapped_tab_15_energy_consumed"
        )
        assert (
            consumed_sensor["variables"]["leg2_consumed"]
            == "sensor.span_panel_unmapped_tab_16_energy_consumed"
        )
        assert consumed_sensor["unit_of_measurement"] == "Wh"
        assert consumed_sensor["device_class"] == "energy"
        assert consumed_sensor["state_class"] == "total_increasing"

    @pytest.mark.asyncio
    async def test_generate_solar_config_single_leg(
        self,
        hass_with_coordinator_data: HomeAssistant,
        mock_config_entry: ConfigEntry,
        temp_config_dir,
    ):
        """Test generating YAML config for single inverter leg."""
        bridge = SyntheticSensorsBridge(
            hass_with_coordinator_data, mock_config_entry, temp_config_dir
        )
        await bridge.generate_solar_config(15, 0)

        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"
        with open(config_file) as f:
            config = yaml.safe_load(f)

        # Check single-leg formulas
        power_sensor = config["sensors"]["span_panel_circuit_15_instant_power"]
        assert power_sensor["formula"] == "leg1_power"
        assert "leg1_power" in power_sensor["variables"]
        assert "leg2_power" not in power_sensor["variables"]

    @pytest.mark.asyncio
    async def test_generate_solar_config_no_valid_legs(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir
    ):
        """Test generating YAML config with no valid legs should not create file."""
        bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_config_dir)
        await bridge.generate_solar_config(0, 0)

        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"
        assert not config_file.exists()

    @pytest.mark.asyncio
    async def test_remove_solar_config(
        self,
        hass_with_coordinator_data: HomeAssistant,
        mock_config_entry: ConfigEntry,
        temp_config_dir,
    ):
        """Test removing the solar configuration file."""
        bridge = SyntheticSensorsBridge(
            hass_with_coordinator_data, mock_config_entry, temp_config_dir
        )

        # First create a config file
        await bridge.generate_solar_config(15, 16)
        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"
        assert config_file.exists()

        # Then remove it
        await bridge.remove_solar_config()
        assert not config_file.exists()

    @pytest.mark.asyncio
    async def test_remove_solar_config_nonexistent_file(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir
    ):
        """Test removing a nonexistent config file should not error."""
        bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_config_dir)
        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"
        assert not config_file.exists()

        # Should not raise an exception
        await bridge.remove_solar_config()

    @pytest.mark.asyncio
    async def test_validate_config_valid(
        self,
        hass_with_coordinator_data: HomeAssistant,
        mock_config_entry: ConfigEntry,
        temp_config_dir,
    ):
        """Test validating a valid configuration."""
        bridge = SyntheticSensorsBridge(
            hass_with_coordinator_data, mock_config_entry, temp_config_dir
        )
        await bridge.generate_solar_config(15, 16)

        assert await bridge.validate_config() is True

    @pytest.mark.asyncio
    async def test_validate_config_nonexistent(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir
    ):
        """Test validating a nonexistent configuration."""
        bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_config_dir)

        assert await bridge.validate_config() is False

    @pytest.mark.asyncio
    async def test_validate_config_invalid_yaml(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir
    ):
        """Test validating invalid YAML configuration."""
        bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_config_dir)
        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"

        # Create invalid YAML
        with open(config_file, "w") as f:
            f.write("invalid: yaml: content:")

        assert await bridge.validate_config() is False

    @pytest.mark.asyncio
    async def test_validate_config_missing_required_fields(
        self, hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir
    ):
        """Test validating configuration missing required fields."""
        bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_config_dir)
        config_file = Path(temp_config_dir) / "span-ha-synthetic.yaml"

        # Create YAML missing required fields
        config = {"version": "1.0"}  # Missing "sensors"
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        assert await bridge.validate_config() is False


class TestSolarSensorIntegration:
    """Test the integration of solar sensors with the main sensor platform."""

    @pytest.mark.asyncio
    async def test_solar_enabled_lifecycle(self, hass: HomeAssistant):
        """Test the complete lifecycle of enabling solar sensors."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            hass.config.config_dir = temp_dir

            # Mock async_add_executor_job to execute functions immediately
            async def mock_async_add_executor_job(func, *args, **kwargs):
                return func(*args, **kwargs)

            hass.async_add_executor_job = mock_async_add_executor_job

            # Set up coordinator data structure for SyntheticSensorsBridge
            mock_coordinator = MagicMock()
            span_panel = MagicMock()
            span_panel.circuits = {
                "unmapped_tab_15": MagicMock(name="Unmapped Tab 15"),
                "unmapped_tab_16": MagicMock(name="Unmapped Tab 16"),
            }
            mock_coordinator.data = span_panel

            from custom_components.span_panel.const import DOMAIN

            hass.data = {DOMAIN: {mock_config_entry.entry_id: {"coordinator": mock_coordinator}}}

            # Mock entity registry
            mock_registry = MagicMock()
            mock_registry.async_get = MagicMock(return_value=MagicMock())
            mock_registry.async_update_entity = MagicMock()
            mock_registry.entities = {
                f"sensor.span_panel_unmapped_tab_{leg}_{sensor_type}": MagicMock()
                for leg in [15, 16]
                for sensor_type in ["power", "produced_energy", "consumed_energy"]
            }

            # Create managers (no need to patch since SolarTabManager is simplified)
            tab_manager = SolarTabManager(hass, mock_config_entry)
            synthetic_bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_dir)

            # Enable solar (simplified - no entity registry manipulation)
            await tab_manager.enable_solar_tabs(15, 16)
            await synthetic_bridge.generate_solar_config(15, 16)

            # Verify YAML config is created and valid
            config_file = Path(temp_dir) / "span-ha-synthetic.yaml"
            assert config_file.exists()
            assert await synthetic_bridge.validate_config() is True

            # Disable solar
            await tab_manager.disable_solar_tabs()
            await synthetic_bridge.remove_solar_config()

            # Verify YAML config is removed
            assert not config_file.exists()

    @pytest.mark.asyncio
    async def test_solar_disabled_lifecycle(self, hass: HomeAssistant):
        """Test the lifecycle when solar is disabled."""
        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: False},  # Solar disabled
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            hass.config.config_dir = temp_dir

            # Create managers (simplified - no entity registry manipulation)
            tab_manager = SolarTabManager(hass, mock_config_entry)
            synthetic_bridge = SyntheticSensorsBridge(hass, mock_config_entry, temp_dir)

            # When solar is disabled, cleanup should work safely
            await tab_manager.disable_solar_tabs()
            await synthetic_bridge.remove_solar_config()

            # Should not error and no config file should exist
            config_file = Path(temp_dir) / "span-ha-synthetic.yaml"
            assert not config_file.exists()

    @pytest.mark.asyncio
    async def test_yaml_compliance_with_ha_synthetic_sensors(
        self, hass_with_coordinator_data: HomeAssistant, mock_config_entry: ConfigEntry
    ):
        """Test that generated YAML is compliant with ha-synthetic-sensors format."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = SyntheticSensorsBridge(hass_with_coordinator_data, mock_config_entry, temp_dir)
            await bridge.generate_solar_config(15, 16)

            config_file = Path(temp_dir) / "span-ha-synthetic.yaml"
            with open(config_file) as f:
                config = yaml.safe_load(f)

            # Verify compliance with ha-synthetic-sensors expected format
            assert isinstance(config, dict)
            assert "version" in config
            assert "sensors" in config
            assert isinstance(config["sensors"], dict)

            # No global_settings should be present (not used in ha-synthetic-sensors)
            assert "global_settings" not in config

            # Each sensor should have required fields
            for _sensor_key, sensor_config in config["sensors"].items():
                assert "name" in sensor_config
                assert "formula" in sensor_config
                assert "variables" in sensor_config
                assert "unit_of_measurement" in sensor_config
                assert "device_class" in sensor_config
                assert "state_class" in sensor_config

            # Verify sensor keys match expected naming
            expected_sensors = [
                "span_panel_circuit_15_16_instant_power",
                "span_panel_circuit_15_16_energy_produced",
                "span_panel_circuit_15_16_energy_consumed",
            ]
            for sensor_key in expected_sensors:
                assert sensor_key in config["sensors"]


class TestCacheWindowConfiguration:
    """Test the cache window configuration for solar sensors."""

    @pytest.mark.asyncio
    async def test_cache_window_calculation_with_solar(self, hass: HomeAssistant):
        """Test that cache window is calculated correctly when solar is enabled."""
        from custom_components.span_panel.span_panel_api import SpanPanelApi

        # Test with different scan intervals
        test_cases = [
            (15, 9.0),  # Default: 15s → 9s cache
            (30, 18.0),  # 30s → 18s cache
            (5, 3.0),  # 5s → 3s cache
            (1, 1.0),  # 1s → 1s cache (minimum)
        ]

        for scan_interval, expected_cache in test_cases:
            api = SpanPanelApi("192.168.1.100", scan_interval=scan_interval)
            cache_window = api._calculate_cache_window()
            assert cache_window == expected_cache, f"Failed for {scan_interval}s interval"

    @pytest.mark.asyncio
    async def test_solar_api_integration_with_cache(self, hass: HomeAssistant):
        """Test that solar API integration properly uses cache window."""
        from custom_components.span_panel.span_panel import SpanPanel
        from custom_components.span_panel.options import Options

        mock_config_entry = create_mock_config_entry(
            {CONF_HOST: "192.168.1.100"},
            {INVERTER_ENABLE: True, INVERTER_LEG1: 15, INVERTER_LEG2: 16},
        )

        options = Options(mock_config_entry)

        with patch("custom_components.span_panel.span_panel_api.SpanPanelClient") as mock_client:
            _span_panel = SpanPanel(
                host="192.168.1.100",
                access_token="test_token",
                options=options,
                use_ssl=False,
                scan_interval=15,
            )

            # Verify that SpanPanelClient was called with cache_window
            mock_client.assert_called_with(
                host="192.168.1.100",
                timeout=30,  # API_TIMEOUT
                use_ssl=False,
                retries=3,  # DEFAULT_API_RETRIES
                retry_timeout=0.5,  # DEFAULT_API_RETRY_TIMEOUT
                retry_backoff_multiplier=2.0,  # DEFAULT_API_RETRY_BACKOFF_MULTIPLIER
                cache_window=9.0,  # 60% of 15 seconds
            )
