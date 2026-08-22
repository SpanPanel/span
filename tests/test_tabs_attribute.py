"""Test script for tabs attribute functionality."""

from span_panel_api import SpanCircuitSnapshot

from custom_components.span_panel.helpers import (
    construct_tabs_attribute,
    construct_voltage_attribute,
)


def _make_circuit(tabs: list[int], instant_power_w: float = 100.0) -> SpanCircuitSnapshot:
    """Create a minimal SpanCircuitSnapshot for tab/voltage tests."""
    return SpanCircuitSnapshot(
        circuit_id="test",
        name="Test Circuit",
        relay_state="CLOSED",
        instant_power_w=instant_power_w,
        produced_energy_wh=0.0,
        consumed_energy_wh=0.0,
        tabs=tabs,
        priority="NEVER",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
    )


def test_tabs_attribute_construction() -> None:
    """Test tabs attribute construction from circuit data."""
    # Single tab (120V)
    assert construct_tabs_attribute(_make_circuit([28])) == "tabs [28]"

    # Two tabs (240V)
    assert construct_tabs_attribute(_make_circuit([30, 32])) == "tabs [30:32]"

    # No tabs
    assert construct_tabs_attribute(_make_circuit([])) is None

    # Unsorted input is ordered, whatever the pole count
    assert construct_tabs_attribute(_make_circuit([32, 30])) == "tabs [30:32]"


def test_tabs_attribute_names_every_position_of_a_multipole_breaker() -> None:
    """A 3- or 4-pole breaker keeps every position it occupies.

    Defensive rather than observed: SPAN has stated its panels "are split-phase
    and publish only 1- or 2-pole breakers", and no circuit on any captured
    panel occupies more than two positions. The `1:4:1` range on `breaker/poles`
    belongs to the generic eBus catalog, which covers load centres that are not
    SPAN.

    What is tested here is that unexpected input degrades instead of failing.
    Three positions used to drop the attribute entirely and log that the
    hardware was "not valid for US electrical system".
    """
    assert construct_tabs_attribute(_make_circuit([17, 19, 21])) == "tabs [17:19:21]"
    assert construct_tabs_attribute(_make_circuit([2, 4, 6, 8])) == "tabs [2:4:6:8]"
    # Ordering is by position, not by arrival.
    assert construct_tabs_attribute(_make_circuit([21, 17, 19])) == "tabs [17:19:21]"


def test_voltage_attribute_construction() -> None:
    """Test voltage attribute construction from circuit data."""
    assert construct_voltage_attribute(_make_circuit([28])) == 120
    assert construct_voltage_attribute(_make_circuit([30, 32])) == 240
    assert construct_voltage_attribute(_make_circuit([])) is None


def test_voltage_is_not_claimed_for_a_multipole_breaker() -> None:
    """Three or more poles is not a split-phase circuit, so we do not guess.

    208V line-to-line on a three-phase wye service and 240V on a high-leg delta
    are both plausible and nothing published distinguishes them. The position
    numbers cannot settle it either: `spaces` is specified as identifying every
    occupied slot "without assuming a numbering convention".

    This is the half that must NOT follow the tabs fix. Naming three positions
    is reporting what the panel published; naming a voltage for them would be
    inventing one.
    """
    assert construct_voltage_attribute(_make_circuit([17, 19, 21])) is None
    assert construct_voltage_attribute(_make_circuit([2, 4, 6, 8])) is None


def test_end_to_end_tabs_workflow() -> None:
    """Test the complete workflow from circuit data to tabs attribute and voltage."""
    circuit = _make_circuit([30, 32])

    tabs_attr = construct_tabs_attribute(circuit)
    assert tabs_attr == "tabs [30:32]"

    assert construct_voltage_attribute(circuit) == 240


def test_amperage_calculation() -> None:
    """Test amperage calculation using voltage and power."""
    # 120V at 1200W -> 10A
    circuit_120v = _make_circuit([28], instant_power_w=1200.0)
    voltage_120v = construct_voltage_attribute(circuit_120v)
    assert voltage_120v is not None
    assert circuit_120v.instant_power_w / voltage_120v == 10.0

    # 240V at 4800W -> 20A
    circuit_240v = _make_circuit([30, 32], instant_power_w=4800.0)
    voltage_240v = construct_voltage_attribute(circuit_240v)
    assert voltage_240v is not None
    assert circuit_240v.instant_power_w / voltage_240v == 20.0

    # 0W -> 0A
    circuit_zero = _make_circuit([28], instant_power_w=0.0)
    voltage_zero = construct_voltage_attribute(circuit_zero)
    assert voltage_zero is not None
    assert circuit_zero.instant_power_w / voltage_zero == 0.0
