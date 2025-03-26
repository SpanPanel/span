"""span_panel_storage_battery"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class SpanPanelStorageBattery:
    """Class to manage the storage battery data."""

    storage_battery_percentage: int
    # Any nested mutable structures should use field with default_factory
    raw_data: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dic(data: Dict[str, Any]) -> "SpanPanelStorageBattery":
        """read the data from the dictionary"""
        return SpanPanelStorageBattery(
            storage_battery_percentage=data.get("percentage", 0)
        )
