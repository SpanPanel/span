"""Tests for EVSE (EV Charger) entity support.

Tests cover:
- EVSE sensor creation when snapshot has EVSE data
- No EVSE sensors created when snapshot.evse is empty
- Charger status enum values propagate correctly
- Advertised current sensor reads from snapshot
- Binary sensor "Charging" ON when status == CHARGING
- Binary sensor "EV Connected" ON for connected statuses
- EVSE device_info has correct via_device linking
- Capability detection includes "evse" when EVSE present
- Multiple EVSE devices create separate entity sets
"""

from __future__ import annotations

from span_panel_api import SpanEvseSnapshot

from custom_components.span_panel.binary_sensor import (
    EVSE_BINARY_SENSORS,
    _EV_CONNECTED_STATUSES,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.sensor_definitions import (
    EVSE_LOCK_STATE_OPTIONS,
    EVSE_SENSORS,
    EVSE_STATUS_OPTIONS,
)
from custom_components.span_panel.sensors.factory import (
    detect_capabilities,
    has_evse,
)
from custom_components.span_panel.util import evse_device_info
from tests.factories import (
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
)


class TestEvseDetection:
    """Test EVSE capability detection."""

    def test_has_evse_returns_true_when_evse_present(self):
        evse = SpanEvseSnapshotFactory.create()
        snapshot = SpanPanelSnapshotFactory.create(evse={"evse-0": evse})
        assert has_evse(snapshot) is True

    def test_has_evse_returns_false_when_empty(self):
        snapshot = SpanPanelSnapshotFactory.create()
        assert has_evse(snapshot) is False

    def test_capability_detection_includes_evse(self):
        evse = SpanEvseSnapshotFactory.create()
        snapshot = SpanPanelSnapshotFactory.create(evse={"evse-0": evse})
        caps = detect_capabilities(snapshot)
        assert "evse" in caps

    def test_capability_detection_includes_evse_via_circuit_device_type(self):
        circuit = SpanCircuitSnapshotFactory.create(circuit_id="ev1", name="EV Charger")
        # Use dataclasses.replace to set device_type since it's frozen
        from dataclasses import replace
        circuit_with_evse = replace(circuit, device_type="evse")
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={"ev1": circuit_with_evse},
        )
        caps = SpanPanelCoordinator._detect_capabilities(snapshot)
        assert "evse" in caps

    def test_capability_detection_excludes_evse_when_absent(self):
        snapshot = SpanPanelSnapshotFactory.create()
        caps = detect_capabilities(snapshot)
        assert "evse" not in caps


class TestEvseSensorDefinitions:
    """Test EVSE sensor definition structure."""

    def test_evse_sensors_count(self):
        assert len(EVSE_SENSORS) == 3

    def test_evse_status_sensor_is_enum(self):
        status_desc = next(d for d in EVSE_SENSORS if d.key == "evse_status")
        assert status_desc.device_class is not None
        assert status_desc.device_class.value == "enum"
        assert status_desc.options == EVSE_STATUS_OPTIONS

    def test_evse_lock_state_sensor_is_enum(self):
        lock_desc = next(d for d in EVSE_SENSORS if d.key == "evse_lock_state")
        assert lock_desc.device_class is not None
        assert lock_desc.device_class.value == "enum"
        assert lock_desc.options == EVSE_LOCK_STATE_OPTIONS

    def test_evse_advertised_current_is_measurement(self):
        current_desc = next(d for d in EVSE_SENSORS if d.key == "evse_advertised_current")
        assert current_desc.native_unit_of_measurement == "A"
        assert current_desc.state_class is not None

    def test_evse_status_value_fn(self):
        evse = SpanEvseSnapshotFactory.create(status="CHARGING")
        status_desc = next(d for d in EVSE_SENSORS if d.key == "evse_status")
        assert status_desc.value_fn(evse) == "CHARGING"

    def test_evse_lock_state_value_fn(self):
        evse = SpanEvseSnapshotFactory.create(lock_state="LOCKED")
        lock_desc = next(d for d in EVSE_SENSORS if d.key == "evse_lock_state")
        assert lock_desc.value_fn(evse) == "LOCKED"

    def test_evse_advertised_current_value_fn(self):
        evse = SpanEvseSnapshotFactory.create(advertised_current_a=32.0)
        current_desc = next(d for d in EVSE_SENSORS if d.key == "evse_advertised_current")
        assert current_desc.value_fn(evse) == 32.0

    def test_evse_advertised_current_none(self):
        evse = SpanEvseSnapshotFactory.create(advertised_current_a=None)
        current_desc = next(d for d in EVSE_SENSORS if d.key == "evse_advertised_current")
        assert current_desc.value_fn(evse) is None


class TestEvseBinarySensorDefinitions:
    """Test EVSE binary sensor definition structure."""

    def test_evse_binary_sensors_count(self):
        assert len(EVSE_BINARY_SENSORS) == 2

    def test_charging_binary_sensor_on_when_charging(self):
        evse = SpanEvseSnapshotFactory.create(status="CHARGING")
        charging_desc = next(d for d in EVSE_BINARY_SENSORS if d.key == "evse_charging")
        assert charging_desc.value_fn(evse) is True

    def test_charging_binary_sensor_off_when_available(self):
        evse = SpanEvseSnapshotFactory.create(status="AVAILABLE")
        charging_desc = next(d for d in EVSE_BINARY_SENSORS if d.key == "evse_charging")
        assert charging_desc.value_fn(evse) is False

    def test_charging_binary_sensor_off_when_preparing(self):
        evse = SpanEvseSnapshotFactory.create(status="PREPARING")
        charging_desc = next(d for d in EVSE_BINARY_SENSORS if d.key == "evse_charging")
        assert charging_desc.value_fn(evse) is False

    def test_ev_connected_on_for_connected_statuses(self):
        connected_desc = next(d for d in EVSE_BINARY_SENSORS if d.key == "evse_ev_connected")
        for status in _EV_CONNECTED_STATUSES:
            evse = SpanEvseSnapshotFactory.create(status=status)
            assert connected_desc.value_fn(evse) is True, f"Expected True for status={status}"

    def test_ev_connected_off_for_disconnected_statuses(self):
        connected_desc = next(d for d in EVSE_BINARY_SENSORS if d.key == "evse_ev_connected")
        for status in ("AVAILABLE", "UNKNOWN", "FAULTED", "UNAVAILABLE", "RESERVED"):
            evse = SpanEvseSnapshotFactory.create(status=status)
            assert connected_desc.value_fn(evse) is False, f"Expected False for status={status}"


class TestEvseDeviceInfo:
    """Test EVSE DeviceInfo construction."""

    def test_evse_device_info_full_metadata(self):
        evse = SpanEvseSnapshotFactory.create(
            node_id="evse-0",
            vendor_name="SPAN",
            product_name="SPAN Drive",
            serial_number="SN123",
            software_version="2.0.0",
        )
        info = evse_device_info("panel-serial", evse)
        identifiers = info.get("identifiers")
        assert identifiers is not None
        assert ("span_panel", "panel-serial_evse_evse-0") in identifiers
        assert info.get("name") == "SPAN Drive"
        assert info.get("manufacturer") == "SPAN"
        assert info.get("model") == "SPAN Drive"
        assert info.get("serial_number") == "SN123"
        assert info.get("sw_version") == "2.0.0"
        via = info.get("via_device")
        assert via == ("span_panel", "panel-serial")

    def test_evse_device_info_fallback_names(self):
        evse = SpanEvseSnapshotFactory.create(
            vendor_name=None,
            product_name=None,
        )
        info = evse_device_info("panel-serial", evse)
        assert info.get("name") == "EV Charger"
        assert info.get("manufacturer") == "SPAN"
        assert info.get("model") == "SPAN Drive"

    def test_evse_device_info_no_serial(self):
        evse = SpanEvseSnapshotFactory.create(serial_number=None)
        info = evse_device_info("panel-serial", evse)
        assert info.get("serial_number") is None


class TestEvseStatusOptions:
    """Test EVSE enum options match expected values."""

    def test_status_options_include_all_ocpp_states(self):
        expected = {
            "UNKNOWN", "AVAILABLE", "PREPARING", "CHARGING",
            "SUSPENDED_EV", "SUSPENDED_EVSE", "FINISHING",
            "RESERVED", "FAULTED", "UNAVAILABLE",
        }
        assert set(EVSE_STATUS_OPTIONS) == expected

    def test_lock_state_options(self):
        assert set(EVSE_LOCK_STATE_OPTIONS) == {"UNKNOWN", "LOCKED", "UNLOCKED"}


class TestEvseMultipleDevices:
    """Test multiple EVSE device handling."""

    def test_multiple_evse_in_snapshot(self):
        evse_a = SpanEvseSnapshotFactory.create(
            node_id="evse-0", status="CHARGING", feed_circuit_id="c1"
        )
        evse_b = SpanEvseSnapshotFactory.create(
            node_id="evse-1", status="AVAILABLE", feed_circuit_id="c2"
        )
        snapshot = SpanPanelSnapshotFactory.create(
            evse={"evse-0": evse_a, "evse-1": evse_b}
        )
        assert len(snapshot.evse) == 2
        assert snapshot.evse["evse-0"].status == "CHARGING"
        assert snapshot.evse["evse-1"].status == "AVAILABLE"

    def test_multiple_evse_device_infos_are_distinct(self):
        evse_a = SpanEvseSnapshotFactory.create(node_id="evse-0")
        evse_b = SpanEvseSnapshotFactory.create(node_id="evse-1")
        info_a = evse_device_info("panel", evse_a)
        info_b = evse_device_info("panel", evse_b)
        assert info_a.get("identifiers") != info_b.get("identifiers")


class TestEvseSnapshotFactory:
    """Test the EVSE snapshot factory itself."""

    def test_default_factory_values(self):
        evse = SpanEvseSnapshotFactory.create()
        assert evse.node_id == "evse-0"
        assert evse.status == "CHARGING"
        assert evse.lock_state == "LOCKED"
        assert evse.advertised_current_a == 32.0
        assert evse.vendor_name == "SPAN"
        assert evse.product_name == "SPAN Drive"

    def test_available_factory(self):
        evse = SpanEvseSnapshotFactory.create_available()
        assert evse.status == "AVAILABLE"
        assert evse.lock_state == "UNLOCKED"
