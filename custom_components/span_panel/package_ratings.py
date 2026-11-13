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
   entry's first, or the circuit no longer lists just the one or two spaces the
   entry was copied for: deleted and the user told. A different first rating
   means the panel was re-commissioned; different spaces mean the circuit no
   longer holds the package that was copied. Spaces are compared in order of
   space, since a circuit may list its two in either order.
5. Otherwise an entry for one space applies: the circuit is judged against its
   capacity. An entry for two spaces is kept, on averaged legs
   (`"averaged_legs"`), and never applies: the panel reports such a circuit's
   current as the average of its two legs, and judging that average against
   the package can hide an overloaded leg. The circuit is judged against the
   panel's rating, as though there were no entry.

Ratings a user enters are judged by the same rule before they are stored
(`admit`), so an entry the user sets is one the rule keeps, and each way it
would not becomes a refusal with its own reason.

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

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal, TypeIs, get_args

from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from .meter_capacity import meter_capacity_a

type Declaration = Literal["declared", "undeclared", "unknown"]
"""Whether the panel declares a property: it does, it does not, or that is not known yet."""

type PackageRatings = tuple[int, int]
"""A package's breaker ratings in the order the SPAN app shows them, the first the one the panel publishes.

A space holds at most a tandem, and a package across two spaces is copied the
same way, so a package here has exactly two.
"""

type JudgedState = Literal["applies", "pending", "averaged_legs"]
"""What a kept entry is.

In force; waiting on a value not yet arrived, with no judged rating; or kept on
a circuit across two spaces, whose averaged legs it is never applied to (rule 5).
"""

type PackageRatingsForm = Literal["one_space", "two_spaces"]
"""Which form a copy of a circuit's package takes: for its one space, or across its two."""

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
    """An entry as stored: the copied ratings, the spaces they were copied for, and the circuit's last label.

    `spaces` is in order of space (`_spaces`), as a stored entry is compared.
    """

    ratings_a: PackageRatings
    spaces: tuple[int, ...]
    label: str


@dataclass(frozen=True, slots=True)
class MonitoringRating:
    """A circuit's rating as readers see it, with the rating the panel published.

    `package_ratings_a` is the entry while it stands, whether it applies, waits
    on a value not yet arrived or is kept on averaged legs, and None where there
    is none or it is being deleted. `state` says which of those it is, and is
    None exactly where `package_ratings_a` is, so a reader need not work it out
    from the other fields.
    """

    judged_a: float | None
    panel_a: float | None
    user_set: bool
    package_ratings_a: PackageRatings | None
    state: JudgedState | None


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
    """The entries that remain after a snapshot, what each is now, and what to tell the user."""

    packages: dict[str, StoredPackage]
    changes: tuple[RatingChange, ...]
    states: dict[str, JudgedState]
    """For each entry kept, whether it applies, waits on a value not yet arrived, or is kept on averaged legs."""


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

    A panel that describes its multi-load breakers, as panels other than MAIN 32
    do, declares `meter/shared-with-device-ids` on its circuits, publishes each
    circuit of a multi-load breaker as its own device with its own commissioned
    rating, and names the circuits that share its meter. Current monitoring then
    judges the shared meter against those ratings without the user's help.
    Package ratings exist for the panel that cannot say any of that: MAIN 32
    declares neither list and publishes a tandem as one circuit with the rating
    of its first breaker.

    The declaration decides where the adapter states it
    (`publishes_shared_meters` true), so the answer is the same on the first
    complete snapshot as on every later one. A circuit that publishes the list
    decides it too, even empty, whatever the flag says or where the adapter does
    not say: only a panel that describes its breakers publishes one, and a group
    of circuits is judged by its capacity, so none of its members may hold a
    package. The flag's default is not relied on.
    """
    if snapshot.publishes_shared_meters is True:
        return True
    return any(circuit.meter_shared_with is not None for circuit in snapshot.circuits.values())


def package_ratings_form(
    snapshot: SpanPanelSnapshot, circuit: SpanCircuitSnapshot
) -> PackageRatingsForm | None:
    """Return which form a copy of the circuit's package takes, or None where none can be entered.

    The one answer every reader draws a copy's field from, so the services and
    every view agree on which circuits take one. None where the panel describes
    its multi-load breakers (`panel_describes_breakers`), where the circuit is a
    meter outside the panel, with no breaker, where it has no rating for a
    copy's first to match, or where it lists no space or more than two. Read
    from the panel's structure, never from a circuit's name or its readings.
    """
    if (
        panel_describes_breakers(snapshot)
        or circuit.measures_outside_panel
        or circuit.breaker_rating_a is None
        or not _holds(circuit, None)
    ):
        return None
    return "one_space" if len(circuit.tabs) == 1 else "two_spaces"


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


type Verdict = ChangeReason | JudgedState
"""What the rule says of one entry: why it is removed, that it waits on a value, or that it applies."""


def _panel_wide(service: Declaration, *, describes_breakers: bool) -> ChangeReason | None:
    """Return why no package holds anywhere on the panel, whatever else the snapshot says, or None.

    The panel describes its multi-load breakers, or declares no service rating
    to bound a capacity: the first of rule 2's reasons.
    """
    if describes_breakers:
        return "panel_describes_breakers"
    if service == "undeclared":
        return "service_rating_undeclared"
    return None


def _known(
    circuit: SpanCircuitSnapshot, service: Declaration, service_a: float | None
) -> tuple[float, float] | None:
    """Return the panel's rating and the service rating a package is checked against.

    None while either, or the circuit's space, waits on a value declared and not
    yet arrived or on a declaration not yet known (rule 3).
    """
    panel = circuit.breaker_rating_a
    if panel is None or service == "unknown" or service_a is None or not circuit.tabs:
        return None
    return panel, service_a


def _spaces(circuit: SpanCircuitSnapshot) -> tuple[int, ...]:
    """Return the circuit's spaces in order of space, as an entry stores and compares them.

    A circuit may list its two spaces in either order, and the same two are the
    same circuit, never one whose spaces changed.
    """
    return tuple(sorted(circuit.tabs))


def _holds(circuit: SpanCircuitSnapshot, spaces: tuple[int, ...] | None) -> bool:
    """Whether the circuit lists one or two spaces: `spaces` where given, else any one or two."""
    return len(circuit.tabs) in (1, 2) and (spaces is None or _spaces(circuit) == spaces)


def judge(
    circuit: SpanCircuitSnapshot | None,
    ratings_a: PackageRatings,
    spaces: tuple[int, ...] | None,
    service: Declaration,
    service_a: float | None,
    *,
    describes_breakers: bool,
) -> Verdict:
    """Rules 2 to 5 of the module for one package, first match winning.

    `spaces` are the spaces a stored entry was copied for, in order of space, or
    None for ratings being entered (`admit`), which take the circuit's one or
    two. `resolve` and `reconcile` read a stored entry through this, and `admit`
    the ratings a user enters, so an entry the user sets is one the rule keeps.
    """
    # First, since each holds panel-wide whatever else the snapshot says.
    reason = _panel_wide(service, describes_breakers=describes_breakers)
    if reason is not None:
        return reason
    if circuit is None:
        return "circuit_removed"
    if circuit.breaker_rating_declared is False:
        return "rating_undeclared"
    known = _known(circuit, service, service_a)
    capacity = _capacity_a(circuit.tabs, ratings_a)
    if known is None or capacity is None:
        return "pending"
    panel, bound = known
    if capacity > bound:
        return "above_service_rating"
    if ratings_a[0] != panel:
        return "panel_rating_changed"
    if not _holds(circuit, spaces):
        return "spaces_changed"
    return "averaged_legs" if len(circuit.tabs) == 2 else "applies"


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

    An entry kept on averaged legs never applies either (rule 5). The circuit is
    judged against the panel's rating, as though there were no entry, while the
    entry is still reported, so a reader can say why it does not apply.
    """
    if circuit is None:
        return MonitoringRating(None, None, False, None, None)
    panel = circuit.breaker_rating_a
    if stored is None:
        return MonitoringRating(panel, panel, False, None, None)
    verdict = judge(
        circuit,
        stored.ratings_a,
        stored.spaces,
        service,
        service_a,
        describes_breakers=describes_breakers,
    )
    if is_change_reason(verdict):
        return MonitoringRating(panel, panel, False, None, None)
    if verdict == "pending":
        return MonitoringRating(None, panel, False, stored.ratings_a, "pending")
    if verdict == "averaged_legs":
        return MonitoringRating(panel, panel, False, stored.ratings_a, "averaged_legs")
    return MonitoringRating(
        _capacity_a(circuit.tabs, stored.ratings_a), panel, True, stored.ratings_a, "applies"
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
    refreshed, what each now is, and a change for each entry removed.
    """
    service_a = service_rating_a(snapshot)
    describes_breakers = panel_describes_breakers(snapshot)
    kept: dict[str, StoredPackage] = {}
    states: dict[str, JudgedState] = {}
    changes: list[RatingChange] = []

    for circuit_id, stored in packages.items():
        circuit = snapshot.circuits.get(circuit_id)
        if circuit is not None:
            stored = replace(stored, label=label_of(circuit, circuit_id))
        verdict = judge(
            circuit,
            stored.ratings_a,
            stored.spaces,
            service,
            service_a,
            describes_breakers=describes_breakers,
        )
        if is_change_reason(verdict):
            panel = circuit.breaker_rating_a if circuit is not None else None
            changes.append(
                RatingChange(circuit_id, stored.label, verdict, stored.ratings_a, panel, service_a)
            )
        else:
            kept[circuit_id] = stored
            states[circuit_id] = verdict

    return Reconciled(kept, tuple(changes), states)


type Refusal = Literal[
    "circuit_unknown",
    "panel_describes_breakers",
    "not_published",
    "not_arrived",
    "no_service_rating",
    "spaces",
    "not_two",
    "first_not_panel",
    "above_service_rating",
]
"""Why ratings a user enters are refused; each has its own message, `package_ratings_<refusal>`."""

_REFUSALS: Final[Mapping[ChangeReason | Literal["pending"], Refusal]] = {
    "panel_describes_breakers": "panel_describes_breakers",
    "service_rating_undeclared": "no_service_rating",
    "circuit_removed": "circuit_unknown",
    "rating_undeclared": "not_published",
    # A value declared and not yet sent, or a declaration not yet known: the
    # user can try again in a moment, where a stored entry would wait.
    "pending": "not_arrived",
    "above_service_rating": "above_service_rating",
    "panel_rating_changed": "first_not_panel",
    "spaces_changed": "spaces",
}
"""What each reason the rule would remove a stored entry for means for ratings being entered."""


@dataclass(frozen=True, slots=True)
class Refused:
    """Ratings a user entered that the rule will not store, and why."""

    reason: Refusal
    panel_a: float | None
    """The rating the panel publishes for the circuit, which a refusal of the first rating names."""
    service_a: float | None
    """The service rating, which a refusal of a capacity above it names."""


type Admission = StoredPackage | Literal["clear"] | Refused
"""What the rule makes of ratings a user enters: an entry to store, a clear, or a refusal."""


def admit(
    snapshot: SpanPanelSnapshot,
    circuit_id: str,
    ratings_a: Sequence[int],
    service: Declaration,
    label_of: Callable[[SpanCircuitSnapshot, str], str],
) -> Admission:
    """Judge ratings a user enters for one circuit, in the SPAN app's order, by the rule a stored entry is read by.

    A single rating is a lone breaker's package, which has nothing to add up: it
    is the clear, wherever the circuit is on the panel and the clear service
    would act, refused only where the panel publishes a different rating for the
    circuit. A package otherwise holds two ratings, and is refused for the first
    of these that holds: the circuit is not on the panel; the panel describes
    its breakers; the circuit's rating or the service rating is not published or
    not yet arrived, or its space has not; the circuit is not in one or two
    spaces; the list is not two ratings; the first is not the panel's; the two
    together are above the service rating. The rule's own stages decide each,
    and `judge`, as a stored entry is read, has the last word, so a package is
    stored only where the rule keeps it: where it applies, or, across two
    spaces, on averaged legs. A verdict that would remove a stored entry is the
    refusal it means here (`_REFUSALS`).
    """
    service_a = service_rating_a(snapshot)
    circuit = snapshot.circuits.get(circuit_id)
    if circuit is None:
        return Refused("circuit_unknown", None, service_a)
    panel = circuit.breaker_rating_a
    if len(ratings_a) == 1:
        if panel is not None and ratings_a[0] != panel:
            return Refused("first_not_panel", panel, service_a)
        return "clear"
    describes = panel_describes_breakers(snapshot)
    reason = _panel_wide(service, describes_breakers=describes)
    if reason is not None:
        return Refused(_REFUSALS[reason], panel, service_a)
    if circuit.breaker_rating_declared is False:
        return Refused(_REFUSALS["rating_undeclared"], panel, service_a)
    if _known(circuit, service, service_a) is None:
        return Refused(_REFUSALS["pending"], panel, service_a)
    if not _holds(circuit, None):
        return Refused(_REFUSALS["spaces_changed"], panel, service_a)
    if len(ratings_a) != 2:
        return Refused("not_two", panel, service_a)
    ratings: PackageRatings = (ratings_a[0], ratings_a[1])
    if ratings[0] != panel:
        return Refused(_REFUSALS["panel_rating_changed"], panel, service_a)
    verdict = judge(circuit, ratings, None, service, service_a, describes_breakers=describes)
    if verdict == "applies" or verdict == "averaged_legs":
        return StoredPackage(ratings, _spaces(circuit), label_of(circuit, circuit_id))
    return Refused(_REFUSALS[verdict], panel, service_a)
