"""Every capture that publishes roles makes a Solar device only from a solar source.

A property over every capture: where the panel publishes roles and has no
solar source, no Solar device exists, whatever its power-flows PV figure
reads; where it does not publish roles, the sources are exactly its
inverters. PV power the panel publishes with no source to attribute it to is
a diagnostics row.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.helpers import has_pv
from custom_components.span_panel.solar_sources import solar_sources
from custom_components.span_panel.util import SUB_DEVICE_PV, classify_sub_device_identifier

from .captures_replay import CAPTURES, Capture, snapshot
from .test_expected_entities import _install

WITHOUT_SOLAR = [
    c
    for c in CAPTURES
    if snapshot(c.tree()).publishes_solar_roles
    and not solar_sources(snapshot(c.tree()))
    and snapshot(c.tree()).battery.present
]


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def test_some_capture_has_a_battery_and_no_solar() -> None:
    """The property below would pass vacuously over captures that all have solar."""
    assert WITHOUT_SOLAR


@pytest.mark.parametrize("captured", WITHOUT_SOLAR, ids=[c.name for c in WITHOUT_SOLAR])
async def test_a_battery_without_solar_creates_no_solar_device(
    hass: HomeAssistant, captured: Capture
) -> None:
    tree = captured.tree()
    replayed = snapshot(tree)
    assert has_pv(replayed) is False

    entry = await _install(hass, tree, captured.name)
    solar_devices = [
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, identifier in device.identifiers
        if domain == DOMAIN and classify_sub_device_identifier(identifier) == SUB_DEVICE_PV
    ]
    assert solar_devices == []

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    flow = replayed.power_flow_pv
    expected = flow if flow is not None and flow != 0 else None
    assert diagnostics["pv_power_without_source_w"] == expected
