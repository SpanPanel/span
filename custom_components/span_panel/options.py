"""Option configurations."""

from homeassistant.config_entries import ConfigEntry

INVERTER_ENABLE = "enable_solar_circuit"
INVERTER_LEG1 = "leg1"
INVERTER_LEG2 = "leg2"
INVERTER_MAXLEG = 32
BATTERY_ENABLE = "enable_battery_percentage"


class Options:
    """Class representing the options like the solar inverter."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.enable_solar_sensors: bool = entry.options.get(INVERTER_ENABLE, False)
        self.inverter_leg1: int = entry.options.get(INVERTER_LEG1, 0)
        self.inverter_leg2: int = entry.options.get(INVERTER_LEG2, 0)
        self.enable_battery_percentage: bool = entry.options.get(BATTERY_ENABLE, False)
