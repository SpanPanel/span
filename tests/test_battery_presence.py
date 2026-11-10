"""A battery the panel declares gets its device and sensors before its first reading.

Where the panel describes its devices, `battery.present` says whether a battery
is declared, and that decides: the battery's device and sensors exist from
setup and read unknown until it reports, rather than appearing only once a
state of charge arrives. Where the panel describes nothing (`present` is None,
as on the flat schema), the state of charge still decides, as it always has.
"""

from __future__ import annotations

from dataclasses import replace

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from span_panel_api import SpanBatterySnapshot

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.helpers import has_bess
from custom_components.span_panel.util import SUB_DEVICE_BESS, classify_sub_device_identifier

from .captures_replay import snapshot
from .factories import SpanPanelSnapshotFactory
from .test_expected_entities import _battery_unvalued, _install, _no_battery


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.mark.parametrize(
    ("present", "soe_percentage", "expected"),
    [
        (True, None, True),
        (True, 80.0, True),
        (False, None, False),
        (None, 80.0, True),
        (None, None, False),
    ],
    ids=["declared-unvalued", "declared-valued", "undeclared", "flat-valued", "flat-unvalued"],
)
def test_the_declaration_decides_and_the_flat_rule_is_unchanged(
    present: bool | None, soe_percentage: float | None, expected: bool
) -> None:
    battery = replace(SpanBatterySnapshot(), present=present, soe_percentage=soe_percentage)

    assert has_bess(SpanPanelSnapshotFactory.create(battery=battery)) is expected


def _battery_devices(hass: HomeAssistant, entry_id: str) -> list[dr.DeviceEntry]:
    return [
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
        for domain, identifier in device.identifiers
        if domain == DOMAIN and classify_sub_device_identifier(identifier) == SUB_DEVICE_BESS
    ]


async def test_a_declared_battery_gets_its_device_before_its_first_reading(
    hass: HomeAssistant,
) -> None:
    tree = _battery_unvalued()
    unvalued = snapshot(tree).battery
    assert unvalued.present is True
    assert unvalued.soe_percentage is None

    entry = await _install(hass, tree, "battery-declared-unvalued")
    (battery,) = _battery_devices(hass, entry.entry_id)
    registry = er.async_get(hass)
    soe = [
        entity
        for entity in er.async_entries_for_device(registry, battery.id)
        if entity.translation_key == "battery_level"
    ]

    assert len(soe) == 1
    state = hass.states.get(soe[0].entity_id)
    assert state is not None and state.state == STATE_UNKNOWN


async def test_a_panel_that_declares_no_battery_gets_none(hass: HomeAssistant) -> None:
    tree = _no_battery()
    assert snapshot(tree).battery.present is False

    entry = await _install(hass, tree, "battery-undeclared")

    assert _battery_devices(hass, entry.entry_id) == []
