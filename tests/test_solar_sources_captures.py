"""Every capture makes a Solar device exactly where its panel publishes a PV flow or a solar source.

The panel's `power-flows/pv` is its solar figure, and a valued flow makes the
Solar device and PV Power whatever it reads, 0.0 included: what it reads says
whether the sun is up, not whether the panel has solar. That is how solar
DC-coupled into a battery, with no SOLAR circuit and no inverter device, gets
its sensors on a panel that publishes roles (SpanPanel/span#347). A battery
capture that leaves the flow unset gets no Solar device. PV the panel
publishes with no source to attribute it to is a diagnostics row.

The flow half of the expectation is read from the capture's retained topics,
not from the snapshot; the source half uses `solar_sources`, which
`test_solar_sources.py` pins on its own.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.solar_sources import solar_sources
from custom_components.span_panel.util import SUB_DEVICE_PV, classify_sub_device_identifier

from .captures_replay import CAPTURES, Capture, RetainedTree, capture, panel_device_id, snapshot
from .test_expected_entities import _install

PV_FLOW_TOPIC: Final = "power-flows/pv"

BATTERY_CAPTURE: Final = "r202639-c"
"""A panel that publishes roles, with a battery, no solar circuit and no inverter device."""


def _publishes_pv_flow(tree: RetainedTree) -> bool:
    """Whether the panel's retained topics carry a PV flow, whatever it reads."""
    return tree[panel_device_id(tree)].get(PV_FLOW_TOPIC, "") != ""


def _has_solar_source(replayed: SpanPanelSnapshot) -> bool:
    return bool(solar_sources(replayed)) or any(
        circuit.device_type == "pv" for circuit in replayed.circuits.values()
    )


FLOW_WITHOUT_SOURCE = [
    c
    for c in CAPTURES
    if _publishes_pv_flow(c.tree()) and not _has_solar_source(snapshot(c.tree()))
]

BATTERY_WITHOUT_FLOW = [
    c for c in CAPTURES if snapshot(c.tree()).battery.present and not _publishes_pv_flow(c.tree())
]


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def _solar_devices(hass: HomeAssistant, entry: MockConfigEntry) -> list[dr.DeviceEntry]:
    return [
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, identifier in device.identifiers
        if domain == DOMAIN and classify_sub_device_identifier(identifier) == SUB_DEVICE_PV
    ]


def _pv_power_entity_id(hass: HomeAssistant, replayed: SpanPanelSnapshot) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"span_{replayed.serial_number}_pv_power"
    )


def test_some_capture_publishes_a_pv_flow_and_no_solar_source() -> None:
    """The property below would pass vacuously if every published flow had a source beside it."""
    assert FLOW_WITHOUT_SOURCE


def test_some_capture_has_a_battery_and_no_pv_flow() -> None:
    """The property below would never see a battery without solar if no capture had one."""
    assert BATTERY_WITHOUT_FLOW


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_the_solar_device_follows_the_published_pv_flow(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    source = _has_solar_source(replayed)
    expected = _publishes_pv_flow(tree) or source

    entry = await _install(hass, tree, captured.name)

    assert bool(_solar_devices(hass, entry)) is expected
    assert (_pv_power_entity_id(hass, replayed) is not None) is expected

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    unattributed = None if source else replayed.power_flow_pv
    assert diagnostics["pv_power_without_source_w"] == unattributed


async def test_solar_fed_through_a_battery_reads_the_pv_flow(hass: HomeAssistant) -> None:
    """No circuit says SOLAR and no inverter is published: the flow alone is the solar (SpanPanel/span#347)."""
    tree = capture(BATTERY_CAPTURE).tree()
    tree[panel_device_id(tree)][PV_FLOW_TOPIC] = "-2500.0"
    replayed = snapshot(tree)
    assert replayed.publishes_solar_roles and replayed.battery.present
    assert not _has_solar_source(replayed)

    await _install(hass, tree, f"{BATTERY_CAPTURE}-dc-coupled-solar")

    entity_id = _pv_power_entity_id(hass, replayed)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == pytest.approx(2500.0)
