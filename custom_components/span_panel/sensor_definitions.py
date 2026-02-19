"""Sensor definitions for SPAN Panel integration.

This file contains sensor definitions for NATIVE integration sensors only:
- Panel status sensors (DSM state, grid state, current run config, main relay state)
- Hardware status sensors (software version)
- Unmapped circuit sensors (power, energy for unmapped breaker positions - invisible backing data)

SYNTHETIC sensors (panel power, circuit power/energy, solar, battery) are now defined
in YAML templates in yaml_templates/ directory and created via ha-synthetic-sensors package.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)

from .const import (
    CIRCUITS_ENERGY_CONSUMED,
    CIRCUITS_ENERGY_PRODUCED,
    CIRCUITS_POWER,
)
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin:
    """Required keys mixin for Span Panel circuit sensors."""

    value_fn: Callable[[SpanPanelCircuit], float | None]


@dataclass(frozen=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    """Describes a Span Panel circuit sensor entity."""


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin:
    """Required keys mixin for Span Panel data sensors."""

    value_fn: Callable[[SpanPanelData], float | str | None]


@dataclass(frozen=True)
class SpanPanelDataSensorEntityDescription(SensorEntityDescription, SpanPanelDataRequiredKeysMixin):
    """Describes a Span Panel data sensor entity."""


@dataclass(frozen=True)
class SpanPanelStatusRequiredKeysMixin:
    """Required keys mixin for Span Panel status sensors."""

    value_fn: Callable[[SpanPanelHardwareStatus], str]


@dataclass(frozen=True)
class SpanPanelStatusSensorEntityDescription(
    SensorEntityDescription, SpanPanelStatusRequiredKeysMixin
):
    """Describes a Span Panel status sensor entity."""


@dataclass(frozen=True)
class SpanPanelBatteryRequiredKeysMixin:
    """Required keys mixin for Span Panel battery sensors."""

    value_fn: Callable[[SpanPanelStorageBattery], int]


@dataclass(frozen=True)
class SpanPanelBatterySensorEntityDescription(
    SensorEntityDescription, SpanPanelBatteryRequiredKeysMixin
):
    """Describes a Span Panel battery sensor entity."""


# Panel data status sensor definitions (native sensors)
PANEL_DATA_STATUS_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="dsm_state",
        name="DSM State",
        value_fn=lambda panel_data: panel_data.dsm_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="dsm_grid_state",
        name="DSM Grid State",
        value_fn=lambda panel_data: panel_data.dsm_grid_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="current_run_config",
        name="Current Run Config",
        value_fn=lambda panel_data: panel_data.current_run_config,
    ),
    SpanPanelDataSensorEntityDescription(
        key="main_relay_state",
        name="Main Relay State",
        value_fn=lambda panel_data: panel_data.main_relay_state,
    ),
)

# Hardware status sensor definitions (native sensors)
STATUS_SENSORS: tuple[SpanPanelStatusSensorEntityDescription,] = (
    SpanPanelStatusSensorEntityDescription(
        key="software_version",
        name="Software Version",
        value_fn=lambda status: getattr(status, "firmware_version", "Unknown"),
    ),
)

# Unmapped circuit sensor definitions (native sensors - invisible backing data for synthetics)
UNMAPPED_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_POWER,
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda circuit: circuit.instant_power,
        entity_registry_enabled_default=True,  # Enabled but invisible
        entity_registry_visible_default=False,  # Hidden from UI
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_PRODUCED,
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.produced_energy,
        entity_registry_enabled_default=True,  # Enabled but invisible
        entity_registry_visible_default=False,  # Hidden from UI
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_CONSUMED,
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.consumed_energy,
        entity_registry_enabled_default=True,  # Enabled but invisible
        entity_registry_visible_default=False,  # Hidden from UI
    ),
)

# Battery sensor definition (native sensor - conditionally created)
BATTERY_SENSOR: SpanPanelBatterySensorEntityDescription = SpanPanelBatterySensorEntityDescription(
    key="storage_battery_percentage",
    name="Battery Level",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.BATTERY,
    value_fn=lambda battery: battery.storage_battery_percentage,
)

# Panel power and energy sensor definitions (native sensors to replace synthetic ones)
PANEL_POWER_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instantGridPowerW",
        name="Current Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda panel_data: panel_data.instant_grid_power,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughPowerW",
        name="Feed Through Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda panel_data: panel_data.feedthrough_power,
    ),
)

PANEL_ENERGY_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergyProducedWh",
        name="Main Meter Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.main_meter_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergyConsumedWh",
        name="Main Meter Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.main_meter_energy_consumed,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyProducedWh",
        name="Feed Through Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.feedthrough_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyConsumedWh",
        name="Feed Through Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.feedthrough_energy_consumed,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterNetEnergyWh",
        name="Main Meter Net Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: (
            (panel_data.main_meter_energy_consumed or 0)
            - (panel_data.main_meter_energy_produced or 0)
        ),
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughNetEnergyWh",
        name="Feed Through Net Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: (
            (panel_data.feedthrough_energy_consumed or 0)
            - (panel_data.feedthrough_energy_produced or 0)
        ),
    ),
)

# Circuit sensor definitions (native sensors to replace synthetic ones)
CIRCUIT_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda circuit: circuit.instant_power,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_produced",
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.produced_energy,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_consumed",
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.consumed_energy,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_net",
        name="Net Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: (circuit.consumed_energy or 0) - (circuit.produced_energy or 0),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
)


# Solar sensor definitions (native sensors to replace synthetic ones)
# These are template sensors that will be created when solar is enabled
@dataclass(frozen=True)
class SpanSolarSensorEntityDescription(SensorEntityDescription):
    """Describes a solar sensor entity that combines leg1 and leg2 circuits."""

    leg1_circuit_suffix: str = ""
    leg2_circuit_suffix: str = ""
    calculation_type: str = "sum"  # "sum" for power, "sum" for energy


SOLAR_SENSORS: tuple[
    SpanSolarSensorEntityDescription,
    SpanSolarSensorEntityDescription,
    SpanSolarSensorEntityDescription,
    SpanSolarSensorEntityDescription,
] = (
    SpanSolarSensorEntityDescription(
        key="solar_current_power",
        name="Solar Current Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        leg1_circuit_suffix="power",
        leg2_circuit_suffix="power",
        calculation_type="sum",
    ),
    SpanSolarSensorEntityDescription(
        key="solar_produced_energy",
        name="Solar Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        leg1_circuit_suffix="produced_energy",
        leg2_circuit_suffix="produced_energy",
        calculation_type="sum",
    ),
    SpanSolarSensorEntityDescription(
        key="solar_consumed_energy",
        name="Solar Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        leg1_circuit_suffix="consumed_energy",
        leg2_circuit_suffix="consumed_energy",
        calculation_type="sum",
    ),
    SpanSolarSensorEntityDescription(
        key="solar_net_energy",
        name="Solar Net Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        leg1_circuit_suffix="net_energy",
        leg2_circuit_suffix="net_energy",
        calculation_type="sum",
    ),
)

# Gen3-only circuit sensor definitions (gated on PanelCapability.PUSH_STREAMING)
CIRCUIT_GEN3_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_voltage_v",
        name="Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.VOLTAGE,
        value_fn=lambda circuit: circuit.voltage_v,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_current_a",
        name="Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.CURRENT,
        value_fn=lambda circuit: circuit.current_a,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_apparent_power_va",
        name="Apparent Power",
        native_unit_of_measurement="VA",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.APPARENT_POWER,
        value_fn=lambda circuit: circuit.apparent_power_va,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_reactive_power_var",
        name="Reactive Power",
        native_unit_of_measurement="var",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        value_fn=lambda circuit: circuit.reactive_power_var,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_frequency_hz",
        name="Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.FREQUENCY,
        value_fn=lambda circuit: circuit.frequency_hz,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_power_factor",
        name="Power Factor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.POWER_FACTOR,
        value_fn=lambda circuit: (
            (circuit.power_factor * 100) if circuit.power_factor is not None else None
        ),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
)

# Gen3-only panel-level sensor definitions (gated on PanelCapability.PUSH_STREAMING)
PANEL_GEN3_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="mainVoltageV",
        name="Main Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.VOLTAGE,
        value_fn=lambda panel_data: panel_data.main_voltage_v,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainCurrentA",
        name="Main Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.CURRENT,
        value_fn=lambda panel_data: panel_data.main_current_a,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainFrequencyHz",
        name="Main Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.FREQUENCY,
        value_fn=lambda panel_data: panel_data.main_frequency_hz,
    ),
)
