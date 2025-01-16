"""Support for Span Panel monitor."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, List, TypeVar

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity,
                                             SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (CIRCUITS_ENERGY_CONSUMED, CIRCUITS_ENERGY_PRODUCED,
                    CIRCUITS_POWER, COORDINATOR, CURRENT_RUN_CONFIG, DOMAIN,
                    DSM_GRID_STATE, DSM_STATE, MAIN_RELAY_STATE,
                    STATUS_SOFTWARE_VER, STORAGE_BATTERY_PERCENTAGE,
                    USE_DEVICE_PREFIX)
from .coordinator import SpanPanelCoordinator
from .options import BATTERY_ENABLE, INVERTER_ENABLE
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery
from .util import panel_to_device_info


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin:
    value_fn: Callable[[SpanPanelCircuit], float]


@dataclass(frozen=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    pass


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin:
    value_fn: Callable[[SpanPanelData], float | str]


@dataclass(frozen=True)
class SpanPanelDataSensorEntityDescription(
    SensorEntityDescription, SpanPanelDataRequiredKeysMixin
):
    pass


@dataclass(frozen=True)
class SpanPanelStatusRequiredKeysMixin:
    value_fn: Callable[[SpanPanelHardwareStatus], str]


@dataclass(frozen=True)
class SpanPanelStatusSensorEntityDescription(
    SensorEntityDescription, SpanPanelStatusRequiredKeysMixin
):
    pass


@dataclass(frozen=True)
class SpanPanelStorageBatteryRequiredKeysMixin:
    value_fn: Callable[[SpanPanelStorageBattery], int]


@dataclass(frozen=True)
class SpanPanelStorageBatterySensorEntityDescription(
    SensorEntityDescription, SpanPanelStorageBatteryRequiredKeysMixin
):
    pass


# pylint: disable=unexpected-keyword-arg
CIRCUITS_SENSORS: tuple[
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
        value_fn=lambda circuit: abs(circuit.instant_power),
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_PRODUCED,
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.produced_energy,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_CONSUMED,
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.consumed_energy,
    ),
)

PANEL_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instantGridPowerW",
        name="Current Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda panel_data: panel_data.instant_grid_power,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughPowerW",
        name="Feed Through Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda panel_data: panel_data.feedthrough_power,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergy.producedEnergyWh",
        name="Main Meter Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.main_meter_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergy.consumedEnergyWh",
        name="Main Meter Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.main_meter_energy_consumed,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergy.producedEnergyWh",
        name="Feed Through Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.feedthrough_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergy.consumedEnergyWh",
        name="Feed Through Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.feedthrough_energy_consumed,
    ),
)

INVERTER_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="solar_inverter_instant_power",
        name="Solar Inverter Instant Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda panel_data: panel_data.solar_inverter_instant_power,
    ),
    SpanPanelDataSensorEntityDescription(
        key="solar_inverter_energy_produced",
        name="Solar Inverter Energy Produced",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda panel_data: panel_data.solar_inverter_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="solar_inverter_energy_consumed",
        name="Solar Inverter Energy Consumed",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda panel_data: panel_data.solar_inverter_energy_consumed,
    ),
)

PANEL_DATA_STATUS_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key=CURRENT_RUN_CONFIG,
        name="Current Run Config",
        value_fn=lambda panel_data: panel_data.current_run_config,
    ),
    SpanPanelDataSensorEntityDescription(
        key=DSM_GRID_STATE,
        name="DSM Grid State",
        value_fn=lambda panel_data: panel_data.dsm_grid_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key=DSM_STATE,
        name="DSM State",
        value_fn=lambda panel_data: panel_data.dsm_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key=MAIN_RELAY_STATE,
        name="Main Relay State",
        value_fn=lambda panel_data: panel_data.main_relay_state,
    ),
)

STATUS_SENSORS: tuple[SpanPanelStatusSensorEntityDescription] = (
    SpanPanelStatusSensorEntityDescription(
        key=STATUS_SOFTWARE_VER,
        name="Software Version",
        value_fn=lambda status: getattr(status, "firmware_version", "unknown_version"),
    ),
)

STORAGE_BATTERY_SENSORS: tuple[SpanPanelStorageBatterySensorEntityDescription] = (
    SpanPanelStorageBatterySensorEntityDescription(
        key=STORAGE_BATTERY_PERCENTAGE,
        name="SPAN Storage Battery Percentage",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda storage_battery: (storage_battery.storage_battery_percentage),
    ),
)

ICON = "mdi:flash"
_LOGGER: logging.Logger = logging.getLogger(__name__)

T = TypeVar("T", bound=SensorEntityDescription)


class SpanSensorBase(CoordinatorEntity[SpanPanelCoordinator], SensorEntity, Generic[T]):
    """Base class for Span Panel Sensors."""

    _attr_icon = ICON
    entity_description: T

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Sensor base entity."""
        super().__init__(data_coordinator, context=description)
        self.entity_description = description
        device_info: DeviceInfo = panel_to_device_info(span_panel)
        self._attr_device_info = device_info
        base_name: str = f"{description.name}"

        if (
            data_coordinator.config_entry is not None
            and data_coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
            and device_info is not None
            and isinstance(device_info, dict)
            and "name" in device_info
        ):
            self._attr_name = f"{device_info['name']} {base_name}"
        else:
            self._attr_name = base_name

        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_{description.key}"
        )

        _LOGGER.debug("CREATE SENSOR SPAN [%s]", self._attr_name)

    @property
    def native_value(self) -> float | str | None:
        """Return the state of the sensor."""
        # Get atomic snapshot of panel data
        span_panel: SpanPanel = self.coordinator.data
        value_function: Callable[[Any], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            return None

        data_source: Any = self.get_data_source(span_panel)
        raw_value: float | int | str | None = value_function(data_source)
        _LOGGER.debug("native_value:[%s] [%s]", self._attr_name, raw_value)

        if isinstance(raw_value, (float, int, str)) or raw_value is None:
            return raw_value

    def get_data_source(self, span_panel: SpanPanel) -> Any:
        """Get the data source for the sensor."""
        raise NotImplementedError("Subclasses must implement this method")


class SpanPanelCircuitSensor(SpanSensorBase[SpanPanelCircuitsSensorEntityDescription]):
    """Initialize SpanPanelCircuitSensor"""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        circuit_id: str,
        name: str,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        # Create a new description with modified name including circuit name
        circuit_description = SpanPanelCircuitsSensorEntityDescription(
            **{**vars(description), "name": f"{name} {description.name}"}
        )
        super().__init__(coordinator, circuit_description, span_panel)
        self.id: str = circuit_id
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_{circuit_id}_{description.key}"
        )

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        return span_panel.circuits[self.id]


class SpanPanelPanel(SpanSensorBase[SpanPanelDataSensorEntityDescription]):
    """Initialize SpanPanelPanel"""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        return span_panel.panel


class SpanPanelPanelStatus(SpanSensorBase[SpanPanelDataSensorEntityDescription]):
    """Initialize SpanPanelPanelStatus"""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        return span_panel.panel


class SpanPanelStatus(SpanSensorBase[SpanPanelStatusSensorEntityDescription]):
    """Initialize SpanPanelStatus"""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        return span_panel.status


class SpanPanelStorageBatteryStatus(
    SpanSensorBase[SpanPanelStorageBatterySensorEntityDescription]
):
    """Initialize SpanPanelStorageBatteryStatus"""

    _attr_icon: str = "mdi:battery"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelStorageBattery:
        return span_panel.storage_battery


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: List[SpanSensorBase[Any]] = []

    for description in PANEL_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    if config_entry.options.get(INVERTER_ENABLE, False):
        for description_i in INVERTER_SENSORS:
            entities.append(
                SpanPanelPanelStatus(coordinator, description_i, span_panel)
            )

    for description_ss in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description_ss, span_panel))

    for description_cs in CIRCUITS_SENSORS:
        for id_c, circuit_data in span_panel.circuits.items():
            entities.append(
                SpanPanelCircuitSensor(
                    coordinator, description_cs, id_c, circuit_data.name, span_panel
                )
            )
    if config_entry.options.get(BATTERY_ENABLE, False):
        for description_sb in STORAGE_BATTERY_SENSORS:
            entities.append(
                SpanPanelStorageBatteryStatus(coordinator, description_sb, span_panel)
            )

    async_add_entities(entities)
