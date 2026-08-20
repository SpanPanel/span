"""Utility functions for the Span integration."""

import logging
from typing import Final

from homeassistant.helpers.device_registry import DeviceInfo
from span_panel_api import (
    SpanBatterySnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
    SpanPVSnapshot,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Every SPAN sub-device hangs off the panel and says so with `via_device_id` --
# the panel's *registry* id, not its identifiers. Identifiers are unique only
# within a config entry, so linking by them is ambiguous by construction; Home
# Assistant deprecated `via_device` for that reason and stops honouring it in
# 2027.8. The id is resolved once during setup and carried on the entry's
# runtime data, because a sub-device is only ever built after the panel device
# exists -- which is what lets the builders below take a plain `str` and leaves
# no caller with an absence to handle.

# A sub-device's registry identifier is `{panel serial}_{kind}`, with EVSE
# carrying its node id after the kind because a panel can have several.
#
# Named here, beside the builders that construct them, because the topology
# WebSocket command has to read the grammar back. It used to restate it, and the
# MID shipped classifying as "unknown" for exactly that reason: a third kind was
# added to the writing end and not to the reading one. `classify_sub_device_identifier`
# is the reading end, so the two cannot drift again.
SUB_DEVICE_BESS: Final = "bess"
SUB_DEVICE_MID: Final = "mid"
SUB_DEVICE_EVSE: Final = "evse"
SUB_DEVICE_PV: Final = "pv"

ADOPTED_IDENTIFIER_TOKEN: Final = "adopted"
"""The infix marking a sub-device identifier as adopted rather than curated.

An adopted device is `{panel serial}_adopted_{anchor}`, where the anchor is
whatever the device was first seen under. Kept here beside the curated kinds
because the two namespaces have to be readable apart, and the reading end below
is what would otherwise mistake one for the other: a vendor device whose id
happens to end in `pv` would classify as the solar sub-device under a suffix
rule that had never heard of adoption.
"""


def classify_sub_device_identifier(identifier: str) -> str | None:
    """Return the kind of sub-device an identifier names, or None if it names none.

    None rather than an "unknown" string: the caller knows whether it is looking
    at something that must be a sub-device, and a sentinel that reads like a kind
    is what let an unclassified device render as a device with no type.

    **Most specific first.** EVSE is the one kind whose token is an infix rather
    than a suffix, and a suffix test cannot tell `..._evse_inverter_pv` from a PV
    identifier. Testing the infix first makes the charger's node id opaque to the
    suffix rules below, which is the only ordering that stays right whatever a
    panel names its nodes.
    """
    # Adopted devices are not a curated kind and must not be read as one. Tested
    # before every suffix rule below, because the anchor that follows the token
    # is vendor vocabulary: a device id ending in `pv` would otherwise classify
    # as the panel's solar sub-device.
    if f"_{ADOPTED_IDENTIFIER_TOKEN}_" in identifier:
        return None

    # Infix, not suffix: the node id follows, and it is what distinguishes one
    # charger from another on the same panel.
    if f"_{SUB_DEVICE_EVSE}_" in identifier:
        return SUB_DEVICE_EVSE
    if identifier.endswith(f"_{SUB_DEVICE_BESS}"):
        return SUB_DEVICE_BESS
    if identifier.endswith(f"_{SUB_DEVICE_MID}"):
        return SUB_DEVICE_MID
    if identifier.endswith(f"_{SUB_DEVICE_PV}"):
        return SUB_DEVICE_PV
    return None


def snapshot_to_device_info(
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
    host: str | None = None,
) -> DeviceInfo:
    """Convert a SpanPanelSnapshot to a Home Assistant device info object.

    Manufacturer, model and hardware revision come from the enclosure's own
    `info` node where it publishes them, and fall back to the strings this
    integration has always shown where it does not.

    **The fallbacks are the point, not a courtesy.** Flat firmware declares none
    of the three, so every existing installation lands on them; a panel that
    omits one must keep the card it has rather than losing a row. `hw_version`
    has no such string to fall back to and so is simply absent on flat --
    `DeviceInfo` omits a `None` field, which is the difference between "this
    panel does not report a revision" and "this panel reports a blank one".
    """
    configuration_url = f"http://{host}" if host else None

    return DeviceInfo(
        identifiers={(DOMAIN, snapshot.serial_number)},
        manufacturer=snapshot.vendor_name or "Span",
        # The published designation is the panel's own model code (`MAIN_40`),
        # which is also what `panel_size` is derived from. Showing it beats
        # "SPAN Panel" on a card whose whole job is saying which hardware this
        # is -- and the generic string remains for anything that publishes none.
        model=snapshot.model or "SPAN Panel",
        name=device_name or "Span Panel",
        sw_version=snapshot.firmware_version,
        hw_version=snapshot.hardware_version,
        configuration_url=configuration_url,
    )


def bess_device_info(
    panel_identifier: str,
    battery: SpanBatterySnapshot,
    panel_name: str,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for a BESS sub-device linked to the parent panel.

    Two panel-shaped arguments doing different jobs: `panel_identifier` is the
    serial this sub-device namespaces its own identity under, `panel_device_id`
    is the registry id it links to.
    """
    name = f"{panel_name} Battery"
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_{SUB_DEVICE_BESS}")},
        name=name,
        manufacturer=battery.vendor_name or "Unknown",
        # `model` is the human designation on both schemas now: v1.0 publishes it as
        # `info/model` and schema_0 translates flat's `bess/product-name` into it.
        model=battery.model or "Battery Storage",
        serial_number=battery.serial_number,
        sw_version=battery.software_version,
        via_device_id=panel_device_id,
    )


def mid_device_info(
    panel_identifier: str,
    mid: SpanMidSnapshot,
    panel_name: str,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for the Microgrid Interconnect Device.

    v1.0 publishes the MID as a device of its own and puts the `grid` capability on it
    rather than on the enclosure — "the enclosure device itself does not publish them" —
    so islanding decisions belong to hardware with its own identity. Registering it here
    keeps the integration's model in step with the library's rather than folding a
    device's properties onto the panel.

    Purely additive: no flat panel publishes a MID, so nothing a user has today changes.

    Linked to the panel, matching BESS and EVSE, even though the wire tree makes the
    MID a child of the BESS. Home Assistant's device graph is about what a user navigates,
    and every SPAN sub-device hangs off the panel there; mirroring the Homie parentage
    would put the MID one level deeper than its siblings for no reader's benefit.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_{SUB_DEVICE_MID}")},
        name=f"{panel_name} Microgrid Interconnect",
        manufacturer=mid.vendor_name or "Unknown",
        model=mid.model or "Microgrid Interconnect Device",
        serial_number=mid.serial_number,
        # Passed through unguarded, exactly as `bess_device_info` does: `DeviceInfo`
        # omits a `None` field and renders an empty string as a present-but-blank row,
        # so `or ""` here would invent a row for a panel that published nothing. The
        # library preserves that distinction for the same reason.
        #
        # Unguarded on schema, deliberately: r202633 documents both on the MID's `info`
        # node, and flat publishes no MID at all, so `has_mid` keeps every caller of this
        # builder off a flat panel. A conditional here would be unreachable code implying
        # a case that cannot arise.
        sw_version=mid.software_version,
        hw_version=mid.hardware_version,
        via_device_id=panel_device_id,
    )


def evse_device_info(
    panel_identifier: str,
    evse: SpanEvseSnapshot,
    panel_name: str,
    display_suffix: str | None = None,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for an EVSE sub-device linked to the parent panel."""
    base_name = evse.model or "EV Charger"
    name = f"{base_name} ({display_suffix})" if display_suffix else base_name
    name = f"{panel_name} {name}"
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_{SUB_DEVICE_EVSE}_{evse.node_id}")},
        name=name,
        manufacturer=evse.vendor_name or "SPAN",
        model=evse.model or "SPAN Drive",
        serial_number=evse.serial_number,
        sw_version=evse.software_version,
        via_device_id=panel_device_id,
    )


def pv_device_info(
    panel_identifier: str,
    pv: SpanPVSnapshot,
    panel_name: str,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for the solar inverter, linked to the parent panel.

    The last DER to get a card of its own. Its vendor, model and nameplate
    capacity have been readable all along and were shown as three diagnostic
    sensors on the *panel's* card, beside the panel's own manufacturer and model,
    which reads as if the enclosure were an Enphase inverter. The firmware
    version the library also reads reached nothing at all, because a version has
    no home but a device card.

    **The identifier deliberately does not mention the inverter's serial.**
    `info/serial-number` is declared by every PV `$description` and published by
    no producer today, so an identifier preferring it would be `<panel>_pv` on
    every panel now and `<panel>_<serial>` on the first panel whose firmware
    starts publishing one -- and a device identifier is what a consumer keys its
    registry on, so that day would read as the inverter being replaced rather
    than as a value arriving. `{panel serial}_pv` answers the only question an
    identifier has to answer, "which panel's inverter", and a panel has exactly
    one `pv` node, so nothing distinguishes two of them. The serial is not on the
    card either, for the same reason it is not in the identifier: nothing in this
    integration should start depending on it before a producer publishes one.

    **No area is seeded**, so an upgraded installation has to assign this card to
    an area the way it assigned the battery's and the chargers'. Both routes were
    considered and neither is clean. `DeviceInfo`'s `suggested_area` is deprecated
    with `breaks_in_ha_version="2026.9"`, one release past the version pinned
    here, so adopting it would be adopting a removal. An explicit
    `device_registry.async_update_device(area_id=...)` after setup cannot tell an
    area a user deliberately cleared from one never assigned -- the registry
    records `None` for both -- so it would silently re-assign the card on every
    reload. Doing nothing also keeps every sub-device the same: the BESS, the MID
    and each charger seed no area either.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_{SUB_DEVICE_PV}")},
        name=f"{panel_name} Solar",
        manufacturer=pv.vendor_name or "Unknown",
        model=pv.model or "Solar Inverter",
        # Passed through unguarded, as on the BESS and the MID: `DeviceInfo`
        # omits a `None` field and renders an empty string as a present-but-blank
        # row, so `or ""` would invent a version row for an inverter that
        # published none.
        sw_version=pv.software_version,
        via_device_id=panel_device_id,
    )
