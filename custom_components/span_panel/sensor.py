"""Support for Span Panel monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any, Generic, TypeVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CIRCUITS_ENERGY_CONSUMED,
    CIRCUITS_ENERGY_PRODUCED,
    CIRCUITS_POWER,
    COORDINATOR,
    CURRENT_RUN_CONFIG,
    DOMAIN,
    DSM_GRID_STATE,
    DSM_STATE,
    MAIN_RELAY_STATE,
    STATUS_SOFTWARE_VER,
    STORAGE_BATTERY_PERCENTAGE,
)
from .coordinator import SpanPanelCoordinator
from .helpers import construct_entity_id, get_user_friendly_suffix
from .options import BATTERY_ENABLE, INVERTER_ENABLE
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery
from .util import panel_to_device_info


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin:
    """Required keys mixin for Span Panel circuit sensors."""

    value_fn: Callable[[SpanPanelCircuit], float]


@dataclass(frozen=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    """Describes a Span Panel circuit sensor entity."""


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin:
    """Required keys mixin for Span Panel data sensors."""

    value_fn: Callable[[SpanPanelData], float | str]


@dataclass(frozen=True)
class SpanPanelDataSensorEntityDescription(
    SensorEntityDescription, SpanPanelDataRequiredKeysMixin
):
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
class SpanPanelStorageBatteryRequiredKeysMixin:
    """Required keys mixin for Span Panel storage battery sensors."""

    value_fn: Callable[[SpanPanelStorageBattery], int]


@dataclass(frozen=True)
class SpanPanelStorageBatterySensorEntityDescription(
    SensorEntityDescription, SpanPanelStorageBatteryRequiredKeysMixin
):
    """Describes a Span Panel storage battery sensor entity."""


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
D = TypeVar("D")  # For the type returned by get_data_source


class SpanSensorBase(
    CoordinatorEntity[SpanPanelCoordinator], SensorEntity, Generic[T, D]
):
    """Base class for Span Panel Sensors."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Sensor base entity."""
        super().__init__(data_coordinator, context=description)
        # See developer_attrtribute_readme.md for why we use
        # entity_description instead of _attr_entity_descriptio
        self.entity_description = description

        if hasattr(description, "device_class"):
            self._attr_device_class = description.device_class

        device_info: DeviceInfo = panel_to_device_info(span_panel)
        self._attr_device_info = device_info
        base_name: str | None = getattr(description, "name", None)

        self._attr_name = base_name

        if span_panel.status.serial_number and description.key:
            self._attr_unique_id = (
                f"span_{span_panel.status.serial_number}_{description.key}"
            )

        self._attr_icon = "mdi:flash"
        _LOGGER.debug("CREATE SENSOR SPAN [%s]", self._attr_name)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_native_value()
        super()._handle_coordinator_update()

    def _update_native_value(self) -> None:
        """Update the native value of the sensor."""
        if not self.coordinator.last_update_success:
            self._attr_native_value = None
            return

        value_function: Callable[[D], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            self._attr_native_value = None
            return

        try:
            data_source: D = self.get_data_source(self.coordinator.data)
            raw_value: float | int | str | None = value_function(data_source)
            _LOGGER.debug("native_value:[%s] [%s]", self._attr_name, raw_value)

            if raw_value is None:
                self._attr_native_value = None
            elif isinstance(raw_value, float | int):
                self._attr_native_value = float(raw_value)
            else:
                self._attr_native_value = str(raw_value)
        except (AttributeError, KeyError, IndexError):
            self._attr_native_value = None

    def get_data_source(self, span_panel: SpanPanel) -> D:
        """Get the data source for the sensor."""
        raise NotImplementedError("Subclasses must implement this method")


class SpanPanelCircuitSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Span Panel circuit sensor entity."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        circuit_id: str,
        name: str,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        # Get the circuit from the span_panel to access its properties
        circuit = span_panel.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        # Get the circuit number (tab position)
        circuit_number = circuit.tabs[0] if circuit.tabs else circuit_id

        entity_suffix = get_user_friendly_suffix(description.key)
        self.entity_id = construct_entity_id(  # type: ignore[assignment]
            coordinator, span_panel, "sensor", name, circuit_number, entity_suffix
        )

        friendly_name = f"{name} {description.name}"

        # Create a new description with the friendly name
        circuit_description = SpanPanelCircuitsSensorEntityDescription(
            key=description.key,
            name=friendly_name,
            device_class=description.device_class,
            entity_category=description.entity_category,
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
            force_update=description.force_update,
            icon=description.icon,
            has_entity_name=description.has_entity_name,
            translation_key=description.translation_key,
            translation_placeholders=description.translation_placeholders,
            unit_of_measurement=description.unit_of_measurement,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            suggested_unit_of_measurement=description.suggested_unit_of_measurement,
            value_fn=description.value_fn,
        )

        super().__init__(coordinator, circuit_description, span_panel)
        self.id: str = circuit_id
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_{circuit_id}_{description.key}"
        )

        # Ensure the native_unit_of_measurement is set correctly from the description
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = (
                description.native_unit_of_measurement
            )

        # Store initial circuit name for change detection in auto-sync names
        self._previous_circuit_name = name

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check for circuit name changes
        span_panel: SpanPanel = self.coordinator.data
        circuit = span_panel.circuits.get(self.id)
        if circuit:
            current_circuit_name = circuit.name

            # Only request reload if the circuit name has actually changed
            if current_circuit_name != self._previous_circuit_name:
                _LOGGER.info(
                    "Auto-sync detected circuit name change from '%s' to '%s', requesting integration reload",
                    self._previous_circuit_name,
                    current_circuit_name,
                )

                # Update stored previous name for next comparison
                self._previous_circuit_name = current_circuit_name

                # Request integration reload for next update cycle
                self.coordinator.request_reload()

        super()._handle_coordinator_update()

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        return span_panel.circuits[self.id]


class SpanPanelPanel(
    SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]
):
    """Span Panel data sensor entity."""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        return span_panel.panel


class SpanPanelPanelStatus(
    SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]
):
    """Span Panel status sensor entity."""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        return span_panel.panel


class SpanPanelStatus(
    SpanSensorBase[SpanPanelStatusSensorEntityDescription, SpanPanelHardwareStatus]
):
    """Span Panel hardware status sensor entity."""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        return span_panel.status


class SpanPanelStorageBatteryStatus(
    SpanSensorBase[
        SpanPanelStorageBatterySensorEntityDescription, SpanPanelStorageBattery
    ]
):
    """Span Panel storage battery sensor entity."""

    _attr_icon: str | None = "mdi:battery"

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

    entities: list[SpanSensorBase[Any, Any]] = []

    for description in PANEL_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Config entry should never be None here, but we check for safety
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
    if config_entry is not None and config_entry.options.get(BATTERY_ENABLE, False):
        for description_sb in STORAGE_BATTERY_SENSORS:
            entities.append(
                SpanPanelStorageBatteryStatus(coordinator, description_sb, span_panel)
            )

    async_add_entities(entities)
