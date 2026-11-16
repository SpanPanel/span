"""An energy reading that has not changed writes no new state (#361).

The recorder stores a `states` row for every `state_changed` event, and Home
Assistant fires one whenever the state or any live attribute differs. That
includes an attribute listed in `_unrecorded_attributes`, which keeps the value
out of `state_attributes` but not the row out of `states`. So an attribute that
moves on every snapshot costs a row per snapshot per energy sensor, whatever the
reading does, and a panel has about three energy sensors per circuit.

These go through a real `EntityPlatform` and the real state machine, because the
event is the whole subject: the attribute dict on its own cannot show whether a
write happened.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST, EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_capture_events,
)
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.sensor_definitions import PANEL_ENERGY_SENSORS
from custom_components.span_panel.sensor_panel import SpanPanelEnergySensor

from .factories import SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device


def _coordinator(hass: HomeAssistant, snapshot: SpanPanelSnapshot) -> MagicMock:
    """A coordinator stand-in carrying what an energy sensor reads, online."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = hass
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    entry.add_to_hass(hass)
    coordinator.config_entry = entry
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, snapshot.serial_number),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    coordinator.dip_offset = MagicMock(return_value=0.0)
    return coordinator


async def _added_consumed_energy_sensor(
    hass: HomeAssistant, wh: float
) -> tuple[SpanPanelEnergySensor, MagicMock]:
    """The main meter's Consumed Energy, added through a real platform and holding its first reading.

    An energy sensor is added `unknown` and reads the panel on its first
    coordinator update, which is a real change of state. That update is
    delivered here, so a test counting writes counts only what follows it.
    """
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=wh)
    coordinator = _coordinator(hass, snapshot)
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )
    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)
    platform = MockEntityPlatform(
        hass, domain="sensor", platform_name=DOMAIN, logger=logging.getLogger(__name__)
    )
    platform.config_entry = coordinator.config_entry
    await platform.async_add_entities([sensor])
    await hass.async_block_till_done()
    assert sensor.entity_id is not None
    await _snapshot_arrives(hass, sensor, coordinator, wh)
    assert sensor.native_value == wh
    return sensor, coordinator


def _writes_of(events: list[Event[EventStateChangedData]], entity_id: str) -> int:
    """How many `state_changed` events, which the recorder stores a row for, name this entity."""
    return sum(1 for event in events if event.data["entity_id"] == entity_id)


async def _snapshot_arrives(
    hass: HomeAssistant, sensor: SpanPanelEnergySensor, coordinator: MagicMock, wh: float
) -> None:
    """A snapshot reading `wh` reaches the sensor, as the coordinator delivers one."""
    coordinator.data = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=wh)
    sensor._handle_coordinator_update()
    await hass.async_block_till_done()


async def test_an_unchanged_reading_writes_no_new_state(
    hass: HomeAssistant,
) -> None:
    """Three snapshots carrying the same reading fire no `state_changed` at all."""
    sensor, coordinator = await _added_consumed_energy_sensor(hass, 1250.0)
    entity_id = sensor.entity_id
    assert entity_id is not None
    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    for _ in range(3):
        await _snapshot_arrives(hass, sensor, coordinator, 1250.0)

    assert _writes_of(events, entity_id) == 0


async def test_a_changed_reading_writes_one_new_state(
    hass: HomeAssistant,
) -> None:
    """A snapshot carrying a new reading fires exactly one `state_changed`."""
    sensor, coordinator = await _added_consumed_energy_sensor(hass, 1250.0)
    entity_id = sensor.entity_id
    assert entity_id is not None
    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    await _snapshot_arrives(hass, sensor, coordinator, 1251.5)

    assert _writes_of(events, entity_id) == 1
    assert sensor.native_value == 1251.5


GRACE_ATTRIBUTES = frozenset(
    {"last_valid_state", "last_valid_changed", "grace_period_remaining", "using_grace_period"}
)


async def test_the_grace_attributes_appear_only_while_the_panel_is_offline(
    hass: HomeAssistant,
) -> None:
    """Going offline puts the grace attributes on the state, and coming back takes them off."""
    sensor, coordinator = await _added_consumed_energy_sensor(hass, 1250.0)
    entity_id = sensor.entity_id
    assert entity_id is not None

    coordinator.panel_offline = True
    await _snapshot_arrives(hass, sensor, coordinator, 1250.0)
    offline = hass.states.get(entity_id)
    assert offline is not None
    assert offline.state == "1250.0"
    assert GRACE_ATTRIBUTES <= set(offline.attributes)
    assert offline.attributes["last_valid_state"] == "1250.0"
    assert offline.attributes["using_grace_period"] == "True"

    coordinator.panel_offline = False
    await _snapshot_arrives(hass, sensor, coordinator, 1250.0)
    online = hass.states.get(entity_id)
    assert online is not None
    assert GRACE_ATTRIBUTES.isdisjoint(online.attributes)
