"""Which way a meter's Net Energy counts, and which energy sensor plays which part.

A net sensor is the difference of two counters on one meter: a circuit, the
main meter or the feed-through lugs. Each is declared once, as a `NetEnergy`
on its description, and its value and the dip-compensation adjustment it adds
are both read from that one declaration's `NetEnergyOrientation`, so the two
cannot disagree about which counter is credited. They used to be two rules:
the value was generation-oriented for a solar circuit while the adjustment was
always load-oriented, so a solar circuit's Net moved by twice each dip the
wrong way.

An energy sensor's part in this -- its meter, its role and, for a Net, its
`NetEnergy` -- is its `EnergyBinding`.

A leaf: it imports only the standard library and `span_panel_api`, so the
descriptions, the coordinator and every sensor module can depend on it
without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

_GENERATION_DEVICE_TYPE: Final = "pv"
"""The circuit `device_type` whose energy is oriented as generation."""


class EnergyCounter(Enum):
    """One of a meter's two cumulative counters.

    A plain `Enum`, not a `StrEnum`: a member equals only itself, so neither a
    role nor a string can answer a lookup keyed by counter. Nothing needs these
    to be strings.
    """

    CONSUMED = "consumed"
    PRODUCED = "produced"


@dataclass(frozen=True, slots=True)
class NetEnergyOrientation:
    """Which counter a Net Energy sensor counts up, and which it counts down."""

    credit: EnergyCounter
    """The counter Net Energy counts up."""

    debit: EnergyCounter
    """The counter Net Energy counts down."""

    def __post_init__(self) -> None:
        """Refuse an orientation that is not a difference at all."""
        if self.credit is self.debit:
            raise ValueError("A net sensor cannot credit and debit one counter")

    def ordered(
        self, *, consumed: float | None, produced: float | None
    ) -> tuple[float | None, float | None]:
        """Return a meter's two readings as `(credit, debit)`."""
        readings = {EnergyCounter.CONSUMED: consumed, EnergyCounter.PRODUCED: produced}
        return readings[self.credit], readings[self.debit]

    def adjustment(self, offset_of: Callable[[EnergyCounter], float]) -> float:
        """Return what dip compensation adds to the raw net: the credited offset minus the debited one."""
        return offset_of(self.credit) - offset_of(self.debit)


LOAD: Final = NetEnergyOrientation(credit=EnergyCounter.CONSUMED, debit=EnergyCounter.PRODUCED)
"""Consumed minus produced: every load circuit, the main meter and the feed-through lugs."""

GENERATION: Final = NetEnergyOrientation(
    credit=EnergyCounter.PRODUCED, debit=EnergyCounter.CONSUMED
)
"""Produced minus consumed: a circuit that feeds a generator."""


def circuit_is_generation(circuit: SpanCircuitSnapshot) -> bool:
    """Whether a circuit's energy is oriented as generation; its power's sign follows the same answer."""
    return circuit.device_type == _GENERATION_DEVICE_TYPE


def circuit_net_orientation(circuit: SpanCircuitSnapshot) -> NetEnergyOrientation:
    """Return the orientation of a circuit's Net Energy, by its device type."""
    return GENERATION if circuit_is_generation(circuit) else LOAD


def panel_meter_net_orientation(snapshot: SpanPanelSnapshot) -> NetEnergyOrientation:
    """Return the orientation of the main meter's and the feed-through lugs' Net Energy: always load."""
    return LOAD


class EnergyRole(Enum):
    """The part an energy sensor plays on its meter, declared on its description.

    A plain `Enum` for the reason `EnergyCounter` is one.
    """

    PRODUCED = "produced"
    CONSUMED = "consumed"
    NET = "net"

    @property
    def counter(self) -> EnergyCounter | None:
        """The counter this role reads, or None for Net, which reads both."""
        if self is EnergyRole.PRODUCED:
            return EnergyCounter.PRODUCED
        if self is EnergyRole.CONSUMED:
            return EnergyCounter.CONSUMED
        return None


class PanelMeter(Enum):
    """The panel's own meters, as opposed to a circuit's."""

    MAIN_METER = "main_meter"
    FEEDTHROUGH = "feedthrough"


@dataclass(frozen=True, slots=True)
class CircuitMeter:
    """A circuit's meter, named by its circuit id."""

    circuit_id: str


type EnergyMeter = CircuitMeter | PanelMeter
"""Any meter; hashable, and with an `EnergyCounter` the coordinator's offset key."""


def _difference(minuend: float | None, subtrahend: float | None) -> float | None:
    """`minuend - subtrahend`, or `None` while either side is unreported.

    Half a difference is not a smaller difference, it is no answer: a circuit
    that has published its consumed counter and not its produced one knows
    nothing yet about the net of the two.
    """
    if minuend is None or subtrahend is None:
        return None
    return minuend - subtrahend


@dataclass(frozen=True, slots=True)
class NetEnergy[D]:
    """A Net Energy sensor's definition: its orientation and the two counters it reads.

    Held by the sensor's description. The description's `value_fn` is `value`,
    and the sensor's dip adjustment reads `orientation_of`, so the orientation
    is declared once and both read the same one.
    """

    orientation_of: Callable[[D], NetEnergyOrientation]
    """The orientation for the reading being processed; a circuit's follows its device type."""

    consumed: Callable[[D], float | None]
    """The meter's consumed counter."""

    produced: Callable[[D], float | None]
    """The meter's produced counter."""

    def value(self, data: D) -> float | None:
        """Return the credited reading minus the debited one, or None while either is unreported."""
        return _difference(
            *self.orientation_of(data).ordered(
                consumed=self.consumed(data), produced=self.produced(data)
            )
        )


@dataclass(frozen=True, slots=True)
class EnergyBinding[D]:
    """The meter an energy sensor reads, the part it plays there and, for a Net, its definition.

    Every energy sensor binds exactly one, before `SpanEnergySensorBase` is
    initialised; see `SpanEnergySensorBase._bind_energy`.
    """

    meter: EnergyMeter | None
    """The meter, or None for a sensor on no meter, which plays no part."""

    role: EnergyRole | None
    """Produced, Consumed or Net, as the description declares."""

    net: NetEnergy[D] | None
    """The Net's definition: set exactly when `role` is Net."""

    def __post_init__(self) -> None:
        """Refuse a role on no meter, and a Net without its definition or a definition on a counter."""
        if self.role is not None and self.meter is None:
            raise ValueError("An energy role is played on a meter")
        if (self.role is EnergyRole.NET) != (self.net is not None):
            raise ValueError("A binding carries a Net definition exactly when its role is Net")

    @property
    def counter(self) -> EnergyCounter | None:
        """The counter this sensor offers its meter's Net, or None for a Net or a sensor with no role."""
        return self.role.counter if self.role is not None else None
