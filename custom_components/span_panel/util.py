"""Utility functions for the Span integration."""

import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from .const import DOMAIN
from .span_panel import SpanPanel

_LOGGER = logging.getLogger(__name__)


def panel_to_device_info(panel: SpanPanel, device_name: str | None = None) -> DeviceInfo:
    """Convert a Span Panel to a Home Assistant device info object.

    For simulator entries, use a per-entry identifier derived from the device name
    so multiple simulators don't collapse into a single device in the registry.
    Live panels continue to use the true serial number identifier.
    """
    if getattr(panel.api, "simulation_mode", False) and device_name:
        device_identifier = slugify(device_name)
    else:
        device_identifier = panel.status.serial_number

    return DeviceInfo(
        identifiers={(DOMAIN, device_identifier)},
        manufacturer="Span",
        model=f"Span Panel ({panel.status.model})",
        name=device_name or "Span Panel",
        sw_version=panel.status.firmware_version,
        configuration_url=f"http://{panel.host}",
    )
