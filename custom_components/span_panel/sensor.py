"""Support for Span Panel monitor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime
import logging
from typing import cast

from .span_panel import CIRCUITS_POWER, CIRCUITS_ENERGY_PRODUCED, CIRCUITS_ENERGY_CONSUMED

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import POWER_WATT, ENERGY_WATT_HOUR
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import COORDINATOR, DOMAIN
from .util import panel_to_device_info

@dataclass
class SpanPanelCircuitsRequiredKeysMixin:
    """Mixin for required keys."""

    value_fn: Callable[[SpanPanelCiruits], str]


@dataclass
class SpanPanelCircuitsSensorEntityDescription(SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin):
    """Describes an SpanPanelCircuits inverter sensor entity."""


CIRCUITS_SENSORS = (
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_POWER,
        name="Power",
        native_unit_of_measurement=POWER_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda circuits, id: abs(cast(float, circuits.power(id))),
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_PRODUCED,
        name="Produced Energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuits, id: circuits.energy_produced(id),
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_CONSUMED,
        name="Consumed Energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuits, id: circuits.energy_consumed(id),
    ),
)

PANEL_SENSORS = (
    SensorEntityDescription(
        key="instantGridPowerW",
        name="Current Power",
        native_unit_of_measurement=POWER_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="feedthroughPowerW",
        name="Feed Through Power",
        native_unit_of_measurement=POWER_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="mainMeterEnergy.producedEnergyWh",
        name="Main Meter Produced Energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="mainMeterEnergy.consumedEnergyWh",
        name="Main Meter Consumed Energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="feedthroughEnergy.producedEnergyWh",
        name="Feed Through Produced Energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    SensorEntityDescription(
        key="feedthroughEnergy.consumedEnergyWh",
        name="Feed Through Consumed Energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
)

ICON = "mdi:flash"
_LOGGER = logging.getLogger(__name__)

class SpanPanelCircuitSensor(CoordinatorEntity, SensorEntity):
    """Envoy inverter entity."""

    _attr_icon = ICON

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        id: str,
        name: str,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        span_panel: SpanPanel = coordinator.data

        self.entity_description = description
        self.id = id
        self._attr_name = f"{name} {description.name}"
        self._attr_unique_id = f"span_{span_panel.serial_number}_{id}_{description.key}"
        self._attr_device_info = panel_to_device_info(span_panel)

        _LOGGER.debug("CREATE SENSOR [%s]" % self._attr_name)
        super().__init__(coordinator)

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        span_panel: SpanPanel = self.coordinator.data
        value = self.entity_description.value_fn(span_panel.circuits, self.id)
        _LOGGER.debug("native_value:[%s] [%s]" % (self._attr_name, value))
        return cast(float, value)


class SpanPanelPanel(CoordinatorEntity, SensorEntity):
    """Envoy inverter entity."""

    _attr_icon = ICON

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        span_panel: SpanPanel = coordinator.data

        self.entity_description = description
        self._attr_name = f"{description.name}"
        self._attr_unique_id = f"span_{span_panel.serial_number}_{description.key}"
        self._attr_device_info = panel_to_device_info(span_panel)

        _LOGGER.debug("CREATE SENSOR SPAN [%s]" % self._attr_name)
        super().__init__(coordinator)

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        key = self.entity_description.key.split(".")
        span_panel: SpanPanel = self.coordinator.data
        panel = span_panel.panel_results.json()
        # Most sensors are simple value = panel[key], but in order
        # to support mainMeterEnergy.producedEnergyWh, we split the
        # key by `.` and loop down through the value
        value = panel
        for k in key:
            value = value[k]
        _LOGGER.debug("NATIVE VALUE [%s] [%s]" % (key, value))
        return cast(float, value)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up envoy sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY SENSOR")
    data: dict = hass.data[DOMAIN][config_entry.entry_id]
    _LOGGER.debug("  config_entry: %s" % config_entry)
    _LOGGER.debug("  config_entry(uid): %s" % config_entry.unique_id)
    _LOGGER.debug("  data: %s" % data)

    coordinator: DataUpdateCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: list[SpanPanelCircuitSensor | SpanPanelPanel] = []

    keys = ["7ef7a4091cdd4910a582b35b40768598"]

    for description in CIRCUITS_SENSORS:
        for id in span_panel.circuits.keys():
           name = span_panel.circuits.name(id)
           entities.append(
              SpanPanelCircuitSensor(coordinator, description, id, name)
           )

    for description in PANEL_SENSORS:
        entities.append(
           SpanPanelPanel(coordinator, description)
        )

    async_add_entities(entities)
