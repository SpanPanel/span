"""When a user's monitoring rating for a circuit applies.

The panel publishes a rating for each breaker, and a user may know better: a
tandem breaker is published at the rating of one half. The user's value is a
monitoring rating, the rating current monitoring judges the circuit against,
stored by the integration. This module is the one rule that says whether it
applies, so every reader of a rating agrees. First match wins:

1. No monitoring rating: the panel's rating.
2. The panel describes its multi-load breakers (`panel_describes_breakers`),
   or declares no service rating, or the circuit declares no rating, or the
   circuit has left a complete snapshot: the monitoring rating is deleted and
   the user told.
3. The circuit's rating, or the service rating, is declared but has no value
   yet, or whether it is declared is not known: the monitoring rating is kept,
   silently, and the circuit has no judged rating, so nothing shows it as
   user-set and monitoring skips it. It applies again when the value arrives.
4. The monitoring rating is above the service rating, or equals the panel's rating:
   deleted and the user told. When the panel has caught up, a later change by
   the panel should show through rather than be masked.
5. Otherwise the monitoring rating applies. When the panel's rating differs from the
   one the monitoring rating was set against, it is kept and the change is
   reported once, then that rating becomes the confirmed value.

"Not declared" differs from "not arrived": a rating's retained value can land
after its description, after a restart, a reconnect or a bridge rebuild. Read
as "not published", that moment would delete a monitoring rating and tell the user
about a change that never happened. Only a declaration the panel has made
(`breaker_rating_declared is False`, a service rating no device declares)
counts as absence; a value not yet arrived, or a declaration not yet known
(`None`, no field metadata), changes nothing.

A leaf: it imports only the standard library and `span_panel_api`. The caller
supplies the circuit label, so the rule need not know how labels are built.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal, TypeIs, get_args

from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

type Declaration = Literal["declared", "undeclared", "unknown"]
"""Whether the panel declares a property: it does, it does not, or that is not known yet."""

type ChangeReason = Literal[
    "panel_changed",
    "panel_agrees",
    "rating_undeclared",
    "circuit_removed",
    "above_service_rating",
    "service_rating_undeclared",
    "panel_describes_breakers",
]
"""Why the user is told about a monitoring rating: kept under a new panel rating, or removed."""

_CHANGE_REASONS: Final = frozenset(
    reason for reason in get_args(ChangeReason.__value__) if isinstance(reason, str)
)
"""Every `ChangeReason`, read off the alias so the two cannot drift apart."""

_SERVICE_RATING_PATHS = ("panel.main_breaker_rating_a", "panel.upstream_protection_rating_a")


def is_change_reason(value: object) -> TypeIs[ChangeReason]:
    """Whether a value read back from storage is a `ChangeReason`."""
    return value in _CHANGE_REASONS


@dataclass(frozen=True, slots=True)
class StoredRating:
    """A user's monitoring rating as stored: the rating, the panel rating it was set against, and the circuit's last label."""

    rating_a: int
    panel_a_at_set: float
    label: str


@dataclass(frozen=True, slots=True)
class MonitoringRating:
    """A circuit's rating as readers see it, with the rating the panel published."""

    judged_a: float | None
    panel_a: float | None
    user_set: bool


@dataclass(frozen=True, slots=True)
class RatingChange:
    """Something the user is to be told about a monitoring rating: kept with a new panel rating, or removed."""

    circuit_id: str
    label: str
    """The circuit's label, carried so the notice reads the same after the monitoring rating is gone."""
    reason: ChangeReason
    rating_a: int
    panel_before_a: float
    panel_after_a: float | None
    service_a: float | None
    removed: bool


@dataclass(frozen=True, slots=True)
class Reconciled:
    """The ratings that remain after a snapshot, and what to tell the user."""

    ratings: dict[str, StoredRating]
    changes: tuple[RatingChange, ...]


def service_rating_a(snapshot: SpanPanelSnapshot) -> float | None:
    """Return the rating the mains legs, and any monitoring rating, are judged against.

    The main breaker where the panel has one; otherwise the rating of the
    protection ahead of its upstream lugs, which is what limits a panel with no
    main breaker of its own. None where the panel reports neither.
    """
    if snapshot.main_breaker_rating_a:
        return float(snapshot.main_breaker_rating_a)
    if snapshot.upstream_protection_rating_a:
        return float(snapshot.upstream_protection_rating_a)
    return None


def panel_describes_breakers(snapshot: SpanPanelSnapshot) -> bool:
    """Whether the panel describes its multi-load breakers, so no monitoring rating is offered.

    A panel that declares `meter/shared-with-device-ids` on any circuit, as panels
    other than MAIN 32 do on every circuit, publishes each circuit
    of a multi-load breaker as its own device with its own commissioned
    rating, and names the circuits that share its meter. Current monitoring
    then judges the shared meter against those ratings without the user's
    help. A monitoring rating exists for the panel that cannot say any of
    that: MAIN 32 declares neither list and publishes a tandem as one circuit
    with the rating of one half.

    The declaration decides, not a value, so the answer is the same on the
    first complete snapshot as on every later one.
    """
    return snapshot.publishes_shared_meters


def service_rating_declaration(field_metadata: Mapping[str, FieldMetadata] | None) -> Declaration:
    """Whether the panel declares a service rating, from the adapter's field metadata.

    "unknown" without metadata. Otherwise "declared" when either service rating
    field has a resolved entry, whether or not a value has arrived.
    """
    if field_metadata is None:
        return "unknown"
    for path in _SERVICE_RATING_PATHS:
        row = field_metadata.get(path)
        if row is not None and row.resolved:
            return "declared"
    return "undeclared"


def resolve(
    circuit: SpanCircuitSnapshot | None,
    stored: StoredRating | None,
    service_a: float | None,
    service: Declaration,
    *,
    describes_breakers: bool,
) -> MonitoringRating:
    """Return the rating a reader should use for one circuit.

    A circuit with a monitoring rating whose service rating is pending, declared with no value
    yet or with its declaration not known, has no judged rating: the
    monitoring rating cannot be checked against its bound, and the panel's rating is
    the one the user set a monitoring rating against. Judging a tandem breaker against it even for a
    moment raises the false alert the monitoring rating exists to stop. The panel's
    rating is still reported, since it is known.

    On a panel that describes its multi-load breakers (`describes_breakers`,
    from `panel_describes_breakers`) a stored monitoring rating never applies:
    it is being deleted, and the panel's own rating is what the circuit is
    judged against.
    """
    if circuit is None:
        return MonitoringRating(None, None, False)
    panel = circuit.breaker_rating_a
    if panel is None:
        return MonitoringRating(None, None, False)
    if stored is None or describes_breakers or service == "undeclared":
        return MonitoringRating(panel, panel, False)
    if service == "unknown" or service_a is None:
        return MonitoringRating(None, panel, False)
    if stored.rating_a <= service_a and stored.rating_a != panel:
        return MonitoringRating(float(stored.rating_a), panel, True)
    return MonitoringRating(panel, panel, False)


def _change(
    circuit_id: str,
    stored: StoredRating,
    reason: ChangeReason,
    panel_after_a: float | None,
    service_a: float | None,
    *,
    removed: bool,
) -> RatingChange:
    return RatingChange(
        circuit_id,
        stored.label,
        reason,
        stored.rating_a,
        stored.panel_a_at_set,
        panel_after_a,
        service_a,
        removed,
    )


def reconcile(
    ratings: Mapping[str, StoredRating],
    snapshot: SpanPanelSnapshot,
    service: Declaration,
    label_of: Callable[[SpanCircuitSnapshot, str], str],
) -> Reconciled:
    """Apply the rule to every stored monitoring rating against one complete snapshot.

    A snapshot holding a circuit is complete by construction, so a circuit
    absent from it has left the panel. Returns the ratings to keep, with
    labels refreshed and a changed panel rating confirmed, and a change for
    each monitoring rating removed or kept under a new panel rating.
    """
    service_a = service_rating_a(snapshot)
    describes_breakers = panel_describes_breakers(snapshot)
    kept: dict[str, StoredRating] = {}
    changes: list[RatingChange] = []

    for circuit_id, stored in ratings.items():
        circuit = snapshot.circuits.get(circuit_id)
        panel = circuit.breaker_rating_a if circuit is not None else None
        if circuit is not None:
            stored = replace(stored, label=label_of(circuit, circuit_id))

        # First, since it holds panel-wide whatever else the snapshot says.
        if describes_breakers:
            changes.append(
                _change(
                    circuit_id,
                    stored,
                    "panel_describes_breakers",
                    panel,
                    service_a,
                    removed=True,
                )
            )
            continue
        if service == "undeclared":
            changes.append(
                _change(
                    circuit_id,
                    stored,
                    "service_rating_undeclared",
                    panel,
                    service_a,
                    removed=True,
                )
            )
            continue
        if circuit is None:
            changes.append(
                _change(circuit_id, stored, "circuit_removed", panel, service_a, removed=True)
            )
            continue
        if circuit.breaker_rating_declared is False:
            changes.append(
                _change(circuit_id, stored, "rating_undeclared", panel, service_a, removed=True)
            )
            continue
        if panel is None or service == "unknown" or service_a is None:
            kept[circuit_id] = stored
            continue
        if stored.rating_a > service_a:
            changes.append(
                _change(circuit_id, stored, "above_service_rating", panel, service_a, removed=True)
            )
            continue
        if stored.rating_a == panel:
            changes.append(
                _change(circuit_id, stored, "panel_agrees", panel, service_a, removed=True)
            )
            continue
        if panel != stored.panel_a_at_set:
            changes.append(
                _change(circuit_id, stored, "panel_changed", panel, service_a, removed=False)
            )
            stored = replace(stored, panel_a_at_set=panel)
        kept[circuit_id] = stored

    return Reconciled(kept, tuple(changes))
