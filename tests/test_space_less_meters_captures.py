"""Every captured meter without a breaker space reads its wire values import-positive, with meter entities only.

A property over every capture: the meters are selected by what each tree
declares, a circuit device whose `$description` declares no `info/spaces`, and
every expectation is read from the same capture's published values.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.helpers import outside_meter_label
from custom_components.span_panel.id_builder import (
    build_circuit_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
)

from .captures_replay import CAPTURES, Capture, RetainedTree, description, devices_of_type
from .test_expected_entities import _install, _topology

CIRCUIT_TYPE = "energy.ebus.device.circuit"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def _space_less(tree: RetainedTree) -> list[str]:
    """Every circuit device the tree describes without a breaker space."""
    found: list[str] = []
    for device_id in devices_of_type(tree, CIRCUIT_TYPE):
        nodes = description(tree, device_id).get("nodes")
        info = nodes.get("info") if isinstance(nodes, dict) else None
        properties = info.get("properties") if isinstance(info, dict) else None
        if not (isinstance(properties, dict) and "spaces" in properties):
            found.append(device_id)
    return found


WITH_METERS = [captured for captured in CAPTURES if _space_less(captured.tree())]


def test_some_capture_has_a_space_less_meter() -> None:
    """The property below would pass vacuously over captures that have none."""
    assert WITH_METERS


@pytest.mark.parametrize("captured", WITH_METERS, ids=[c.name for c in WITH_METERS])
async def test_a_space_less_meter_reads_its_wire_values_import_positive(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    entry = await _install(hass, tree, captured.name)
    serial = str(entry.unique_id)
    registry = er.async_get(hass)

    def state(meter: str, key: str) -> float:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, build_circuit_unique_id(serial, meter, key)
        )
        assert entity_id is not None, (meter, key)
        current = hass.states.get(entity_id)
        assert current is not None, (meter, key)
        return float(current.state)

    for meter in _space_less(tree):
        wire = tree[meter]
        imported = float(wire["meter/imported-energy"])
        exported = float(wire["meter/exported-energy"])
        assert state(meter, "instantPowerW") == float(wire["meter/active-power"])
        assert state(meter, "consumedEnergyWh") == imported
        assert state(meter, "producedEnergyWh") == exported
        assert state(meter, "netEnergyWh") == pytest.approx(imported - exported)


@pytest.mark.parametrize("captured", WITH_METERS, ids=[c.name for c in WITH_METERS])
async def test_a_space_less_meter_gets_meter_entities_only(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    entry = await _install(hass, tree, captured.name)
    serial = str(entry.unique_id)
    registered = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }

    for meter in _space_less(tree):
        assert build_circuit_unique_id(serial, meter, "instantPowerW") in registered
        assert build_circuit_unique_id(serial, meter, "breaker_rating") not in registered
        assert build_switch_unique_id(serial, meter) not in registered
        assert build_select_unique_id(serial, meter) not in registered


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_topology_names_a_space_less_meter_like_its_entities(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, captured: Capture
) -> None:
    tree = captured.tree()
    meters = set(_space_less(tree))
    entry = await _install(hass, tree, captured.name)
    topology = await _topology(hass, hass_ws_client, entry)
    assert isinstance(topology, dict)
    circuits = topology["circuits"]
    assert isinstance(circuits, dict)

    for circuit_id, row in circuits.items():
        assert row["outside_panel"] is (circuit_id in meters), circuit_id
        if circuit_id in meters:
            assert row["name"] == outside_meter_label(circuit_id)
            assert row["tabs"] == []
