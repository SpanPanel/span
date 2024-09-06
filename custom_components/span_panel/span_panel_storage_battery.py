"""span_panel_storage_battery"""

import dataclasses
from typing import Any, Dict


@dataclasses.dataclass
class SpanPanelStorageBattery:
    """Class to manage the storage battery data."""

    storage_battery_percentage: int

    @staticmethod
    def from_dic(data: Dict[str, Any]) -> "SpanPanelStorageBattery":
        """read the data from the dictionary"""
        return SpanPanelStorageBattery(
            storage_battery_percentage=int(data.get("storage_battery_percentage", 0))
        )
