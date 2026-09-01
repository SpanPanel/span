"""Curation records: parsing, validation, sanitisation, and the description helpers."""

from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel import async_remove_entry
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import (
    CurationError,
    CurationOverlay,
    CurationRecord,
    RowContext,
    allowed_device_classes,
    allowed_state_classes,
    async_forget_curation,
    async_load_curation,
    async_save_record,
    binary_sensor_device_class,
    entity_category_for,
    parse_record,
    record_as_dict,
    sanitise,
    sensor_description,
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


def test_allowed_device_classes_respect_the_wire_datatype() -> None:
    """A device class the row's datatype cannot satisfy is not an offer worth making.

    The unit gate alone let a `string` row be offered `power_factor` and `aqi` --
    classes that constrain no unit, and so passed -- which reads unknown forever
    because the value behind them is text.
    """
    numeric = allowed_device_classes(SENSOR_FLOAT_V)
    assert "enum" not in numeric
    assert "date" not in numeric
    assert "timestamp" not in numeric
    assert "voltage" in numeric

    text = allowed_device_classes(SENSOR_STRING)
    assert "power_factor" not in text
    assert "aqi" not in text
    assert {"enum", "timestamp"} <= set(text)


def test_a_device_class_the_datatype_cannot_satisfy_is_refused() -> None:
    """Both directions, because the partition has two halves and each is a real mistake."""
    with pytest.raises(CurationError) as err:
        validate_record({"device_class": "enum"}, SENSOR_FLOAT_V)
    assert err.value.code == "incompatible_device_class"
    assert "float" in str(err.value)

    with pytest.raises(CurationError) as err:
        validate_record({"device_class": "power_factor"}, SENSOR_STRING)
    assert err.value.code == "incompatible_device_class"
    assert "string" in str(err.value)


def test_a_device_class_the_datatype_does_satisfy_is_accepted() -> None:
    assert validate_record({"device_class": "enum"}, SENSOR_STRING).device_class == "enum"


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


async def test_removing_the_entry_forgets_the_curated_records(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The keys are wire addresses, not registry ids.

    A store left behind is one the next entry added for the same panel would
    load and apply, re-asserting metadata for a panel the user removed.
    """
    entry = _entry()
    await async_save_record(hass, entry, "bess/b/p", CurationRecord(promote=True))

    await async_forget_curation(hass, entry)

    assert "span_panel.curation.test-entry" not in hass_storage
    assert (await async_load_curation(hass, entry)).as_dicts() == {}


async def test_removing_the_config_entry_is_what_calls_the_forget(hass: HomeAssistant) -> None:
    """The store outliving the entry is only prevented if the removal hook says so."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="removed-entry", unique_id="sp3-001")
    entry.add_to_hass(hass)
    await async_save_record(hass, entry, "bess/b/p", CurationRecord(promote=True))

    await async_remove_entry(hass, entry)

    assert (await async_load_curation(hass, entry)).as_dicts() == {}


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


# The description helpers. Every curated value reaches an entity through one of
# these three, which is what lets `adoption.py` and `extension.py` stay free of
# the tokens their AST guards forbid.


def test_sensor_description_without_a_record_matches_todays_behaviour() -> None:
    description = sensor_description("b/p", "V", SensorDeviceClass.VOLTAGE, None)
    assert description.state_class is None
    assert description.device_class is SensorDeviceClass.VOLTAGE
    assert description.native_unit_of_measurement == "V"


def test_a_record_supplies_state_class_and_overrides_the_default_device_class() -> None:
    record = CurationRecord(state_class=SensorStateClass.MEASUREMENT, device_class="energy")
    description = sensor_description("b/p", "Wh", SensorDeviceClass.ENERGY, record)
    assert description.state_class is SensorStateClass.MEASUREMENT
    assert description.device_class is SensorDeviceClass.ENERGY


def test_entity_category_promotes_only_on_an_explicit_record() -> None:
    assert entity_category_for(None) is EntityCategory.DIAGNOSTIC
    assert entity_category_for(CurationRecord()) is EntityCategory.DIAGNOSTIC
    assert entity_category_for(CurationRecord(promote=True)) is None


def test_binary_sensor_device_class_reads_the_record_only() -> None:
    assert binary_sensor_device_class(None) is None
    assert binary_sensor_device_class(CurationRecord(device_class="problem")) is (
        BinarySensorDeviceClass.PROBLEM
    )
