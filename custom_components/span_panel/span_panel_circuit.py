"""Data models for Span Panel circuit information."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from span_panel_api import SpanCircuitSnapshot

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
    # Gen3-only fields (None for Gen2 panels — entities gated on PUSH_STREAMING capability)
    voltage_v: float | None = None
    current_a: float | None = None
    apparent_power_va: float | None = None
    reactive_power_var: float | None = None
    frequency_hz: float | None = None
    power_factor: float | None = None
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

    @staticmethod
    def from_snapshot(snapshot: SpanCircuitSnapshot) -> "SpanPanelCircuit":
        """Create a SpanPanelCircuit from a transport-agnostic SpanCircuitSnapshot.

        Used by both Gen2 (via get_snapshot()) and Gen3 transports.
        Fields absent from Gen3 snapshots default to neutral values; they are
        only accessed by capability-gated entity classes that won't be created
        for Gen3 panels.
        """
        relay = snapshot.relay_state
        return SpanPanelCircuit(
            circuit_id=snapshot.circuit_id,
            name=snapshot.name,
            relay_state=relay if relay is not None else CircuitRelayState.CLOSED.name,
            instant_power=snapshot.power_w,
            instant_power_update_time=0,
            produced_energy=snapshot.energy_produced_wh
            if snapshot.energy_produced_wh is not None
            else 0.0,
            consumed_energy=snapshot.energy_consumed_wh
            if snapshot.energy_consumed_wh is not None
            else 0.0,
            energy_accum_update_time=0,
            tabs=list(snapshot.tabs) if snapshot.tabs is not None else [],
            priority=snapshot.priority if snapshot.priority is not None else "MUST_HAVE",
            is_user_controllable=relay is not None,
            is_sheddable=False,
            is_never_backup=False,
            voltage_v=snapshot.voltage_v,
            current_a=snapshot.current_a,
            apparent_power_va=snapshot.apparent_power_va,
            reactive_power_var=snapshot.reactive_power_var,
            frequency_hz=snapshot.frequency_hz,
            power_factor=snapshot.power_factor,
        )

    def copy(self) -> "SpanPanelCircuit":
        """Create a deep copy for atomic operations."""
        # Circuit contains nested mutable objects, use deepcopy
        return deepcopy(self)
