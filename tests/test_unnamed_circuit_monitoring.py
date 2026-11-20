"""Monitoring names a circuit without a name by its breaker positions, never by its wire id.

A circuit the panel publishes no name for is named, wherever the integration
shows it, by its label: "Circuit 9", or "Circuit 14 16" for a breaker on two
spaces. Monitoring named such a circuit by its id instead, which on firmware
r202645 is a long opaque one, in alert text and in its status.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from .captures_replay import capture, snapshot
from .replayed_panel import Panel, replayed_panels


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


@pytest.mark.parametrize("name", ["r202645-e", "main32_r202639"])
async def test_an_unnamed_circuit_is_monitored_under_its_label(
    panels: Callable[..., Panel], name: str
) -> None:
    """An alert and the monitoring status name a circuit without a name by its breaker positions.

    Never by its wire id, which on r202645 is the long new one. Fails when the
    monitor falls back to the circuit id again.
    """
    tree = capture(name).tree()
    snap = snapshot(tree)
    circuit_id, circuit = next(
        (cid, c)
        for cid, c in sorted(snap.circuits.items())
        if c.tabs
        and c.breaker_rating_a
        and not c.measures_outside_panel
        and not c.meter_shared_with
    )
    del tree[circuit_id]["info/name"]
    tree[circuit_id]["meter/current"] = str(2 * (circuit.breaker_rating_a or 0))
    label = "Circuit " + " ".join(str(tab) for tab in sorted(circuit.tabs))
    panel = panels(tree)

    with patch("custom_components.span_panel.current_monitor.dispatch_alert") as alerts:
        await panel.set_up(tree)

    names = {
        call.kwargs["alert_name"]
        for call in alerts.call_args_list
        if call.kwargs["alert_id"] == circuit_id
    }
    assert names == {label}
    assert panel.monitoring(circuit_id)["name"] == label
