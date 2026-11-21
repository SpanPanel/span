"""The services that answer a typed shape answer all of it, as JSON.

`get_monitoring_status` and `get_graph_settings` build a `TypedDict` and answer
it through `json_values.json_object`, so every field the shape declares reaches
the card. Run over a replayed panel's real setup, with monitoring on.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
from typing import Final

from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel.const import DEFAULT_GRAPH_HORIZON, DOMAIN
from custom_components.span_panel.current_monitor import (
    CircuitMonitoringStatus,
    PointMonitoringStatus,
)
from custom_components.span_panel.services import _async_register_graph_horizon_services

from .captures_replay import capture
from .replayed_panel import Panel, replayed_panels

TREE: Final = "r202645-e"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


async def _respond(hass: HomeAssistant, service: str) -> object:
    response = await hass.services.async_call(
        DOMAIN, service, {}, blocking=True, return_response=True
    )
    # What Home Assistant sends a caller: it must survive JSON as it is.
    assert json.loads(json.dumps(response)) == response
    return response


async def test_the_monitoring_status_answers_every_field_of_every_point(
    panels: Callable[..., Panel],
) -> None:
    """Each circuit and the mains carry exactly the fields their shapes declare.

    Fails when the status is answered as anything but the whole shape.
    """
    panel = panels(capture(TREE).tree())
    await panel.set_up(capture(TREE).tree())

    response = await _respond(panel.hass, "get_monitoring_status")

    assert isinstance(response, dict)
    assert response["enabled"] is True
    circuits, mains = response["circuits"], response["mains"]
    assert isinstance(circuits, dict) and len(circuits) == len(panel.snapshot.circuits)
    assert isinstance(mains, dict) and len(mains) == 1
    for entry in circuits.values():
        assert set(entry) == set(CircuitMonitoringStatus.__annotations__) | set(
            PointMonitoringStatus.__annotations__
        )
    for entry in mains.values():
        assert set(entry) == set(PointMonitoringStatus.__annotations__)


async def test_the_graph_settings_answer_every_horizon(panels: Callable[..., Panel]) -> None:
    """The global horizon, a circuit's and a sub-device's, as the card reads them.

    Fails when the settings are answered as anything but the whole shape.
    """
    panel = panels(capture(TREE).tree())
    await panel.set_up(capture(TREE).tree())
    circuit_id = next(iter(panel.snapshot.circuits))
    await panel.call("set_circuit_graph_horizon", circuit_id=circuit_id, horizon="1w")
    await panel.call(
        "set_subdevice_graph_horizon", subdevice_id=panel.runtime.panel_device_id, horizon="1d"
    )

    response = await _respond(panel.hass, "get_graph_settings")

    assert response == {
        "global_horizon": DEFAULT_GRAPH_HORIZON,
        "circuits": {circuit_id: {"horizon": "1w", "has_override": True}},
        "sub_devices": {panel.runtime.panel_device_id: {"horizon": "1d", "has_override": True}},
    }


async def test_the_graph_settings_with_no_panel_loaded_answer_the_same_shape(
    hass: HomeAssistant,
) -> None:
    """With no panel loaded, the defaults: the global horizon, and no override of either kind.

    Fails when the fallback answers a shape other than `GraphHorizonSettings`.
    """
    _async_register_graph_horizon_services(hass)

    response = await _respond(hass, "get_graph_settings")

    assert response == {"global_horizon": DEFAULT_GRAPH_HORIZON, "circuits": {}, "sub_devices": {}}
