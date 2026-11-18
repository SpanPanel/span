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

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from pytest_homeassistant.common import async_capture_events

from .helpers import add_main_meter_consumed_energy


def _writes_of(events: list[Event[EventStateChangedData]], entity_id: str) -> int:
    """How many `state_changed` events, which the recorder stores a row for, name this entity."""
    return sum(1 for event in events if event.data["entity_id"] == entity_id)


async def test_an_unchanged_reading_writes_no_new_state(
    hass: HomeAssistant,
) -> None:
    """Three snapshots carrying the same reading fire no `state_changed` at all."""
    meter = await add_main_meter_consumed_energy(hass, 1250.0)
    entity_id = meter.sensor.entity_id
    assert entity_id is not None
    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    for _ in range(3):
        await meter.reads(hass, 1250.0)

    assert _writes_of(events, entity_id) == 0


async def test_a_changed_reading_writes_one_new_state(
    hass: HomeAssistant,
) -> None:
    """A snapshot carrying a new reading fires exactly one `state_changed`."""
    meter = await add_main_meter_consumed_energy(hass, 1250.0)
    entity_id = meter.sensor.entity_id
    assert entity_id is not None
    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    await meter.reads(hass, 1251.5)

    assert _writes_of(events, entity_id) == 1
    assert meter.sensor.native_value == 1251.5


GRACE_ATTRIBUTES = frozenset(
    {"last_valid_state", "last_valid_changed", "grace_period_remaining", "using_grace_period"}
)


async def test_the_grace_attributes_appear_only_while_the_panel_is_offline(
    hass: HomeAssistant,
) -> None:
    """Going offline puts the grace attributes on the state, and coming back takes them off."""
    meter = await add_main_meter_consumed_energy(hass, 1250.0)
    entity_id = meter.sensor.entity_id
    assert entity_id is not None

    meter.coordinator.panel_offline = True
    await meter.reads(hass, 1250.0)
    offline = hass.states.get(entity_id)
    assert offline is not None
    assert offline.state == "1250.0"
    assert GRACE_ATTRIBUTES <= set(offline.attributes)
    assert offline.attributes["last_valid_state"] == "1250.0"
    assert offline.attributes["using_grace_period"] == "True"

    meter.coordinator.panel_offline = False
    await meter.reads(hass, 1250.0)
    online = hass.states.get(entity_id)
    assert online is not None
    assert GRACE_ATTRIBUTES.isdisjoint(online.attributes)
