"""Whether a circuit's energy counter holds a reading its breaker could ever have carried.

A breaker bounds what its circuit can pass: its rating at the circuit's nominal
voltage, every hour of a thirty-year life, is more energy than the circuit can
ever have metered. A counter above that is not energy; it is a counter the
panel has not got right yet. Dip compensation must never take such a reading
as its baseline, because the panel correcting it in place would then look like
a dip of that size and be booked as a permanent offset in long-term statistics.

The reading itself is still reported as published: only its use as a baseline
is refused. Counters with no rating behind them (the lugs, a meter outside the
panel, the panel's own meters) have no bound and are never judged.

A leaf: it imports only the standard library and `span_panel_api`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

LIFETIME_HOURS: Final = 24 * 365 * 30
"""Thirty years of continuous operation, the life the bound allows a circuit."""

LEG_VOLTAGE_V: Final = 120.0
"""The voltage of one leg, for a circuit that declares no nominal voltage of its own."""


def lifetime_bound_wh(circuit: SpanCircuitSnapshot) -> float | None:
    """Return the most energy the circuit's breaker can ever have passed, in Wh; None where unbounded.

    The breaker rating at the circuit's declared nominal voltage, or at one
    leg's voltage per pole where it declares none, for `LIFETIME_HOURS`. None
    without a rating, or without a voltage or a pole count to take one from.
    """
    rating = circuit.breaker_rating_a
    if rating is None or rating <= 0:
        return None
    voltage = circuit.nominal_voltage_v
    if voltage is None:
        if not circuit.tabs:
            return None
        voltage = LEG_VOLTAGE_V * len(circuit.tabs)
    return rating * voltage * LIFETIME_HOURS


def exceeds_lifetime_bound(circuit: SpanCircuitSnapshot, reading_wh: float) -> bool:
    """Whether one counter reading is beyond anything the circuit's breaker could have passed."""
    bound = lifetime_bound_wh(circuit)
    return bound is not None and reading_wh > bound


type Counter = Literal["consumed", "produced"]


@dataclass(frozen=True, slots=True)
class ImplausibleCounter:
    """One counter reading beyond its circuit's lifetime bound, for diagnostics."""

    circuit_id: str
    counter: Counter
    reading_wh: float
    bound_wh: float


def implausible_counters(snapshot: SpanPanelSnapshot) -> list[ImplausibleCounter]:
    """Every circuit counter in the snapshot that reads beyond its lifetime bound."""
    found: list[ImplausibleCounter] = []
    for circuit_id, circuit in snapshot.circuits.items():
        bound = lifetime_bound_wh(circuit)
        if bound is None:
            continue
        readings: tuple[tuple[Counter, float | None], ...] = (
            ("consumed", circuit.consumed_energy_wh),
            ("produced", circuit.produced_energy_wh),
        )
        found.extend(
            ImplausibleCounter(circuit_id, counter, reading, bound)
            for counter, reading in readings
            if reading is not None and reading > bound
        )
    return found
