import dataclasses
from typing import Any
from .options import Options, INVERTER_MAXLEG


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
    solar_inverter_instant_power: float
    solar_inverter_energy_produced: float
    solar_inverter_energy_consumed: float

    @staticmethod
    def from_dict(data: dict[str, Any], options: Options | None) -> "SpanPanelData":
        if not options or not options.enable_solar_sensors:
            spd = SpanPanelData(
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
                solar_inverter_instant_power=0.0,
                solar_inverter_energy_produced=0.0,
                solar_inverter_energy_consumed=0.0
            )
        else:
            if 1 <= options.inverter_leg1 <= INVERTER_MAXLEG:
                leg1_branch = data["branches"][options.inverter_leg1-1]
                leg1_power = leg1_branch["instantPowerW"]
                leg1_energy_produced = leg1_branch["importedActiveEnergyWh"]
                leg1_energy_consumed = leg1_branch["exportedActiveEnergyWh"]
            else:
                leg1_power = leg1_energy_produced = leg1_energy_consumed = 0
            if 1 <= options.inverter_leg2 <= INVERTER_MAXLEG:
                leg2_branch = data["branches"][options.inverter_leg2-1]
                leg2_power = leg2_branch["instantPowerW"]
                leg2_energy_produced = leg2_branch["importedActiveEnergyWh"]
                leg2_energy_consumed = leg2_branch["exportedActiveEnergyWh"]
            else:
                leg2_power = leg2_energy_produced = leg2_energy_consumed = 0

            spd = SpanPanelData(
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
                solar_inverter_instant_power=leg1_power + leg2_power,
                solar_inverter_energy_produced=leg1_energy_produced + leg2_energy_produced,
                solar_inverter_energy_consumed=leg1_energy_consumed + leg2_energy_consumed
            )
        return spd
