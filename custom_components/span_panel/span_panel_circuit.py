import dataclasses
from typing import Any

CIRCUITS_RELAY_OPEN = "OPEN"
CIRCUITS_RELAY_CLOSED = "CLOSED"


@dataclasses.dataclass
class SpanPanelCircuit:
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

    @property
    def is_relay_closed(self):
        return self.relay_state == CIRCUITS_RELAY_CLOSED

    @staticmethod
    def from_dict(data: dict[str, Any]):
        return SpanPanelCircuit(
            circuit_id=data["id"],
            name=data["name"],
            relay_state=data["relayState"],
            instant_power=data["instantPowerW"],
            instant_power_update_time=data["instantPowerUpdateTimeS"],
            produced_energy=data["producedEnergyWh"],
            consumed_energy=data["consumedEnergyWh"],
            energy_accum_update_time=data["energyAccumUpdateTimeS"],
            tabs=data["tabs"],
            priority=data["priority"],
            is_user_controllable=data["isUserControllable"],
            is_sheddable=data["isSheddable"],
            is_never_backup=data["isNeverBackup"],
        )
