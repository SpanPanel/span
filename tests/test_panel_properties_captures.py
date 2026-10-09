"""Every capture's lock, readings and mains limit follow what that panel declares and publishes.

A property over every capture: chargers with a settable lock get a lock entity
whose commands go to the lock's `/set` topic; a charger that declares no
advertised current gets no sensor for it; the busbar current and frequency
sensors exist exactly where the panel declares those properties; a circuit
that declares no switch gets no switch or select; and the current monitor
judges the mains against the upstream protection where there is no main
breaker.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.span_panel.id_builder import (
    build_evse_unique_id,
    build_panel_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
)

from .captures_replay import (
    CAPTURES,
    Capture,
    RetainedTree,
    description,
    devices_of_type,
    panel_device_id,
    snapshot,
)
from .test_current_monitor import _make_hass, _make_monitor
from .test_expected_entities import _install

CIRCUIT_TYPE = "energy.ebus.device.circuit"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def _declares(tree: RetainedTree, device_id: str, node: str, prop: str | None = None) -> bool:
    nodes = description(tree, device_id).get("nodes")
    declared = nodes.get(node) if isinstance(nodes, dict) else None
    if prop is None:
        return isinstance(declared, dict)
    properties = declared.get("properties") if isinstance(declared, dict) else None
    return isinstance(properties, dict) and prop in properties


def _registered(hass: HomeAssistant, entry_id: str) -> set[str]:
    return {e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id)}


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_each_settable_lock_is_a_lock_entity_on_its_set_topic(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    entry = await _install(hass, tree, captured.name)
    registered = _registered(hass, entry.entry_id)

    for evse_id, evse in replayed.evse.items():
        lock = build_evse_unique_id(replayed.serial_number, evse_id, "lock")
        assert (lock in registered) is (evse.lock_control is not None), evse_id
        if evse.lock_control is not None:
            assert evse.lock_control.topic.endswith("/switch/lock-state/set")


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_readings_exist_exactly_where_declared(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    panel = panel_device_id(tree)
    entry = await _install(hass, tree, captured.name)
    registered = _registered(hass, entry.entry_id)
    serial = replayed.serial_number

    for key, prop in (("busbar_current", "busbar-current"), ("frequency", "frequency")):
        assert (build_panel_unique_id(serial, key) in registered) is _declares(
            tree, panel, "meter", prop
        ), key

    chargers = {
        tree[device].get("info/serial-number", device): device
        for device in devices_of_type(tree, "energy.ebus.device.evse")
    }
    for evse_id in replayed.evse:
        device = chargers.get(evse_id, evse_id if evse_id in tree else None)
        assert device is not None, evse_id
        advertised = build_evse_unique_id(serial, evse_id, "evse_advertised_current")
        assert (advertised in registered) is _declares(
            tree, device, "meter", "advertised-current"
        ), evse_id


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_a_circuit_that_declares_no_switch_has_no_control(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    serial = snapshot(tree).serial_number
    entry = await _install(hass, tree, captured.name)
    registered = _registered(hass, entry.entry_id)

    for circuit in devices_of_type(tree, CIRCUIT_TYPE):
        if not _declares(tree, circuit, "switch"):
            assert build_switch_unique_id(serial, circuit) not in registered, circuit
            assert build_select_unique_id(serial, circuit) not in registered, circuit


WITHOUT_MAIN_BREAKER = [
    c
    for c in CAPTURES
    if snapshot(c.tree()).main_breaker_rating_a is None
    and snapshot(c.tree()).upstream_protection_rating_a is not None
]


def test_some_capture_has_no_main_breaker() -> None:
    """The property below would pass vacuously over captures that all have one."""
    assert WITHOUT_MAIN_BREAKER


@pytest.mark.parametrize(
    "captured", WITHOUT_MAIN_BREAKER, ids=[c.name for c in WITHOUT_MAIN_BREAKER]
)
def test_the_monitor_judges_the_mains_against_the_upstream_protection(captured: Capture) -> None:
    replayed = snapshot(captured.tree())
    monitor = _make_monitor(_make_hass())

    monitor.process_snapshot(replayed)

    (mains,) = monitor.get_monitoring_status()["mains"].values()
    assert mains["breaker_rating_a"] == float(replayed.upstream_protection_rating_a or 0)
