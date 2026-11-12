"""When a tandem's package ratings, copied from the SPAN app, apply.

A MAIN 32 panel publishes a tandem breaker as one circuit with the rating of
its first breaker, while the one space's sensor reads both. SPAN's cloud holds
the package's ratings and the SPAN app shows them ("20-20A"); the panel does not.
So the user copies them, and the space is judged against what both breakers
carry together: the capacity, by `meter_capacity_a`, the rule every shared meter
is judged by. That is the circuit's monitoring rating. This module is the one rule
that says whether an entry applies, so every reader of a rating agrees. First
match wins:

1. No entry: the panel's rating.
2. The panel describes its multi-load breakers (`panel_describes_breakers`),
   or declares no service rating, or the circuit declares no rating, or the
   circuit has left a complete snapshot: the entry is deleted and the user told.
3. The circuit's rating, or the service rating, is declared but has no value
   yet, or whether it is declared is not known, or the circuit lists no space
   yet: the entry is kept, silently, and the circuit has no judged rating, so
   nothing shows it as user-set and monitoring skips it. It applies again when
   the value arrives.
4. The capacity is above the service rating, the panel's rating is no longer the
   entry's first, or the circuit no longer lists just the space the entry was
   copied for: deleted and the user told. A different first rating means the
   panel was re-commissioned; a different space means the circuit no longer holds
   the package that was copied.
5. Otherwise the entry applies: the circuit is judged against its capacity.

"Not declared" differs from "not arrived": a retained value can land after its
description, after a restart, a reconnect or a bridge rebuild. Read as "not
published", that moment would delete an entry and tell the user about a change
that never happened. Only a declaration the panel has made
(`breaker_rating_declared is False`, a service rating no device declares) counts
as absence; a value not yet arrived, or a declaration not yet known (`None`, no
field metadata), changes nothing. A circuit always sits in a space, so one that
lists none has not had its space arrive.

A leaf: it imports only the standard library, `span_panel_api` and the
`meter_capacity` leaf. The caller supplies the circuit label, so the rule need
not know how labels are built.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal, TypeIs, get_args

from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from .meter_capacity import meter_capacity_a

type Declaration = Literal["declared", "undeclared", "unknown"]
"""Whether the panel declares a property: it does, it does not, or that is not known yet."""

type PackageRatings = tuple[int, int]
"""A package's breaker ratings in the order the SPAN app shows them, the first the one the panel publishes.

A space holds at most a tandem, so a package here has exactly two.
"""

type ChangeReason = Literal[
    "panel_rating_changed",
    "spaces_changed",
    "rating_undeclared",
    "circuit_removed",
    "above_service_rating",
    "service_rating_undeclared",
    "panel_describes_breakers",
]
"""Why the user is told an entry was removed."""

_CHANGE_REASONS: Final = frozenset(
    reason for reason in get_args(ChangeReason.__value__) if isinstance(reason, str)
)
"""Every `ChangeReason`, read off the alias so the two cannot drift apart."""

_SERVICE_RATING_PATHS = ("panel.main_breaker_rating_a", "panel.upstream_protection_rating_a")


def is_change_reason(value: object) -> TypeIs[ChangeReason]:
    """Whether a value read back from storage is a `ChangeReason`."""
    return value in _CHANGE_REASONS


@dataclass(frozen=True, slots=True)
class StoredPackage:
    """An entry as stored: the copied ratings, the space they were copied for, and the circuit's last label."""

    ratings_a: PackageRatings
    space: int
    label: str


@dataclass(frozen=True, slots=True)
class MonitoringRating:
    """A circuit's rating as readers see it, with the rating the panel published.

    `package_ratings_a` is the entry while it stands, whether it applies or waits
    on a value not yet arrived, and None where there is none or it is being deleted.
    """

    judged_a: float | None
    panel_a: float | None
    user_set: bool
    package_ratings_a: PackageRatings | None


@dataclass(frozen=True, slots=True)
class RatingChange:
    """An entry removed, and why, for the user to be told."""

    circuit_id: str
    label: str
    """The circuit's label, carried so the notice reads the same after the entry is gone."""
    reason: ChangeReason
    package_ratings_a: PackageRatings
    panel_after_a: float | None
    """The rating the panel publishes now, None where it publishes none or the circuit is gone."""
    service_a: float | None


@dataclass(frozen=True, slots=True)
class Reconciled:
    """The entries that remain after a snapshot, and what to tell the user."""

    packages: dict[str, StoredPackage]
    changes: tuple[RatingChange, ...]


def package_ratings_payload(ratings: PackageRatings | None) -> list[int] | None:
    """Return a package as the topology, the monitoring status and the alert payload carry it: a list, null where none."""
    return None if ratings is None else list(ratings)


def service_rating_a(snapshot: SpanPanelSnapshot) -> float | None:
    """Return the rating the mains legs, and any capacity, are judged against.

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
    """Whether the panel describes its multi-load breakers, so no package ratings are offered.

    A panel that declares `meter/shared-with-device-ids` on any circuit, as panels
    other than MAIN 32 do on every circuit, publishes each circuit
    of a multi-load breaker as its own device with its own commissioned
    rating, and names the circuits that share its meter. Current monitoring
    then judges the shared meter against those ratings without the user's
    help. Package ratings exist for the panel that cannot say any of that:
    MAIN 32 declares neither list and publishes a tandem as one circuit with
    the rating of its first breaker.

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


def _capacity_a(spaces: Collection[int], ratings_a: Iterable[int]) -> float | None:
    """Return what a space can carry with a package's breakers in it: `meter_capacity_a`, one member per breaker.

    One space's sensor reads both breakers of a tandem, so their ratings add up.
    None while the circuit lists no space yet, where the package cannot be placed.
    """
    return meter_capacity_a((spaces, float(rating)) for rating in ratings_a)


type Verdict = ChangeReason | Literal["pending", "applies"]
"""What the rule says of one entry: why it is removed, that it waits on a value, or that it applies."""


def judge(
    circuit: SpanCircuitSnapshot | None,
    stored: StoredPackage,
    service: Declaration,
    service_a: float | None,
    *,
    describes_breakers: bool,
) -> Verdict:
    """Rules 2 to 5 of the module for one entry, first match winning.

    `resolve` and `reconcile` read an entry through this, and so does the set
    service before it stores one, so an entry the user sets is one that applies.
    """
    # First, since each holds panel-wide whatever else the snapshot says.
    if describes_breakers:
        return "panel_describes_breakers"
    if service == "undeclared":
        return "service_rating_undeclared"
    if circuit is None:
        return "circuit_removed"
    if circuit.breaker_rating_declared is False:
        return "rating_undeclared"
    panel = circuit.breaker_rating_a
    capacity = _capacity_a(circuit.tabs, stored.ratings_a)
    if panel is None or service == "unknown" or service_a is None or capacity is None:
        return "pending"
    if capacity > service_a:
        return "above_service_rating"
    if stored.ratings_a[0] != panel:
        return "panel_rating_changed"
    if circuit.tabs != [stored.space]:
        return "spaces_changed"
    return "applies"


def resolve(
    circuit: SpanCircuitSnapshot | None,
    stored: StoredPackage | None,
    service_a: float | None,
    service: Declaration,
    *,
    describes_breakers: bool,
) -> MonitoringRating:
    """Return the rating a reader should use for one circuit.

    A circuit with an entry whose panel rating, service rating or space is
    pending, declared with no value yet or with its declaration not known, has
    no judged rating: its capacity cannot be checked against its bound, and the
    panel's rating is the one the entry exists to replace. Judging a tandem
    against it even for a moment raises the false alert the entry exists to
    stop. The panel's rating is still reported, since it is known.

    An entry the rule removes never applies: it is being deleted, and the
    panel's own rating is what the circuit is judged against.
    """
    if circuit is None:
        return MonitoringRating(None, None, False, None)
    panel = circuit.breaker_rating_a
    if stored is None:
        return MonitoringRating(panel, panel, False, None)
    verdict = judge(circuit, stored, service, service_a, describes_breakers=describes_breakers)
    if is_change_reason(verdict):
        return MonitoringRating(panel, panel, False, None)
    if verdict == "pending":
        return MonitoringRating(None, panel, False, stored.ratings_a)
    return MonitoringRating(
        _capacity_a(circuit.tabs, stored.ratings_a), panel, True, stored.ratings_a
    )


def reconcile(
    packages: Mapping[str, StoredPackage],
    snapshot: SpanPanelSnapshot,
    service: Declaration,
    label_of: Callable[[SpanCircuitSnapshot, str], str],
) -> Reconciled:
    """Apply the rule to every stored entry against one complete snapshot.

    A snapshot holding a circuit is complete by construction, so a circuit
    absent from it has left the panel. Returns the entries to keep, with labels
    refreshed, and a change for each entry removed.
    """
    service_a = service_rating_a(snapshot)
    describes_breakers = panel_describes_breakers(snapshot)
    kept: dict[str, StoredPackage] = {}
    changes: list[RatingChange] = []

    for circuit_id, stored in packages.items():
        circuit = snapshot.circuits.get(circuit_id)
        if circuit is not None:
            stored = replace(stored, label=label_of(circuit, circuit_id))
        verdict = judge(circuit, stored, service, service_a, describes_breakers=describes_breakers)
        if is_change_reason(verdict):
            panel = circuit.breaker_rating_a if circuit is not None else None
            changes.append(
                RatingChange(circuit_id, stored.label, verdict, stored.ratings_a, panel, service_a)
            )
        else:
            kept[circuit_id] = stored

    return Reconciled(kept, tuple(changes))
