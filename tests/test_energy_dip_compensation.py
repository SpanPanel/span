"""Tests for energy dip compensation feature."""

# ruff: noqa: D102

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorStateClass

from custom_components.span_panel.const import ENABLE_ENERGY_DIP_COMPENSATION
from custom_components.span_panel.energy_dip import PendingDip
from custom_components.span_panel.options import ENERGY_REPORTING_GRACE_PERIOD
from custom_components.span_panel.sensor_base import (
    SpanEnergyExtraStoredData,
    SpanEnergySensorBase,
)


class DummyDipSensor(SpanEnergySensorBase):
    """Minimal concrete energy sensor for dip compensation tests."""

    def __init__(  # pylint: disable=super-init-not-called
        self,
        dip_enabled: bool = True,
        state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING,
    ) -> None:
        """Bypass parent __init__ to avoid full HA dependencies."""
        self.coordinator = SimpleNamespace(
            panel_offline=False,
            config_entry=SimpleNamespace(
                options={
                    ENERGY_REPORTING_GRACE_PERIOD: 15,
                    ENABLE_ENERGY_DIP_COMPENSATION: dip_enabled,
                },
            ),
            data=SimpleNamespace(),
            report_energy_dip=MagicMock(),
        )
        self._mock_panel_value: float | None = None
        self.entity_description = SimpleNamespace(
            device_class="energy",
            state_class=state_class,
            key="dummy",
            value_fn=lambda _: self._mock_panel_value,
            native_unit_of_measurement="Wh",
        )
        self._attr_native_value: float | None = None
        self._last_valid_state: float | None = None
        self._last_valid_changed = None
        self._grace_period_minutes = 15
        self._previous_circuit_name = None
        self._attr_unique_id = "dummy_sensor"
        self._attr_name = "Dummy"
        self._restored_from_storage: bool = False

        # Energy dip compensation state
        self._energy_offset: float = 0.0
        self._last_panel_reading: float | None = None
        self._last_dip_delta: float | None = None
        self._pending_dip: PendingDip | None = None
        self._is_total_increasing: bool = state_class == SensorStateClass.TOTAL_INCREASING
        self._dip_compensation_enabled: bool = dip_enabled

    def _generate_unique_id(self, snapshot, description):
        return "dummy_sensor"

    def get_data_source(self, snapshot):
        return "dummy_data"


# =============================================================================
# SpanEnergyExtraStoredData round-trip with new fields
# =============================================================================


class TestExtraStoredDataDipFields:
    """Tests for energy dip fields in SpanEnergyExtraStoredData."""

    def test_as_dict_includes_dip_fields(self):
        """Verify as_dict includes the three new dip fields."""
        data = SpanEnergyExtraStoredData(
            native_value=100.0,
            native_unit_of_measurement="Wh",
            last_valid_state=100.0,
            last_valid_changed="2025-12-01T00:00:00",
            energy_offset=5.0,
            last_panel_reading=95.0,
            last_dip_delta=5.0,
        )
        result = data.as_dict()
        assert result["energy_offset"] == 5.0
        assert result["last_panel_reading"] == 95.0
        assert result["last_dip_delta"] == 5.0

    def test_from_dict_restores_dip_fields(self):
        """Verify from_dict restores the three new dip fields."""
        stored = {
            "native_value": 200.0,
            "native_unit_of_measurement": "Wh",
            "last_valid_state": 200.0,
            "last_valid_changed": "2025-12-01T12:00:00",
            "energy_offset": 10.0,
            "last_panel_reading": 190.0,
            "last_dip_delta": 10.0,
        }
        result = SpanEnergyExtraStoredData.from_dict(stored)
        assert result is not None
        assert result.energy_offset == 10.0
        assert result.last_panel_reading == 190.0
        assert result.last_dip_delta == 10.0

    def test_backward_compat_missing_dip_fields(self):
        """Old stored data without dip fields deserializes with None."""
        stored = {
            "native_value": 300.0,
            "native_unit_of_measurement": "Wh",
            "last_valid_state": 300.0,
            "last_valid_changed": "2025-12-01T06:00:00",
        }
        result = SpanEnergyExtraStoredData.from_dict(stored)
        assert result is not None
        assert result.energy_offset is None
        assert result.last_panel_reading is None
        assert result.last_dip_delta is None

    def test_roundtrip_with_dip_fields(self):
        """Data survives a full round-trip through serialization."""
        original = SpanEnergyExtraStoredData(
            native_value=500.0,
            native_unit_of_measurement="Wh",
            last_valid_state=500.0,
            last_valid_changed="2025-12-01T09:00:00",
            energy_offset=25.0,
            last_panel_reading=475.0,
            last_dip_delta=8.0,
        )
        restored = SpanEnergyExtraStoredData.from_dict(original.as_dict())
        assert restored is not None
        assert restored.energy_offset == original.energy_offset
        assert restored.last_panel_reading == original.last_panel_reading
        assert restored.last_dip_delta == original.last_dip_delta

    def test_roundtrip_with_none_dip_fields(self):
        """None dip fields survive round-trip."""
        original = SpanEnergyExtraStoredData(
            native_value=100.0,
            native_unit_of_measurement="Wh",
            last_valid_state=100.0,
            last_valid_changed="2025-12-01T09:00:00",
            energy_offset=None,
            last_panel_reading=None,
            last_dip_delta=None,
        )
        restored = SpanEnergyExtraStoredData.from_dict(original.as_dict())
        assert restored is not None
        assert restored.energy_offset is None
        assert restored.last_panel_reading is None
        assert restored.last_dip_delta is None


# =============================================================================
# Dip compensation logic
# =============================================================================


class TestDipCompensation:
    """Tests for the core dip compensation logic in _process_raw_value."""

    def test_first_reading_sets_baseline(self):
        """First reading sets _last_panel_reading without applying offset."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        assert sensor._attr_native_value == 1000.0
        assert sensor._last_panel_reading == 1000.0
        assert sensor._energy_offset == 0.0

    def test_normal_increase_passthrough(self):
        """Normal increasing values pass through without offset."""
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 1100.0
        sensor._update_native_value()

        assert sensor._attr_native_value == 1100.0
        assert sensor._last_panel_reading == 1100.0
        assert sensor._energy_offset == 0.0

    def test_dip_applies_offset(self):
        """A dip in raw value produces compensated output."""
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        # Dip of 50 Wh
        sensor._mock_panel_value = 950.0
        sensor._update_native_value()

        # HA should see 950 + 50 = 1000
        assert sensor._attr_native_value == 1000.0
        assert sensor._energy_offset == 50.0
        assert sensor._last_panel_reading == 950.0
        assert sensor._last_dip_delta == 50.0

    def test_below_threshold_ignored(self):
        """Dips below 1.0 Wh threshold are not compensated."""
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        # Dip of 0.5 Wh — below threshold
        sensor._mock_panel_value = 999.5
        sensor._update_native_value()

        assert sensor._attr_native_value == 999.5
        assert sensor._energy_offset == 0.0
        assert sensor._last_dip_delta is None

    def test_multiple_dips_accumulate(self):
        """Multiple confirmed dips accumulate the offset.

        Each has to be corroborated by the counter climbing from its new, lower
        base — which is what a firmware reset actually looks like — rather than
        by the counter returning to where it was, which is what a transient bad
        reading looks like and is now retracted instead.
        """
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        # First dip: 20 Wh
        sensor._mock_panel_value = 980.0
        sensor._update_native_value()
        assert sensor._energy_offset == 20.0
        assert sensor._attr_native_value == 1000.0

        # Counting up from the lower base confirms it
        sensor._mock_panel_value = 985.0
        sensor._update_native_value()
        assert sensor._energy_offset == 20.0
        assert sensor._attr_native_value == 1005.0  # 985 + 20

        # Second dip: 25 Wh
        sensor._mock_panel_value = 960.0
        sensor._update_native_value()
        assert sensor._energy_offset == 45.0  # 20 + 25
        assert sensor._attr_native_value == 1005.0  # 960 + 45

        sensor._mock_panel_value = 965.0
        sensor._update_native_value()
        assert sensor._energy_offset == 45.0
        assert sensor._attr_native_value == 1010.0  # 965 + 45

    def test_a_counter_that_comes_back_leaves_no_offset_behind(self):
        """The behaviour this rule changed, stated directly.

        A reading that dipped and then rose past where it started was compensated
        permanently: the offset stayed, inflating the sensor by the size of a
        drop that never happened. `SpanPanel/span#259` is the same arithmetic on
        a lifetime counter instead of 20 Wh.
        """
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 980.0
        sensor._update_native_value()
        assert sensor._energy_offset == 20.0

        sensor._mock_panel_value = 1010.0
        sensor._update_native_value()

        assert sensor._energy_offset == 0.0
        assert sensor._attr_native_value == 1010.0
        assert sensor._last_dip_delta is None
        sensor.coordinator.report_energy_dip.assert_not_called()

    def test_disabled_passthrough(self):
        """When disabled, dips pass through without compensation."""
        sensor = DummyDipSensor(dip_enabled=False)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 950.0
        sensor._update_native_value()

        # No compensation — raw value passed through
        assert sensor._attr_native_value == 950.0
        assert sensor._energy_offset == 0.0

    def test_non_total_increasing_passthrough(self):
        """MEASUREMENT sensors pass through without compensation."""
        sensor = DummyDipSensor(
            dip_enabled=True,
            state_class=SensorStateClass.MEASUREMENT,
        )

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 950.0
        sensor._update_native_value()

        # Not a TOTAL_INCREASING sensor — no compensation
        assert sensor._attr_native_value == 950.0
        assert sensor._energy_offset == 0.0

    def test_dip_reports_to_coordinator_once_confirmed(self):
        """Confirmation calls coordinator.report_energy_dip, not detection.

        The notification waits for the counter to corroborate the drop. Firing
        on sight is what produced the `SpanPanel/span#259` notification naming
        essentially every circuit on the panel for an event that had not
        happened.
        """
        sensor = DummyDipSensor(dip_enabled=True)
        sensor.entity_id = "sensor.test_energy"

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 950.0
        sensor._update_native_value()
        sensor.coordinator.report_energy_dip.assert_not_called()

        sensor._mock_panel_value = 955.0
        sensor._update_native_value()

        sensor.coordinator.report_energy_dip.assert_called_once_with(
            "sensor.test_energy", 50.0, 50.0
        )

    def test_no_report_when_no_dip(self):
        """Normal increases don't trigger coordinator notification."""
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 1100.0
        sensor._update_native_value()

        sensor.coordinator.report_energy_dip.assert_not_called()

    def test_exactly_at_threshold_triggers(self):
        """A dip of exactly 1.0 Wh triggers compensation."""
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()

        sensor._mock_panel_value = 999.0
        sensor._update_native_value()

        assert sensor._energy_offset == 1.0
        assert sensor._attr_native_value == 1000.0


# =============================================================================
# Extra state attributes
# =============================================================================


class TestDipAttributes:
    """Tests for energy dip compensation state attributes."""

    def test_shown_when_offset_nonzero(self):
        """Attributes include energy_offset when it is nonzero."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._energy_offset = 25.0
        sensor._last_dip_delta = 10.0

        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert attrs["energy_offset"] == 25.0
        assert attrs["last_dip_delta"] == 10.0

    def test_hidden_when_offset_zero_no_dip(self):
        """Attributes omit energy_offset when zero and no dip has occurred."""
        sensor = DummyDipSensor(dip_enabled=True)
        # Defaults: offset=0.0, last_dip_delta=None

        attrs = sensor.extra_state_attributes
        # No dip fields should appear
        assert attrs is None or "energy_offset" not in attrs

    def test_hidden_when_disabled(self):
        """Dip attributes are not shown when compensation is disabled."""
        sensor = DummyDipSensor(dip_enabled=False)
        sensor._energy_offset = 25.0
        sensor._last_dip_delta = 10.0

        attrs = sensor.extra_state_attributes
        assert attrs is None or "energy_offset" not in attrs

    def test_last_dip_shown_when_dip_occurred(self):
        """last_dip_delta appears even when offset is zero (shouldn't happen, but edge case)."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._energy_offset = 0.0
        sensor._last_dip_delta = 5.0

        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert attrs["last_dip_delta"] == 5.0
        # energy_offset is 0, should not be included
        assert "energy_offset" not in attrs


# =============================================================================
# Restoration
# =============================================================================


class TestDipRestoration:
    """Tests for dip compensation state restoration."""

    def test_offset_restored_when_enabled(self):
        """Verify _energy_offset is restored from stored data when enabled."""
        sensor = DummyDipSensor(dip_enabled=True)

        # Simulate what async_added_to_hass restoration does
        restored = SpanEnergyExtraStoredData(
            native_value=1000.0,
            native_unit_of_measurement="Wh",
            last_valid_state=1000.0,
            last_valid_changed="2025-12-01T00:00:00",
            energy_offset=50.0,
            last_panel_reading=950.0,
            last_dip_delta=10.0,
        )

        # Apply restoration (mimicking the async_added_to_hass logic)
        if sensor._dip_compensation_enabled and sensor._is_total_increasing:
            if restored.energy_offset is not None:
                sensor._energy_offset = restored.energy_offset
            if restored.last_panel_reading is not None:
                sensor._last_panel_reading = restored.last_panel_reading
            if restored.last_dip_delta is not None:
                sensor._last_dip_delta = restored.last_dip_delta

        assert sensor._energy_offset == 50.0
        assert sensor._last_panel_reading == 950.0
        assert sensor._last_dip_delta == 10.0

    def test_offset_not_restored_when_disabled(self):
        """Verify offsets are NOT restored when compensation is disabled."""
        sensor = DummyDipSensor(dip_enabled=False)

        restored = SpanEnergyExtraStoredData(
            native_value=1000.0,
            native_unit_of_measurement="Wh",
            last_valid_state=1000.0,
            last_valid_changed="2025-12-01T00:00:00",
            energy_offset=50.0,
            last_panel_reading=950.0,
            last_dip_delta=10.0,
        )

        # Apply restoration logic (gate on enabled flag)
        if sensor._dip_compensation_enabled and sensor._is_total_increasing:
            if restored.energy_offset is not None:
                sensor._energy_offset = restored.energy_offset

        # Disabled — should remain at defaults
        assert sensor._energy_offset == 0.0
        assert sensor._last_panel_reading is None
        assert sensor._last_dip_delta is None

    def test_extra_restore_state_data_includes_dip_fields(self):
        """extra_restore_state_data includes the dip compensation fields."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._attr_native_value = 1000.0
        sensor._energy_offset = 25.0
        sensor._last_panel_reading = 975.0
        sensor._last_dip_delta = 5.0
        sensor._last_valid_state = 1000.0

        stored = sensor.extra_restore_state_data
        d = stored.as_dict()
        assert d["energy_offset"] == 25.0
        assert d["last_panel_reading"] == 975.0
        assert d["last_dip_delta"] == 5.0

    def test_extra_restore_state_data_zero_offset_stored_as_none(self):
        """Zero offset is stored as None to keep stored data compact."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._attr_native_value = 1000.0
        sensor._energy_offset = 0.0

        stored = sensor.extra_restore_state_data
        d = stored.as_dict()
        assert d["energy_offset"] is None


# =============================================================================
# An unsettled dip across a restart
# =============================================================================


class TestPendingDipSurvivesARestart:
    """A restart between booking a dip and settling it must not settle it.

    This is the sequence that matters most, because the two are adjacent: the
    reading that would settle a dip is the one after the reading that booked it,
    and Home Assistant can stop in between. Forgetting the dip was provisional
    would confirm it by default — turning the restart itself into the thing that
    makes a bad offset permanent, which is `SpanPanel/span#259` exactly.
    """

    def test_the_pending_record_is_persisted(self):
        sensor = DummyDipSensor(dip_enabled=True)

        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()
        sensor._mock_panel_value = 0.0
        sensor._update_native_value()

        stored = sensor.extra_restore_state_data.as_dict()

        assert stored["pending_dip_baseline"] == 1000.0
        assert stored["pending_dip_delta"] == 1000.0

    def test_nothing_pending_is_persisted_as_none(self):
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._attr_native_value = 1000.0

        stored = sensor.extra_restore_state_data.as_dict()

        assert stored["pending_dip_baseline"] is None
        assert stored["pending_dip_delta"] is None

    def test_a_restart_mid_dip_still_retracts_when_the_counter_returns(self):
        """The offset booked before the restart is given back after it."""
        before = DummyDipSensor(dip_enabled=True)
        before._mock_panel_value = 1000.0
        before._update_native_value()
        before._mock_panel_value = 0.0
        before._update_native_value()
        stored = before.extra_restore_state_data.as_dict()

        after = DummyDipSensor(dip_enabled=True)
        restored = SpanEnergyExtraStoredData.from_dict(stored)
        assert restored is not None
        after._energy_offset = restored.energy_offset or 0.0
        after._last_panel_reading = restored.last_panel_reading
        after._last_dip_delta = restored.last_dip_delta
        assert restored.pending_dip_baseline is not None
        assert restored.pending_dip_delta is not None
        after._pending_dip = PendingDip(
            baseline=restored.pending_dip_baseline, delta=restored.pending_dip_delta
        )

        after._mock_panel_value = 1007.6
        after._update_native_value()

        assert after._energy_offset == 0.0
        assert after._attr_native_value == 1007.6
        after.coordinator.report_energy_dip.assert_not_called()


class TestAnOutageDoesNotSettleADip:
    """Grace period and dip compensation share a stored record but not a decision.

    While the panel is offline `_update_native_value` hands off to the grace
    period and returns before any dip processing, so an outage cannot book,
    confirm or retract anything. That is the intended division: grace period
    answers "what should this sensor read while the panel is unreachable", dip
    compensation answers "what did the counter do", and an outage is evidence
    about the transport rather than about the counter.
    """

    def test_an_outage_leaves_an_unsettled_dip_exactly_as_it_was(self):
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()
        sensor._mock_panel_value = 0.0
        sensor._update_native_value()
        booked = sensor._pending_dip

        sensor.coordinator.panel_offline = True
        sensor._mock_panel_value = 12345.0  # must not be read while offline
        sensor._update_native_value()

        assert sensor._pending_dip == booked
        assert sensor._energy_offset == 1000.0
        assert sensor._last_panel_reading == 0.0

    def test_grace_period_holds_the_compensated_value(self):
        """What HA was showing is what it keeps showing, offset included."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()
        sensor._mock_panel_value = 0.0
        sensor._update_native_value()

        sensor.coordinator.panel_offline = True
        sensor._update_native_value()

        assert sensor._attr_native_value == 1000.0
        assert sensor._last_valid_state == 1000.0

    def test_the_dip_settles_normally_once_the_panel_returns(self):
        """The outage delays the verdict rather than deciding it."""
        sensor = DummyDipSensor(dip_enabled=True)
        sensor._mock_panel_value = 1000.0
        sensor._update_native_value()
        sensor._mock_panel_value = 0.0
        sensor._update_native_value()

        sensor.coordinator.panel_offline = True
        sensor._update_native_value()
        sensor.coordinator.panel_offline = False
        sensor._mock_panel_value = 1007.6
        sensor._update_native_value()

        assert sensor._energy_offset == 0.0
        assert sensor._pending_dip is None
        assert sensor._attr_native_value == 1007.6
        sensor.coordinator.report_energy_dip.assert_not_called()
