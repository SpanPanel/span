"""Utility functions for the Span integration."""

import logging

from homeassistant.helpers.device_registry import DeviceInfo
from span_panel_api import (
    SpanBatterySnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
) -> DeviceInfo:
    """Create DeviceInfo for a BESS sub-device linked to the parent panel."""
    name = f"{panel_name} Battery"
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_bess")},
        name=name,
        manufacturer=battery.vendor_name or "Unknown",
        # `model` is the human designation on both schemas now: v1.0 publishes it as
        # `info/model` and schema_0 translates flat's `bess/product-name` into it.
        model=battery.model or "Battery Storage",
        serial_number=battery.serial_number,
        sw_version=battery.software_version,
        via_device=(DOMAIN, panel_identifier),
    )


def mid_device_info(
    panel_identifier: str,
    mid: SpanMidSnapshot,
    panel_name: str,
) -> DeviceInfo:
    """Create DeviceInfo for the Microgrid Interconnect Device.

    v1.0 publishes the MID as a device of its own and puts the `grid` capability on it
    rather than on the enclosure — "the enclosure device itself does not publish them" —
    so islanding decisions belong to hardware with its own identity. Registering it here
    keeps the integration's model in step with the library's rather than folding a
    device's properties onto the panel.

    Purely additive: no flat panel publishes a MID, so nothing a user has today changes.

    `via_device` the panel, matching BESS and EVSE, even though the wire tree makes the
    MID a child of the BESS. Home Assistant's device graph is about what a user navigates,
    and every SPAN sub-device hangs off the panel there; mirroring the Homie parentage
    would put the MID one level deeper than its siblings for no reader's benefit.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_mid")},
        name=f"{panel_name} Microgrid Interconnect",
        manufacturer=mid.vendor_name or "Unknown",
        model=mid.model or "Microgrid Interconnect Device",
        serial_number=mid.serial_number,
        via_device=(DOMAIN, panel_identifier),
    )


def evse_device_info(
    panel_identifier: str,
    evse: SpanEvseSnapshot,
    panel_name: str,
    display_suffix: str | None = None,
) -> DeviceInfo:
    """Create DeviceInfo for an EVSE sub-device linked to the parent panel."""
    base_name = evse.model or "EV Charger"
    name = f"{base_name} ({display_suffix})" if display_suffix else base_name
    name = f"{panel_name} {name}"
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_identifier}_evse_{evse.node_id}")},
        name=name,
        manufacturer=evse.vendor_name or "SPAN",
        model=evse.model or "SPAN Drive",
        serial_number=evse.serial_number,
        sw_version=evse.software_version,
        via_device=(DOMAIN, panel_identifier),
    )
