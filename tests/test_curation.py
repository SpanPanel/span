"""Curation records: parsing, validation, sanitisation, and the description helpers."""

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import Platform

from custom_components.span_panel.curation import (
    CurationError,
    CurationRecord,
    RowContext,
    allowed_device_classes,
    allowed_state_classes,
    parse_record,
    record_as_dict,
    sanitise,
    validate_record,
)

SENSOR_FLOAT_V = RowContext(platform=Platform.SENSOR, datatype="float", unit="V")
SENSOR_STRING = RowContext(platform=Platform.SENSOR, datatype="string", unit=None)
BINARY = RowContext(platform=Platform.BINARY_SENSOR, datatype="boolean", unit=None)
SWITCH = RowContext(platform=Platform.SWITCH, datatype="boolean", unit=None)


def test_a_numeric_sensor_row_accepts_a_state_class() -> None:
    record = validate_record({"state_class": "measurement"}, SENSOR_FLOAT_V)
    assert record.state_class is SensorStateClass.MEASUREMENT


def test_a_string_sensor_row_refuses_a_state_class() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"state_class": "measurement"}, SENSOR_STRING)
    assert err.value.code == "invalid_state_class"


def test_a_binary_row_refuses_a_state_class() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"state_class": "measurement"}, BINARY)
    assert err.value.code == "invalid_state_class"


def test_a_control_row_accepts_only_promotion() -> None:
    record = validate_record({"entity_category": "none"}, SWITCH)
    assert record.promote is True
    with pytest.raises(CurationError) as err:
        validate_record({"device_class": "power", "entity_category": "none"}, SWITCH)
    assert err.value.code == "invalid_field_for_platform"


def test_a_device_class_incompatible_with_the_wire_unit_is_refused() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"device_class": "temperature"}, SENSOR_FLOAT_V)
    assert err.value.code == "incompatible_device_class"


def test_a_device_class_compatible_with_the_wire_unit_is_accepted() -> None:
    record = validate_record({"device_class": "voltage"}, SENSOR_FLOAT_V)
    assert record.device_class == "voltage"


def test_an_unknown_device_class_is_refused() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"device_class": "not-a-class"}, SENSOR_FLOAT_V)
    assert err.value.code == "invalid_device_class"


def test_a_binary_row_takes_binary_device_classes_only() -> None:
    assert validate_record({"device_class": "problem"}, BINARY).device_class == "problem"
    with pytest.raises(CurationError):
        validate_record({"device_class": "voltage"}, BINARY)


def test_an_entity_category_other_than_none_is_refused() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"entity_category": "config"}, SENSOR_FLOAT_V)
    assert err.value.code == "invalid_entity_category"


def test_sanitise_drops_a_field_the_wire_no_longer_supports_and_keeps_the_rest() -> None:
    record = CurationRecord(
        state_class=SensorStateClass.MEASUREMENT, device_class="voltage", promote=True
    )
    sanitised, dropped = sanitise(record, SENSOR_STRING)
    assert sanitised.state_class is None
    assert sanitised.device_class is None
    assert sanitised.promote is True
    assert set(dropped) == {"state_class", "device_class"}


def test_allowed_state_classes_are_empty_off_numeric_sensor_rows() -> None:
    assert allowed_state_classes(SENSOR_FLOAT_V) == [cls.value for cls in SensorStateClass]
    assert allowed_state_classes(SENSOR_STRING) == []
    assert allowed_state_classes(SWITCH) == []


def test_allowed_device_classes_respect_the_wire_unit() -> None:
    allowed = allowed_device_classes(SENSOR_FLOAT_V)
    assert "voltage" in allowed
    assert "temperature" not in allowed
    assert allowed_device_classes(SWITCH) == []


def test_record_round_trips_through_its_dict_form() -> None:
    record = CurationRecord(
        state_class=SensorStateClass.TOTAL_INCREASING, device_class="energy", promote=True
    )
    assert parse_record(record_as_dict(record)) == record
    assert parse_record({"unknown": "shape"}) is None
    assert parse_record("not a mapping") is None


def test_an_unknown_state_class_is_refused_on_a_row_that_could_carry_one() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"state_class": "not-a-state-class"}, SENSOR_FLOAT_V)
    assert err.value.code == "invalid_state_class"


def test_a_control_row_refuses_a_state_class_before_reading_it() -> None:
    with pytest.raises(CurationError) as err:
        validate_record({"state_class": "measurement"}, SWITCH)
    assert err.value.code == "invalid_field_for_platform"


def test_a_stored_record_the_wire_vocabulary_has_outgrown_reads_as_absent() -> None:
    assert parse_record({"state_class": "no-longer-a-state-class"}) is None
    assert parse_record({"entity_category": "diagnostic"}) is None
    assert parse_record({}) is None


def test_a_binary_row_is_offered_every_binary_device_class() -> None:
    assert allowed_device_classes(BINARY) == [cls.value for cls in BinarySensorDeviceClass]
