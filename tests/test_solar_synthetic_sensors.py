"""Test basic functionality of solar synthetic sensors."""

import tempfile
from unittest.mock import patch

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
async def test_reload_for_removal_method_exists(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test that the reload for removal method exists and can be called."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock the hass.config_entries to avoid actual config operations
    with patch.object(hass.config_entries, "async_entries", return_value=[]):
        # Test that the method can be called without error
        try:
            success = await solar_sensors._reload_synthetic_sensors_for_removal()
            # Method should return False when no config entries found
            assert success is False
        except Exception as e:
            pytest.fail(f"Unexpected error in reload method: {e}")


@pytest.mark.asyncio
async def test_write_config_file(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test writing config to file."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    test_config = {"sensors": {"test_sensor": {"entity_id": "sensor.test", "formula": "1 + 1"}}}

    await solar_sensors._write_solar_config(test_config)

    # Check that file was created
    assert solar_sensors.config_file_path.exists()

    # Check file contents
    with open(solar_sensors.config_file_path) as f:
        content = f.read()
        assert "test_sensor" in content
        assert "sensor.test" in content


@pytest.mark.asyncio
async def test_stable_synthetic_sensor_naming_verification(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test that synthetic sensor entity IDs are stable and don't use circuit-based naming."""
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    from unittest.mock import MagicMock

    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()

    # Test entity ID construction with friendly naming (should be stable)
    power_entity_id = solar_sensors._construct_solar_inverter_entity_id(
        mock_coordinator,
        mock_span_panel,
        "sensor",
        15,  # leg1
        16,  # leg2
        "instant_power",
        "Solar Inverter Instant Power",  # Friendly name - should produce stable ID
    )

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
    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    from unittest.mock import MagicMock

    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()

    # Test entity ID construction with friendly naming
    power_entity_id = solar_sensors._construct_solar_inverter_entity_id(
        mock_coordinator,
        mock_span_panel,
        "sensor",
        15,  # leg1
        16,  # leg2
        "instant_power",
        "Solar Inverter Instant Power",  # Friendly name - should produce stable ID
    )

    # The entity ID should not contain circuit numbers when using friendly naming
    # It should be stable and descriptive
    if power_entity_id:
        assert "circuit_15" not in power_entity_id
        assert "circuit_16" not in power_entity_id
        assert "solar" in power_entity_id.lower()
        assert "instant_power" in power_entity_id

    print(f"Generated stable synthetic sensor ID: {power_entity_id}")


@pytest.mark.asyncio
async def test_construct_solar_inverter_entity_id_single_leg(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test _construct_solar_inverter_entity_id with single leg."""
    from unittest.mock import MagicMock

    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()

    with patch(
        "custom_components.span_panel.solar_synthetic_sensors.construct_synthetic_entity_id"
    ) as mock_construct:
        # With stable synthetic naming, this should return stable naming
        mock_construct.return_value = "sensor.span_panel_solar_inverter_power"

        result = solar_sensors._construct_solar_inverter_entity_id(
            mock_coordinator, mock_span_panel, "sensor", 30, 0, "power"
        )

        # Verify it was called with correct circuit numbers
        mock_construct.assert_called_once_with(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_numbers=[30],
            suffix="power",
            friendly_name=None,
        )
        assert result == "sensor.span_panel_solar_inverter_power"


@pytest.mark.asyncio
async def test_construct_solar_inverter_entity_id_dual_leg(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, temp_config_dir: str
):
    """Test _construct_solar_inverter_entity_id with dual legs."""
    from unittest.mock import MagicMock

    solar_sensors = SolarSyntheticSensors(hass, mock_config_entry, temp_config_dir)

    # Mock coordinator and span panel data
    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()

    with patch(
        "custom_components.span_panel.solar_synthetic_sensors.construct_synthetic_entity_id"
    ) as mock_construct:
        # With stable synthetic naming, this should return stable naming
        mock_construct.return_value = "sensor.span_panel_solar_production_power"

        result = solar_sensors._construct_solar_inverter_entity_id(
            mock_coordinator, mock_span_panel, "sensor", 30, 32, "power", "Solar Production"
        )

        # Verify it was called with correct circuit numbers
        mock_construct.assert_called_once_with(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_numbers=[30, 32],
            suffix="power",
            friendly_name="Solar Production",
        )
        assert result == "sensor.span_panel_solar_production_power"
