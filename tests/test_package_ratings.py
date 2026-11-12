"""Tests for the pure rule deciding when a tandem's copied package ratings apply."""

from __future__ import annotations

from dataclasses import replace

import pytest
from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.package_ratings import (
    Declaration,
    MonitoringRating,
    RatingChange,
    Reconciled,
    StoredPackage,
    is_change_reason,
    reconcile,
    resolve,
    service_rating_declaration,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

PANEL_A = 20.0
PACKAGE: tuple[int, int] = (20, 20)
CAPACITY_A = 40.0
SPACE = 7
SERVICE_A = 100.0
CIRCUIT_ID = "circuit-2"
LIVE_LABEL = "label of Oven (circuit-2)"


def _label(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    return f"label of {circuit.name} ({circuit_id})"


def _circuit(
    rating: float | None = PANEL_A,
    declared: bool | None = True,
    name: str = "Oven",
    tabs: tuple[int, ...] = (SPACE,),
) -> SpanCircuitSnapshot:
    return SpanCircuitSnapshotFactory.create(
        circuit_id=CIRCUIT_ID,
        name=name,
        tabs=list(tabs),
        breaker_rating_a=rating,
        breaker_rating_declared=declared,
    )


def _snapshot(circuit: SpanCircuitSnapshot | None, main: int | None = 100) -> SpanPanelSnapshot:
    circuits = {CIRCUIT_ID: circuit} if circuit is not None else {}
    return SpanPanelSnapshotFactory.create(circuits=circuits, main_breaker_rating_a=main)


def _stored(ratings: tuple[int, int] = PACKAGE, space: int = SPACE) -> StoredPackage:
    return StoredPackage(ratings_a=ratings, space=space, label="stored label")


def _reconcile(
    snapshot: SpanPanelSnapshot,
    service: Declaration = "declared",
    stored: StoredPackage | None = None,
) -> Reconciled:
    return reconcile({CIRCUIT_ID: stored or _stored()}, snapshot, service, _label)


def _change(
    reason: str,
    *,
    label: str = LIVE_LABEL,
    after: float | None = PANEL_A,
    service: float | None = SERVICE_A,
    ratings: tuple[int, int] = PACKAGE,
) -> RatingChange:
    assert is_change_reason(reason)
    return RatingChange(CIRCUIT_ID, label, reason, ratings, after, service)


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


def _resolve(
    circuit: SpanCircuitSnapshot | None = None,
    stored: StoredPackage | None = None,
    service_a: float | None = SERVICE_A,
    service: Declaration = "declared",
    *,
    describes_breakers: bool = False,
) -> MonitoringRating:
    return resolve(
        _circuit() if circuit is None else circuit, stored, service_a, service, describes_breakers=describes_breakers
    )


def test_resolve_with_no_package_judges_the_panel_rating() -> None:
    assert _resolve() == MonitoringRating(PANEL_A, PANEL_A, False, None)


def test_resolve_judges_a_package_against_both_its_breakers() -> None:
    assert _resolve(stored=_stored()) == MonitoringRating(CAPACITY_A, PANEL_A, True, PACKAGE)


@pytest.mark.parametrize(("ratings", "capacity"), [((20, 30), 50.0), ((15, 20), 35.0)])
def test_resolve_judges_an_unequal_tandem_against_its_sum(ratings: tuple[int, int], capacity: float) -> None:
    """A 20-30 publishes 20 A and a 15-20 15 A; one space's sensor reads both breakers, so the space carries their sum."""
    circuit = _circuit(rating=float(ratings[0]))
    assert _resolve(circuit, _stored(ratings)) == MonitoringRating(capacity, float(ratings[0]), True, ratings)


def test_resolve_a_capacity_equal_to_the_service_rating_applies() -> None:
    assert _resolve(stored=_stored((20, 80))).user_set is True


def test_resolve_a_missing_circuit_is_unrated() -> None:
    assert resolve(None, _stored(), SERVICE_A, "declared", describes_breakers=False) == MonitoringRating(None, None, False, None)


def test_resolve_a_pending_panel_rating_is_unrated_and_keeps_the_package() -> None:
    assert _resolve(_circuit(rating=None), _stored()) == MonitoringRating(None, None, False, PACKAGE)


def test_resolve_a_pending_service_rating_is_unrated_and_keeps_the_package() -> None:
    """A space with copied ratings is not judged against the one the panel publishes, even for a moment."""
    pending = MonitoringRating(None, PANEL_A, False, PACKAGE)
    assert _resolve(stored=_stored(), service_a=None) == pending
    assert _resolve(stored=_stored(), service="unknown") == pending
    assert _resolve(stored=_stored(), service_a=None, service="unknown") == pending


def test_resolve_a_space_not_yet_arrived_is_unrated_and_keeps_the_package() -> None:
    """A circuit always sits in a space, so listing none means the value has not landed, not that the package moved."""
    assert _resolve(_circuit(tabs=()), _stored()) == MonitoringRating(None, PANEL_A, False, PACKAGE)


def test_resolve_a_pending_service_rating_with_no_package_judges_the_panel_rating() -> None:
    expected = MonitoringRating(PANEL_A, PANEL_A, False, None)
    assert _resolve(service_a=None) == expected
    assert _resolve(service="unknown") == expected


@pytest.mark.parametrize(
    ("circuit", "stored", "service_a", "service"),
    [
        pytest.param(_circuit(), _stored(), SERVICE_A, "undeclared", id="no service rating published"),
        pytest.param(_circuit(), _stored((20, 90)), SERVICE_A, "declared", id="capacity above the service rating"),
        pytest.param(_circuit(rating=30.0), _stored(), SERVICE_A, "declared", id="the panel's rating changed"),
        pytest.param(_circuit(tabs=(9,)), _stored(), SERVICE_A, "declared", id="moved to another space"),
        pytest.param(_circuit(tabs=(7, 9)), _stored(), SERVICE_A, "declared", id="now lists two spaces"),
    ],
)
def test_resolve_a_package_that_no_longer_holds_judges_the_panel_rating(
    circuit: SpanCircuitSnapshot, stored: StoredPackage, service_a: float, service: Declaration
) -> None:
    """Each is being deleted, so the circuit is judged against what the panel publishes."""
    panel = circuit.breaker_rating_a
    assert _resolve(circuit, stored, service_a, service) == MonitoringRating(panel, panel, False, None)


# --- reconcile ---


def test_reconcile_unchanged_keeps_silently_with_the_label_refreshed() -> None:
    result = _reconcile(_snapshot(_circuit(name="Range")))
    assert result.changes == ()
    assert result.packages == {CIRCUIT_ID: StoredPackage(PACKAGE, SPACE, "label of Range (circuit-2)")}


def test_reconcile_a_changed_panel_rating_removes_the_copy() -> None:
    """The panel was re-commissioned, so what was copied against its old rating is stale."""
    result = _reconcile(_snapshot(_circuit(rating=30.0)))
    assert result.packages == {}
    assert result.changes == (_change("panel_rating_changed", after=30.0),)


@pytest.mark.parametrize("tabs", [(9,), (7, 9)], ids=["moved to another space", "now lists two spaces"])
def test_reconcile_changed_spaces_remove_the_copy(tabs: tuple[int, ...]) -> None:
    """The circuit no longer holds the package that was copied, whatever its id and first rating."""
    result = _reconcile(_snapshot(_circuit(tabs=tabs)))
    assert result.packages == {}
    assert result.changes == (_change("spaces_changed"),)


def test_reconcile_an_undeclared_rating_removes_the_copy() -> None:
    result = _reconcile(_snapshot(_circuit(rating=None, declared=False)))
    assert result.packages == {}
    assert result.changes == (_change("rating_undeclared", after=None),)


@pytest.mark.parametrize(
    ("snapshot", "service"),
    [
        pytest.param(_snapshot(_circuit(rating=None, declared=True)), "declared", id="rating declared, no value yet"),
        pytest.param(_snapshot(_circuit(rating=None, declared=None)), "declared", id="rating declaration not known"),
        pytest.param(_snapshot(_circuit(rating=30.0)), "unknown", id="service declaration not known"),
        pytest.param(_snapshot(_circuit(rating=30.0), main=None), "declared", id="service rating declared, no value yet"),
        pytest.param(_snapshot(_circuit(rating=None, declared=None), main=None), "unknown", id="replayed with no metadata"),
        pytest.param(_snapshot(_circuit(tabs=())), "declared", id="space not yet arrived"),
        pytest.param(_snapshot(_circuit(rating=30.0, tabs=())), "declared", id="space not yet arrived, rating changed"),
    ],
)
def test_reconcile_keeps_a_pending_copy_silently(snapshot: SpanPanelSnapshot, service: Declaration) -> None:
    result = _reconcile(snapshot, service)
    assert result.changes == ()
    assert result.packages[CIRCUIT_ID].ratings_a == PACKAGE


def test_reconcile_a_capacity_above_the_service_rating_removes_the_copy() -> None:
    result = _reconcile(_snapshot(_circuit(), main=30))
    assert result.packages == {}
    assert result.changes == (_change("above_service_rating", service=30.0),)


def test_reconcile_a_capacity_equal_to_the_service_rating_keeps_the_copy() -> None:
    result = _reconcile(_snapshot(_circuit(), main=40))
    assert result.changes == ()
    assert CIRCUIT_ID in result.packages


def test_reconcile_an_undeclared_service_rating_removes_every_copy() -> None:
    packages = {CIRCUIT_ID: _stored(), "circuit-9": _stored()}
    result = reconcile(packages, _snapshot(_circuit()), "undeclared", _label)
    assert result.packages == {}
    assert {c.circuit_id for c in result.changes} == set(packages)
    assert all(c.reason == "service_rating_undeclared" for c in result.changes)
    assert {c.circuit_id: c.label for c in result.changes} == {CIRCUIT_ID: LIVE_LABEL, "circuit-9": "stored label"}


def test_reconcile_an_absent_circuit_removes_the_copy_with_its_last_label() -> None:
    result = _reconcile(_snapshot(None))
    assert result.packages == {}
    assert result.changes == (_change("circuit_removed", label="stored label", after=None),)


def test_reconcile_gives_a_capacity_above_the_service_rating_before_a_changed_panel_rating() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0), main=30))
    assert [change.reason for change in result.changes] == ["above_service_rating"]


def test_reconcile_gives_a_changed_panel_rating_before_changed_spaces() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0, tabs=(9,))))
    assert [change.reason for change in result.changes] == ["panel_rating_changed"]


# --- a panel that describes its multi-load breakers ---


def _describing(snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
    """The same panel, on a model that declares `meter/shared-with-device-ids`."""
    return replace(snapshot, publishes_shared_meters=True)


def test_resolve_on_a_panel_that_describes_its_breakers_judges_the_panel_rating() -> None:
    """Each circuit there publishes its own rating, so a stored copy never applies."""
    expected = MonitoringRating(PANEL_A, PANEL_A, False, None)
    assert _resolve(stored=_stored(), describes_breakers=True) == expected
    assert _resolve(describes_breakers=True) == expected


def test_resolve_on_a_panel_that_describes_its_breakers_still_needs_a_panel_rating() -> None:
    assert _resolve(_circuit(rating=None), _stored(), describes_breakers=True) == MonitoringRating(None, None, False, None)


def test_reconcile_on_a_panel_that_describes_its_breakers_removes_every_copy() -> None:
    packages = {CIRCUIT_ID: _stored(), "circuit-9": _stored()}
    result = reconcile(packages, _describing(_snapshot(_circuit())), "declared", _label)
    assert result.packages == {}
    assert result.changes == (
        _change("panel_describes_breakers"),
        RatingChange("circuit-9", "stored label", "panel_describes_breakers", PACKAGE, None, SERVICE_A),
    )


def test_reconcile_gives_the_described_breakers_as_the_reason_before_any_other() -> None:
    """Whatever else the panel stopped publishing, the copy is gone because the panel describes its breakers."""
    snapshot = _describing(_snapshot(_circuit(rating=None, declared=False)))
    for service in ("declared", "undeclared", "unknown"):
        result = reconcile({CIRCUIT_ID: _stored()}, snapshot, service, _label)
        assert [change.reason for change in result.changes] == ["panel_describes_breakers"]
        assert result.packages == {}


def test_every_reason_reads_back_from_storage_and_retired_ones_do_not() -> None:
    for reason in (
        "panel_rating_changed",
        "spaces_changed",
        "rating_undeclared",
        "circuit_removed",
        "above_service_rating",
        "service_rating_undeclared",
        "panel_describes_breakers",
    ):
        assert is_change_reason(reason)
    assert not is_change_reason("panel_changed")
    assert not is_change_reason("panel_agrees")
