"""Binary Sensors for status entities."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (COORDINATOR, DOMAIN, SYSTEM_DOOR_STATE_CLOSED,
                    SYSTEM_DOOR_STATE_OPEN)
from .coordinator import SpanPanelCoordinator
from .span_panel import SpanPanel
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .util import panel_to_device_info

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpanPanelRequiredKeysMixin:
    value_fn: Callable[[SpanPanelHardwareStatus], bool | None]


@dataclass(frozen=True)
class SpanPanelBinarySensorEntityDescription(
    BinarySensorEntityDescription, SpanPanelRequiredKeysMixin
):
    """Describes an SpanPanelCircuits sensor entity."""

# Door state has benn observed to return UNKNOWN if the door 
# has not been operated recently so we check for invalid values
# pylint: disable=unexpected-keyword-arg
BINARY_SENSORS = (
    SpanPanelBinarySensorEntityDescription(
        key="doorState",
        name="Door State",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda status_data: None if status_data.door_state not in [SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN] 
                                    else not status_data.is_door_closed,
    ),
    SpanPanelBinarySensorEntityDescription(
        key="eth0Link",
        name="Ethernet Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda status_data: status_data.is_ethernet_connected,
    ),
    SpanPanelBinarySensorEntityDescription(
        key="wlanLink",
        name="Wi-Fi Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda status_data: status_data.is_wifi_connected,
    ),
    SpanPanelBinarySensorEntityDescription(
        key="wwanLink",
        name="Cellular Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda status_data: status_data.is_cellular_connected,
    ),
)


class SpanPanelBinarySensor(
    CoordinatorEntity[SpanPanelCoordinator], BinarySensorEntity
):
    """Binary Sensor status entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelBinarySensorEntityDescription,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        span_panel: SpanPanel = data_coordinator.data

        self.entity_description = description
        self._attr_name = f"{description.name}"
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_{description.key}"
        )
        self._attr_device_info = panel_to_device_info(span_panel)

        _LOGGER.debug("CREATE BINSENSOR [%s]", self._attr_name)

    @property
    def is_on(self) -> bool | None:
        """Return the status of the sensor."""
        # Get atomic snapshot of panel data
        span_panel: SpanPanel = self.coordinator.data
        description = cast(
            SpanPanelBinarySensorEntityDescription, self.entity_description
        )
        # Get atomic snapshot of status data
        status = span_panel.status
        status_is_on = description.value_fn(status)
        _LOGGER.debug("BINSENSOR [%s] is_on:[%s]", self._attr_name, status_is_on)
        return status_is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.is_on is not None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up status sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY BINARYSENSOR")

    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SpanPanelCoordinator = data[COORDINATOR]

    entities: list[SpanPanelBinarySensor] = []

    for description in BINARY_SENSORS:
        entities.append(SpanPanelBinarySensor(coordinator, description))

    async_add_entities(entities)
