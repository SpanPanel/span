"""The panel device a test registers is one the device registry resolves.

Every sub-device names the panel device as its parent, and Core drops an
entity whose device names an unregistered parent, logging an error rather than
raising. So a test's runtime data has to name a device that exists, and
`register_panel_device` is how it gets one.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.span_panel.const import DOMAIN

from .helpers import add_panel_entry, register_panel_device

SERIAL = "sp3-registered-001"


async def test_register_panel_device_returns_a_registered_device(hass: HomeAssistant) -> None:
    entry = add_panel_entry(hass, "entry-registered", SERIAL)

    device_id = register_panel_device(hass, entry, SERIAL)

    device = dr.async_get(hass).async_get(device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, SERIAL)}
    # A device belongs to one config entry; `config_entries` is deprecated for it.
    assert device.config_entry_id == entry.entry_id
