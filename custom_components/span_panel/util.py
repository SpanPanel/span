from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .span_panel import SpanPanel


def panel_to_device_info(panel: SpanPanel):
    return DeviceInfo(
        identifiers={(DOMAIN, panel.status.serial_number)},
        manufacturer="Span",
        model=f"Span Panel ({panel.status.model})",
        name="Span Panel",
        sw_version=panel.status.firmware_version,
        configuration_url=f"http://{panel.host}",
    )
