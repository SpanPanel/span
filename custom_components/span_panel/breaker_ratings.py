"""When a user's correction of a circuit's breaker rating applies.

The panel publishes a rating for each breaker, and a user may know better: a
tandem breaker is published at the rating of one half. The correction is an
override stored by the integration, and this module is the one rule that says
whether it applies, so every reader of a rating agrees. First match wins:

1. No override: the panel's rating.
2. The panel declares no service rating, or the circuit declares no rating, or
   the circuit has left a complete snapshot: the override is deleted and the
   user told.
3. The circuit's rating, or the service rating, is declared but has no value
   yet, or whether it is declared is not known: the override is kept and the
   panel's rating shows, silently. It applies again when the value arrives.
4. The override is above the service rating, or equals the panel's rating:
   deleted and the user told. When the panel has caught up, a later change by
   the panel should show through rather than be masked.
5. Otherwise the override applies. When the panel's rating differs from the
   one the override was set against, the override is kept and the change is
   reported once, then that rating becomes the confirmed value.

"Not declared" differs from "not arrived": a rating's retained value can land
after its description, after a restart, a reconnect or a bridge rebuild. Read
as "not published", that moment would delete a correction and tell the user
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
from typing import Literal

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
]

_SERVICE_RATING_PATHS = ("panel.main_breaker_rating_a", "panel.upstream_protection_rating_a")


@dataclass(frozen=True, slots=True)
class StoredOverride:
    """A user's correction as stored: the rating, the panel rating it was set against, and the circuit's last label."""

    rating_a: int
    panel_a_at_set: float
    label: str


@dataclass(frozen=True, slots=True)
class BreakerRating:
    """A circuit's rating as readers see it, with the rating the panel published."""

    effective_a: float | None
    panel_a: float | None
    user_set: bool


@dataclass(frozen=True, slots=True)
class OverrideChange:
    """Something the user is to be told about an override: kept with a new panel rating, or removed."""

    circuit_id: str
    reason: ChangeReason
    rating_a: int
    panel_before_a: float
    panel_after_a: float | None
    service_a: float | None
    removed: bool


@dataclass(frozen=True, slots=True)
class Reconciled:
    """The overrides that remain after a snapshot, and what to tell the user."""

    overrides: dict[str, StoredOverride]
    changes: tuple[OverrideChange, ...]


def service_rating_a(snapshot: SpanPanelSnapshot) -> float | None:
    """Return the rating the mains legs, and any override, are judged against.

    The main breaker where the panel has one; otherwise the rating of the
    protection ahead of its upstream lugs, which is what limits a panel with no
    main breaker of its own. None where the panel reports neither.
    """
    if snapshot.main_breaker_rating_a:
        return float(snapshot.main_breaker_rating_a)
    if snapshot.upstream_protection_rating_a:
        return float(snapshot.upstream_protection_rating_a)
    return None


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
    override: StoredOverride | None,
    service_a: float | None,
    service: Declaration,
) -> BreakerRating:
    """Return the rating a reader should use for one circuit."""
    if circuit is None:
        return BreakerRating(None, None, False)
    panel = circuit.breaker_rating_a
    if panel is None:
        return BreakerRating(None, None, False)
    if (
        override is not None
        and service == "declared"
        and service_a is not None
        and override.rating_a <= service_a
        and override.rating_a != panel
    ):
        return BreakerRating(float(override.rating_a), panel, True)
    return BreakerRating(panel, panel, False)


def _change(
    circuit_id: str,
    override: StoredOverride,
    reason: ChangeReason,
    panel_after_a: float | None,
    service_a: float | None,
    *,
    removed: bool,
) -> OverrideChange:
    return OverrideChange(
        circuit_id,
        reason,
        override.rating_a,
        override.panel_a_at_set,
        panel_after_a,
        service_a,
        removed,
    )


def reconcile(
    overrides: Mapping[str, StoredOverride],
    snapshot: SpanPanelSnapshot,
    service: Declaration,
    label_of: Callable[[SpanCircuitSnapshot, str], str],
) -> Reconciled:
    """Apply the rule to every stored override against one complete snapshot.

    A snapshot holding a circuit is complete by construction, so a circuit
    absent from it has left the panel. Returns the overrides to keep, with
    labels refreshed and a changed panel rating confirmed, and a change for
    each override removed or kept under a new panel rating.
    """
    service_a = service_rating_a(snapshot)
    kept: dict[str, StoredOverride] = {}
    changes: list[OverrideChange] = []

    for circuit_id, override in overrides.items():
        circuit = snapshot.circuits.get(circuit_id)
        panel = circuit.breaker_rating_a if circuit is not None else None

        if service == "undeclared":
            changes.append(
                _change(
                    circuit_id,
                    override,
                    "service_rating_undeclared",
                    panel,
                    service_a,
                    removed=True,
                )
            )
            continue
        if circuit is None:
            changes.append(
                _change(circuit_id, override, "circuit_removed", panel, service_a, removed=True)
            )
            continue
        override = replace(override, label=label_of(circuit, circuit_id))
        if circuit.breaker_rating_declared is False:
            changes.append(
                _change(circuit_id, override, "rating_undeclared", panel, service_a, removed=True)
            )
            continue
        if panel is None or service == "unknown" or service_a is None:
            kept[circuit_id] = override
            continue
        if override.rating_a > service_a:
            changes.append(
                _change(
                    circuit_id, override, "above_service_rating", panel, service_a, removed=True
                )
            )
            continue
        if override.rating_a == panel:
            changes.append(
                _change(circuit_id, override, "panel_agrees", panel, service_a, removed=True)
            )
            continue
        if panel != override.panel_a_at_set:
            changes.append(
                _change(circuit_id, override, "panel_changed", panel, service_a, removed=False)
            )
            override = replace(override, panel_a_at_set=panel)
        kept[circuit_id] = override

    return Reconciled(kept, tuple(changes))
