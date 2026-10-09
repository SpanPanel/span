"""Every captured group of circuits that share a meter or relay keeps its entities and names its peers.

A property over every capture: groups are read from what each tree declares,
circuits naming each other in `shared-with-device-ids`, and every expectation
comes from the same capture.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from span_panel_api import shared_meter_groups

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.current_monitor import monitored_circuits
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.helpers import construct_circuit_label, shared_relay_groups
from custom_components.span_panel.id_builder import build_circuit_unique_id, build_switch_unique_id

from .captures_replay import CAPTURES, Capture, snapshot
from .test_expected_entities import _install, _topology

WITH_GROUPS = [c for c in CAPTURES if shared_meter_groups(snapshot(c.tree()).circuits)]

MEMBER_SENSORS = ("instantPowerW", "producedEnergyWh", "consumedEnergyWh", "current")


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def test_some_capture_has_a_shared_group() -> None:
    """The properties below would pass vacuously over captures that have none."""
    assert WITH_GROUPS


@pytest.mark.parametrize("captured", WITH_GROUPS, ids=[c.name for c in WITH_GROUPS])
async def test_every_member_keeps_its_entities_and_names_its_peers(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    entry = await _install(hass, tree, captured.name)
    serial = str(entry.unique_id)
    registry = er.async_get(hass)

    for members in shared_meter_groups(replayed.circuits).values():
        for member in members:
            for key in MEMBER_SENSORS:
                unique_id = build_circuit_unique_id(serial, member, key)
                assert registry.async_get_entity_id("sensor", DOMAIN, unique_id), unique_id
            switch = registry.async_get_entity_id(
                "switch", DOMAIN, build_switch_unique_id(serial, member)
            )
            assert switch is not None, member

            peers = [
                construct_circuit_label(replayed.circuits[peer], peer)
                for peer in members
                if peer != member
            ]
            power = registry.async_get_entity_id(
                "sensor", DOMAIN, build_circuit_unique_id(serial, member, "instantPowerW")
            )
            assert power is not None
            for entity_id in (power, switch):
                state = hass.states.get(entity_id)
                assert state is not None, entity_id
                assert state.attributes["meter_shared_with"] == peers, entity_id
                assert state.attributes["relay_shared_with"] == peers, entity_id


@pytest.mark.parametrize("captured", WITH_GROUPS, ids=[c.name for c in WITH_GROUPS])
def test_a_shared_meter_is_rated_by_its_members_sum(captured: Capture) -> None:
    replayed = snapshot(captured.tree())
    points = monitored_circuits(replayed)

    for key, members in shared_meter_groups(replayed.circuits).items():
        ratings = [replayed.circuits[member].breaker_rating_a for member in members]
        assert all(rating is not None for rating in ratings), key
        for member in members:
            assert points[member].point_id == key
            assert points[member].basis == "group"
            assert points[member].rating_a == sum(r for r in ratings if r is not None)


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_topology_and_diagnostics_carry_the_groups(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    meter_groups = shared_meter_groups(replayed.circuits)
    relay_groups = shared_relay_groups(replayed.circuits)
    entry = await _install(hass, tree, captured.name)
    topology = await _topology(hass, hass_ws_client, entry)
    assert isinstance(topology, dict)
    rows = topology["circuits"]
    assert isinstance(rows, dict)

    meter_of = {member: key for key, members in meter_groups.items() for member in members}
    relay_of = {member: key for key, members in relay_groups.items() for member in members}
    for circuit_id, row in rows.items():
        assert row["shared_meter_group"] == meter_of.get(circuit_id), circuit_id
        assert row["shared_relay_group"] == relay_of.get(circuit_id), circuit_id

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["shared_meter_groups"] == meter_groups
    assert diagnostics["shared_relay_groups"] == relay_groups
