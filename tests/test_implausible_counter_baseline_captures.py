"""Every captured counter beyond its circuit's lifetime bound is shown as published and never the baseline.

A property over every capture: the counters judged implausible are exactly
those above their circuit's bound, and after a real setup each one's sensor
reports the published reading while holding no dip baseline from it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel.counter_plausibility import (
    implausible_counters,
    lifetime_bound_wh,
)
from custom_components.span_panel.energy_orientation import CircuitMeter, EnergyCounter
from custom_components.span_panel.runtime import loaded_runtime_data

from .captures_replay import CAPTURES, Capture, snapshot
from .test_expected_entities import _install

COUNTERS = {"consumed": EnergyCounter.CONSUMED, "produced": EnergyCounter.PRODUCED}

FLAGGED = [c for c in CAPTURES if implausible_counters(snapshot(c.tree()))]


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
def test_exactly_the_counters_above_their_bound_are_flagged(captured: Capture) -> None:
    replayed = snapshot(captured.tree())
    flagged = {(row.circuit_id, row.counter) for row in implausible_counters(replayed)}

    for circuit_id, circuit in replayed.circuits.items():
        bound = lifetime_bound_wh(circuit)
        for counter, reading in (
            ("consumed", circuit.consumed_energy_wh),
            ("produced", circuit.produced_energy_wh),
        ):
            above = bound is not None and reading is not None and reading > bound
            assert ((circuit_id, counter) in flagged) is above, (circuit_id, counter)


def test_some_capture_has_an_implausible_counter() -> None:
    """The property below would pass vacuously over captures that have none."""
    assert FLAGGED


@pytest.mark.parametrize("captured", FLAGGED, ids=[c.name for c in FLAGGED])
async def test_a_flagged_counter_is_shown_as_published_and_never_the_baseline(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    entry = await _install(hass, tree, captured.name)
    runtime = loaded_runtime_data(entry)
    assert runtime is not None
    sources = runtime.coordinator._energy_offset_sources

    for row in implausible_counters(snapshot(tree)):
        sensor = sources[(CircuitMeter(row.circuit_id), COUNTERS[row.counter])]
        assert sensor.native_value == row.reading_wh, (row.circuit_id, row.counter)
        assert sensor._last_panel_reading is None, (row.circuit_id, row.counter)
        assert sensor._implausible_reading == row.reading_wh
        assert sensor.energy_offset == 0.0
