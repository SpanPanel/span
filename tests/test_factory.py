"""Tests for the factory classes and their use of constants."""

from unittest.mock import patch

from custom_components.span_panel.binary_sensor import BINARY_SENSORS
from custom_components.span_panel.const import (
    CURRENT_RUN_CONFIG,
    DSM_GRID_STATE,
    DSM_STATE,
    DSM_GRID_UP,
    DSM_ON_GRID,
    MAIN_RELAY_STATE,
    PANEL_ON_GRID,
    SYSTEM_CELLULAR_LINK,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
    SYSTEM_ETHERNET_LINK,
    SYSTEM_WIFI_LINK,
)
from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)
from tests.factories import (
    SpanPanelApiResponseFactory,
    SpanPanelDataFactory,
    SpanPanelStatusFactory,
)


def test_panel_factory_uses_correct_constants():
    """Test that panel factory uses the correct constant keys."""
    panel_data = SpanPanelDataFactory.create_on_grid_panel_data()

    # Verify that the factory uses the constant keys
    assert CURRENT_RUN_CONFIG in panel_data
    assert DSM_GRID_STATE in panel_data
    assert DSM_STATE in panel_data
    assert MAIN_RELAY_STATE in panel_data

    # Verify expected values
    assert panel_data[CURRENT_RUN_CONFIG] == PANEL_ON_GRID
    assert panel_data[DSM_GRID_STATE] == DSM_GRID_UP
    assert panel_data[DSM_STATE] == DSM_ON_GRID
    assert panel_data[MAIN_RELAY_STATE] == "CLOSED"


def test_status_factory_uses_correct_constants():
    """Test that status factory uses the correct constant values."""
    status_data = SpanPanelStatusFactory.create_status()

    # Verify that the factory uses the correct constant values
    assert status_data["system"]["doorState"] == SYSTEM_DOOR_STATE_CLOSED

    # Verify network link constants are used as keys
    assert SYSTEM_ETHERNET_LINK in status_data["network"]
    assert SYSTEM_WIFI_LINK in status_data["network"]
    assert SYSTEM_CELLULAR_LINK in status_data["network"]

    # Verify expected structure for API compatibility
    assert "software" in status_data
    assert "firmwareVersion" in status_data["software"]
    assert "system" in status_data
    assert "network" in status_data

    # Verify default network values
    assert status_data["network"][SYSTEM_ETHERNET_LINK] is True
    assert status_data["network"][SYSTEM_WIFI_LINK] is True
    assert status_data["network"][SYSTEM_CELLULAR_LINK] is False


def test_status_factory_network_configuration():
    """Test that status factory can create different network configurations."""
    # Test with all connections disabled
    status_offline = SpanPanelStatusFactory.create_status(
        ethernet_link=False,
        wifi_link=False,
        cellular_link=False,
    )

    assert status_offline["network"][SYSTEM_ETHERNET_LINK] is False
    assert status_offline["network"][SYSTEM_WIFI_LINK] is False
    assert status_offline["network"][SYSTEM_CELLULAR_LINK] is False

    # Test with only cellular enabled
    status_cellular = SpanPanelStatusFactory.create_status(
        ethernet_link=False,
        wifi_link=False,
        cellular_link=True,
    )

    assert status_cellular["network"][SYSTEM_ETHERNET_LINK] is False
    assert status_cellular["network"][SYSTEM_WIFI_LINK] is False
    assert status_cellular["network"][SYSTEM_CELLULAR_LINK] is True


def test_status_factory_integration_with_hardware_status():
    """Test that status factory data works correctly with SpanPanelHardwareStatus."""
    # Test with mixed network connectivity
    status_data = SpanPanelStatusFactory.create_status(
        ethernet_link=True,
        wifi_link=False,
        cellular_link=True,
        software_version="2.5.1",
        serial_number="TEST123456789",
    )

    # Create actual SpanPanelHardwareStatus object
    hardware_status = SpanPanelHardwareStatus.from_dict(status_data)

    # Verify that network constants are properly mapped to boolean properties
    assert hardware_status.is_ethernet_connected is True
    assert hardware_status.is_wifi_connected is False
    assert hardware_status.is_cellular_connected is True

    # Verify other properties work as expected
    assert hardware_status.firmware_version == "2.5.1"
    assert hardware_status.serial_number == "TEST123456789"
    assert hardware_status.door_state == SYSTEM_DOOR_STATE_CLOSED


def test_door_state_tamper_sensor_logic():
    """Test that door state works correctly as a tamper sensor."""
    # Test door CLOSED (tamper sensor should be OFF/clear)
    status_closed = SpanPanelStatusFactory.create_status(door_state=SYSTEM_DOOR_STATE_CLOSED)
    hardware_status_closed = SpanPanelHardwareStatus.from_dict(status_closed)

    assert hardware_status_closed.door_state == SYSTEM_DOOR_STATE_CLOSED
    assert hardware_status_closed.is_door_closed is True
    # Tamper sensor logic: not is_door_closed -> not True -> False (clear/OFF)
    tamper_sensor_value_closed = not hardware_status_closed.is_door_closed
    assert tamper_sensor_value_closed is False  # Tamper clear when door closed

    # Test door OPEN (tamper sensor should be ON/detected)
    status_open = SpanPanelStatusFactory.create_status(door_state=SYSTEM_DOOR_STATE_OPEN)
    hardware_status_open = SpanPanelHardwareStatus.from_dict(status_open)

    assert hardware_status_open.door_state == SYSTEM_DOOR_STATE_OPEN
    assert hardware_status_open.is_door_closed is False
    # Tamper sensor logic: not is_door_closed -> not False -> True (tampered/ON)
    tamper_sensor_value_open = not hardware_status_open.is_door_closed
    assert tamper_sensor_value_open is True  # Tamper detected when door open

    # Test unknown door state (tamper sensor should be unavailable)
    status_unknown = SpanPanelStatusFactory.create_status(door_state="UNKNOWN")
    hardware_status_unknown = SpanPanelHardwareStatus.from_dict(status_unknown)

    assert hardware_status_unknown.door_state == "UNKNOWN"
    assert hardware_status_unknown.is_door_closed is None
    # When is_door_closed is None, the binary sensor should be unavailable
    # (This matches the binary sensor logic that checks for None)


def test_door_state_binary_sensor_availability():
    """Test that door state binary sensor handles availability correctly."""

    # Find the door state sensor description
    door_sensor = None
    for sensor in BINARY_SENSORS:
        if sensor.key == "doorState":
            door_sensor = sensor
            break

    assert door_sensor is not None, "Door state sensor should be defined"
    assert door_sensor.device_class is not None
    assert door_sensor.device_class.value == "tamper"

    # Test the actual value_fn logic used by the binary sensor

    # Test with door closed - should return False (tamper clear)
    status_closed = SpanPanelStatusFactory.create_status(door_state=SYSTEM_DOOR_STATE_CLOSED)
    hardware_closed = SpanPanelHardwareStatus.from_dict(status_closed)
    sensor_value_closed = door_sensor.value_fn(hardware_closed)
    assert sensor_value_closed is False  # Tamper clear

    # Test with door open - should return True (tamper detected)
    status_open = SpanPanelStatusFactory.create_status(door_state=SYSTEM_DOOR_STATE_OPEN)
    hardware_open = SpanPanelHardwareStatus.from_dict(status_open)
    sensor_value_open = door_sensor.value_fn(hardware_open)
    assert sensor_value_open is True  # Tamper detected

    # Test with unknown state - should return None (unavailable)
    status_unknown = SpanPanelStatusFactory.create_status(door_state="UNKNOWN")
    hardware_unknown = SpanPanelHardwareStatus.from_dict(status_unknown)
    sensor_value_unknown = door_sensor.value_fn(hardware_unknown)
    assert sensor_value_unknown is None  # Unavailable


def test_complete_response_factory_structure():
    """Test that the complete response factory creates the expected structure."""
    response = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Verify top-level structure
    assert "circuits" in response
    assert "panel" in response
    assert "status" in response
    assert "battery" in response

    # Verify panel data uses constants
    panel_data = response["panel"]
    assert CURRENT_RUN_CONFIG in panel_data
    assert DSM_GRID_STATE in panel_data
    assert DSM_STATE in panel_data
    assert MAIN_RELAY_STATE in panel_data

    # Verify status data uses constants and correct structure
    status_data = response["status"]
    assert status_data["system"]["doorState"] == SYSTEM_DOOR_STATE_CLOSED
    assert "firmwareVersion" in status_data["software"]  # API field, not constant

    # Verify network data uses constants as keys
    assert SYSTEM_ETHERNET_LINK in status_data["network"]
    assert SYSTEM_WIFI_LINK in status_data["network"]
    assert SYSTEM_CELLULAR_LINK in status_data["network"]
