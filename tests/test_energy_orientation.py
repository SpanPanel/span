"""Which way each Net Energy counts, and which sensor plays which part in it.

A net sensor is the difference of two counters on one meter. Its value and the
dip-compensation adjustment it adds are both read from one orientation, so the
two cannot disagree about which counter is credited -- the disagreement that
made a solar circuit's Net move the wrong way at each compensated dip.
"""

from __future__ import annotations

from typing import Final

import pytest

from custom_components.span_panel.energy_orientation import (
    GENERATION,
    LOAD,
    CircuitMeter,
    EnergyCounter,
    EnergyRole,
    NetEnergyOrientation,
    PanelMeter,
    circuit_is_generation,
    circuit_net_orientation,
)

from .factories import SpanCircuitSnapshotFactory

OFFSETS: Final = {EnergyCounter.CONSUMED: 7.0, EnergyCounter.PRODUCED: 50.0}


def test_a_load_credits_consumed_and_generation_credits_produced() -> None:
    assert NetEnergyOrientation(credit=EnergyCounter.CONSUMED, debit=EnergyCounter.PRODUCED) == LOAD
    assert (
        NetEnergyOrientation(credit=EnergyCounter.PRODUCED, debit=EnergyCounter.CONSUMED)
        == GENERATION
    )


def test_an_orientation_cannot_credit_and_debit_one_counter() -> None:
    with pytest.raises(ValueError, match="one counter"):
        NetEnergyOrientation(credit=EnergyCounter.CONSUMED, debit=EnergyCounter.CONSUMED)


@pytest.mark.parametrize(
    ("orientation", "expected"),
    [(LOAD, (30.0, 1000.0)), (GENERATION, (1000.0, 30.0))],
    ids=["load", "generation"],
)
def test_ordered_puts_the_credited_reading_first(
    orientation: NetEnergyOrientation, expected: tuple[float, float]
) -> None:
    assert orientation.ordered(consumed=30.0, produced=1000.0) == expected


def test_ordered_keeps_an_unreported_reading_unreported() -> None:
    assert LOAD.ordered(consumed=None, produced=4.0) == (None, 4.0)
    assert GENERATION.ordered(consumed=None, produced=4.0) == (4.0, None)


@pytest.mark.parametrize(
    ("orientation", "expected"),
    [(LOAD, 7.0 - 50.0), (GENERATION, 50.0 - 7.0)],
    ids=["load", "generation"],
)
def test_the_adjustment_is_the_credited_offset_minus_the_debited_one(
    orientation: NetEnergyOrientation, expected: float
) -> None:
    assert orientation.adjustment(OFFSETS.__getitem__) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("device_type", "generation"), [("pv", True), ("circuit", False), ("evse", False)]
)
def test_a_circuit_is_oriented_by_its_device_type(device_type: str, generation: bool) -> None:
    circuit = SpanCircuitSnapshotFactory.create(device_type=device_type)

    assert circuit_is_generation(circuit) is generation
    assert circuit_net_orientation(circuit) is (GENERATION if generation else LOAD)


def test_a_counter_role_names_its_counter_and_net_names_none() -> None:
    assert EnergyRole.PRODUCED.counter is EnergyCounter.PRODUCED
    assert EnergyRole.CONSUMED.counter is EnergyCounter.CONSUMED
    assert EnergyRole.NET.counter is None


def test_meters_are_hashable_keys() -> None:
    keys = {
        (CircuitMeter("c1"), EnergyCounter.PRODUCED),
        (CircuitMeter("c1"), EnergyCounter.PRODUCED),
        (CircuitMeter("c2"), EnergyCounter.PRODUCED),
        (PanelMeter.MAIN_METER, EnergyCounter.PRODUCED),
        (PanelMeter.FEEDTHROUGH, EnergyCounter.PRODUCED),
    }
    assert len(keys) == 4
