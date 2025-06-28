"""Test YAML generation for synthetic sensors."""

from unittest.mock import Mock

import pytest

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.span_sensor_manager import SpanSensorManager


@pytest.mark.asyncio
async def test_panel_sensor_yaml_generation_device_prefix(hass):
    """Test panel sensor YAML generation with device prefix."""
    # Create a mock circuit object for testing
    circuit = Mock()
    circuit.id = "1"
    circuit.name = "Test Circuit"
    circuit.relayState = "CLOSED"
    circuit.instantPowerW = 100.0
    circuit.tabs = ["1", "2"]

    # Create mock panel with realistic serial number
    mock_panel = Mock()
    mock_panel.status.serial_number = "sp3-242424-001"
    mock_panel.circuits = {"1": circuit}  # Dictionary format expected by SpanSensorManager

    # Create sensor manager
    manager = SpanSensorManager(hass, mock_panel, "test_entry")

    # Configure for device prefix
    config_options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry = Mock()
    entry.options = config_options
    entry.entry_id = "test_entry"
    hass.data = {DOMAIN: {"test_entry": {"entry": entry}}}

    # Create mock coordinator
    mock_coordinator = Mock()
    mock_coordinator.config_entry = entry

    # Generate YAML for sensors
    yaml_config = manager.generate_unified_config_sync(mock_coordinator, mock_panel)

    # Check that panel sensor has correct structure
    panel_sensor_key = "span_sp3-242424-001_instantgridpowerw"
    assert panel_sensor_key in yaml_config

    panel_sensor = yaml_config[panel_sensor_key]
    assert panel_sensor["name"] == "Current Power"
    assert panel_sensor["entity_id"] == "sensor.span_panel_current_power"
    assert panel_sensor["formula"] == "source_value"
    assert "variables" in panel_sensor
    assert (
        panel_sensor["variables"]["source_value"]
        == "span_panel_synthetic_backing.circuit_0_instant_grid_power"
    )
    assert panel_sensor["unit_of_measurement"] == "W"
    assert panel_sensor["device_class"] == "power"
    assert panel_sensor["state_class"] == "measurement"
    assert panel_sensor["device_identifier"] == "span_panel_sp3-242424-001"


@pytest.mark.asyncio
async def test_circuit_sensor_yaml_generation_device_prefix(hass):
    """Test circuit sensor YAML generation with device prefix."""
    # Create a mock circuit object for testing
    circuit = Mock()
    circuit.id = "1"
    circuit.name = "Test Circuit"
    circuit.relayState = "CLOSED"
    circuit.instantPowerW = 100.0
    circuit.tabs = ["1", "2"]

    # Create mock panel with realistic serial number
    mock_panel = Mock()
    mock_panel.status.serial_number = "sp3-242424-001"
    mock_panel.circuits = {"1": circuit}  # Dictionary format expected by SpanSensorManager

    # Create sensor manager
    manager = SpanSensorManager(hass, mock_panel, "test_entry")

    # Configure for device prefix
    config_options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry = Mock()
    entry.options = config_options
    entry.entry_id = "test_entry"
    hass.data = {DOMAIN: {"test_entry": {"entry": entry}}}

    # Create mock coordinator
    mock_coordinator = Mock()
    mock_coordinator.config_entry = entry

    # Generate YAML for sensors
    yaml_config = manager.generate_unified_config_sync(mock_coordinator, mock_panel)

    # Check that circuit sensor has correct structure
    circuit_sensor_key = "span_sp3-242424-001_circuit_1_instantpowerw"
    assert circuit_sensor_key in yaml_config

    circuit_sensor = yaml_config[circuit_sensor_key]
    assert circuit_sensor["name"] == "Test Circuit Power"
    assert circuit_sensor["entity_id"] == "sensor.span_panel_test_circuit_power"
    assert circuit_sensor["formula"] == "source_value"
    assert "variables" in circuit_sensor
    assert (
        circuit_sensor["variables"]["source_value"]
        == "span_panel_synthetic_backing.circuit_1_power"
    )
    assert circuit_sensor["unit_of_measurement"] == "W"
    assert circuit_sensor["device_class"] == "power"
    assert circuit_sensor["state_class"] == "measurement"
    assert circuit_sensor["device_identifier"] == "span_panel_sp3-242424-001"


@pytest.mark.asyncio
async def test_panel_sensor_yaml_generation_legacy(hass):
    """Test panel sensor YAML generation with legacy behavior."""
    # Create a mock circuit object for testing
    circuit = Mock()
    circuit.id = "1"
    circuit.name = "Test Circuit"
    circuit.relayState = "CLOSED"
    circuit.instantPowerW = 100.0
    circuit.tabs = ["1", "2"]

    # Create mock panel with realistic serial number
    mock_panel = Mock()
    mock_panel.status.serial_number = "sp3-242424-001"
    mock_panel.circuits = {"1": circuit}  # Dictionary format expected by SpanSensorManager

    # Create sensor manager
    manager = SpanSensorManager(hass, mock_panel, "test_entry")

    # Configure for legacy behavior (empty options)
    config_options = {}
    entry = Mock()
    entry.options = config_options
    entry.entry_id = "test_entry"
    hass.data = {DOMAIN: {"test_entry": {"entry": entry}}}

    # Create mock coordinator
    mock_coordinator = Mock()
    mock_coordinator.config_entry = entry

    # Generate YAML for sensors
    yaml_config = manager.generate_unified_config_sync(mock_coordinator, mock_panel)

    # Check that panel sensor has correct structure
    panel_sensor_key = "span_sp3-242424-001_instantgridpowerw"
    assert panel_sensor_key in yaml_config

    panel_sensor = yaml_config[panel_sensor_key]
    assert panel_sensor["name"] == "Current Power"
    assert panel_sensor["entity_id"] == "sensor.current_power"  # No device prefix in legacy
    assert panel_sensor["formula"] == "source_value"
    assert "variables" in panel_sensor
    assert (
        panel_sensor["variables"]["source_value"]
        == "span_panel_synthetic_backing.circuit_0_instant_grid_power"
    )
    assert panel_sensor["device_identifier"] == "span_panel_sp3-242424-001"


@pytest.mark.asyncio
async def test_circuit_sensor_yaml_generation_legacy(hass):
    """Test circuit sensor YAML generation with legacy behavior."""
    # Create a mock circuit object for testing
    circuit = Mock()
    circuit.id = "1"
    circuit.name = "Test Circuit"
    circuit.relayState = "CLOSED"
    circuit.instantPowerW = 100.0
    circuit.tabs = ["1", "2"]

    # Create mock panel with realistic serial number
    mock_panel = Mock()
    mock_panel.status.serial_number = "sp3-242424-001"
    mock_panel.circuits = {"1": circuit}  # Dictionary format expected by SpanSensorManager

    # Create sensor manager
    manager = SpanSensorManager(hass, mock_panel, "test_entry")

    # Configure for legacy behavior
    config_options = {}
    entry = Mock()
    entry.options = config_options
    entry.entry_id = "test_entry"
    hass.data = {DOMAIN: {"test_entry": {"entry": entry}}}

    # Create mock coordinator
    mock_coordinator = Mock()
    mock_coordinator.config_entry = entry

    # Generate YAML for sensors
    yaml_config = manager.generate_unified_config_sync(mock_coordinator, mock_panel)

    # Check that circuit sensor has correct structure
    circuit_sensor_key = "span_sp3-242424-001_circuit_1_instantpowerw"
    assert circuit_sensor_key in yaml_config

    circuit_sensor = yaml_config[circuit_sensor_key]
    assert circuit_sensor["name"] == "Test Circuit Power"
    assert circuit_sensor["entity_id"] == "sensor.test_circuit_power"  # No device prefix in legacy
    assert circuit_sensor["formula"] == "source_value"
    assert "variables" in circuit_sensor
    assert (
        circuit_sensor["variables"]["source_value"]
        == "span_panel_synthetic_backing.circuit_1_power"
    )
    assert circuit_sensor["device_identifier"] == "span_panel_sp3-242424-001"


@pytest.mark.asyncio
async def test_circuit_numbers_yaml_generation(hass):
    """Test YAML generation with circuit numbers enabled."""
    # Create a mock circuit object for testing
    circuit = Mock()
    circuit.id = "1"
    circuit.name = "Test Circuit"
    circuit.relayState = "CLOSED"
    circuit.instantPowerW = 100.0
    circuit.tabs = ["1", "2"]

    # Create mock panel with realistic serial number
    mock_panel = Mock()
    mock_panel.status.serial_number = "sp3-242424-001"
    mock_panel.circuits = {"1": circuit}  # Dictionary format expected by SpanSensorManager

    # Create sensor manager
    manager = SpanSensorManager(hass, mock_panel, "test_entry")

    # Configure for circuit numbers
    config_options = {
        "use_device_prefix": True,
        "use_circuit_numbers": True,
    }
    entry = Mock()
    entry.options = config_options
    entry.entry_id = "test_entry"
    hass.data = {DOMAIN: {"test_entry": {"entry": entry}}}

    # Create mock coordinator
    mock_coordinator = Mock()
    mock_coordinator.config_entry = entry

    # Generate YAML for sensors
    yaml_config = manager.generate_unified_config_sync(mock_coordinator, mock_panel)

    # Check that circuit sensor has correct structure with circuit number
    circuit_sensor_key = "span_sp3-242424-001_circuit_1_instantpowerw"
    assert circuit_sensor_key in yaml_config

    circuit_sensor = yaml_config[circuit_sensor_key]
    assert (
        circuit_sensor["name"] == "Test Circuit Power"
    )  # Should use circuit name, not "Circuit 1 Power"
    assert circuit_sensor["entity_id"] == "sensor.span_panel_circuit_1_power"
    assert circuit_sensor["device_identifier"] == "span_panel_sp3-242424-001"
