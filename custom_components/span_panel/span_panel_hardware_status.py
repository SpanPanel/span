"""Span Panel Hardware Status."""

from copy import deepcopy
from dataclasses import dataclass, field
import logging
from typing import Any

from span_panel_api import SpanPanelSnapshot

from .const import SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpanPanelHardwareStatus:
    """Class representing the hardware status of the Span Panel."""

    firmware_version: str
    update_status: str
    env: str
    manufacturer: str
    serial_number: str
    model: str
    door_state: str | None
    uptime: int
    is_ethernet_connected: bool
    is_wifi_connected: bool
    is_cellular_connected: bool
    proximity_proven: bool | None = None
    remaining_auth_unlock_button_presses: int = 0
    _system_data: dict[str, Any] = field(default_factory=dict)

    # Door state has been known to return UNKNOWN if the door has not been
    # operated recently Sensor is a tamper sensor not a door sensor
    @property
    def is_door_closed(self) -> bool | None:
        """Return whether the door is closed, or None if state is unknown."""
        if self.door_state is None:
            return None
        if self.door_state not in (SYSTEM_DOOR_STATE_OPEN, SYSTEM_DOOR_STATE_CLOSED):
            return None
        return self.door_state == SYSTEM_DOOR_STATE_CLOSED

    @property
    def system_data(self) -> dict[str, Any]:
        """Return the system data."""
        return deepcopy(self._system_data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpanPanelHardwareStatus":
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
            _system_data=system_data,
        )

    @classmethod
    def from_snapshot(cls, snapshot: SpanPanelSnapshot) -> "SpanPanelHardwareStatus":
        """Create a SpanPanelHardwareStatus from a transport-agnostic snapshot.

        Gen2 panels populate all hardware status fields.  Gen3 panels only
        populate serial_number and firmware_version; all other fields default
        to None or False because the corresponding entity classes are gated
        behind PanelCapability.HARDWARE_STATUS and will not be created.
        """
        return cls(
            firmware_version=snapshot.firmware_version,
            update_status=snapshot.hardware_update_status or "",
            env=snapshot.hardware_env or "",
            manufacturer=snapshot.hardware_manufacturer or "",
            serial_number=snapshot.serial_number,
            model=snapshot.hardware_model or "",
            door_state=snapshot.hardware_door_state,
            uptime=snapshot.hardware_uptime or 0,
            is_ethernet_connected=snapshot.hardware_is_ethernet_connected or False,
            is_wifi_connected=snapshot.hardware_is_wifi_connected or False,
            is_cellular_connected=snapshot.hardware_is_cellular_connected or False,
            proximity_proven=snapshot.hardware_proximity_proven,
            remaining_auth_unlock_button_presses=0,
        )

    def copy(self) -> "SpanPanelHardwareStatus":
        """Create a deep copy of hardware status."""
        return deepcopy(self)
