"""span_panel_storage_battery"""

from typing import Any, Dict
from dataclasses import dataclass, replace, field
from copy import deepcopy

@dataclass
class SpanPanelStorageBattery:
    """Class to manage the storage battery data."""

    storage_battery_percentage: int
    # Any nested mutable structures should use field with default_factory
    raw_data: dict = field(default_factory=dict)

    @staticmethod
    def from_dic(data: Dict[str, Any]) -> "SpanPanelStorageBattery":
        """read the data from the dictionary"""
        return SpanPanelStorageBattery(
            storage_battery_percentage=data.get("percentage", 0)
        )

    def copy(self) -> 'SpanPanelStorageBattery':
        """Create a deep copy of storage battery data"""
        return deepcopy(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'SpanPanelStorageBattery':
        """Create instance from dict with deep copy of input data"""
        data = deepcopy(data)
        return cls(
            storage_battery_percentage=data.get("batteryPercentage", 0),
            raw_data=data
        )
