"""Every entity that reads one snapshot field must say which field."""

from __future__ import annotations

from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    FieldPathDeclarationMixin,
    Producibility,
    declared_field_paths,
    platform_descriptions,
    residual_field_paths,
)
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS


def test_circuit_power_declares_its_field_path() -> None:
    power = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")
    assert power.field_path == "circuit.instant_power_w"
    assert power.derived is None


def test_derived_sensor_declares_no_path() -> None:
    """dsm_state is a multi-signal derivation with no single source field."""
    from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

    dsm = next(d for d in PANEL_DATA_STATUS_SENSORS if d.key == "dsm_state")
    assert dsm.derived is DerivedReason.NO_SOURCE_FIELD
    assert dsm.field_path is None


def test_declared_field_paths_includes_residuals() -> None:
    """Readers that live in entity code rather than on a description still count."""
    paths = declared_field_paths()
    assert "circuit.relay_state" in paths
    assert "circuit.priority" in paths


def test_every_description_declares_exactly_one() -> None:
    """A description inheriting the mixin but setting neither field is invisible.

    The `TypeError` guard in `declared_field_paths` only catches a description
    that lacks the mixin entirely. Every new sensor inherits it automatically,
    so the likelier mistake is inheriting it and declaring nothing — which
    drops the entity from every gate with no signal. This is that signal.

    "Exactly one" is per `DerivedReason`, because the reasons differ on whether
    a single source field exists at all. `MULTIPLE_FIELDS` has none to name.
    `NO_SOURCE_FIELD` may name the one field it reads that the adapter maps no
    row for, and `test_a_no_source_field_description_names_only_an_unmapped_field`
    holds that name to the adapter. A description with no reason must name its
    field.
    """
    for description in platform_descriptions():
        assert isinstance(description, FieldPathDeclarationMixin), description
        if description.derived is DerivedReason.NO_SOURCE_FIELD:
            continue
        names_field = description.field_path is not None
        assert names_field != (description.derived is not None), (
            f"{description.key} must declare exactly one of field_path / a DerivedReason"
        )


def test_a_no_source_field_description_names_only_an_unmapped_field() -> None:
    """A field a `NO_SOURCE_FIELD` description names must be enumerated, as unmapped.

    Its path cannot enter `declared_field_paths()` — the description is derived
    — so `RESIDUAL_EXEMPT_PATHS` is the only place it is written down and the
    only place it is checked against the adapter. Absent from there, it would be
    read by an entity, gated by nothing, and reported by `evaluate_field_metadata`
    as produced-but-unread; `panel.dominant_power_source` was once exactly that.

    And it must be `UNMAPPED`. A path the adapter maps is a reading the entity
    could be validated on, so a description reading one is a plain declaration,
    not one with no source field.
    """
    named = 0
    for description in platform_descriptions():
        assert isinstance(description, FieldPathDeclarationMixin), description
        if description.derived is not DerivedReason.NO_SOURCE_FIELD:
            continue
        path = description.field_path
        if path is None:
            continue
        named += 1
        assert RESIDUAL_EXEMPT_PATHS.get(path) is Producibility.UNMAPPED, (
            f"{description.key} is NO_SOURCE_FIELD and names {path!r}, which "
            "RESIDUAL_EXEMPT_PATHS must enumerate as UNMAPPED; a mapped path makes "
            "it a plain declaration"
        )
    assert named, "no NO_SOURCE_FIELD description names a field, so this checks nothing"


def test_residual_buckets_are_disjoint() -> None:
    """A residual path is either producible or exempt, never both."""
    assert not (residual_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
    assert not (declared_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
