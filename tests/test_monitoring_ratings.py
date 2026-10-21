"""Tests for the pure rule deciding when a monitoring rating applies."""

from __future__ import annotations

from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.monitoring_ratings import (
    Declaration,
    MonitoringRating,
    RatingChange,
    Reconciled,
    StoredRating,
    reconcile,
    resolve,
    service_rating_declaration,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

PANEL_A = 20.0
MONITORING_A = 50
SERVICE_A = 100.0
CIRCUIT_ID = "circuit-2"
LIVE_LABEL = "label of Oven (circuit-2)"


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


def _stored(rating: int = MONITORING_A, at_set: float = PANEL_A) -> StoredRating:
    return StoredRating(rating_a=rating, panel_a_at_set=at_set, label="stored label")


def _reconcile(
    snapshot: SpanPanelSnapshot,
    service: Declaration = "declared",
    stored: StoredRating | None = None,
) -> Reconciled:
    return reconcile({CIRCUIT_ID: stored or _stored()}, snapshot, service, _label)


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


def test_resolve_no_monitoring_rating_shows_panel() -> None:
    assert resolve(_circuit(), None, SERVICE_A, "declared") == MonitoringRating(
        PANEL_A, PANEL_A, False
    )


def test_resolve_monitoring_rating_applies() -> None:
    assert resolve(_circuit(), _stored(), SERVICE_A, "declared") == MonitoringRating(
        float(MONITORING_A), PANEL_A, True
    )


def test_resolve_monitoring_rating_equal_to_service_applies() -> None:
    assert resolve(_circuit(), _stored(100), SERVICE_A, "declared").user_set is True


def test_resolve_missing_circuit_is_unrated() -> None:
    assert resolve(None, _stored(), SERVICE_A, "declared") == MonitoringRating(None, None, False)


def test_resolve_pending_panel_rating_is_unrated() -> None:
    assert resolve(_circuit(rating=None), _stored(), SERVICE_A, "declared") == MonitoringRating(
        None, None, False
    )


def test_resolve_pending_service_rating_is_unrated() -> None:
    """A corrected circuit is not judged against the rating the user corrected, even for a moment.

    The panel's own rating is still reported beside it: it is the wire value, and
    known.
    """
    pending = MonitoringRating(None, PANEL_A, False)
    assert resolve(_circuit(), _stored(), None, "declared") == pending
    assert resolve(_circuit(), _stored(), SERVICE_A, "unknown") == pending
    assert resolve(_circuit(), _stored(), None, "unknown") == pending


def test_resolve_pending_service_rating_without_a_monitoring_rating_shows_panel() -> None:
    expected = MonitoringRating(PANEL_A, PANEL_A, False)
    assert resolve(_circuit(), None, None, "declared") == expected
    assert resolve(_circuit(), None, SERVICE_A, "unknown") == expected


def test_resolve_undeclared_service_rating_shows_panel() -> None:
    """No service rating published: the stored is being deleted, and the panel's rating applies."""
    expected = MonitoringRating(PANEL_A, PANEL_A, False)
    assert resolve(_circuit(), _stored(), None, "undeclared") == expected
    assert resolve(_circuit(), _stored(), SERVICE_A, "undeclared") == expected


def test_resolve_monitoring_rating_above_service_shows_panel() -> None:
    assert resolve(_circuit(), _stored(101), SERVICE_A, "declared") == MonitoringRating(
        PANEL_A, PANEL_A, False
    )


def test_resolve_monitoring_rating_equal_to_panel_shows_panel() -> None:
    assert resolve(_circuit(), _stored(20), SERVICE_A, "declared") == MonitoringRating(
        PANEL_A, PANEL_A, False
    )


# --- reconcile ---


def test_reconcile_unchanged_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit()))
    assert result.changes == ()
    assert result.ratings == {
        CIRCUIT_ID: StoredRating(MONITORING_A, PANEL_A, _label(_circuit(), CIRCUIT_ID))
    }


def test_reconcile_refreshes_label_silently() -> None:
    result = _reconcile(_snapshot(_circuit(name="Range")))
    assert result.changes == ()
    assert result.ratings[CIRCUIT_ID].label == "label of Range (circuit-2)"


def test_reconcile_panel_agrees_removes() -> None:
    result = _reconcile(_snapshot(_circuit(rating=50.0)))
    assert result.ratings == {}
    assert result.changes == (
        RatingChange(
            CIRCUIT_ID, LIVE_LABEL, "panel_agrees", MONITORING_A, PANEL_A, 50.0, SERVICE_A, True
        ),
    )


def test_reconcile_panel_changed_keeps_and_reconfirms() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0)))
    assert result.ratings[CIRCUIT_ID].panel_a_at_set == 30.0
    assert result.ratings[CIRCUIT_ID].rating_a == MONITORING_A
    assert result.changes == (
        RatingChange(
            CIRCUIT_ID, LIVE_LABEL, "panel_changed", MONITORING_A, PANEL_A, 30.0, SERVICE_A, False
        ),
    )


def test_reconcile_rating_undeclared_removes() -> None:
    result = _reconcile(_snapshot(_circuit(rating=None, declared=False)))
    assert result.ratings == {}
    assert result.changes == (
        RatingChange(
            CIRCUIT_ID,
            LIVE_LABEL,
            "rating_undeclared",
            MONITORING_A,
            PANEL_A,
            None,
            SERVICE_A,
            True,
        ),
    )


def test_reconcile_declared_unvalued_keeps_silently() -> None:
    before = _stored()
    result = _reconcile(_snapshot(_circuit(rating=None, declared=True)), stored=before)
    assert result.changes == ()
    assert result.ratings[CIRCUIT_ID].rating_a == before.rating_a
    assert result.ratings[CIRCUIT_ID].panel_a_at_set == before.panel_a_at_set


def test_reconcile_declaration_not_known_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit(rating=None, declared=None)))
    assert result.changes == ()
    assert CIRCUIT_ID in result.ratings


def test_reconcile_declaration_unknown_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0)), service="unknown")
    assert result.changes == ()
    assert result.ratings[CIRCUIT_ID].panel_a_at_set == PANEL_A


def test_reconcile_service_declared_unvalued_keeps_silently() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0), main=None))
    assert result.changes == ()
    assert result.ratings[CIRCUIT_ID].panel_a_at_set == PANEL_A


def test_reconcile_above_service_removes() -> None:
    result = _reconcile(_snapshot(_circuit(), main=40))
    assert result.ratings == {}
    assert result.changes == (
        RatingChange(
            CIRCUIT_ID,
            LIVE_LABEL,
            "above_service_rating",
            MONITORING_A,
            PANEL_A,
            PANEL_A,
            40.0,
            True,
        ),
    )


def test_reconcile_equal_to_service_keeps() -> None:
    result = _reconcile(_snapshot(_circuit(), main=MONITORING_A))
    assert result.changes == ()
    assert CIRCUIT_ID in result.ratings


def test_reconcile_service_undeclared_removes_all() -> None:
    ratings = {
        CIRCUIT_ID: _stored(),
        "circuit-9": _stored(),
    }
    result = reconcile(ratings, _snapshot(_circuit()), "undeclared", _label)
    assert result.ratings == {}
    assert {c.circuit_id for c in result.changes} == set(ratings)
    assert all(c.reason == "service_rating_undeclared" and c.removed for c in result.changes)
    labels = {c.circuit_id: c.label for c in result.changes}
    assert labels == {CIRCUIT_ID: LIVE_LABEL, "circuit-9": "stored label"}


def test_reconcile_absent_circuit_removes_with_last_label() -> None:
    result = _reconcile(_snapshot(None))
    assert result.ratings == {}
    assert result.changes == (
        RatingChange(
            CIRCUIT_ID,
            "stored label",
            "circuit_removed",
            MONITORING_A,
            PANEL_A,
            None,
            SERVICE_A,
            True,
        ),
    )


def test_reconcile_unknown_declaration_with_no_metadata_deletes_nothing() -> None:
    """A rating replayed after its description while field_metadata is None."""
    result = _reconcile(
        _snapshot(_circuit(rating=None, declared=None), main=None), service="unknown"
    )
    assert result.changes == ()
    assert CIRCUIT_ID in result.ratings
