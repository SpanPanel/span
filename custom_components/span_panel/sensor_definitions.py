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
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.helpers.entity import EntityCategory
from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
)

from .field_paths import FieldPathDeclarationMixin


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel circuit sensors."""

    value_fn: Callable[[SpanCircuitSnapshot], float | None]


@dataclass(frozen=True, kw_only=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    """Describes a Span Panel circuit sensor entity."""


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel data sensors."""

    value_fn: Callable[[SpanPanelSnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanPanelDataSensorEntityDescription(SensorEntityDescription, SpanPanelDataRequiredKeysMixin):
    """Describes a Span Panel data sensor entity."""


@dataclass(frozen=True)
class SpanPanelStatusRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel status sensors."""

    value_fn: Callable[[SpanPanelSnapshot], str]


@dataclass(frozen=True, kw_only=True)
class SpanPanelStatusSensorEntityDescription(
    SensorEntityDescription, SpanPanelStatusRequiredKeysMixin
):
    """Describes a Span Panel status sensor entity."""


@dataclass(frozen=True)
class SpanPanelBatteryRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel battery sensors."""

    value_fn: Callable[[SpanBatterySnapshot], float | None]


@dataclass(frozen=True, kw_only=True)
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
        derived=True,
        translation_key="dsm_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["dsm_off_grid", "dsm_on_grid", "unknown"],
        value_fn=lambda s: s.dsm_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="dsm_grid_state",
        derived=True,
        translation_key="dsm_grid_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["dsm_off_grid", "dsm_on_grid", "unknown"],
        value_fn=lambda s: s.dsm_state,  # deprecated alias — reads dsm_state
    ),
    SpanPanelDataSensorEntityDescription(
        key="current_run_config",
        derived=True,
        translation_key="current_run_config",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["panel_backup", "panel_off_grid", "panel_on_grid", "unknown"],
        value_fn=lambda s: s.current_run_config,
    ),
    SpanPanelDataSensorEntityDescription(
        key="main_relay_state",
        field_path="panel.main_relay_state",
        translation_key="main_relay_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["closed", "open", "unknown"],
        value_fn=lambda s: s.main_relay_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="grid_forming_entity",
        derived=True,
        translation_key="grid_forming_entity",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["battery", "generator", "grid", "none", "pv", "unknown"],
        value_fn=lambda s: s.dominant_power_source or "unknown",
    ),
    SpanPanelDataSensorEntityDescription(
        key="vendor_cloud",
        field_path="panel.vendor_cloud",
        translation_key="vendor_cloud",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["connected", "unconnected", "unknown"],
        value_fn=lambda s: s.vendor_cloud or "unknown",
    ),
)

# Hardware status sensor definitions
STATUS_SENSORS: tuple[SpanPanelStatusSensorEntityDescription,] = (
    SpanPanelStatusSensorEntityDescription(
        key="software_version",
        field_path="panel.firmware_version",
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
        field_path="circuit.instant_power_w",
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
        field_path="circuit.produced_energy_wh",
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
        field_path="circuit.consumed_energy_wh",
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
    field_path="battery.soe_percentage",
    translation_key="battery_level",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.BATTERY,
    value_fn=lambda b: b.soe_percentage,
)

# ---------------------------------------------------------------------------
# Panel diagnostic sensors (promoted from attributes)
# ---------------------------------------------------------------------------

# L1/L2 voltage sensors (v2 only, conditionally created)
L1_VOLTAGE_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="l1_voltage",
    field_path="panel.l1_voltage",
    translation_key="l1_voltage",
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    suggested_display_precision=1,
    value_fn=lambda s: s.l1_voltage,
)

L2_VOLTAGE_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="l2_voltage",
    field_path="panel.l2_voltage",
    translation_key="l2_voltage",
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    suggested_display_precision=1,
    value_fn=lambda s: s.l2_voltage,
)

# Upstream/downstream lug current sensors (v2 only, conditionally created)
UPSTREAM_L1_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="upstream_l1_current",
        field_path="panel.upstream_l1_current_a",
        translation_key="upstream_l1_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.upstream_l1_current_a,
    )
)

UPSTREAM_L2_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="upstream_l2_current",
        field_path="panel.upstream_l2_current_a",
        translation_key="upstream_l2_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.upstream_l2_current_a,
    )
)

DOWNSTREAM_L1_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="downstream_l1_current",
        field_path="panel.downstream_l1_current_a",
        translation_key="downstream_l1_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda s: s.downstream_l1_current_a,
    )
)

DOWNSTREAM_L2_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="downstream_l2_current",
        field_path="panel.downstream_l2_current_a",
        translation_key="downstream_l2_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda s: s.downstream_l2_current_a,
    )
)

# Main breaker rating sensor (v2 only, conditionally created)
MAIN_BREAKER_RATING_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="main_breaker_rating",
        field_path="panel.main_breaker_rating_a",
        translation_key="main_breaker_rating",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.main_breaker_rating_a,
    )
)

# ---------------------------------------------------------------------------
# Circuit diagnostic sensors (promoted from attributes)
# ---------------------------------------------------------------------------

# Per-circuit current sensor (v2 only, conditionally created)
CIRCUIT_CURRENT_SENSOR: SpanPanelCircuitsSensorEntityDescription = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_current",
        field_path="circuit.current_a",
        name="Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=lambda c: c.current_a,
    )
)

# Per-circuit breaker rating sensor (v2 only, conditionally created)
CIRCUIT_BREAKER_RATING_SENSOR: SpanPanelCircuitsSensorEntityDescription = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_breaker_rating",
        field_path="circuit.breaker_rating_a",
        name="Breaker Rating",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.breaker_rating_a,
    )
)

# ---------------------------------------------------------------------------
# BESS metadata sensors (on BESS sub-device)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanBessMetadataRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for BESS metadata sensors."""

    value_fn: Callable[[SpanBatterySnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanBessMetadataSensorEntityDescription(
    SensorEntityDescription, SpanBessMetadataRequiredKeysMixin
):
    """Describes a BESS metadata sensor entity."""


@dataclass(frozen=True)
class SpanMidRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for MID sensors."""

    value_fn: Callable[[SpanMidSnapshot], str | None]


@dataclass(frozen=True, kw_only=True)
class SpanMidSensorEntityDescription(SensorEntityDescription, SpanMidRequiredKeysMixin):
    """Describes a sensor on the Microgrid Interconnect Device."""


MID_SENSORS: tuple[SpanMidSensorEntityDescription, ...] = (
    SpanMidSensorEntityDescription(
        key="mid_grid_state",
        derived=True,
        translation_key="mid_grid_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["up", "down", "degraded", "unknown"],
        value_fn=lambda m: (m.grid_state or "unknown").lower(),
    ),
)
"""Sensors on the MID device.

Only `grid-state` — utility-supply health, genuinely new in v1.0 with no flat
equivalent, and the one non-metadata addition the MID brings.

Islanding state and the grid-forming entity are deliberately *not* duplicated here.
Both already reach a user through entities that predate v1.0 — `dsm_state` and
`grid_forming_entity` on the panel — and those must keep their ids and their history.
Showing the same fact twice is not the benign cell of the absorb-or-surface policy;
adding a fact nobody had is.
"""


BESS_METADATA_SENSORS: tuple[
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
] = (
    SpanBessMetadataSensorEntityDescription(
        key="vendor",
        field_path="battery.vendor_name",
        translation_key="bess_vendor",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.vendor_name,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="model",
        field_path="battery.model",
        translation_key="bess_model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.model,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="part_number",
        field_path="battery.part_number",
        translation_key="bess_part_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda b: b.part_number,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="serial_number",
        field_path="battery.serial_number",
        translation_key="bess_serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.serial_number,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="firmware_version",
        field_path="battery.software_version",
        translation_key="bess_firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.software_version,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="nameplate_capacity",
        field_path="battery.nameplate_capacity_kwh",
        translation_key="bess_nameplate_capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda b: b.nameplate_capacity_kwh,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="soe_kwh",
        field_path="battery.soe_kwh",
        translation_key="bess_soe_kwh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda b: b.soe_kwh,
    ),
)

# ---------------------------------------------------------------------------
# PV metadata sensors (on main panel device)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanPVMetadataRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for PV metadata sensors."""

    value_fn: Callable[[SpanPanelSnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanPVMetadataSensorEntityDescription(
    SensorEntityDescription, SpanPVMetadataRequiredKeysMixin
):
    """Describes a PV metadata sensor entity."""


PV_METADATA_SENSORS: tuple[
    SpanPVMetadataSensorEntityDescription,
    SpanPVMetadataSensorEntityDescription,
    SpanPVMetadataSensorEntityDescription,
] = (
    SpanPVMetadataSensorEntityDescription(
        key="pv_vendor",
        field_path="pv.vendor_name",
        translation_key="pv_vendor",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.pv.vendor_name,
    ),
    SpanPVMetadataSensorEntityDescription(
        key="pv_product",
        field_path="pv.model",
        translation_key="pv_product",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.pv.model,
    ),
    SpanPVMetadataSensorEntityDescription(
        key="pv_nameplate_capacity",
        field_path="pv.nameplate_capacity_w",
        translation_key="pv_nameplate_capacity",
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.pv.nameplate_capacity_w,
    ),
)


# Panel power sensor definitions
PANEL_POWER_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instantGridPowerW",
        field_path="panel.instant_grid_power_w",
        translation_key="instant_grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda s: s.instant_grid_power_w,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughPowerW",
        field_path="panel.feedthrough_power_w",
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
    field_path="panel.power_flow_battery",
    translation_key="battery_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: (-s.power_flow_battery or 0.0) if s.power_flow_battery is not None else 0.0,
)

# PV power sensor (conditionally created when PV is commissioned)
PV_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="pvPowerW",
    field_path="panel.power_flow_pv",
    translation_key="pv_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: (-s.power_flow_pv or 0.0) if s.power_flow_pv is not None else 0.0,
)

# Grid power flow sensor (conditionally created when power-flows data is available)
GRID_POWER_FLOW_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="gridPowerFlowW",
    field_path="panel.power_flow_grid",
    translation_key="grid_power_flow",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: (-s.power_flow_grid or 0.0) if s.power_flow_grid is not None else 0.0,
)

# Site power sensor (conditionally created when power-flows data is available)
SITE_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="sitePowerW",
    field_path="panel.power_flow_site",
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
        field_path="panel.main_meter_energy_produced_wh",
        translation_key="main_meter_produced_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.main_meter_energy_produced_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergyConsumedWh",
        field_path="panel.main_meter_energy_consumed_wh",
        translation_key="main_meter_consumed_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.main_meter_energy_consumed_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyProducedWh",
        field_path="panel.feedthrough_energy_produced_wh",
        translation_key="feedthrough_produced_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.feedthrough_energy_produced_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyConsumedWh",
        field_path="panel.feedthrough_energy_consumed_wh",
        translation_key="feedthrough_consumed_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.feedthrough_energy_consumed_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterNetEnergyWh",
        derived=True,
        translation_key="main_meter_net_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        entity_registry_enabled_default=False,
        value_fn=lambda s: (
            (s.main_meter_energy_consumed_wh or 0) - (s.main_meter_energy_produced_wh or 0)
        ),
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughNetEnergyWh",
        derived=True,
        translation_key="feedthrough_net_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        entity_registry_enabled_default=False,
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
        field_path="circuit.instant_power_w",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda c: (
            (-c.instant_power_w or 0.0) if c.device_type == "pv" else c.instant_power_w
        ),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_produced",
        field_path="circuit.produced_energy_wh",
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
        field_path="circuit.consumed_energy_wh",
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
        derived=True,
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
class SpanEvseRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for EVSE sensors."""

    value_fn: Callable[[SpanEvseSnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanEvseSensorEntityDescription(SensorEntityDescription, SpanEvseRequiredKeysMixin):
    """Describes an EVSE sensor entity."""


EVSE_SENSORS: tuple[
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
] = (
    SpanEvseSensorEntityDescription(
        key="evse_status",
        field_path="evse.status",
        translation_key="evse_status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "available",
            "charging",
            "faulted",
            "finishing",
            "preparing",
            "reserved",
            "suspended_ev",
            "suspended_evse",
            "unavailable",
            "unknown",
        ],
        value_fn=lambda e: e.status or "unknown",
    ),
    SpanEvseSensorEntityDescription(
        key="evse_advertised_current",
        field_path="evse.advertised_current_a",
        translation_key="evse_advertised_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=1,
        value_fn=lambda e: e.advertised_current_a,
    ),
    SpanEvseSensorEntityDescription(
        key="evse_lock_state",
        field_path="evse.lock_state",
        translation_key="evse_lock_state",
        device_class=SensorDeviceClass.ENUM,
        options=["locked", "unlocked", "unknown"],
        value_fn=lambda e: e.lock_state or "unknown",
    ),
)


def all_sensor_descriptions() -> tuple[SensorEntityDescription, ...]:
    """Every sensor description, without deduplication.

    Deliberately not keyed by `description.key`: keys such as "model" and
    "serial_number" repeat across device classes, so a dict keyed on them
    silently drops descriptions.
    """
    return (
        *PANEL_DATA_STATUS_SENSORS,
        *STATUS_SENSORS,
        *UNMAPPED_SENSORS,
        *MID_SENSORS,
        *BESS_METADATA_SENSORS,
        *PV_METADATA_SENSORS,
        *PANEL_POWER_SENSORS,
        *PANEL_ENERGY_SENSORS,
        *CIRCUIT_SENSORS,
        *EVSE_SENSORS,
        BATTERY_SENSOR,
        BATTERY_POWER_SENSOR,
        PV_POWER_SENSOR,
        GRID_POWER_FLOW_SENSOR,
        SITE_POWER_SENSOR,
        L1_VOLTAGE_SENSOR,
        L2_VOLTAGE_SENSOR,
        UPSTREAM_L1_CURRENT_SENSOR,
        UPSTREAM_L2_CURRENT_SENSOR,
        DOWNSTREAM_L1_CURRENT_SENSOR,
        DOWNSTREAM_L2_CURRENT_SENSOR,
        MAIN_BREAKER_RATING_SENSOR,
        CIRCUIT_CURRENT_SENSOR,
        CIRCUIT_BREAKER_RATING_SENSOR,
    )


def sensor_descriptions_by_field_path() -> dict[str, SensorEntityDescription]:
    """Every non-derived sensor description, keyed by the field path it reads.

    Keyed by field path rather than `description.key` for the reason
    `all_sensor_descriptions` returns a tuple: keys such as "model" and
    "serial_number" repeat across device classes, so a dict keyed on them
    silently drops descriptions. Derived descriptions are excluded — they read
    several fields, or none, so no single path identifies them.

    Lives here rather than at the call site so the narrowing to
    `FieldPathDeclarationMixin` stays with the descriptions that carry it.
    """
    by_field_path: dict[str, SensorEntityDescription] = {}
    for description in all_sensor_descriptions():
        if not isinstance(description, FieldPathDeclarationMixin):
            # `declared_field_paths` raises on this; here it is simply nothing
            # to key on.
            continue
        if description.derived or description.field_path is None:
            continue
        by_field_path[description.field_path] = description
    return by_field_path
