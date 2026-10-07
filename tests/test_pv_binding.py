"""Nothing moves: the Solar card's PV entities through every shape a panel publishes."""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel import async_remove_entry
from custom_components.span_panel.const import DOMAIN

ENTRY_ID: Final = "entry-pv-forget"


async def test_removing_the_entry_forgets_the_record(hass: HomeAssistant, hass_storage: dict[str, object]) -> None:
    """Driven through the removal hook, so the wiring is what is proved."""
    key = f"{DOMAIN}.pv_binding.{ENTRY_ID}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"circuit_id": "c"}}
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=ENTRY_ID, unique_id="sp3-001")
    entry.add_to_hass(hass)

    await async_remove_entry(hass, entry)

    assert key not in hass_storage
