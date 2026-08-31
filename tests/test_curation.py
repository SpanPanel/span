"""Curation records: parsing, validation, sanitisation, and the description helpers."""

from unittest.mock import MagicMock

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from custom_components.span_panel.curation import (
    CurationError,
    CurationOverlay,
    CurationRecord,
    RowContext,
    allowed_device_classes,
    allowed_state_classes,
    async_load_curation,
    async_save_record,
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


# The store. Loading is awaited from `async_setup_entry`, so every shape the disk
# is allowed to hold has to end in an overlay rather than an exception -- these
# are the shapes, not a survey of them.


def _entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test-entry"
    return entry


async def test_save_load_round_trip(hass: HomeAssistant) -> None:
    entry = _entry()
    record = CurationRecord(state_class=SensorStateClass.MEASUREMENT, device_class="voltage")
    await async_save_record(hass, entry, "bess/battery-2/cell-voltage", record)
    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for("bess/battery-2/cell-voltage") == record
    assert overlay.as_dicts() == {
        "bess/battery-2/cell-voltage": {"state_class": "measurement", "device_class": "voltage"}
    }


async def test_saving_none_clears_the_record(hass: HomeAssistant) -> None:
    entry = _entry()
    await async_save_record(hass, entry, "bess/b/p", CurationRecord(promote=True))
    await async_save_record(hass, entry, "bess/b/p", None)
    overlay = await async_load_curation(hass, entry)
    assert overlay.record_for("bess/b/p") is None


async def test_a_record_that_asserts_nothing_clears_rather_than_being_written(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Saving `CurationRecord()` may not leave the one shape the loader calls damaged.

    Its stored form is `{}`, which `parse_record` refuses -- so writing it would
    put a record on disk that the next load reports as unreadable and the save
    after that silently deletes, over a value the signature accepts.
    """
    entry = _entry()
    await async_save_record(hass, entry, "bess/b/p", CurationRecord(promote=True))
    await async_save_record(hass, entry, "bess/b/p", CurationRecord())
    overlay = await async_load_curation(hass, entry)
    assert overlay.as_dicts() == {}
    assert "unreadable" not in caplog.text


async def test_a_wrong_shaped_store_loads_as_empty(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    hass_storage["span_panel.curation.test-entry"] = {
        "version": 1,
        "data": {"records": "not a mapping"},
    }
    overlay = await async_load_curation(hass, _entry())
    assert overlay.as_dicts() == {}


async def test_an_unreadable_record_is_skipped_not_fatal(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    hass_storage["span_panel.curation.test-entry"] = {
        "version": 1,
        "data": {"records": {"good/b/p": {"entity_category": "none"}, "bad/b/p": {"x": 1}}},
    }
    overlay = await async_load_curation(hass, _entry())
    assert overlay.record_for("good/b/p") == CurationRecord(promote=True)
    assert overlay.record_for("bad/b/p") is None


def test_for_row_sanitises_and_stale_fields_reports() -> None:
    overlay = CurationOverlay(
        {"k": CurationRecord(state_class=SensorStateClass.MEASUREMENT, promote=True)}
    )
    sanitised = overlay.for_row("k", SENSOR_STRING)
    assert sanitised == CurationRecord(promote=True)
    assert overlay.stale_fields("k", SENSOR_STRING) == ("state_class",)
    assert overlay.for_row("missing", SENSOR_STRING) is None
    assert overlay.stale_fields("missing", SENSOR_STRING) == ()


def test_an_empty_overlay_answers_for_a_row_it_has_never_heard_of() -> None:
    """What a setup that stored nothing runs against, and every row goes through it."""
    overlay = CurationOverlay.empty()
    assert overlay.as_dicts() == {}
    assert overlay.for_row("bess/b/p", SENSOR_STRING) is None


def test_a_dropped_field_is_named_in_the_warning_it_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line is all the user gets: their assertion stops applying and nothing else says so."""
    overlay = CurationOverlay(
        {"bess/battery-2/cell-voltage": CurationRecord(device_class="voltage")}
    )
    assert overlay.for_row("bess/battery-2/cell-voltage", SENSOR_STRING) == CurationRecord()
    assert "bess/battery-2/cell-voltage" in caplog.text
    assert "device_class" in caplog.text
