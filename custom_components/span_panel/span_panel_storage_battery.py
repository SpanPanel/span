import dataclasses
from typing import Any

@dataclasses.dataclass
class SpanPanelStorageBattery:
    storage_battery_percentage: int

    @staticmethod
    def from_dic(data):
        return data
