from __future__ import annotations

from collections.abc import Callable

import pytest
from span_panel_api.models import FieldMetadata

from custom_components.span_panel import sensor_definitions
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    FieldPathDeclarationMixin,
    declared_field_paths,
)
from custom_components.span_panel.schema_validation import (
    SchemaFindings,
    evaluate_field_metadata,
)
from custom_components.span_panel.sensor_definitions import (
    all_sensor_descriptions,
    sensor_descriptions_by_field_path,
)
from tests.adapter_fixtures import (
    schema_one_metadata,
    schema_one_metadata_batteryless,
    schema_zero_metadata,
)

MetadataFn = Callable[[], dict[str, FieldMetadata]]


def test_unresolved_entry_is_degradation() -> None:
    findings = evaluate_field_metadata(
        {"circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)},
        sensor_defs={},
    )
    assert "circuit.instant_power_w" in findings.unresolved


def test_absent_entry_is_not_degradation() -> None:
    """No entry means the hardware is not installed — not a defect."""
    findings = evaluate_field_metadata({}, sensor_defs={})
    assert findings.unresolved == frozenset()


def test_unit_mismatch_is_reported() -> None:
    """Deliberately not `circuit.instant_power_w`/"kW".

    That pair is the one entry in `KNOWN_BAD_SCHEMA_UNITS`, so it would prove the
    exception rather than the check.
    """
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import UnitOfElectricPotential

    description = SensorEntityDescription(
        key="l1_voltage", native_unit_of_measurement=UnitOfElectricPotential.VOLT
    )
    findings = evaluate_field_metadata(
        {"panel.l1_voltage": FieldMetadata("kV", "float")},
        sensor_defs={"panel.l1_voltage": description},
    )
    assert findings.unit_mismatches[0].field_path == "panel.l1_voltage"
    assert findings.unit_mismatches[0].schema_unit == "kV"


def test_unitless_sensor_still_checked_for_resolution() -> None:
    """Resolution is checked before the unit is.

    The old code short-circuited on `ha_unit is None` BEFORE the lookup, so enum
    and string sensors could go dead with no signal.
    """
    from homeassistant.components.sensor import SensorEntityDescription

    description = SensorEntityDescription(key="evse_status")
    findings = evaluate_field_metadata(
        {"evse.status": FieldMetadata(None, "unknown", resolved=False)},
        sensor_defs={"evse.status": description},
    )
    assert "evse.status" in findings.unresolved


def test_unresolved_entry_is_never_a_unit_mismatch() -> None:
    """An unresolved entry carries `unit=None` by construction.

    Comparing that against a declared unit would raise a false mismatch on every
    affected sensor, so resolution must be branched on first.
    """
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import UnitOfPower

    description = SensorEntityDescription(
        key="circuit_power", native_unit_of_measurement=UnitOfPower.WATT
    )
    findings = evaluate_field_metadata(
        {"circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)},
        sensor_defs={"circuit.instant_power_w": description},
    )
    assert findings.unresolved == frozenset({"circuit.instant_power_w"})
    assert findings.unit_mismatches == ()


def test_matching_unit_is_not_a_mismatch() -> None:
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import UnitOfPower

    description = SensorEntityDescription(
        key="circuit_power", native_unit_of_measurement=UnitOfPower.WATT
    )
    findings = evaluate_field_metadata(
        {"circuit.instant_power_w": FieldMetadata("W", "float")},
        sensor_defs={"circuit.instant_power_w": description},
    )
    assert findings.unit_mismatches == ()
    assert findings.unresolved == frozenset()


def test_produced_but_unread_fields_are_inventoried() -> None:
    """An addition is legal within a major version — inventory, not a defect."""
    findings = evaluate_field_metadata(
        {"panel.some_future_field": FieldMetadata("W", "float")}, sensor_defs={}
    )
    assert findings.unread == frozenset({"panel.some_future_field"})
    assert findings.unresolved == frozenset()
    assert findings.unit_mismatches == ()


def test_declared_and_resolved_fields_are_not_unread() -> None:
    findings = evaluate_field_metadata(
        {"circuit.instant_power_w": FieldMetadata("W", "float")}, sensor_defs={}
    )
    assert findings.unread == frozenset()


def test_empty_metadata_is_healthy_not_unknown() -> None:
    """A pass over a healthy panel is expressible and is not the None sentinel.

    Task 7 needs three distinct states; this is the middle one. "Unknown" is the
    coordinator's `schema_findings is None`, which `evaluate_field_metadata` can
    no longer produce — it no longer accepts the sentinel at all.
    """
    findings = evaluate_field_metadata({})
    assert findings == SchemaFindings(frozenset(), (), frozenset())


def test_every_declared_field_path_keys_a_sensor_or_a_residual_reader() -> None:
    """`sensor_descriptions_by_field_path` must not drop descriptions.

    Keys such as "model" and "serial_number" repeat across device classes, so a
    dict keyed on `description.key` would silently collapse them.
    """
    by_path = sensor_descriptions_by_field_path()
    assert by_path.keys() <= declared_field_paths()
    assert {"battery.model", "pv.model"} <= by_path.keys()
    for field_path, description in by_path.items():
        assert description.field_path == field_path
        assert not description.derived


def test_resolved_unitless_sensor_yields_no_mismatch() -> None:
    """A resolved field read by an enum or string sensor has nothing to compare."""
    from homeassistant.components.sensor import SensorEntityDescription

    description = SensorEntityDescription(key="evse_status")
    findings = evaluate_field_metadata(
        {"evse.status": FieldMetadata(None, "enum")},
        sensor_defs={"evse.status": description},
    )
    assert findings.unresolved == frozenset()
    assert findings.unit_mismatches == ()


def test_descriptions_without_a_declaration_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both consumers of the shared traversal reject an undeclared description.

    Skipping it would drop the sensor from the unit cross-check silently, which
    is the drift `field_paths` exists to prevent. `declared_field_paths` raises
    on the same input, and both now do so from one place.
    """
    from homeassistant.components.sensor import SensorEntityDescription

    monkeypatch.setattr(
        sensor_definitions,
        "all_sensor_descriptions",
        lambda: (SensorEntityDescription(key="undeclared"),),
    )
    with pytest.raises(TypeError, match="carries no field-path declaration"):
        sensor_definitions.sensor_descriptions_by_field_path()


@pytest.mark.parametrize(
    "metadata_fn",
    [
        pytest.param(schema_zero_metadata, id="schema_0"),
        pytest.param(schema_one_metadata, id="schema_1"),
    ],
)
def test_real_adapter_metadata_produces_no_findings(metadata_fn: MetadataFn) -> None:
    """A healthy panel of either generation must be finding-free.

    The rest of this file drives the evaluator with synthetic single-entry dicts,
    which cannot show what real firmware actually declares. This is the standing
    guard against a day-one Repair that no user can act on.
    """
    findings = evaluate_field_metadata(metadata_fn(), sensor_descriptions_by_field_path())
    assert findings.unresolved == frozenset()
    assert findings.unit_mismatches == ()


def test_known_bad_schema_unit_exception_is_narrow() -> None:
    """Only the exact known-bad unit is excused; anything else is new information."""
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import UnitOfPower

    description = SensorEntityDescription(
        key="circuit_power", native_unit_of_measurement=UnitOfPower.WATT
    )
    findings = evaluate_field_metadata(
        {"circuit.instant_power_w": FieldMetadata("MW", "float")},
        sensor_defs={"circuit.instant_power_w": description},
    )
    assert [m.schema_unit for m in findings.unit_mismatches] == ["MW"]


def test_absent_hardware_on_real_metadata_is_not_degradation() -> None:
    """A batteryless panel simply omits the battery rows — nothing is wrong.

    Stronger than the empty-dict case, which passes whether or not the
    `entry is None` arm exists: here 8 `battery.*` paths are declared and read,
    and every one of them is missing from the adapter's output.
    """
    metadata = schema_one_metadata_batteryless()
    battery_paths = {p for p in declared_field_paths() if p.startswith("battery.")}
    assert battery_paths
    assert battery_paths.isdisjoint(metadata)

    findings = evaluate_field_metadata(metadata, sensor_descriptions_by_field_path())
    assert findings.unresolved == frozenset()
    assert findings.unit_mismatches == ()


@pytest.mark.parametrize(
    "metadata_fn",
    [
        pytest.param(schema_zero_metadata, id="schema_0"),
        pytest.param(schema_one_metadata, id="schema_1"),
    ],
)
def test_unread_excludes_readers_exempt_from_the_producible_gate(
    metadata_fn: MetadataFn,
) -> None:
    """`RESIDUAL_EXEMPT_PATHS` are read, just not required of both adapters.

    They are absent from `declared_field_paths()`, so a plain set difference
    reports them as produced-but-unread — false for 10 of schema_0's 17.
    """
    findings = evaluate_field_metadata(metadata_fn(), sensor_descriptions_by_field_path())
    assert findings.unread.isdisjoint(RESIDUAL_EXEMPT_PATHS)


def test_readers_of_the_same_field_path_agree_on_unit() -> None:
    """`sensor_descriptions_by_field_path` keeps one reader per path.

    Several field paths are read by two descriptions (an unmapped-circuit raw
    key and its named-circuit twin). Dropping one is only safe while they agree
    on what the unit check would compare, so pin that here rather than trusting
    it.
    """
    from collections import defaultdict

    by_path: defaultdict[str, list[object]] = defaultdict(list)
    for description in all_sensor_descriptions():
        if not isinstance(description, FieldPathDeclarationMixin):
            continue
        if description.derived or description.field_path is None:
            continue
        by_path[description.field_path].append(description)

    colliding = {path: ds for path, ds in by_path.items() if len(ds) > 1}
    assert colliding, "expected at least one field path with two readers"
    for path, descriptions in colliding.items():
        units = {d.native_unit_of_measurement for d in descriptions}
        assert len(units) == 1, f"readers of {path} disagree on unit: {units}"


def test_known_bad_schema_unit_exception_is_keyed_on_the_field_path() -> None:
    """The other half of the pair: only `circuit.instant_power_w` is excused.

    `test_known_bad_schema_unit_exception_is_narrow` pins the unit half — a
    different unit on the same path is still reported. Without this, widening
    the check to a unit-only membership test ("is kW ever known-bad?") would
    pass the whole suite while silently excusing every field that declares kW.
    """
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import UnitOfPower

    description = SensorEntityDescription(
        key="grid_power", native_unit_of_measurement=UnitOfPower.WATT
    )
    findings = evaluate_field_metadata(
        {"panel.instant_grid_power_w": FieldMetadata("kW", "float")},
        sensor_defs={"panel.instant_grid_power_w": description},
    )
    assert [m.field_path for m in findings.unit_mismatches] == ["panel.instant_grid_power_w"]
    assert findings.unit_mismatches[0].schema_unit == "kW"
