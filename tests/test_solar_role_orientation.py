"""A circuit whose feeds role is solar reads as generation, as a PV circuit does.

The eBus catalog's `connection/feeds-role` lets a panel say a circuit feeds a
solar source without naming a PV device behind it. Such a circuit's power and
Net Energy then read the way a PV circuit's do: power positive while it
generates, Net counting produced energy up. Its controls stay as they were,
because the PV control rule keys on the device type, not on the role.

No reference capture declares a solar role, so these tests pin the catalog's
behaviour with synthetic circuits. The MAIN 32 captures hold the other side:
their orientation is what it was before roles were read.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

import pytest
from span_panel_api import FeedsRole, SpanCircuitSnapshot

from custom_components.span_panel.energy_orientation import (
    GENERATION,
    LOAD,
    circuit_is_generation,
    circuit_net_orientation,
)
from custom_components.span_panel.helpers import (
    circuit_has_a_breaker_switch,
    circuit_has_a_priority_select,
)
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS

from .captures_replay import CAPTURES, Capture, snapshot
from .factories import SpanCircuitSnapshotFactory

POWER: Final = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")
NET: Final = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_energy_net")


def _solar_role_circuit() -> SpanCircuitSnapshot:
    """A circuit that generates: the panel's frame reads it negative, and produced exceeds consumed."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="solar-role",
        name="Roof Array",
        tabs=[12, 14],
        instant_power_w=-2400.0,
        consumed_energy_wh=30.0,
        produced_energy_wh=1000.0,
    )
    return replace(circuit, feeds_role="SOLAR")


@pytest.mark.spec_only
def test_a_solar_role_circuit_power_and_net_read_as_generation() -> None:
    circuit = _solar_role_circuit()

    assert circuit.device_type == "circuit"
    assert circuit_is_generation(circuit) is True
    assert circuit_net_orientation(circuit) is GENERATION
    assert POWER.value_fn(circuit) == 2400.0
    assert NET.value_fn(circuit) == pytest.approx(1000.0 - 30.0)


@pytest.mark.spec_only
def test_a_solar_role_circuit_keeps_its_controls() -> None:
    circuit = _solar_role_circuit()

    assert circuit_has_a_breaker_switch(circuit) is True
    assert circuit_has_a_priority_select(circuit) is True


OTHER_ROLES: Final[tuple[FeedsRole | None, ...]] = (
    "LOADS",
    "SUBPANEL",
    "STORAGE",
    "GENERATOR",
    "MIXED",
    "UNUSED",
    None,
)


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_any_other_role_leaves_a_circuit_load_oriented(role: FeedsRole | None) -> None:
    circuit = replace(_solar_role_circuit(), feeds_role=role)

    assert circuit_is_generation(circuit) is False
    assert circuit_net_orientation(circuit) is LOAD


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
def test_the_captures_orientation_is_their_device_types(captured: Capture) -> None:
    """Every captured circuit is oriented exactly as its device type alone orients it."""
    circuits = snapshot(captured.tree()).circuits

    assert circuits
    for circuit_id, circuit in circuits.items():
        assert circuit_is_generation(circuit) is (circuit.device_type == "pv"), circuit_id
