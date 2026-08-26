"""A derived sensor whose inputs are unreported is unknown, not zero.

`span-panel-api` reports a reading the panel has not sent as `None` rather than
fabricating `0.0` (`SpanPanel/span#259`). The derived sensors here compose two
such readings, and each was coalescing with `or 0` — so a circuit that had
reported nothing produced a confident net energy of `0`, and the fabrication the
library stopped making reappeared one layer out.

`None` in, `None` out. `_process_raw_value` already maps that to the sensor's
unknown value and, on a `TOTAL_INCREASING` sensor, skips dip compensation
entirely — leaving `_last_panel_reading` untouched, which is what keeps the
absent reading from being mistaken for a counter reset when the real value
lands.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.sensor_definitions import (
    CIRCUIT_SENSORS,
    PANEL_ENERGY_SENSORS,
)
from tests.factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

_NET_ENERGY_SENSORS = [
    ("mainMeterNetEnergyWh", "main_meter_energy_consumed_wh", "main_meter_energy_produced_wh"),
    ("feedthroughNetEnergyWh", "feedthrough_energy_consumed_wh", "feedthrough_energy_produced_wh"),
]


def _circuit_value_fn(key: str) -> Callable[[SpanCircuitSnapshot], float | None]:
    """Return the value function of the named circuit sensor description."""
    return next(desc for desc in CIRCUIT_SENSORS if desc.key == key).value_fn


def _panel_value_fn(key: str) -> Callable[[SpanPanelSnapshot], float | str | None]:
    """Return the value function of the named panel energy sensor description."""
    return next(desc for desc in PANEL_ENERGY_SENSORS if desc.key == key).value_fn


class TestCircuitNetEnergy:
    """`circuit_energy_net` composes the two circuit counters."""

    def test_unknown_while_neither_counter_has_reported(self) -> None:
        """A circuit that has published nothing knows nothing about its net."""
        circuit = SpanCircuitSnapshotFactory.create(
            consumed_energy_wh=None, produced_energy_wh=None
        )

        assert _circuit_value_fn("circuit_energy_net")(circuit) is None

    def test_unknown_while_only_one_counter_has_reported(self) -> None:
        """Half a difference is not a smaller difference, it is no answer."""
        circuit = SpanCircuitSnapshotFactory.create(
            consumed_energy_wh=1500.0, produced_energy_wh=None
        )

        assert _circuit_value_fn("circuit_energy_net")(circuit) is None

    def test_reported_zeros_still_compute(self) -> None:
        """Two counters that have published zero have a net of zero."""
        circuit = SpanCircuitSnapshotFactory.create(consumed_energy_wh=0.0, produced_energy_wh=0.0)

        assert _circuit_value_fn("circuit_energy_net")(circuit) == 0.0

    def test_the_arithmetic_is_unchanged_when_both_report(self) -> None:
        """Consumption less generation, exactly as before."""
        circuit = SpanCircuitSnapshotFactory.create(
            consumed_energy_wh=1500.0, produced_energy_wh=400.0
        )

        assert _circuit_value_fn("circuit_energy_net")(circuit) == pytest.approx(1100.0)

    def test_a_pv_circuit_keeps_its_inverted_sign(self) -> None:
        """A PV circuit nets generation less consumption, the other way round."""
        circuit = SpanCircuitSnapshotFactory.create(
            device_type="pv", consumed_energy_wh=400.0, produced_energy_wh=1500.0
        )

        assert _circuit_value_fn("circuit_energy_net")(circuit) == pytest.approx(1100.0)


class TestCircuitPower:
    """`circuit_power` negates for PV, which cannot be done to `None`."""

    def test_unknown_while_the_meter_has_not_reported(self) -> None:
        """An unreported power reading passes straight through as unknown."""
        circuit = SpanCircuitSnapshotFactory.create(instant_power_w=None)

        assert _circuit_value_fn("circuit_power")(circuit) is None

    def test_unknown_for_a_pv_circuit_that_has_not_reported(self) -> None:
        """The negation is why this one raised rather than lying.

        `-None` is a `TypeError`, caught upstream and logged as a value-function
        failure on every PV circuit, on every replay.
        """
        circuit = SpanCircuitSnapshotFactory.create(device_type="pv", instant_power_w=None)

        assert _circuit_value_fn("circuit_power")(circuit) is None

    def test_a_pv_circuit_still_reports_generation_as_positive(self) -> None:
        """Backfeed is negative on the wire and positive on the sensor."""
        circuit = SpanCircuitSnapshotFactory.create(device_type="pv", instant_power_w=-1500.0)

        assert _circuit_value_fn("circuit_power")(circuit) == pytest.approx(1500.0)

    def test_a_pv_circuit_at_zero_does_not_report_negative_zero(self) -> None:
        """Preserved from the original expression.

        `-0.0` compares equal to `0.0` but renders as "-0.0" in the UI.
        """
        circuit = SpanCircuitSnapshotFactory.create(device_type="pv", instant_power_w=0.0)

        result = _circuit_value_fn("circuit_power")(circuit)

        assert result == 0.0
        assert str(result) == "0.0"


class TestPanelNetEnergy:
    """The two panel-level net sensors compose the lugs counters.

    Worse exposed than a circuit's: both lugs devices are told apart by a single
    `direction` property, so until it arrives neither role resolves and all four
    inputs are unreported at once.
    """

    @pytest.mark.parametrize(("key", "consumed_field", "produced_field"), _NET_ENERGY_SENSORS)
    def test_unknown_while_the_lugs_have_not_reported(
        self, key: str, consumed_field: str, produced_field: str
    ) -> None:
        """Neither lugs role resolved means no net to report."""
        snapshot = SpanPanelSnapshotFactory.create(**{consumed_field: None, produced_field: None})

        assert _panel_value_fn(key)(snapshot) is None

    @pytest.mark.parametrize(("key", "consumed_field", "produced_field"), _NET_ENERGY_SENSORS)
    def test_the_arithmetic_is_unchanged_when_both_report(
        self, key: str, consumed_field: str, produced_field: str
    ) -> None:
        """Import less export, exactly as before."""
        snapshot = SpanPanelSnapshotFactory.create(
            **{consumed_field: 5000.0, produced_field: 1200.0}
        )

        assert _panel_value_fn(key)(snapshot) == pytest.approx(3800.0)
