from __future__ import annotations

import pytest
from span_panel_api.models import FieldMetadata

from custom_components.span_panel import sensor_definitions
from custom_components.span_panel.field_paths import declared_field_paths
from custom_components.span_panel.schema_validation import (
    SchemaFindings,
    evaluate_field_metadata,
)
from custom_components.span_panel.sensor_definitions import (
    sensor_descriptions_by_field_path,
)


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
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import UnitOfPower

    description = SensorEntityDescription(
        key="circuit_power", native_unit_of_measurement=UnitOfPower.WATT
    )
    findings = evaluate_field_metadata(
        {"circuit.instant_power_w": FieldMetadata("kW", "float")},
        sensor_defs={"circuit.instant_power_w": description},
    )
    assert findings.unit_mismatches[0].field_path == "circuit.instant_power_w"
    assert findings.unit_mismatches[0].schema_unit == "kW"


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


def test_none_metadata_yields_empty_findings() -> None:
    """The module-level fallback.

    Callers that must distinguish "unknown" from "healthy" — the coordinator
    does — check for None before calling.
    """
    findings = evaluate_field_metadata(None)
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


def test_descriptions_without_a_declaration_are_not_keyed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`declared_field_paths` is the enforcement point; here there is no key.

    A description that carries no `FieldPathDeclarationMixin` names no field, so
    it cannot appear in a field-path-keyed map. `declared_field_paths` raises on
    the same input, which is where the drift is caught.
    """
    from homeassistant.components.sensor import SensorEntityDescription

    monkeypatch.setattr(
        sensor_definitions,
        "all_sensor_descriptions",
        lambda: (SensorEntityDescription(key="undeclared"),),
    )
    assert sensor_definitions.sensor_descriptions_by_field_path() == {}
