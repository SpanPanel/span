"""Span Panel Data"""

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
        common_data: dict[str, Any] = {
            "main_relay_state": str(data["mainRelayState"]),
            "main_meter_energy_produced": float(
                data["mainMeterEnergy"]["producedEnergyWh"]
            ),
            "main_meter_energy_consumed": float(
                data["mainMeterEnergy"]["consumedEnergyWh"]
            ),
            "instant_grid_power": float(data["instantGridPowerW"]),
            "feedthrough_power": float(data["feedthroughPowerW"]),
            "feedthrough_energy_produced": float(
                data["feedthroughEnergy"]["producedEnergyWh"]
            ),
            "feedthrough_energy_consumed": float(
                data["feedthroughEnergy"]["consumedEnergyWh"]
            ),
            "grid_sample_start_ms": int(data["gridSampleStartMs"]),
            "grid_sample_end_ms": int(data["gridSampleEndMs"]),
            "dsm_grid_state": str(data["dsmGridState"]),
            "dsm_state": str(data["dsmState"]),
            "current_run_config": str(data["currentRunConfig"]),
            "solar_inverter_instant_power": 0.0,
            "solar_inverter_energy_produced": 0.0,
            "solar_inverter_energy_consumed": 0.0,
        }

        if options and options.enable_solar_sensors:
            for leg in [options.inverter_leg1, options.inverter_leg2]:
                if 1 <= leg <= INVERTER_MAXLEG:
                    branch = data["branches"][leg - 1]
                    common_data["solar_inverter_instant_power"] += float(
                        branch["instantPowerW"]
                    )
                    common_data["solar_inverter_energy_produced"] += float(
                        branch["importedActiveEnergyWh"]
                    )
                    common_data["solar_inverter_energy_consumed"] += float(
                        branch["exportedActiveEnergyWh"]
                    )

        return SpanPanelData(**common_data)
