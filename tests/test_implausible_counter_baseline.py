"""An energy counter reading beyond what its breaker could ever pass is never the dip baseline.

A breaker's rating at the circuit's nominal voltage, for thirty years without a
break, bounds what its counter can hold. A reading above that is shown as
published, but it neither books a dip nor becomes `_last_panel_reading`, live or
restored, so the panel correcting it in place books nothing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel.counter_plausibility import (
    LIFETIME_HOURS,
    implausible_counters,
    lifetime_bound_wh,
)
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.grace_period import SpanEnergyExtraStoredData
from custom_components.span_panel.sensor_circuit import SpanCircuitEnergySensor
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS

from .captures_replay import MAIN32_CAPTURES, Capture, snapshot
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .test_diagnostics import _reference_entry
from .test_energy_dip_compensation import DummyDipSensor

BOUND: Final = 15.0 * 120.0 * LIFETIME_HOURS
"""A 15 A single-pole circuit's lifetime bound."""

IMPLAUSIBLE: Final = 2_504_911_685_498.9
"""An illustrative counter far beyond any breaker's lifetime."""


class BoundedDipSensor(DummyDipSensor):
    """A dip-compensated counter with a lifetime bound, as a circuit's energy sensor has."""

    def __init__(self, bound: float = BOUND) -> None:
        """Bound the counter at `bound` Wh."""
        super().__init__(dip_enabled=True, state_class=SensorStateClass.TOTAL_INCREASING)
        self._bound = bound

    def _reading_is_implausible(self, reading: float) -> bool:
        return reading > self._bound


def _feed(sensor: BoundedDipSensor, *readings: float) -> None:
    for reading in readings:
        sensor._mock_panel_value = reading
        sensor._update_native_value()


@pytest.mark.parametrize(
    ("rating", "tabs", "nominal", "expected"),
    [
        (15.0, [1], None, 15.0 * 120.0 * LIFETIME_HOURS),
        (30.0, [1, 3], None, 30.0 * 240.0 * LIFETIME_HOURS),
        (20.0, [1, 3], 208.0, 20.0 * 208.0 * LIFETIME_HOURS),
        (None, [1], None, None),
        (15.0, [], None, None),
    ],
    ids=["one-pole", "two-pole", "declared-voltage", "unrated", "no-poles"],
)
def test_the_lifetime_bound(
    rating: float | None, tabs: list[int], nominal: float | None, expected: float | None
) -> None:
    circuit = replace(
        SpanCircuitSnapshotFactory.create(tabs=tabs, breaker_rating_a=rating),
        nominal_voltage_v=nominal,
    )

    assert lifetime_bound_wh(circuit) == expected


def test_an_implausible_reading_is_shown_as_published_and_never_the_baseline() -> None:
    sensor = BoundedDipSensor()

    _feed(sensor, 1000.0, IMPLAUSIBLE)

    assert sensor.native_value == IMPLAUSIBLE
    assert sensor._last_panel_reading == 1000.0
    assert sensor._implausible_reading == IMPLAUSIBLE
    assert sensor._implausible_since is not None


def test_the_correction_books_no_dip() -> None:
    sensor = BoundedDipSensor()

    _feed(sensor, 1000.0, IMPLAUSIBLE, 1001.0)

    assert sensor._energy_offset == 0.0
    assert sensor._pending_dip is None
    assert sensor._last_panel_reading == 1001.0
    assert sensor.native_value == 1001.0
    assert sensor._implausible_reading is None


def test_a_counter_implausible_from_its_first_reading_books_no_dip_when_corrected() -> None:
    sensor = BoundedDipSensor()

    _feed(sensor, IMPLAUSIBLE, IMPLAUSIBLE, 5.0)

    assert sensor._energy_offset == 0.0
    assert sensor._pending_dip is None
    assert sensor._last_panel_reading == 5.0


def test_an_implausible_stored_baseline_is_dropped_on_restore_and_the_offset_kept() -> None:
    """A baseline an earlier release stored from such a reading cannot anchor a dip after a restart."""
    stored = SpanEnergyExtraStoredData(
        native_value=IMPLAUSIBLE + 7.0,
        native_unit_of_measurement="Wh",
        last_valid_state=IMPLAUSIBLE + 7.0,
        last_valid_changed=None,
        energy_offset=7.0,
        last_panel_reading=IMPLAUSIBLE,
    )
    sensor = BoundedDipSensor()

    sensor._restore_dip_state(stored)
    _feed(sensor, 1000.0)

    assert sensor._last_panel_reading == 1000.0
    assert sensor._energy_offset == 7.0
    assert sensor._pending_dip is None
    assert sensor.native_value == 1007.0


def test_the_flag_survives_a_restart() -> None:
    before = BoundedDipSensor()
    _feed(before, 1000.0, IMPLAUSIBLE)
    stored = SpanEnergyExtraStoredData.from_dict(before.extra_restore_state_data.as_dict())
    assert stored is not None

    after = BoundedDipSensor()
    after._restore_dip_state(stored)

    assert after._implausible_reading == IMPLAUSIBLE
    assert after._implausible_since == before._implausible_since
    assert after._last_panel_reading == 1000.0


async def test_diagnostics_list_each_implausible_counter(hass: HomeAssistant) -> None:
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="c-1",
        tabs=[1],
        breaker_rating_a=15.0,
        consumed_energy_wh=IMPLAUSIBLE,
        produced_energy_wh=10.0,
    )
    panel = SpanPanelSnapshotFactory.create(circuits={"c-1": circuit})

    result = await async_get_config_entry_diagnostics(hass, _reference_entry(panel))

    assert result["implausible_counters"] == [
        {"circuit_id": "c-1", "counter": "consumed", "reading_wh": IMPLAUSIBLE, "bound_wh": BOUND}
    ]


@pytest.mark.parametrize("captured", MAIN32_CAPTURES, ids=[c.name for c in MAIN32_CAPTURES])
def test_no_main32_counter_is_implausible(captured: Capture) -> None:
    assert implausible_counters(snapshot(captured.tree())) == []


def test_the_sensor_is_told_by_its_circuit() -> None:
    """A circuit energy sensor applies its own circuit's bound."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c-1", tabs=[1], breaker_rating_a=15.0)
    panel = SpanPanelSnapshotFactory.create(circuits={"c-1": circuit})
    coordinator = MagicMock()
    coordinator.data = panel
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {}
    consumed = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_energy_consumed")
    sensor = SpanCircuitEnergySensor(coordinator, consumed, panel, "c-1")

    assert sensor._reading_is_implausible(BOUND + 1.0) is True
    assert sensor._reading_is_implausible(BOUND) is False
