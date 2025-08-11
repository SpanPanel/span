"""Option configurations."""

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_API_RETRIES,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRY_TIMEOUT,
    CONF_SIMULATION_START_TIME,
    DEFAULT_API_RETRIES,
    DEFAULT_API_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_API_RETRY_TIMEOUT,
)

INVERTER_ENABLE = "enable_solar_circuit"
INVERTER_LEG1 = "leg1"
INVERTER_LEG2 = "leg2"
INVERTER_MAXLEG = 32
BATTERY_ENABLE = "enable_battery_percentage"
POWER_DISPLAY_PRECISION = "power_display_precision"
ENERGY_DISPLAY_PRECISION = "energy_display_precision"
ENERGY_REPORTING_GRACE_PERIOD = "energy_reporting_grace_period"


class Options:
    """Class representing the options like the solar inverter."""

    # pylint: disable=R0903

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the options."""
        self.enable_solar_sensors: bool = entry.options.get(INVERTER_ENABLE, False)
        self.inverter_leg1: int = entry.options.get(INVERTER_LEG1, 0)
        self.inverter_leg2: int = entry.options.get(INVERTER_LEG2, 0)
        self.enable_battery_percentage: bool = entry.options.get(BATTERY_ENABLE, False)
        self.power_display_precision: int = entry.options.get(POWER_DISPLAY_PRECISION, 0)
        self.energy_display_precision: int = entry.options.get(ENERGY_DISPLAY_PRECISION, 2)
        self.energy_reporting_grace_period: int = entry.options.get(
            ENERGY_REPORTING_GRACE_PERIOD, 15
        )

        # API retry configuration options
        self.api_retries: int = int(entry.options.get(CONF_API_RETRIES, DEFAULT_API_RETRIES))
        self.api_retry_timeout: float = float(
            entry.options.get(CONF_API_RETRY_TIMEOUT, str(DEFAULT_API_RETRY_TIMEOUT))
        )
        self.api_retry_backoff_multiplier: float = float(
            entry.options.get(
                CONF_API_RETRY_BACKOFF_MULTIPLIER, DEFAULT_API_RETRY_BACKOFF_MULTIPLIER
            )
        )

        # Simulation time configuration
        simulation_start_time_str = entry.options.get(CONF_SIMULATION_START_TIME)
        self.simulation_start_time: datetime | None = None
        if simulation_start_time_str:
            try:
                self.simulation_start_time = datetime.fromisoformat(simulation_start_time_str)
            except (ValueError, TypeError):
                # If parsing fails, use None (current time)
                self.simulation_start_time = None

    def get_options(self) -> dict[str, Any]:
        """Return the current options as a dictionary."""
        options: dict[str, Any] = {
            INVERTER_ENABLE: self.enable_solar_sensors,
            INVERTER_LEG1: self.inverter_leg1,
            INVERTER_LEG2: self.inverter_leg2,
            BATTERY_ENABLE: self.enable_battery_percentage,
            POWER_DISPLAY_PRECISION: self.power_display_precision,
            ENERGY_DISPLAY_PRECISION: self.energy_display_precision,
            ENERGY_REPORTING_GRACE_PERIOD: self.energy_reporting_grace_period,
            CONF_API_RETRIES: self.api_retries,
            CONF_API_RETRY_TIMEOUT: self.api_retry_timeout,
            CONF_API_RETRY_BACKOFF_MULTIPLIER: self.api_retry_backoff_multiplier,
        }

        # Add simulation start time if set
        if self.simulation_start_time is not None:
            options[CONF_SIMULATION_START_TIME] = self.simulation_start_time.isoformat()

        return options
