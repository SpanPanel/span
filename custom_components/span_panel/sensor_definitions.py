"""Sensor definitions for SPAN Panel integration.

This file contains sensor definitions for all native integration sensors:
- Panel status sensors (grid state, run config, relay state, dominant power source, vendor cloud)
- Hardware status sensors (software version)
- Panel power and energy sensors (grid, feedthrough, battery, site)
- Circuit power and energy sensors
- Unmapped circuit sensors (invisible backing data)
- Battery sensor
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.helpers.entity import EntityCategory
from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanPanelSnapshot,
)


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin:
    """Required keys mixin for Span Panel circuit sensors."""

    value_fn: Callable[[SpanCircuitSnapshot], float]


@dataclass(frozen=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    """Describes a Span Panel circuit sensor entity."""


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin:
    """Required keys mixin for Span Panel data sensors."""

    value_fn: Callable[[SpanPanelSnapshot], float | str]


@dataclass(frozen=True)
class SpanPanelDataSensorEntityDescription(SensorEntityDescription, SpanPanelDataRequiredKeysMixin):
    """Describes a Span Panel data sensor entity."""


@dataclass(frozen=True)
class SpanPanelStatusRequiredKeysMixin:
    """Required keys mixin for Span Panel status sensors."""

    value_fn: Callable[[SpanPanelSnapshot], str]


@dataclass(frozen=True)
class SpanPanelStatusSensorEntityDescription(
    SensorEntityDescription, SpanPanelStatusRequiredKeysMixin
):
    """Describes a Span Panel status sensor entity."""


@dataclass(frozen=True)
class SpanPanelBatteryRequiredKeysMixin:
    """Required keys mixin for Span Panel battery sensors."""

    value_fn: Callable[[SpanBatterySnapshot], float | None]


@dataclass(frozen=True)
class SpanPanelBatterySensorEntityDescription(
    SensorEntityDescription, SpanPanelBatteryRequiredKeysMixin
):
    """Describes a Span Panel battery sensor entity."""


# Panel data status sensor definitions
PANEL_DATA_STATUS_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="dsm_state",
        translation_key="dsm_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["unknown"],
        value_fn=lambda s: s.dsm_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="dsm_grid_state",
        translation_key="dsm_grid_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["unknown"],
        value_fn=lambda s: s.dsm_state,  # deprecated alias — reads dsm_state
    ),
    SpanPanelDataSensorEntityDescription(
        key="current_run_config",
        translation_key="current_run_config",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["unknown"],
        value_fn=lambda s: s.current_run_config,
    ),
    SpanPanelDataSensorEntityDescription(
        key="main_relay_state",
        translation_key="main_relay_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["unknown"],
        value_fn=lambda s: s.main_relay_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="grid_forming_entity",
        translation_key="grid_forming_entity",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["unknown"],
        value_fn=lambda s: s.dominant_power_source or "unknown",
    ),
    SpanPanelDataSensorEntityDescription(
        key="vendor_cloud",
        translation_key="vendor_cloud",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["unknown"],
        value_fn=lambda s: s.vendor_cloud or "unknown",
    ),
)

# Hardware status sensor definitions
STATUS_SENSORS: tuple[SpanPanelStatusSensorEntityDescription,] = (
    SpanPanelStatusSensorEntityDescription(
        key="software_version",
        translation_key="software_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.firmware_version,
    ),
)

# Unmapped circuit sensor definitions (invisible backing data)
# Keys are inline string literals preserving the v1 camelCase values for unique_id stability
UNMAPPED_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key="instantPowerW",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda c: c.instant_power_w,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=False,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="producedEnergyWh",
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: c.produced_energy_wh,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=False,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="consumedEnergyWh",
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: c.consumed_energy_wh,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=False,
    ),
)

# Battery sensor definition (conditionally created when battery data available)
BATTERY_SENSOR: SpanPanelBatterySensorEntityDescription = SpanPanelBatterySensorEntityDescription(
    key="storage_battery_percentage",
    translation_key="battery_level",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.BATTERY,
    value_fn=lambda b: b.soe_percentage,
)

# Panel power sensor definitions
PANEL_POWER_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instantGridPowerW",
        translation_key="instant_grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda s: s.instant_grid_power_w,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughPowerW",
        translation_key="feedthrough_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda s: s.feedthrough_power_w,
    ),
)

# Battery power sensor (conditionally created when BESS is commissioned)
BATTERY_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="batteryPowerW",
    translation_key="battery_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: s.power_flow_battery if s.power_flow_battery is not None else 0.0,
)

# PV power sensor (conditionally created when PV is commissioned)
PV_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="pvPowerW",
    translation_key="pv_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: -s.power_flow_pv if s.power_flow_pv is not None else 0.0,
)

# Site power sensor (conditionally created when power-flows data is available)
SITE_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="sitePowerW",
    translation_key="site_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: s.power_flow_site if s.power_flow_site is not None else 0.0,
)

# Panel energy sensor definitions
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
        translation_key="main_meter_produced_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.main_meter_energy_produced_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergyConsumedWh",
        translation_key="main_meter_consumed_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.main_meter_energy_consumed_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyProducedWh",
        translation_key="feedthrough_produced_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.feedthrough_energy_produced_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyConsumedWh",
        translation_key="feedthrough_consumed_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.feedthrough_energy_consumed_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterNetEnergyWh",
        translation_key="main_meter_net_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: (
            (s.main_meter_energy_consumed_wh or 0) - (s.main_meter_energy_produced_wh or 0)
        ),
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughNetEnergyWh",
        translation_key="feedthrough_net_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: (
            (s.feedthrough_energy_consumed_wh or 0) - (s.feedthrough_energy_produced_wh or 0)
        ),
    ),
)

# Circuit sensor definitions
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
        value_fn=lambda c: -c.instant_power_w if c.device_type == "pv" else c.instant_power_w,
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
        value_fn=lambda c: c.produced_energy_wh,
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
        value_fn=lambda c: c.consumed_energy_wh,
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
        value_fn=lambda c: (
            (c.produced_energy_wh or 0) - (c.consumed_energy_wh or 0)
            if c.device_type == "pv"
            else (c.consumed_energy_wh or 0) - (c.produced_energy_wh or 0)
        ),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
)


# ---------------------------------------------------------------------------
# EVSE (EV Charger) sensor definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanEvseRequiredKeysMixin:
    """Required keys mixin for EVSE sensors."""

    value_fn: Callable[[SpanEvseSnapshot], float | str | None]


@dataclass(frozen=True)
class SpanEvseSensorEntityDescription(SensorEntityDescription, SpanEvseRequiredKeysMixin):
    """Describes an EVSE sensor entity."""


EVSE_SENSORS: tuple[
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
] = (
    SpanEvseSensorEntityDescription(
        key="evse_status",
        translation_key="evse_status",
        device_class=SensorDeviceClass.ENUM,
        options=["unknown"],
        value_fn=lambda e: e.status if e.status else "unknown",
    ),
    SpanEvseSensorEntityDescription(
        key="evse_advertised_current",
        translation_key="evse_advertised_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=1,
        value_fn=lambda e: e.advertised_current_a,
    ),
    SpanEvseSensorEntityDescription(
        key="evse_lock_state",
        translation_key="evse_lock_state",
        device_class=SensorDeviceClass.ENUM,
        options=["unknown"],
        value_fn=lambda e: e.lock_state if e.lock_state else "unknown",
    ),
)
