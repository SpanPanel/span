"""Tests for the pure rule deciding when a breaker rating override applies."""

from __future__ import annotations

from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.breaker_ratings import (
    BreakerRating,
    Declaration,
    OverrideChange,
    Reconciled,
    StoredOverride,
    reconcile,
    resolve,
    service_rating_declaration,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

PANEL_A = 20.0
OVERRIDE_A = 50
SERVICE_A = 100.0
CIRCUIT_ID = "circuit-2"


def _label(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    return f"label of {circuit.name} ({circuit_id})"


def _circuit(
    rating: float | None = PANEL_A,
    declared: bool | None = True,
    name: str = "Oven",
) -> SpanCircuitSnapshot:
    return SpanCircuitSnapshotFactory.create(
        circuit_id=CIRCUIT_ID,
        name=name,
        breaker_rating_a=rating,
        breaker_rating_declared=declared,
    )


def _snapshot(circuit: SpanCircuitSnapshot | None, main: int | None = 100) -> SpanPanelSnapshot:
    circuits = {CIRCUIT_ID: circuit} if circuit is not None else {}
    return SpanPanelSnapshotFactory.create(circuits=circuits, main_breaker_rating_a=main)


def _override(rating: int = OVERRIDE_A, at_set: float = PANEL_A) -> StoredOverride:
    return StoredOverride(rating_a=rating, panel_a_at_set=at_set, label="stored label")


def _reconcile(
    snapshot: SpanPanelSnapshot,
    service: Declaration = "declared",
    override: StoredOverride | None = None,
) -> Reconciled:
    return reconcile({CIRCUIT_ID: override or _override()}, snapshot, service, _label)


# --- service_rating_declaration ---


def test_service_declaration_unknown_without_metadata() -> None:
    assert service_rating_declaration(None) == "unknown"


def test_service_declaration_from_main_breaker() -> None:
    metadata = {"panel.main_breaker_rating_a": FieldMetadata("A", "integer", True)}
    assert service_rating_declaration(metadata) == "declared"


def test_service_declaration_from_upstream_protection() -> None:
    metadata = {"panel.upstream_protection_rating_a": FieldMetadata("A", "integer", True)}
    assert service_rating_declaration(metadata) == "declared"


def test_service_declaration_unresolved_or_absent_is_undeclared() -> None:
    assert service_rating_declaration({}) == "undeclared"
    metadata = {
        "panel.main_breaker_rating_a": FieldMetadata(None, "integer", False),
        "panel.upstream_protection_rating_a": FieldMetadata(None, "integer", False),
    }
    assert service_rating_declaration(metadata) == "undeclared"


# --- resolve ---


def test_resolve_no_override_shows_panel() -> None:
    assert resolve(_circuit(), None, SERVICE_A, "declared") == BreakerRating(
        PANEL_A, PANEL_A, False
    )


def test_resolve_override_applies() -> None:
    assert resolve(_circuit(), _override(), SERVICE_A, "declared") == BreakerRating(
        float(OVERRIDE_A), PANEL_A, True
    )


def test_resolve_override_equal_to_service_applies() -> None:
    assert resolve(_circuit(), _override(100), SERVICE_A, "declared").user_set is True


def test_resolve_missing_circuit_is_unrated() -> None:
    assert resolve(None, _override(), SERVICE_A, "declared") == BreakerRating(None, None, False)


def test_resolve_pending_panel_rating_is_unrated() -> None:
    assert resolve(_circuit(rating=None), _override(), SERVICE_A, "declared") == BreakerRating(
        None, None, False
    )


def test_resolve_pending_service_rating_shows_panel() -> None:
    expected = BreakerRating(PANEL_A, PANEL_A, False)
    assert resolve(_circuit(), _override(), None, "declared") == expected
    assert resolve(_circuit(), _override(), SERVICE_A, "unknown") == expected
    assert resolve(_circuit(), _override(), None, "undeclared") == expected


def test_resolve_override_above_service_shows_panel() -> None:
    assert resolve(_circuit(), _override(101), SERVICE_A, "declared") == BreakerRating(
        PANEL_A, PANEL_A, False
    )


def test_resolve_override_equal_to_panel_shows_panel() -> None:
    assert resolve(_circuit(), _override(20), SERVICE_A, "declared") == BreakerRating(
        PANEL_A, PANEL_A, False
    )


# --- reconcile ---


def test_reconcile_unchanged_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit()))
    assert result.changes == ()
    assert result.overrides == {
        CIRCUIT_ID: StoredOverride(OVERRIDE_A, PANEL_A, _label(_circuit(), CIRCUIT_ID))
    }


def test_reconcile_refreshes_label_silently() -> None:
    result = _reconcile(_snapshot(_circuit(name="Range")))
    assert result.changes == ()
    assert result.overrides[CIRCUIT_ID].label == "label of Range (circuit-2)"


def test_reconcile_panel_agrees_removes() -> None:
    result = _reconcile(_snapshot(_circuit(rating=50.0)))
    assert result.overrides == {}
    assert result.changes == (
        OverrideChange(CIRCUIT_ID, "panel_agrees", OVERRIDE_A, PANEL_A, 50.0, SERVICE_A, True),
    )


def test_reconcile_panel_changed_keeps_and_reconfirms() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0)))
    assert result.overrides[CIRCUIT_ID].panel_a_at_set == 30.0
    assert result.overrides[CIRCUIT_ID].rating_a == OVERRIDE_A
    assert result.changes == (
        OverrideChange(CIRCUIT_ID, "panel_changed", OVERRIDE_A, PANEL_A, 30.0, SERVICE_A, False),
    )


def test_reconcile_rating_undeclared_removes() -> None:
    result = _reconcile(_snapshot(_circuit(rating=None, declared=False)))
    assert result.overrides == {}
    assert result.changes == (
        OverrideChange(CIRCUIT_ID, "rating_undeclared", OVERRIDE_A, PANEL_A, None, SERVICE_A, True),
    )


def test_reconcile_declared_unvalued_keeps_silently() -> None:
    before = _override()
    result = _reconcile(_snapshot(_circuit(rating=None, declared=True)), override=before)
    assert result.changes == ()
    assert result.overrides[CIRCUIT_ID].rating_a == before.rating_a
    assert result.overrides[CIRCUIT_ID].panel_a_at_set == before.panel_a_at_set


def test_reconcile_declaration_not_known_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit(rating=None, declared=None)))
    assert result.changes == ()
    assert CIRCUIT_ID in result.overrides


def test_reconcile_declaration_unknown_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0)), service="unknown")
    assert result.changes == ()
    assert result.overrides[CIRCUIT_ID].panel_a_at_set == PANEL_A


def test_reconcile_service_declared_unvalued_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0), main=None))
    assert result.changes == ()
    assert result.overrides[CIRCUIT_ID].panel_a_at_set == PANEL_A


def test_reconcile_above_service_removes() -> None:
    result = _reconcile(_snapshot(_circuit(), main=40))
    assert result.overrides == {}
    assert result.changes == (
        OverrideChange(
            CIRCUIT_ID, "above_service_rating", OVERRIDE_A, PANEL_A, PANEL_A, 40.0, True
        ),
    )


def test_reconcile_equal_to_service_keeps() -> None:
    result = _reconcile(_snapshot(_circuit(), main=OVERRIDE_A))
    assert result.changes == ()
    assert CIRCUIT_ID in result.overrides


def test_reconcile_service_undeclared_removes_all() -> None:
    overrides = {
        CIRCUIT_ID: _override(),
        "circuit-9": _override(),
    }
    result = reconcile(overrides, _snapshot(_circuit()), "undeclared", _label)
    assert result.overrides == {}
    assert {c.circuit_id for c in result.changes} == set(overrides)
    assert all(c.reason == "service_rating_undeclared" and c.removed for c in result.changes)


def test_reconcile_absent_circuit_removes_with_last_label() -> None:
    result = _reconcile(_snapshot(None))
    assert result.overrides == {}
    assert result.changes == (
        OverrideChange(CIRCUIT_ID, "circuit_removed", OVERRIDE_A, PANEL_A, None, SERVICE_A, True),
    )


def test_reconcile_unknown_declaration_with_no_metadata_deletes_nothing() -> None:
    """A rating replayed after its description while field_metadata is None."""
    result = _reconcile(
        _snapshot(_circuit(rating=None, declared=None), main=None), service="unknown"
    )
    assert result.changes == ()
    assert CIRCUIT_ID in result.overrides
