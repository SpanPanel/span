"""Every capture that declares a battery has its device; a circuit-fed battery reports its connection.

A property over every capture: the battery is selected by what the tree
declares, and every expectation comes from the same capture.
"""

from __future__ import annotations

from homeassistant.const import STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.util import SUB_DEVICE_BESS, classify_sub_device_identifier

from .captures_replay import CAPTURES, Capture, devices_of_type, snapshot
from .test_expected_entities import _install

BATTERY_TYPE = "energy.ebus.device.bess"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


CIRCUIT_FED = [c for c in CAPTURES if snapshot(c.tree()).battery.feed_circuit_id is not None]


def _battery_devices(hass: HomeAssistant, entry_id: str) -> list[dr.DeviceEntry]:
    return [
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
        for domain, identifier in device.identifiers
        if domain == DOMAIN and classify_sub_device_identifier(identifier) == SUB_DEVICE_BESS
    ]


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_a_battery_device_exists_exactly_where_one_is_declared(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    declared = bool(devices_of_type(tree, BATTERY_TYPE))
    entry = await _install(hass, tree, captured.name)
    batteries = _battery_devices(hass, entry.entry_id)

    assert len(batteries) == (1 if declared else 0)
    if not declared:
        return
    (battery,) = batteries
    levels = [
        entity
        for entity in er.async_entries_for_device(er.async_get(hass), battery.id)
        if entity.translation_key == "battery_level"
    ]
    assert len(levels) == 1
    state = hass.states.get(levels[0].entity_id)
    assert state is not None
    if replayed.battery.soe_percentage is None:
        assert state.state == STATE_UNKNOWN
    else:
        assert float(state.state) == replayed.battery.soe_percentage


def test_some_capture_has_a_circuit_fed_battery() -> None:
    """The property below would pass vacuously over captures that have none."""
    assert CIRCUIT_FED


@pytest.mark.parametrize("captured", CIRCUIT_FED, ids=[c.name for c in CIRCUIT_FED])
async def test_a_circuit_fed_battery_reports_its_connection(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    assert replayed.battery.connected is True
    entry = await _install(hass, tree, captured.name)
    (battery,) = _battery_devices(hass, entry.entry_id)
    connected = [
        entity
        for entity in er.async_entries_for_device(er.async_get(hass), battery.id)
        if entity.translation_key == "bess_connected"
    ]

    assert len(connected) == 1
    state = hass.states.get(connected[0].entity_id)
    assert state is not None and state.state == STATE_ON
