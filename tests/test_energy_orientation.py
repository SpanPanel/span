"""Which way each Net Energy counts, and which sensor plays which part in it.

A net sensor is the difference of two counters on one meter. Its value and the
dip-compensation adjustment it adds are both read from one orientation, so the
two cannot disagree about which counter is credited -- the disagreement that
made a solar circuit's Net move the wrong way at each compensated dip.
"""

from __future__ import annotations

from collections import Counter
from typing import Final

from homeassistant.components.sensor import SensorEntityDescription
import pytest

from custom_components.span_panel import sensor_definitions
from custom_components.span_panel.energy_orientation import (
    GENERATION,
    LOAD,
    CircuitMeter,
    EnergyBinding,
    EnergyCounter,
    EnergyRole,
    NetEnergy,
    NetEnergyOrientation,
    PanelMeter,
    circuit_is_generation,
    circuit_net_orientation,
    panel_meter_net_orientation,
)
from custom_components.span_panel.sensor_definitions import (
    CIRCUIT_BREAKER_RATING_SENSOR,
    CIRCUIT_CURRENT_SENSOR,
    CIRCUIT_SENSORS,
    PANEL_ENERGY_SENSORS,
    UNMAPPED_SENSORS,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

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


def test_a_counter_key_answers_neither_a_role_nor_a_string() -> None:
    """The offset map is keyed by counter, and that holds at runtime, not only for mypy."""
    offsets = {(PanelMeter.MAIN_METER, EnergyCounter.PRODUCED): 1.0}

    assert (PanelMeter.MAIN_METER, EnergyRole.PRODUCED) not in offsets
    assert (PanelMeter.MAIN_METER, "produced") not in offsets


def test_the_panel_meters_are_load_oriented() -> None:
    assert panel_meter_net_orientation(SpanPanelSnapshotFactory.create()) is LOAD


# ---------------------------------------------------------------------------
# A Net is one orientation, read by both its value and its adjustment
# ---------------------------------------------------------------------------

CIRCUIT_NET: Final = NetEnergy(
    orientation_of=circuit_net_orientation,
    consumed=lambda c: c.consumed_energy_wh,
    produced=lambda c: c.produced_energy_wh,
)


@pytest.mark.parametrize(("device_type", "expected"), [("pv", 970.0), ("circuit", -970.0)])
def test_a_net_value_is_the_credited_reading_minus_the_debited_one(device_type: str, expected: float) -> None:
    circuit = SpanCircuitSnapshotFactory.create(
        device_type=device_type, consumed_energy_wh=30.0, produced_energy_wh=1000.0
    )

    assert CIRCUIT_NET.value(circuit) == pytest.approx(expected)


def test_a_net_value_is_unknown_while_a_counter_is_unreported() -> None:
    circuit = SpanCircuitSnapshotFactory.create(consumed_energy_wh=None, produced_energy_wh=4.0)

    assert CIRCUIT_NET.value(circuit) is None


def test_a_binding_declares_a_net_exactly_when_its_role_is_net() -> None:
    with pytest.raises(ValueError, match="Net"):
        EnergyBinding(meter=CircuitMeter("c1"), role=EnergyRole.NET, net=None)
    with pytest.raises(ValueError, match="Net"):
        EnergyBinding(meter=CircuitMeter("c1"), role=EnergyRole.PRODUCED, net=CIRCUIT_NET)


def test_a_binding_plays_its_role_on_a_meter() -> None:
    with pytest.raises(ValueError, match="meter"):
        EnergyBinding(meter=None, role=EnergyRole.CONSUMED, net=None)


def test_a_binding_names_the_counter_it_offers() -> None:
    meter = CircuitMeter("c1")
    assert EnergyBinding(meter=meter, role=EnergyRole.PRODUCED, net=None).counter is EnergyCounter.PRODUCED
    assert EnergyBinding(meter=meter, role=EnergyRole.CONSUMED, net=None).counter is EnergyCounter.CONSUMED
    assert EnergyBinding(meter=meter, role=EnergyRole.NET, net=CIRCUIT_NET).counter is None
    assert EnergyBinding(meter=None, role=None, net=None).counter is None


def test_meters_are_hashable_keys() -> None:
    keys = {
        (CircuitMeter("c1"), EnergyCounter.PRODUCED),
        (CircuitMeter("c1"), EnergyCounter.PRODUCED),
        (CircuitMeter("c2"), EnergyCounter.PRODUCED),
        (PanelMeter.MAIN_METER, EnergyCounter.PRODUCED),
        (PanelMeter.FEEDTHROUGH, EnergyCounter.PRODUCED),
    }
    assert len(keys) == 4


# ---------------------------------------------------------------------------
# The roles are data on the descriptions
# ---------------------------------------------------------------------------


def _every_description() -> list[SensorEntityDescription]:
    """Every sensor description the catalog module defines, alone or in a tuple, once each."""
    found: dict[int, SensorEntityDescription] = {}
    for value in vars(sensor_definitions).values():
        candidates = value if isinstance(value, tuple) else (value,)
        for candidate in candidates:
            if isinstance(candidate, SensorEntityDescription):
                found[id(candidate)] = candidate
    return list(found.values())


def test_each_circuit_energy_description_declares_its_role() -> None:
    assert {d.key: d.energy_role for d in CIRCUIT_SENSORS} == {
        "circuit_power": None,
        "circuit_energy_produced": EnergyRole.PRODUCED,
        "circuit_energy_consumed": EnergyRole.CONSUMED,
        "circuit_energy_net": EnergyRole.NET,
    }
    for other in (*UNMAPPED_SENSORS, CIRCUIT_CURRENT_SENSOR, CIRCUIT_BREAKER_RATING_SENSOR):
        assert other.energy_role is None, other.key


def test_panel_energy_descriptions_set_role_and_meter_together_and_nothing_else_does() -> None:
    for description in PANEL_ENERGY_SENSORS:
        assert description.energy_role is not None, description.key
        assert description.panel_meter is not None, description.key
    others = [
        d
        for d in _every_description()
        if isinstance(d, SpanPanelDataSensorEntityDescription) and d not in PANEL_ENERGY_SENSORS
    ]
    assert others, "the catalog has other panel data descriptions to check"
    for description in others:
        assert description.energy_role is None, description.key
        assert description.panel_meter is None, description.key


def test_each_panel_meter_has_one_produced_one_consumed_and_one_net() -> None:
    for meter in PanelMeter:
        roles = [d.energy_role for d in PANEL_ENERGY_SENSORS if d.panel_meter is meter]
        assert Counter(roles) == Counter(EnergyRole), meter


def test_a_description_carries_a_net_exactly_when_its_role_is_net() -> None:
    energy_descriptions = [
        d
        for d in _every_description()
        if isinstance(d, SpanPanelCircuitsSensorEntityDescription | SpanPanelDataSensorEntityDescription)
    ]
    for description in energy_descriptions:
        assert (description.net_energy is not None) is (description.energy_role is EnergyRole.NET), description.key


def test_exactly_three_descriptions_are_net_sensors() -> None:
    nets = [d for d in _every_description() if getattr(d, "energy_role", None) is EnergyRole.NET]
    assert sorted(d.key for d in nets) == [
        "circuit_energy_net",
        "feedthroughNetEnergyWh",
        "mainMeterNetEnergyWh",
    ]


def test_a_circuits_net_comes_after_its_siblings() -> None:
    roles = [d.energy_role for d in CIRCUIT_SENSORS]
    assert roles.index(EnergyRole.NET) > roles.index(EnergyRole.PRODUCED)
    assert roles.index(EnergyRole.NET) > roles.index(EnergyRole.CONSUMED)


def test_each_panel_meters_net_comes_after_its_siblings() -> None:
    for meter in PanelMeter:
        roles = [d.energy_role for d in PANEL_ENERGY_SENSORS if d.panel_meter is meter]
        assert roles[-1] is EnergyRole.NET, meter


# ---------------------------------------------------------------------------
# Values are unchanged by the move to the orientation
# ---------------------------------------------------------------------------


def _value(
    key: str,
    *,
    device_type: str,
    produced_energy_wh: float | None,
    consumed_energy_wh: float | None,
    instant_power_w: float | None = 0.0,
) -> float | None:
    description = next(d for d in CIRCUIT_SENSORS if d.key == key)
    return description.value_fn(
        SpanCircuitSnapshotFactory.create(
            device_type=device_type,
            produced_energy_wh=produced_energy_wh,
            consumed_energy_wh=consumed_energy_wh,
            instant_power_w=instant_power_w,
        )
    )


def test_circuit_values_are_unchanged() -> None:
    assert _value(
        "circuit_energy_net", device_type="pv", produced_energy_wh=1000.0, consumed_energy_wh=30.0
    ) == pytest.approx(970.0)
    assert _value(
        "circuit_energy_net",
        device_type="circuit",
        produced_energy_wh=3.0,
        consumed_energy_wh=500.0,
    ) == pytest.approx(497.0)
    assert _value(
        "circuit_power",
        device_type="pv",
        produced_energy_wh=0.0,
        consumed_energy_wh=0.0,
        instant_power_w=-2100.0,
    ) == pytest.approx(2100.0)
    assert _value(
        "circuit_power",
        device_type="circuit",
        produced_energy_wh=0.0,
        consumed_energy_wh=0.0,
        instant_power_w=120.0,
    ) == pytest.approx(120.0)
    assert (
        _value(
            "circuit_energy_net", device_type="pv", produced_energy_wh=None, consumed_energy_wh=4.0
        )
        is None
    )


def test_panel_net_values_are_unchanged() -> None:
    snapshot = SpanPanelSnapshotFactory.create(
        main_meter_energy_consumed_wh=2500.0,
        main_meter_energy_produced_wh=300.0,
        feedthrough_energy_consumed_wh=80.0,
        feedthrough_energy_produced_wh=20.0,
    )
    values = {d.key: d.value_fn(snapshot) for d in PANEL_ENERGY_SENSORS}
    assert values["mainMeterNetEnergyWh"] == pytest.approx(2200.0)
    assert values["feedthroughNetEnergyWh"] == pytest.approx(60.0)
