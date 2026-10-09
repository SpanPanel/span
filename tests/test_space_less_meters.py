"""A meter without a breaker space gets meter entities only, a name from its id, and no grid slot.

The library reads a circuit device that declares no `info/spaces` as a meter
outside the panel: import-positive power, imported energy as consumed, exported
energy as produced, no relay, no breaker and no position. Here such a meter is
added to the MAIN 32 r202639 capture as a device of its own, `meter-a`, and the
tree is set up through the integration's real setup, so every assertion is about
what the pinned library and the integration make of a declaration rather than
about values a test wrote into a snapshot.
"""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Final

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.span_panel.const import DOMAIN, USE_CIRCUIT_NUMBERS
from custom_components.span_panel.helpers import outside_meter_label
from custom_components.span_panel.id_builder import (
    build_circuit_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
)
from custom_components.span_panel.sensor_circuit import (
    _resolve_circuit_identifier,
    _resolve_circuit_identifier_for_sync,
)

from .captures_replay import (
    RetainedTree,
    capture,
    description,
    panel_device_id,
    snapshot,
    without_topics,
)
from .test_expected_entities import R202639_CAPTURE, _install, _topology

METER: Final = "meter-a"
CIRCUIT_TYPE: Final = "energy.ebus.device.circuit"

METER_VALUES: Final = {
    "meter/active-power": "13.5",
    "meter/current": "0.1",
    "meter/imported-energy": "150745.3",
    "meter/exported-energy": "944.4",
}
"""What the meter publishes: illustrative values, import-positive as the wire states them."""

METER_SENSOR_KEYS: Final = (
    "instantPowerW",
    "producedEnergyWh",
    "consumedEnergyWh",
    "netEnergyWh",
    "current",
)
"""The circuit sensors a meter gets: power, the three energies and current."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def _float_property(name: str, unit: str) -> dict[str, str]:
    return {"datatype": "float", "name": name, "unit": unit}


def _with_meter(*, valued: bool = True) -> RetainedTree:
    """Add one circuit device to the capture: a meter that declares no breaker space."""
    tree = capture(R202639_CAPTURE).tree()
    panel = panel_device_id(tree)
    panel_description = dict(description(tree, panel))
    children = panel_description.get("children")
    assert isinstance(children, list)
    panel_description["children"] = [*children, METER]
    tree[panel]["$description"] = json.dumps(panel_description)
    tree[METER] = {
        "$description": json.dumps(
            {
                "homie": "5.0",
                "name": METER,
                "version": 1,
                "type": CIRCUIT_TYPE,
                "parent": panel,
                "root": panel,
                "children": [],
                "extensions": [],
                "nodes": {
                    "meter": {
                        "name": "meter",
                        "type": "energy.ebus.capability.meter",
                        "properties": {
                            "active-power": _float_property("Measured active power", "W"),
                            "current": _float_property("Measured current", "A"),
                            "imported-energy": _float_property("Measured energy imported", "Wh"),
                            "exported-energy": _float_property("Measured energy exported", "Wh"),
                        },
                    }
                },
            }
        ),
        "$state": "ready",
        **METER_VALUES,
    }
    if not valued:
        return without_topics(tree, METER, *METER_VALUES)
    return tree


def _meter_sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> er.RegistryEntry:
    """Return the meter's sensor for one description key, as the registry holds it."""
    registry = er.async_get(hass)
    unique_id = build_circuit_unique_id(str(entry.unique_id), METER, key)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    registered = registry.async_get(entity_id)
    assert registered is not None
    return registered


def _meter_state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    current = hass.states.get(_meter_sensor(hass, entry, key).entity_id)
    assert current is not None, key
    return current.state


def test_the_label_is_the_ids_trailing_number_or_the_whole_id() -> None:
    assert outside_meter_label("meter-7") == "Remote CT 7"
    assert outside_meter_label("meter-12") == "Remote CT 12"
    assert outside_meter_label("ab") == "Remote CT ab"


def test_the_library_reads_the_added_device_as_a_meter_outside_the_panel() -> None:
    """The premise every other test here rests on."""
    meter = snapshot(_with_meter()).circuits[METER]

    assert meter.measures_outside_panel is True
    assert meter.tabs == []
    assert meter.is_user_controllable is False
    assert meter.breaker_rating_a is None


async def test_a_space_less_meter_gets_meter_entities_only(hass: HomeAssistant) -> None:
    entry = await _install(hass, _with_meter(), "space-less-meter")
    serial = str(entry.unique_id)
    registered = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }

    assert {build_circuit_unique_id(serial, METER, key) for key in METER_SENSOR_KEYS} <= registered
    assert build_circuit_unique_id(serial, METER, "breaker_rating") not in registered
    assert build_switch_unique_id(serial, METER) not in registered
    assert build_select_unique_id(serial, METER) not in registered


async def test_a_space_less_meter_reads_its_wire_values_import_positive(
    hass: HomeAssistant,
) -> None:
    """Power and consumed are what it imports, produced what it exports; Net is their difference."""
    entry = await _install(hass, _with_meter(), "space-less-meter")

    def state(key: str) -> float:
        return float(_meter_state(hass, entry, key))

    imported = float(METER_VALUES["meter/imported-energy"])
    exported = float(METER_VALUES["meter/exported-energy"])
    assert state("instantPowerW") == float(METER_VALUES["meter/active-power"])
    assert state("consumedEnergyWh") == imported
    assert state("producedEnergyWh") == exported
    assert state("netEnergyWh") == pytest.approx(imported - exported)


async def test_an_unvalued_space_less_meter_reads_unknown(hass: HomeAssistant) -> None:
    """Declared but not yet published: still a meter outside the panel, every reading unknown."""
    tree = _with_meter(valued=False)
    assert snapshot(tree).circuits[METER].measures_outside_panel is True

    entry = await _install(hass, tree, "space-less-meter-unvalued")

    for key in ("instantPowerW", "producedEnergyWh", "consumedEnergyWh", "netEnergyWh"):
        assert _meter_state(hass, entry, key) == STATE_UNKNOWN, key


async def test_a_space_less_meter_is_named_by_its_id_in_both_modes(hass: HomeAssistant) -> None:
    """No name and no positions to number, so both naming modes give the same label."""
    meter = snapshot(_with_meter()).circuits[METER]
    label = outside_meter_label(METER)

    assert _resolve_circuit_identifier(meter, METER, {USE_CIRCUIT_NUMBERS: False}) == label
    assert _resolve_circuit_identifier(meter, METER, {USE_CIRCUIT_NUMBERS: True}) == label
    assert _resolve_circuit_identifier_for_sync(meter, METER) == label
    named = replace(meter, name="Anything")
    assert _resolve_circuit_identifier(named, METER, {USE_CIRCUIT_NUMBERS: False}) == label

    entry = await _install(hass, _with_meter(), "space-less-meter")
    assert _meter_sensor(hass, entry, "instantPowerW").original_name == f"{label} Power"


async def test_topology_marks_a_space_less_meter_outside_the_panel(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await _install(hass, _with_meter(), "space-less-meter")
    topology = await _topology(hass, hass_ws_client, entry)
    assert isinstance(topology, dict)
    circuits = topology["circuits"]
    assert isinstance(circuits, dict)

    assert circuits[METER]["outside_panel"] is True
    assert circuits[METER]["tabs"] == []
    hosted = {circuit_id: row for circuit_id, row in circuits.items() if circuit_id != METER}
    assert hosted
    assert all(row["outside_panel"] is False for row in hosted.values())


async def test_topology_names_a_space_less_meter_like_its_entities(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    tree = _with_meter()
    replayed = snapshot(tree)
    entry = await _install(hass, tree, "space-less-meter")
    topology = await _topology(hass, hass_ws_client, entry)
    assert isinstance(topology, dict)
    circuits = topology["circuits"]
    assert isinstance(circuits, dict)

    assert circuits[METER]["name"] == outside_meter_label(METER)
    for circuit_id, row in circuits.items():
        if circuit_id != METER:
            assert row["name"] == (replayed.circuits[circuit_id].name or None), circuit_id
