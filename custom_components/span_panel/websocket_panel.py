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
    """
    # When the minimum Home Assistant version reaches 2026.9, replace this lookup
    # -- the composite splits, the `async_get` fallback and `_owned_by_span` --
    # with `dr.async_get_device_and_config_entry_for_domain(hass, device_id,
    # domain=DOMAIN)`, which 2026.9 adds to do exactly this, an id from before
    # 2026.8 included. Its `(None, None)` -- an unknown or child-device id -- is
    # `device_not_found`, and its `(device, None)` -- a device no SPAN entry owns
    # -- is `not_span_panel`. The sub-device refusal below stays: the helper
    # checks ownership, not `via_device_id`.
    device_registry = dr.async_get(hass)
    device_id: str = msg["device_id"]
    candidates = device_registry.async_get_devices_for_composite_device_id(device_id)
    # From 2026.9 `async_get` can answer with a child device, a part of another
    # device that is never a SPAN panel. It is refused as not found, as the 2026.9
    # helper refuses it, so the swap above changes no answer.
    if not candidates and isinstance(
        device_entry := device_registry.async_get(device_id), dr.DeviceEntry
    ):
        candidates = [device_entry]
    if not candidates:
        connection.send_error(msg["id"], "device_not_found", "Device not found")
        return None

    owned = _owned_by_span(hass, candidates)
    if owned is None:
        connection.send_error(msg["id"], "not_span_panel", "Device is not a SPAN Panel device")
        return None

    panel_device, entry = owned
    # Every sub-device registers with via_device_id pointing at the panel.
    if panel_device.via_device_id is not None:
        connection.send_error(
            msg["id"],
            "not_panel_device",
            "Use the SPAN panel device registry ID, not a sub-device.",
        )
        return None
    return panel_device, entry


def _owned_by_span(
    hass: HomeAssistant, devices: list[dr.DeviceEntry]
) -> tuple[dr.DeviceEntry, ConfigEntry] | None:
    """Return the first of these devices a SPAN entry owns, with that entry."""
    for device_entry in devices:
        entry = hass.config_entries.async_get_entry(device_entry.config_entry_id)
        if entry is not None and entry.domain == DOMAIN:
            return device_entry, entry
    return None
