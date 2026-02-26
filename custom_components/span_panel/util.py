"""Utility functions for the Span integration."""

import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify
from span_panel_api import SpanPanelSnapshot

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def snapshot_to_device_info(
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
    is_simulator: bool = False,
    host: str | None = None,
) -> DeviceInfo:
    """Convert a SpanPanelSnapshot to a Home Assistant device info object.

    For simulator entries, use a per-entry identifier derived from the device name
    so multiple simulators don't collapse into a single device in the registry.
    Live panels continue to use the true serial number identifier.
    """
    if is_simulator and device_name:
        device_identifier = slugify(device_name)
    else:
        device_identifier = snapshot.serial_number

    configuration_url = f"http://{host}" if host else None

    return DeviceInfo(
        identifiers={(DOMAIN, device_identifier)},
        manufacturer="Span",
        model="SPAN Panel",
        name=device_name or "Span Panel",
        sw_version=snapshot.firmware_version,
        configuration_url=configuration_url,
    )
