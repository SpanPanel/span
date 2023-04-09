import dataclasses
from typing import Any


@dataclasses.dataclass
class SpanPanelData:
    main_relay_state: str
    main_meter_energy_produced: float
    main_meter_energy_consumed: float
    instant_grid_power: float
    feedthrough_power: float
    feedthrough_energy_produced: float
    feedthrough_energy_consumed: float
    grid_sample_start_ms: int
    grid_sample_end_ms: int
    dsm_grid_state: str
    dsm_state: str
    current_run_config: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SpanPanelData":
        return SpanPanelData(
            main_relay_state=data["mainRelayState"],
            main_meter_energy_produced=data["mainMeterEnergy"]["producedEnergyWh"],
            main_meter_energy_consumed=data["mainMeterEnergy"]["consumedEnergyWh"],
            instant_grid_power=data["instantGridPowerW"],
            feedthrough_power=data["feedthroughPowerW"],
            feedthrough_energy_produced=data["feedthroughEnergy"]["producedEnergyWh"],
            feedthrough_energy_consumed=data["feedthroughEnergy"]["consumedEnergyWh"],
            grid_sample_start_ms=data["gridSampleStartMs"],
            grid_sample_end_ms=data["gridSampleEndMs"],
            dsm_grid_state=data["dsmGridState"],
            dsm_state=data["dsmState"],
            current_run_config=data["currentRunConfig"],
        )
