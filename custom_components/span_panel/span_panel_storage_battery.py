"""span_panel_storage_battery."""

from dataclasses import dataclass, field
from typing import Any

from span_panel_api import SpanPanelSnapshot


@dataclass
class SpanPanelStorageBattery:
    """Class to manage the storage battery data."""

    storage_battery_percentage: int
    # Any nested mutable structures should use field with default_factory
    raw_data: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SpanPanelStorageBattery":
        """Read the data from the dictionary."""
        return SpanPanelStorageBattery(storage_battery_percentage=data.get("percentage", 0))

    @staticmethod
    def from_snapshot(snapshot: SpanPanelSnapshot) -> "SpanPanelStorageBattery":
        """Create a SpanPanelStorageBattery from a transport-agnostic snapshot."""
        percentage = int(snapshot.battery_soe) if snapshot.battery_soe is not None else 0
        return SpanPanelStorageBattery(storage_battery_percentage=percentage)
