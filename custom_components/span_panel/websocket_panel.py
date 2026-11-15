"""The SPAN panel a WebSocket request names.

Every command that takes a panel takes it the same way -- the device registry id
of the main panel device -- and has to refuse the same things with the same
codes, because a consumer that learned one set of codes must not meet a second.
One function resolves it for all of them. Two copies had already drifted: the
topology command took an arbitrary one of a device's entries where the adopted
commands checked its domain.

A module of its own because both `websocket.py` and `websocket_adopted.py` need
it, and `websocket_adopted.py` must not import `websocket.py`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def resolve_panel_device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> tuple[dr.DeviceEntry, ConfigEntry] | None:
    """Resolve the device id in a request to the panel device and the SPAN entry owning it.

    Sends the refusal itself and answers None, so a handler goes on only with a
    SPAN panel device in hand. What the handler does with the entry -- whether it
    is loaded, what its runtime data holds -- stays the handler's to check.

    **A device is a SPAN panel when a SPAN entry owns it.** Since Home Assistant
    2026.8 a device belongs to exactly one config entry, its `config_entry_id`;
    the `config_entries` set this used to search is a deprecated shim holding just
    that one. An identifier in SPAN's domain does not settle it, since another
    entry can own a device carrying one, so the owner's domain is what is checked.

    **Of the devices a SPAN entry owns, only the panel is accepted.** A sub-device
    hangs off the panel with `via_device_id`, and a circuit's own device is a
    child device whose parent is the panel. Each is refused as `not_panel_device`:
    a SPAN entry does own it, so `not_span_panel` would be false.

    **An id saved before 2026.8 is resolved to the device it became.** 2026.8 split
    every device that belonged to several config entries into one device per
    entry, each with a new id -- and a SPAN panel was one whenever a helper built
    on its sensors had linked itself to the panel's device, as helpers did until
    that release. The dashboard card keeps the id it was configured with, so it
    can send the old one. The registry answers that id with a read-only composite
    until 2027.8, but not usefully here: the composite's `config_entry_id` is
    whichever entry was the old device's primary, and no sub-device points at it
    any more, because the migration moved every `via_device_id` onto the split its
    own entry owns. So the old id is resolved to the split a SPAN entry owns, and
    the caller works with that device rather than with the id it was handed.

    **The lookup is Home Assistant's own**,
    `dr.async_get_device_and_config_entry_for_domain`: a device's id, a child
    device's, or an id from before 2026.8, answered with the device and the SPAN
    entry that owns it. It checks ownership and nothing more, so telling the
    panel from the other devices a SPAN entry owns stays here.
    """
    device, entry = dr.async_get_device_and_config_entry_for_domain(
        hass, msg["device_id"], domain=DOMAIN
    )
    if device is None:
        connection.send_error(msg["id"], "device_not_found", "Device not found")
        return None
    # A device with no SPAN entry behind it, a child device included: it is
    # judged by its owner as any other device is. An id from before 2026.8 that
    # no SPAN entry owns a split of is answered the same way.
    if entry is None:
        connection.send_error(msg["id"], "not_span_panel", "Device is not a SPAN Panel device")
        return None

    # A child device a SPAN entry owns is a circuit's own device: a part of the
    # panel, and never the panel.
    if isinstance(device, dr.ChildDeviceEntry):
        connection.send_error(
            msg["id"],
            "not_panel_device",
            "Use the SPAN panel device registry ID, not a circuit device.",
        )
        return None
    # Every sub-device registers with via_device_id pointing at the panel.
    if device.via_device_id is not None:
        connection.send_error(
            msg["id"],
            "not_panel_device",
            "Use the SPAN panel device registry ID, not a sub-device.",
        )
        return None
    return device, entry
