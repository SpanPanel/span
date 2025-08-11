"""Test template-based synthetic sensor system."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel.synthetic_panel_circuits import (
    generate_panel_sensors,
)
from custom_components.span_panel.synthetic_sensors import (
    create_data_provider_callback,
)


@pytest.mark.asyncio
async def test_template_loading_and_processing():
    """Test that templates can be loaded and processed correctly."""
    from custom_components.span_panel.synthetic_utils import fill_template, load_template

    # Test loading a template
    template_content = await load_template("panel_sensor")
    assert template_content is not None
    assert isinstance(template_content, str)

    # The template should contain placeholders
    assert "{{sensor_key}}" in template_content
    assert "{{sensor_name}}" in template_content
    assert "{{entity_id}}" in template_content
    assert "{{backing_entity_id}}" in template_content

    # Test filling template with placeholders that match the template
    placeholders = {
        "sensor_key": "test_device_current_power",
        "sensor_name": "Test Device Current Power",
        "entity_id": "sensor.test_device_current_power",
        "backing_entity_id": "sensor.test_device_backing_current_power",
    }

    filled_template = fill_template(template_content, placeholders)
    assert "{{" not in filled_template  # Should have no remaining placeholders
    assert "test_device_current_power" in filled_template
    assert "Test Device Current Power" in filled_template


@pytest.mark.asyncio
async def test_yaml_template_combination():
    """Test that header and sensor templates can be combined correctly."""
    from custom_components.span_panel.synthetic_utils import combine_yaml_templates

    # Test combining header with a sensor template
    placeholders = {
        "device_identifier": "SP3-242424-001",
        "panel_id": "SP3-242424-001",
        "sensor_key": "span_sp3-242424-001_instantgridpowerw",
        "sensor_name": "Current Power",
        "entity_id": "sensor.span_panel_current_power",
        "backing_entity_id": "sensor.span_242424_001_backing_instant_grid_power",
    }

    # Combine header with panel sensor template
    combined_result = await combine_yaml_templates(["panel_sensor"], placeholders)

    # Verify the result structure
    assert isinstance(combined_result, dict)
    assert "global_settings" in combined_result
    assert "sensor_configs" in combined_result

    # Check global settings
    global_settings = combined_result["global_settings"]
    assert isinstance(global_settings, dict)
    assert "device_identifier" in global_settings
    assert global_settings["device_identifier"] == "SP3-242424-001"

    # Check sensor configs
    sensor_configs = combined_result["sensor_configs"]
    assert isinstance(sensor_configs, dict)
    assert len(sensor_configs) > 0

    # Verify sensor config structure
    for _sensor_key, sensor_config in sensor_configs.items():
        assert "name" in sensor_config
        assert "entity_id" in sensor_config
        assert "formula" in sensor_config
        assert "variables" in sensor_config
        assert "metadata" in sensor_config

        # Verify entity_id format
        entity_id = sensor_config["entity_id"]
        assert entity_id.startswith("sensor.")
        assert "span_panel" in entity_id or "242424" in entity_id


@pytest.mark.asyncio
async def test_sensor_set_building():
    """Test building complete sensor set from templates."""
    # Create mock coordinator and span panel with proper data structure
    mock_coordinator = MagicMock()
    mock_coordinator.options.use_circuit_numbers = False
    mock_coordinator.options.use_device_prefix = True

    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "test_serial_123"

    # Mock the panel data object with proper attribute names
    mock_panel_data = MagicMock()
    mock_panel_data.instantGridPowerW = 1500.0
    mock_panel_data.feedthroughPowerW = 500.0
    mock_panel_data.mainMeterEnergyProducedWh = 1000.0
    mock_panel_data.mainMeterEnergyConsumedWh = 2000.0
    mock_panel_data.feedthroughEnergyProducedWh = 300.0
    mock_panel_data.feedthroughEnergyConsumedWh = 400.0
    mock_span_panel.panel = mock_panel_data  # Use panel instead of data

    # Test building sensor set
    sensor_set_dict, backing_entities, global_settings = await generate_panel_sensors(
        mock_coordinator, mock_span_panel
    )

    # Verify sensor set structure
    assert "version" not in sensor_set_dict  # generate_panel_sensors returns configs directly
    assert isinstance(sensor_set_dict, dict)  # Direct sensor configs
    assert isinstance(backing_entities, list)
    assert isinstance(global_settings, dict)

    # Verify we have panel sensors
    sensors = sensor_set_dict  # Direct sensor configs, not nested under "sensors"
    assert len(sensors) > 0

    # Check that sensor configs are properly formatted
    for sensor_id, sensor_config in sensors.items():
        # Sensor ID is the key (like "instantGridPowerW")
        assert sensor_id in [
            "span_sp3-test_serial_123-001_instantgridpowerw",
            "span_sp3-test_serial_123-001_feedthroughpowerw",
            "span_sp3-test_serial_123-001_mainmeterenergyproducedwh",
            "span_sp3-test_serial_123-001_mainmeterenergyconsumedwh",
            "span_sp3-test_serial_123-001_feedthroughenergyproducedwh",
            "span_sp3-test_serial_123-001_feedthroughenergyconsumedwh",
        ] or sensor_id.startswith("span_sp3-test_serial_123-001_")
        assert "name" in sensor_config
        assert "entity_id" in sensor_config
        # The entity_id should contain the serial number
        assert (
            "test_serial_123" in sensor_config["entity_id"]
            or "span_panel" in sensor_config["entity_id"]
        )


@pytest.mark.asyncio
async def test_data_provider_callback():
    """Test data provider callback functionality."""
    # Create mock backing entities with correct structure
    backing_entities = [
        {
            "entity_id": "sensor.test_serial_123_backing_current_power",
            "value": 1500.0,  # Mock power value
            "data_path": "current_power",
        }
    ]

    # Create mock coordinator for data provider
    mock_coordinator = MagicMock()
    mock_span_panel = MagicMock()
    mock_data = MagicMock()
    mock_data.current_power = 1500.0
    mock_span_panel.panel = mock_data
    mock_coordinator.span_panel_api = mock_span_panel

    # Create data provider callback
    data_provider = create_data_provider_callback(mock_coordinator, backing_entities)

    # Test with valid entity ID
    result = data_provider("sensor.test_serial_123_backing_current_power")
    assert "value" in result
    assert "exists" in result

    # Test with invalid entity ID
    result = data_provider("sensor.invalid_entity")
    assert result["exists"] is False


@pytest.mark.asyncio
async def test_integration_setup_with_synthetic_sensors(hass: HomeAssistant):
    """Test that the SyntheticSensorManager sets up synthetic sensors correctly."""
    with (
        patch(
            "custom_components.span_panel.synthetic_sensors.StorageManager"
        ) as mock_storage_manager,
        patch(
            "custom_components.span_panel.synthetic_sensors.async_setup_synthetic_sensors"
        ) as mock_setup,
    ):
        # Mock the storage manager
        mock_storage_instance = AsyncMock()
        mock_storage_manager.return_value = mock_storage_instance

        # Mock async_setup_synthetic_sensors
        mock_setup.return_value = None

        # Create mock config entry
        mock_entry = MagicMock(spec=ConfigEntry)
        mock_entry.entry_id = "test_entry"
        mock_entry.data = {"host": "192.168.1.100", "access_token": "test_token"}
        mock_entry.options = {}

        # Mock coordinator with span_panel_api
        mock_coordinator = MagicMock()
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "test_serial_123"

        # Mock the data object with panel data
        mock_data = MagicMock()
        mock_data.instantGridPowerW = 1500.0
        mock_data.feedthroughPowerW = 500.0
        mock_data.mainMeterEnergyProducedWh = 1000.0
        mock_data.mainMeterEnergyConsumedWh = 2000.0
        mock_data.feedthroughEnergyProducedWh = 300.0
        mock_data.feedthroughEnergyConsumedWh = 400.0
        mock_span_panel.panel = mock_data  # Use panel instead of data

        mock_coordinator.span_panel_api = mock_span_panel
        mock_coordinator.options.use_circuit_numbers = False
        mock_coordinator.options.use_device_prefix = True

        # Test the SyntheticSensorManager
        from custom_components.span_panel.synthetic_sensors import SyntheticSensorManager

        manager = SyntheticSensorManager(hass, mock_entry, mock_coordinator)

        # Mock async_add_entities
        mock_async_add_entities = AsyncMock()

        # Should not raise an exception
        await manager.async_setup_synthetic_sensors(mock_async_add_entities)

        # Verify storage manager was created and loaded
        mock_storage_manager.assert_called_once_with(hass)
        mock_storage_instance.async_load.assert_called_once()

        # Verify async_setup_synthetic_sensors was called
        mock_setup.assert_called_once()


def test_backing_entity_id_format():
    """Test that backing entity IDs have correct sensor. prefix."""
    from custom_components.span_panel.helpers import construct_backing_entity_id
    from custom_components.span_panel.span_panel import SpanPanel

    # Create mock span panel
    mock_span_panel = MagicMock(spec=SpanPanel)
    mock_span_panel.status.serial_number = "test_serial_123"

    # Test backing entity ID generation
    backing_entity_id = construct_backing_entity_id(
        mock_span_panel, circuit_id="0", suffix="current_power"
    )

    # Should have sensor. prefix
    assert backing_entity_id.startswith("sensor.")
    assert "test_serial_123" in backing_entity_id
    assert "backing" in backing_entity_id
    assert "current_power" in backing_entity_id
