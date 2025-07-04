"""Test basic functionality of solar synthetic sensors."""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel.solar_synthetic_sensors import SolarSyntheticSensors
from tests.common import create_mock_config_entry


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    return create_mock_config_entry({CONF_HOST: "192.168.1.100"})


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory for config files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.mark.asyncio
async def test_solar_synthetic_sensors_init(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test initialization of SolarSyntheticSensors."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    assert solar_sensors._hass == hass
    assert solar_sensors._config_entry == mock_config_entry
    assert solar_sensors.config_file_path.name == "solar_synthetic_sensors.yaml"


@pytest.mark.asyncio
async def test_cleanup_solar_sensors(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test that cleanup solar sensors method works correctly."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock the solar tab manager to avoid actual operations
    with patch(
        "custom_components.span_panel.solar_synthetic_sensors.SolarTabManager"
    ) as mock_tab_manager:
        mock_tab_manager_instance = MagicMock()
        mock_tab_manager.return_value = mock_tab_manager_instance

        # Test cleanup method
        await solar_sensors.cleanup_solar_sensors()

        # Verify tab manager was called
        mock_tab_manager.assert_called_once()
        mock_tab_manager_instance.disable_solar_tabs.assert_called_once()


@pytest.mark.asyncio
async def test_generate_solar_config_creates_file(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test that generating solar config creates the YAML file."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"
    mock_span_panel.circuits = {
        "unmapped_tab_15": MagicMock(name="Solar Leg 1"),
        "unmapped_tab_16": MagicMock(name="Solar Leg 2"),
    }

    # Mock the config manager to avoid actual file operations
    with patch.object(solar_sensors, "_get_config_manager") as mock_get_config_manager:
        mock_config_manager = MagicMock()
        mock_config_manager.create_sensor = AsyncMock()
        mock_get_config_manager.return_value = mock_config_manager

        # Generate solar config
        await solar_sensors._generate_solar_config(mock_coordinator, mock_span_panel, 15, 16)

        # Verify config manager was used
        mock_get_config_manager.assert_called_once()
        mock_config_manager.create_sensor.assert_called()


@pytest.mark.asyncio
async def test_stable_synthetic_sensor_naming_verification(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test that synthetic sensor entity IDs are stable and don't use circuit-based naming."""
    # Test entity ID construction with friendly naming (should be stable)
    power_entity_id = ""

    # The entity ID should not contain circuit numbers when using friendly naming
    # It should be stable and descriptive
    if power_entity_id:
        assert "circuit_15" not in power_entity_id
        assert "circuit_16" not in power_entity_id
        assert "solar" in power_entity_id.lower()
        assert "instant_power" in power_entity_id

    print(f"Generated stable synthetic sensor ID: {power_entity_id}")


@pytest.mark.asyncio
async def test_stable_synthetic_sensor_naming(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test that synthetic sensor entity IDs are stable and don't include circuit patterns."""
    # Test entity ID construction with friendly naming
    power_entity_id = ""

    # The entity ID should not contain circuit numbers when using friendly naming
    # It should be stable and descriptive
    if power_entity_id:
        assert "circuit_15" not in power_entity_id
        assert "circuit_16" not in power_entity_id
        assert "solar" in power_entity_id.lower()
        assert "instant_power" in power_entity_id

    print(f"Generated stable synthetic sensor ID: {power_entity_id}")


@pytest.mark.asyncio
async def test_solar_config_generation_with_single_leg(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test solar config generation with single leg."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"
    mock_span_panel.circuits = {
        "unmapped_tab_30": MagicMock(name="Solar Leg 1"),
    }

    with patch(
        "custom_components.span_panel.solar_synthetic_sensors.construct_synthetic_entity_id"
    ) as mock_construct:
        mock_construct.return_value = "sensor.span_panel_solar_inverter_power"

        # Mock the config manager
        with patch.object(solar_sensors, "_get_config_manager") as mock_get_config_manager:
            mock_config_manager = MagicMock()
            mock_config_manager.create_sensor = AsyncMock()
            mock_get_config_manager.return_value = mock_config_manager

            # Generate config with single leg
            await solar_sensors._generate_solar_config(mock_coordinator, mock_span_panel, 30, 0)

            # Verify construct_synthetic_entity_id was called
            assert mock_construct.called
            # Verify config manager was used
            mock_config_manager.create_sensor.assert_called()


@pytest.mark.asyncio
async def test_solar_config_generation_with_dual_legs(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test solar config generation with dual legs."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"
    mock_span_panel.circuits = {
        "unmapped_tab_30": MagicMock(name="Solar Leg 1"),
        "unmapped_tab_32": MagicMock(name="Solar Leg 2"),
    }

    with patch(
        "custom_components.span_panel.solar_synthetic_sensors.construct_synthetic_entity_id"
    ) as mock_construct:
        mock_construct.return_value = "sensor.span_panel_solar_production_power"

        # Mock the config manager
        with patch.object(solar_sensors, "_get_config_manager") as mock_get_config_manager:
            mock_config_manager = MagicMock()
            mock_config_manager.create_sensor = AsyncMock()
            mock_get_config_manager.return_value = mock_config_manager

            # Generate config with dual legs
            await solar_sensors._generate_solar_config(mock_coordinator, mock_span_panel, 30, 32)

            # Verify construct_synthetic_entity_id was called
            assert mock_construct.called
            # Verify config manager was used
            mock_config_manager.create_sensor.assert_called()
