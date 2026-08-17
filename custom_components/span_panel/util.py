"""Utility functions for the Span integration."""

import logging
from typing import Final

from homeassistant.helpers.device_registry import DeviceInfo
from span_panel_api import (
    SpanBatterySnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
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


def classify_sub_device_identifier(identifier: str) -> str | None:
    """Return the kind of sub-device an identifier names, or None if it names none.

    None rather than an "unknown" string: the caller knows whether it is looking
    at something that must be a sub-device, and a sentinel that reads like a kind
    is what let an unclassified device render as a device with no type.
    """
    if identifier.endswith(f"_{SUB_DEVICE_BESS}"):
        return SUB_DEVICE_BESS
    if identifier.endswith(f"_{SUB_DEVICE_MID}"):
        return SUB_DEVICE_MID
    # Infix, not suffix: the node id follows, and it is what distinguishes one
    # charger from another on the same panel.
    if f"_{SUB_DEVICE_EVSE}_" in identifier:
        return SUB_DEVICE_EVSE
    return None


def snapshot_to_device_info(
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
    host: str | None = None,
) -> DeviceInfo:
    """Convert a SpanPanelSnapshot to a Home Assistant device info object."""
    configuration_url = f"http://{host}" if host else None

    return DeviceInfo(
        identifiers={(DOMAIN, snapshot.serial_number)},
        manufacturer="Span",
        model="SPAN Panel",
        name=device_name or "Span Panel",
        sw_version=snapshot.firmware_version,
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
