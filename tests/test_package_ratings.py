"""Tests for the pure rule deciding when a tandem's copied package ratings apply."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import get_args

import pytest
from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.package_ratings import (
    _REFUSALS,
    ChangeReason,
    Declaration,
    MonitoringRating,
    RatingChange,
    Reconciled,
    Refusal,
    Refused,
    StoredPackage,
    admit,
    circuit_package_ratings_form,
    is_change_reason,
    package_ratings_form,
    panel_describes_breakers,
    reconcile,
    resolve,
    service_rating_declaration,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

PANEL_A = 20.0
PACKAGE: tuple[int, int] = (20, 20)
CAPACITY_A = 40.0
SPACE = 7
TWO = (18, 20)
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


def _stored(ratings: tuple[int, int] = PACKAGE, spaces: tuple[int, ...] = (SPACE,)) -> StoredPackage:
    return StoredPackage(ratings_a=ratings, spaces=spaces, label="stored label")


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
    assert _resolve() == MonitoringRating(PANEL_A, PANEL_A, False, None, None)


def test_resolve_judges_a_package_against_both_its_breakers() -> None:
    assert _resolve(stored=_stored()) == MonitoringRating(CAPACITY_A, PANEL_A, True, PACKAGE, "applies")


@pytest.mark.parametrize(("ratings", "capacity"), [((20, 30), 50.0), ((15, 20), 35.0)])
def test_resolve_judges_an_unequal_tandem_against_its_sum(ratings: tuple[int, int], capacity: float) -> None:
    """A 20-30 publishes 20 A and a 15-20 15 A; one space's sensor reads both breakers, so the space carries their sum."""
    circuit = _circuit(rating=float(ratings[0]))
    assert _resolve(circuit, _stored(ratings)) == MonitoringRating(capacity, float(ratings[0]), True, ratings, "applies")


def test_resolve_a_capacity_equal_to_the_service_rating_applies() -> None:
    assert _resolve(stored=_stored((20, 80))).user_set is True


def test_resolve_a_missing_circuit_is_unrated() -> None:
    assert resolve(None, _stored(), SERVICE_A, "declared", describes_breakers=False) == MonitoringRating(None, None, False, None, None)


def test_resolve_a_pending_panel_rating_is_unrated_and_keeps_the_package() -> None:
    assert _resolve(_circuit(rating=None), _stored()) == MonitoringRating(None, None, False, PACKAGE, "pending")


def test_resolve_a_pending_service_rating_is_unrated_and_keeps_the_package() -> None:
    """A space with copied ratings is not judged against the one the panel publishes, even for a moment."""
    pending = MonitoringRating(None, PANEL_A, False, PACKAGE, "pending")
    assert _resolve(stored=_stored(), service_a=None) == pending
    assert _resolve(stored=_stored(), service="unknown") == pending
    assert _resolve(stored=_stored(), service_a=None, service="unknown") == pending


def test_resolve_a_space_not_yet_arrived_is_unrated_and_keeps_the_package() -> None:
    """A circuit always sits in a space, so listing none means the value has not landed, not that the package moved."""
    assert _resolve(_circuit(tabs=()), _stored()) == MonitoringRating(None, PANEL_A, False, PACKAGE, "pending")


def test_resolve_a_pending_service_rating_with_no_package_judges_the_panel_rating() -> None:
    expected = MonitoringRating(PANEL_A, PANEL_A, False, None, None)
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
    assert _resolve(circuit, stored, service_a, service) == MonitoringRating(panel, panel, False, None, None)


def test_resolve_a_two_space_package_is_kept_on_averaged_legs_and_judged_by_the_panel() -> None:
    rating = _resolve(_circuit(tabs=TWO), _stored((20, 30), TWO))
    assert rating == MonitoringRating(PANEL_A, PANEL_A, False, (20, 30), "averaged_legs")


def test_resolve_states_applies_and_pending() -> None:
    assert _resolve(stored=_stored()).state == "applies"
    assert _resolve(_circuit(rating=None), _stored()).state == "pending"
    assert _resolve().state is None


# --- reconcile ---


def test_reconcile_unchanged_keeps_silently_with_the_label_refreshed() -> None:
    result = _reconcile(_snapshot(_circuit(name="Range")))
    assert result.changes == ()
    assert result.packages == {CIRCUIT_ID: StoredPackage(PACKAGE, (SPACE,), "label of Range (circuit-2)")}


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


def test_reconcile_keeps_a_two_space_package_whose_spaces_arrive_in_another_order() -> None:
    result = _reconcile(_snapshot(_circuit(tabs=(20, 18))), stored=_stored((20, 30), TWO))
    assert result.changes == () and result.states == {CIRCUIT_ID: "averaged_legs"}


def test_reconcile_removes_a_two_space_package_whose_panel_rating_changed() -> None:
    result = _reconcile(_snapshot(_circuit(rating=30.0, tabs=TWO)), stored=_stored((20, 30), TWO))
    assert result.changes == (_change("panel_rating_changed", after=30.0, ratings=(20, 30)),)


@pytest.mark.parametrize(
    "tabs", [(18,), (18, 22), (18, 20, 22)], ids=["now in one space", "another pair of spaces", "now three spaces"]
)
def test_reconcile_removes_a_two_space_package_whose_spaces_changed(tabs: tuple[int, ...]) -> None:
    """The stored spaces are compared as a whole: one of them still listed is not the package that was copied."""
    result = _reconcile(_snapshot(_circuit(tabs=tabs)), stored=_stored((20, 30), TWO))
    assert result.packages == {}
    assert result.changes == (_change("spaces_changed", ratings=(20, 30)),)


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


def _sharing(snapshot: SpanPanelSnapshot, shared_with: tuple[str, ...] = ("circuit-9",)) -> SpanPanelSnapshot:
    """The same panel with its circuit publishing the list of circuits that share its meter."""
    circuit = replace(snapshot.circuits[CIRCUIT_ID], meter_shared_with=shared_with)
    return replace(snapshot, circuits={CIRCUIT_ID: circuit})


def test_a_panel_that_declares_the_shared_meter_list_describes_its_breakers() -> None:
    assert panel_describes_breakers(_describing(_snapshot(_circuit())))


def test_a_panel_that_declares_no_list_and_names_no_shared_meter_does_not() -> None:
    assert not panel_describes_breakers(_snapshot(_circuit()))


def test_a_circuit_naming_a_shared_meter_describes_the_breakers_whatever_the_flag_says() -> None:
    """Only a panel that describes its breakers names a shared meter, so a group is never judged against a package."""
    assert panel_describes_breakers(_sharing(_snapshot(_circuit())))


def test_a_circuit_publishing_an_empty_shared_meter_list_describes_the_breakers() -> None:
    """An empty list is still the panel declaring it: no other circuit shares this one's meter."""
    assert panel_describes_breakers(_sharing(_snapshot(_circuit()), ()))


@pytest.mark.parametrize("flag", [None, False])
@pytest.mark.parametrize(
    ("shared_with", "describes"),
    [(("circuit-9",), True), ((), True), (None, False)],
)
def test_a_flag_that_is_not_true_leaves_it_to_the_circuits(
    flag: bool | None, shared_with: tuple[str, ...] | None, describes: bool
) -> None:
    """`publishes_shared_meters` None, where the adapter did not say, or False: a circuit publishing the list decides."""
    snapshot = _snapshot(_circuit()) if shared_with is None else _sharing(_snapshot(_circuit()), shared_with)
    assert panel_describes_breakers(replace(snapshot, publishes_shared_meters=flag)) is describes


def _describing(snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
    """The same panel, on a model that declares `meter/shared-with-device-ids`."""
    return replace(snapshot, publishes_shared_meters=True)


def test_resolve_on_a_panel_that_describes_its_breakers_judges_the_panel_rating() -> None:
    """Each circuit there publishes its own rating, so a stored copy never applies."""
    expected = MonitoringRating(PANEL_A, PANEL_A, False, None, None)
    assert _resolve(stored=_stored(), describes_breakers=True) == expected
    assert _resolve(describes_breakers=True) == expected


def test_resolve_on_a_panel_that_describes_its_breakers_still_needs_a_panel_rating() -> None:
    assert _resolve(_circuit(rating=None), _stored(), describes_breakers=True) == MonitoringRating(None, None, False, None, None)


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


# --- admit: ratings a user enters, judged by the same rule ---


def _admit(
    snapshot: SpanPanelSnapshot, ratings: list[int], service: Declaration = "declared", circuit_id: str = CIRCUIT_ID
) -> object:
    return admit(snapshot, circuit_id, ratings, service, _label)


def test_admit_stores_a_package_for_the_circuits_one_space() -> None:
    assert _admit(_snapshot(_circuit()), [20, 30]) == StoredPackage((20, 30), (SPACE,), LIVE_LABEL)


def test_admit_stores_a_two_space_package_with_its_spaces_sorted() -> None:
    assert _admit(_snapshot(_circuit(tabs=(20, 18))), [20, 30]) == StoredPackage((20, 30), (18, 20), LIVE_LABEL)


def test_admit_refuses_a_circuit_across_three_spaces() -> None:
    admission = _admit(_snapshot(_circuit(tabs=(7, 9, 11))), [20, 30])
    assert isinstance(admission, Refused) and admission.reason == "spaces"


@pytest.mark.parametrize(
    ("snapshot", "ratings"),
    [
        pytest.param(_snapshot(_circuit()), [20], id="the panel's rating alone"),
        pytest.param(_snapshot(_circuit(rating=None)), [25], id="no panel rating to contradict it"),
        pytest.param(_snapshot(_circuit(rating=None, declared=False)), [25], id="no panel rating declared"),
        pytest.param(_snapshot(_circuit(), main=None), [20], id="service rating not arrived"),
        pytest.param(_snapshot(_circuit(tabs=())), [20], id="space not arrived"),
        pytest.param(_describing(_snapshot(_circuit())), [20], id="a panel that describes its breakers"),
    ],
)
def test_admit_reads_a_single_rating_as_the_clear_wherever_the_circuit_is_on_the_panel(
    snapshot: SpanPanelSnapshot, ratings: list[int]
) -> None:
    assert _admit(snapshot, ratings) == "clear"


@pytest.mark.parametrize(
    ("snapshot", "ratings", "service", "reason"),
    [
        pytest.param(_snapshot(_circuit()), [20, 30], "declared", "circuit_unknown", id="no such circuit"),
        pytest.param(_snapshot(_circuit()), [25], "declared", "first_not_panel", id="a single rating not the panel's"),
        pytest.param(_snapshot(_circuit()), [], "declared", "not_two", id="no ratings"),
        pytest.param(_snapshot(_circuit()), [20, 20, 20], "declared", "not_two", id="three ratings"),
        pytest.param(_describing(_snapshot(_circuit())), [20, 30], "declared", "panel_describes_breakers", id="describes"),
        pytest.param(_snapshot(_circuit()), [20, 30], "undeclared", "no_service_rating", id="no service rating"),
        pytest.param(_snapshot(_circuit(rating=None, declared=False)), [20, 30], "declared", "not_published", id="no rating"),
        pytest.param(_snapshot(_circuit(rating=None)), [20, 30], "declared", "not_arrived", id="rating not arrived"),
        pytest.param(_snapshot(_circuit(), main=None), [20, 30], "declared", "not_arrived", id="service not arrived"),
        pytest.param(_snapshot(_circuit()), [20, 30], "unknown", "not_arrived", id="service declaration not known"),
        pytest.param(_snapshot(_circuit(tabs=())), [20, 30], "declared", "not_arrived", id="space not arrived"),
        pytest.param(_snapshot(_circuit(), main=40), [20, 30], "declared", "above_service_rating", id="above service"),
        pytest.param(_snapshot(_circuit()), [30, 20], "declared", "first_not_panel", id="first not the panel's"),
        pytest.param(_snapshot(_circuit(tabs=(7, 9, 11))), [20, 30], "declared", "spaces", id="three spaces"),
        pytest.param(_snapshot(_circuit(tabs=TWO), main=40), [20, 30], "declared", "above_service_rating", id="two spaces above service"),
        pytest.param(_snapshot(_circuit(tabs=TWO)), [30, 20], "declared", "first_not_panel", id="two spaces, first not the panel's"),
    ],
)
def test_admit_refuses_by_the_rule_each_with_its_reason(
    snapshot: SpanPanelSnapshot, ratings: list[int], service: Declaration, reason: str
) -> None:
    circuit_id = "circuit-9" if reason == "circuit_unknown" else CIRCUIT_ID
    admission = _admit(snapshot, ratings, service, circuit_id)
    assert isinstance(admission, Refused)
    assert admission.reason == reason


def test_admit_names_the_panels_rating_and_the_service_rating_its_refusals_quote() -> None:
    assert _admit(_snapshot(_circuit()), [30, 20]) == Refused("first_not_panel", PANEL_A, SERVICE_A)
    assert _admit(_snapshot(_circuit(), main=40), [20, 30]) == Refused("above_service_rating", PANEL_A, 40.0)


@pytest.mark.parametrize(
    ("snapshot", "ratings", "reason"),
    [
        pytest.param(_describing(_snapshot(_circuit(rating=None, tabs=(7, 9, 11)))), [20, 20, 20], "panel_describes_breakers", id="describes before all"),
        pytest.param(_snapshot(_circuit(rating=None, tabs=(7, 9, 11))), [30, 30, 30], "not_arrived", id="not arrived before spaces"),
        pytest.param(_snapshot(_circuit(rating=None, declared=False, tabs=(7, 9, 11))), [30, 30], "not_published", id="not published before spaces"),
        pytest.param(_snapshot(_circuit(tabs=(7, 9, 11))), [30, 30, 30], "spaces", id="spaces before the count"),
        pytest.param(_snapshot(_circuit(tabs=(7, 9, 11))), [30, 30], "spaces", id="spaces before the first rating"),
        pytest.param(_snapshot(_circuit(tabs=(7, 9, 11))), [50, 60], "spaces", id="spaces before the service rating"),
        pytest.param(_snapshot(_circuit()), [30, 30, 30], "not_two", id="the count before the first rating"),
        pytest.param(_snapshot(_circuit(), main=40), [30, 30], "first_not_panel", id="the first rating before the service rating"),
    ],
)
def test_admit_refuses_by_one_precedence(snapshot: SpanPanelSnapshot, ratings: list[int], reason: str) -> None:
    """Unknown circuit, then a panel that describes its breakers, then a rating not published or not arrived, then the
    spaces, the count, the first rating and the service rating: the first that holds is the refusal."""
    admission = _admit(snapshot, ratings)
    assert isinstance(admission, Refused)
    assert admission.reason == reason


# --- package_ratings_form: where a copy can be entered ---


@pytest.mark.parametrize(("tabs", "form"), [((7,), "one_space"), ((20, 18), "two_spaces"), ((), None), ((7, 9, 11), None)])
def test_package_ratings_form_by_spaces(tabs: tuple[int, ...], form: str | None) -> None:
    circuit = _circuit(tabs=tabs)
    assert package_ratings_form(_snapshot(circuit), circuit) == form


def test_package_ratings_form_none_without_a_panel_rating_or_on_a_panel_that_describes_breakers() -> None:
    assert package_ratings_form(_snapshot(_circuit(rating=None)), _circuit(rating=None)) is None
    assert package_ratings_form(_describing(_snapshot(_circuit())), _circuit()) is None


def test_package_ratings_form_none_for_a_meter_outside_the_panel() -> None:
    """A meter outside the panel has no breaker, so there is no package to copy whatever it lists."""
    circuit = replace(_circuit(), measures_outside_panel=True)
    assert package_ratings_form(_snapshot(circuit), circuit) is None


@pytest.mark.parametrize(
    "circuit",
    [
        pytest.param(_circuit(), id="one space"),
        pytest.param(_circuit(tabs=TWO), id="two spaces"),
        pytest.param(_circuit(rating=None), id="no rating"),
        pytest.param(_circuit(tabs=()), id="no space"),
        pytest.param(replace(_circuit(), measures_outside_panel=True), id="outside the panel"),
    ],
)
def test_circuit_package_ratings_form_is_the_rule_for_the_panel_answered_once(circuit: SpanCircuitSnapshot) -> None:
    """Given whether the panel describes its breakers, a circuit's form is the one `package_ratings_form` gives.

    A caller that asks for every circuit asks the panel once; on a panel that
    describes its breakers no circuit takes a copy.
    """
    for snapshot in (_snapshot(circuit), _describing(_snapshot(circuit))):
        describes = panel_describes_breakers(snapshot)
        assert circuit_package_ratings_form(circuit, describes_breakers=describes) == package_ratings_form(snapshot, circuit)
    assert circuit_package_ratings_form(circuit, describes_breakers=True) is None


def test_every_reason_the_rule_removes_an_entry_for_is_a_refusal_for_entered_ratings() -> None:
    """A verdict with no refusal would raise a KeyError where an entry should have been refused."""
    assert set(_REFUSALS) == {*get_args(ChangeReason.__value__), "pending"}
    assert set(_REFUSALS.values()) <= set(get_args(Refusal.__value__))


def test_every_refusal_has_its_own_message_and_every_message_a_refusal() -> None:
    strings = json.loads((Path(__file__).parent.parent / "custom_components/span_panel/strings.json").read_text(encoding="utf-8"))
    keys = {key for key in strings["exceptions"] if key.startswith("package_ratings_")}
    # Raised by the services, before any rule is asked: no panel to judge by.
    assert keys - {"package_ratings_panel_not_loaded"} == {f"package_ratings_{reason}" for reason in get_args(Refusal.__value__)}
