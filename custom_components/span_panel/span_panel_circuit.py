"""Data models for Span Panel circuit information."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .const import CircuitRelayState


@dataclass
class SpanPanelCircuit:
    """Class representing a Span Panel circuit."""

    circuit_id: str
    name: str
    relay_state: str
    instant_power: float
    instant_power_update_time: int
    produced_energy: float
    consumed_energy: float
    energy_accum_update_time: int
    tabs: list[int]
    priority: str
    is_user_controllable: bool
    is_sheddable: bool
    is_never_backup: bool
    breaker_positions: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    circuit_config: dict[str, Any] = field(default_factory=dict)
    state_config: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_relay_closed(self) -> bool:
        """Return True if the relay is in closed state."""
        return self.relay_state == CircuitRelayState.CLOSED.name

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SpanPanelCircuit":
        """Create a SpanPanelCircuit instance from a dictionary.

        Args:
            data: Dictionary containing circuit data from the Span Panel API.

        Returns:
            A new SpanPanelCircuit instance.
        """
        data_copy: dict[str, Any] = deepcopy(data)
        return SpanPanelCircuit(
            circuit_id=data_copy["id"],
            name=data_copy["name"],
            relay_state=data_copy["relayState"],
            instant_power=data_copy["instantPowerW"],
            instant_power_update_time=data_copy["instantPowerUpdateTimeS"],
            produced_energy=data_copy["producedEnergyWh"],
            consumed_energy=data_copy["consumedEnergyWh"],
            energy_accum_update_time=data_copy["energyAccumUpdateTimeS"],
            tabs=data_copy["tabs"],
            priority=data_copy["priority"],
            is_user_controllable=data_copy["isUserControllable"],
            is_sheddable=data_copy["isSheddable"],
            is_never_backup=data_copy["isNeverBackup"],
            circuit_config=data_copy.get("config", {}),
            state_config=data_copy.get("state", {}),
            raw_data=data_copy,
        )

    def copy(self) -> "SpanPanelCircuit":
        """Create a deep copy for atomic operations."""
        # Circuit contains nested mutable objects, use deepcopy
        return deepcopy(self)
