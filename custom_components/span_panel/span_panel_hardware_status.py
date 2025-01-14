"""Span Panel Hardware Status"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict

from .const import SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN


@dataclass
class SpanPanelHardwareStatus:
    firmware_version: str
    update_status: str
    env: str
    manufacturer: str
    serial_number: str
    model: str
    door_state: str
    uptime: int
    is_ethernet_connected: bool
    is_wifi_connected: bool
    is_cellular_connected: bool
    proximity_proven: bool | None = None
    remaining_auth_unlock_button_presses: int = 0
    _system_data: Dict[str, Any] = field(default_factory=dict)

    # Door state has been known to return UNKNOWN if the door has not been operated recently
    # Sensor is a tamper sensor not a door sensor
    @property
    def is_door_closed(self) -> bool | None:
        if self.door_state is None:
            return None
        if self.door_state not in (SYSTEM_DOOR_STATE_OPEN, SYSTEM_DOOR_STATE_CLOSED):
            return None
        return self.door_state == SYSTEM_DOOR_STATE_CLOSED

    @property
    def system_data(self) -> Dict[str, Any]:
        return deepcopy(self._system_data)

    @classmethod
    def from_dict(cls, data: dict) -> 'SpanPanelHardwareStatus':
        """Create a new instance with deep copied data."""
        data_copy = deepcopy(data)
        system_data = data_copy.get("system", {})

        # Handle proximity authentication for both new and old firmware
        proximity_proven = None
        remaining_auth_unlock_button_presses = 0

        if "proximityProven" in system_data:
            # New firmware (r202342 and newer)
            proximity_proven = system_data["proximityProven"]
        else:
            # Old firmware (before r202342)
            remaining_auth_unlock_button_presses = system_data.get(
                "remainingAuthUnlockButtonPresses", 0
            )

        return cls(
            firmware_version=data_copy["software"]["firmwareVersion"],
            update_status=data_copy["software"]["updateStatus"],
            env=data_copy["software"]["env"],
            manufacturer=data_copy["system"]["manufacturer"],
            serial_number=data_copy["system"]["serial"],
            model=data_copy["system"]["model"],
            door_state=data_copy["system"]["doorState"],
            uptime=data_copy["system"]["uptime"],
            is_ethernet_connected=data_copy["network"]["eth0Link"],
            is_wifi_connected=data_copy["network"]["wlanLink"],
            is_cellular_connected=data_copy["network"]["wwanLink"],
            proximity_proven=proximity_proven,
            remaining_auth_unlock_button_presses=remaining_auth_unlock_button_presses,
            _system_data=system_data
        )

    def copy(self) -> 'SpanPanelHardwareStatus':
        """Create a deep copy of hardware status"""
        return deepcopy(self)
