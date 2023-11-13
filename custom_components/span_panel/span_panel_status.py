import dataclasses
from typing import Any

SYSTEM_DOOR_STATE_CLOSED = "CLOSED"
SYSTEM_DOOR_STATE_OPEN = "OPEN"


@dataclasses.dataclass
class SpanPanelStatus:
    firmware_version: str
    update_status: str
    env: str
    manufacturer: str
    serial_number: str
    model: str
    door_state: str
    proximity_proven: bool
    uptime: int
    is_ethernet_connected: bool
    is_wifi_connected: bool
    is_cellular_connected: bool

    @property
    def is_door_closed(self) -> bool:
        return self.door_state == SYSTEM_DOOR_STATE_CLOSED

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SpanPanelStatus":
        return SpanPanelStatus(
            firmware_version=data["software"]["firmwareVersion"],
            update_status=data["software"]["updateStatus"],
            env=data["software"]["env"],
            manufacturer=data["system"]["manufacturer"],
            serial_number=data["system"]["serial"],
            model=data["system"]["model"],
            door_state=data["system"]["doorState"],
            proximity_proven=data["system"]["proximityProven"],
            uptime=data["system"]["uptime"],
            is_ethernet_connected=data["network"]["eth0Link"],
            is_wifi_connected=data["network"]["wlanLink"],
            is_cellular_connected=data["network"]["wwanLink"],
        )
