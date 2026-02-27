"""Tests for the factory classes and snapshot types."""

from custom_components.span_panel.binary_sensor import BINARY_SENSORS
from custom_components.span_panel.const import (
    DSM_ON_GRID,
    PANEL_ON_GRID,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
)
from tests.factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanPanelSnapshotFactory,
)


def test_panel_factory_creates_correct_defaults():
    """Test that panel factory creates snapshot with correct default values."""
    snapshot = SpanPanelSnapshotFactory.create()

    assert snapshot.current_run_config == PANEL_ON_GRID
    assert snapshot.dsm_grid_state == DSM_ON_GRID
    assert snapshot.main_relay_state == "CLOSED"
    assert snapshot.serial_number == "sp3-242424-001"
    assert snapshot.firmware_version == "1.2.3"


def test_panel_factory_on_grid():
    """Test on-grid panel snapshot has correct state."""
    snapshot = SpanPanelSnapshotFactory.create_on_grid()

    assert snapshot.current_run_config == PANEL_ON_GRID
    assert snapshot.dsm_grid_state == DSM_ON_GRID
    assert snapshot.instant_grid_power_w == 1850.5


def test_status_factory_network_defaults():
    """Test that snapshot has correct network connectivity defaults."""
    snapshot = SpanPanelSnapshotFactory.create()

    assert snapshot.door_state == SYSTEM_DOOR_STATE_CLOSED
    assert snapshot.eth0_link is True
    assert snapshot.wlan_link is True
    assert snapshot.wwan_link is False


def test_status_factory_network_configuration():
    """Test that snapshot can be created with different network configurations."""
    snapshot_offline = SpanPanelSnapshotFactory.create(
        eth0_link=False,
        wlan_link=False,
        wwan_link=False,
    )

    assert snapshot_offline.eth0_link is False
    assert snapshot_offline.wlan_link is False
    assert snapshot_offline.wwan_link is False

    snapshot_cellular = SpanPanelSnapshotFactory.create(
        eth0_link=False,
        wlan_link=False,
        wwan_link=True,
    )

    assert snapshot_cellular.eth0_link is False
    assert snapshot_cellular.wlan_link is False
    assert snapshot_cellular.wwan_link is True


def test_door_state_tamper_sensor_logic():
    """Test that door state works correctly as a tamper sensor."""
    # Door CLOSED -> tamper clear
    snapshot_closed = SpanPanelSnapshotFactory.create(door_state=SYSTEM_DOOR_STATE_CLOSED)
    assert snapshot_closed.door_state == SYSTEM_DOOR_STATE_CLOSED
    tamper_closed = snapshot_closed.door_state != SYSTEM_DOOR_STATE_CLOSED
    assert tamper_closed is False

    # Door OPEN -> tamper detected
    snapshot_open = SpanPanelSnapshotFactory.create(door_state=SYSTEM_DOOR_STATE_OPEN)
    assert snapshot_open.door_state == SYSTEM_DOOR_STATE_OPEN
    tamper_open = snapshot_open.door_state != SYSTEM_DOOR_STATE_CLOSED
    assert tamper_open is True

    # UNKNOWN door state
    snapshot_unknown = SpanPanelSnapshotFactory.create(door_state="UNKNOWN")
    assert snapshot_unknown.door_state == "UNKNOWN"


def test_door_state_binary_sensor_availability():
    """Test that door state binary sensor handles availability correctly."""
    door_sensor = None
    for sensor in BINARY_SENSORS:
        if sensor.key == "doorState":
            door_sensor = sensor
            break

    assert door_sensor is not None, "Door state sensor should be defined"
    assert door_sensor.device_class is not None
    assert door_sensor.device_class.value == "tamper"

    # Door closed -> tamper clear (False)
    snapshot_closed = SpanPanelSnapshotFactory.create(door_state=SYSTEM_DOOR_STATE_CLOSED)
    assert door_sensor.value_fn(snapshot_closed) is False

    # Door open -> tamper detected (True)
    snapshot_open = SpanPanelSnapshotFactory.create(door_state=SYSTEM_DOOR_STATE_OPEN)
    assert door_sensor.value_fn(snapshot_open) is True

    # Unknown state -> unavailable (None)
    snapshot_unknown = SpanPanelSnapshotFactory.create(door_state="UNKNOWN")
    assert door_sensor.value_fn(snapshot_unknown) is None


def test_complete_response_factory_structure():
    """Test that the complete factory creates expected snapshot structure."""
    snapshot = SpanPanelSnapshotFactory.create_complete()

    assert snapshot.serial_number == "sp3-242424-001"
    assert len(snapshot.circuits) == 3
    assert snapshot.battery.soe_percentage == 85.0

    # Verify panel data
    assert snapshot.current_run_config == PANEL_ON_GRID
    assert snapshot.dsm_grid_state == DSM_ON_GRID
    assert snapshot.main_relay_state == "CLOSED"

    # Verify status data
    assert snapshot.door_state == SYSTEM_DOOR_STATE_CLOSED
    assert snapshot.firmware_version == "1.2.3"
    assert snapshot.eth0_link is True
    assert snapshot.wlan_link is True
    assert snapshot.wwan_link is False


def test_circuit_factory_defaults():
    """Test circuit factory creates correct defaults."""
    circuit = SpanCircuitSnapshotFactory.create()

    assert circuit.circuit_id == "1"
    assert circuit.name == "Test Circuit"
    assert circuit.relay_state == "CLOSED"
    assert circuit.instant_power_w == 150.5
    assert circuit.consumed_energy_wh == 1500.0
    assert circuit.produced_energy_wh == 0.0
    assert circuit.is_user_controllable is True
    assert circuit.tabs == [1]


def test_battery_factory_defaults():
    """Test battery factory creates correct defaults."""
    battery = SpanBatterySnapshotFactory.create()

    assert battery.soe_percentage == 85.0
    assert battery.soe_kwh is None
