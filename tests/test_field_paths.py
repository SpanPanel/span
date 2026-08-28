"""Every entity that reads one snapshot field must say which field."""

from __future__ import annotations

from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    FieldPathDeclarationMixin,
    Producibility,
    conditional_field_paths,
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
    a single source field exists at all. `NO_SOURCE_FIELD` and
    `MULTIPLE_FIELDS` have none to name. `SCHEMA_CONDITIONAL_FIELD` has exactly
    one and must name it: `derived` excuses the path from the *producible* gate,
    which is a claim about the other adapter, and saying nothing about the field
    would additionally excuse the entity from the Repair count and the
    availability probe — the invisibility this whole module exists to prevent.
    """
    for description in platform_descriptions():
        assert isinstance(description, FieldPathDeclarationMixin), description
        names_field = description.field_path is not None
        if description.derived is DerivedReason.SCHEMA_CONDITIONAL_FIELD:
            assert names_field, (
                f"{description.key} is SCHEMA_CONDITIONAL_FIELD, which reads exactly one "
                "field, and must declare it as field_path= so the Repair and the "
                "availability probe can see it"
            )
            continue
        assert names_field != (description.derived is not None), (
            f"{description.key} must declare exactly one of field_path / a DerivedReason"
        )


def test_schema_conditional_descriptions_name_an_exempt_field() -> None:
    """The field a schema-conditional description names must be enumerated.

    Its source path cannot enter `declared_field_paths()` — one adapter does not
    produce it — so `RESIDUAL_EXEMPT_PATHS` is the only place it is written down
    and the only place its producibility is checked against the adapters. A
    schema-conditional description naming a path absent from there would be read
    by an entity, gated by nothing, and reported by `evaluate_field_metadata` as
    produced-but-unread. `panel.dominant_power_source` was exactly that.

    `NEITHER` is excluded on purpose: a path no adapter publishes a metadata row
    for has nothing to resolve, so a description reading one is
    `NO_SOURCE_FIELD`, not schema-conditional.
    """
    for description in platform_descriptions():
        assert isinstance(description, FieldPathDeclarationMixin), description
        if description.derived is not DerivedReason.SCHEMA_CONDITIONAL_FIELD:
            continue
        path = description.field_path
        assert path in RESIDUAL_EXEMPT_PATHS, (
            f"{description.key} reads {path!r}, which no adapter pair produces and "
            "RESIDUAL_EXEMPT_PATHS does not enumerate"
        )
        assert RESIDUAL_EXEMPT_PATHS[path] is not Producibility.NEITHER, (
            f"{description.key} claims SCHEMA_CONDITIONAL_FIELD but {path!r} is "
            "annotated NEITHER — no adapter produces a row for it, so there is "
            "nothing schema-conditional about it"
        )


def test_conditional_paths_are_exactly_the_unresolvable_reads() -> None:
    """`conditional_field_paths()` must be disjoint from the declared set.

    The two together are what `evaluate_field_metadata` asks the adapter about.
    An overlap would mean a path was both gated and exempted, which is the
    contradiction `test_residual_buckets_are_disjoint` rules out one level down;
    asserting it here keeps the union honest as the two functions change.
    """
    conditional = conditional_field_paths()
    assert conditional
    assert not (conditional & declared_field_paths())
    assert conditional <= RESIDUAL_EXEMPT_PATHS.keys()


def test_residual_buckets_are_disjoint() -> None:
    """A residual path is either producible or exempt, never both."""
    assert not (residual_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
    assert not (declared_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
