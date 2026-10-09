"""The topology carries the panel's first and last breaker positions as the snapshot reports them.

The positions say which slots the breaker grid has, occupied or not; the
topology lists only real circuits beside them, never one made up for an empty
position. Where the panel reports a model with no known range, the library
sizes it 0 and takes the range from the occupied spaces, and the topology
carries exactly that.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from .captures_replay import CAPTURES, Capture, RetainedTree, capture, panel_device_id, snapshot
from .test_expected_entities import R202639_CAPTURE, _install, _topology

UNKNOWN_MODEL: Final = "UNKNOWN"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def _unknown_model() -> RetainedTree:
    """Return the capture with its panel reporting a model whose range is not known."""
    tree = capture(R202639_CAPTURE).tree()
    tree[panel_device_id(tree)]["info/model"] = UNKNOWN_MODEL
    return tree


async def _positions(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, tree: RetainedTree, stem: str
) -> dict[str, object]:
    entry = await _install(hass, tree, stem)
    topology = await _topology(hass, hass_ws_client, entry)
    assert isinstance(topology, dict)
    return topology


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.stem for c in CAPTURES])
async def test_the_topology_carries_the_snapshots_position_range(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    topology = await _positions(hass, hass_ws_client, tree, captured.stem)

    assert (topology["first_position"], topology["last_position"]) == (
        replayed.first_position,
        replayed.last_position,
    )
    assert topology["panel_size"] == replayed.panel_size


async def test_an_unknown_models_topology_is_sized_0_with_the_occupied_range(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    tree = _unknown_model()
    replayed = snapshot(tree)
    occupied = [tab for circuit in replayed.circuits.values() for tab in circuit.tabs]
    topology = await _positions(hass, hass_ws_client, tree, "unknown-model")

    assert topology["panel_size"] == 0
    assert (topology["first_position"], topology["last_position"]) == (min(occupied), max(occupied))


async def test_the_topology_lists_only_real_circuits_beside_the_range(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """No row is made up for an unoccupied position."""
    tree = capture(R202639_CAPTURE).tree()
    replayed = snapshot(tree)
    topology = await _positions(hass, hass_ws_client, tree, R202639_CAPTURE)
    circuits = topology["circuits"]
    assert isinstance(circuits, dict)

    assert set(circuits) == set(replayed.circuits)
