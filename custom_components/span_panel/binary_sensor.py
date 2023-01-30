"""Support for Enphase Envoy solar energy monitor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime
import logging
from typing import cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import COORDINATOR, DOMAIN
from .util import panel_to_device_info

_LOGGER = logging.getLogger(__name__)

@dataclass
class SpanPanelRequiredKeysMixin:
    """Mixin for required keys."""

    value_fn: Callable[[SpanPanel], str]


@dataclass
class SpanPanelBinarySensorEntityDescription(BinarySensorEntityDescription, SpanPanelRequiredKeysMixin):
    """Describes an SpanPanelCircuits inverter sensor entity."""


BINARY_SENSORS = (
    SpanPanelBinarySensorEntityDescription(
        key = "doorState",
        name="Door State",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda span_panel: span_panel.is_door_open(),
    ),
    SpanPanelBinarySensorEntityDescription(
        key = "eth0Link",
        name="Ethernet Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda span_panel: span_panel.is_ethernet_connected(),
    ),
    SpanPanelBinarySensorEntityDescription(
        key = "wlanLink",
        name="Wi-Fi Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda span_panel: span_panel.is_wifi_connected(),
    ),
    SpanPanelBinarySensorEntityDescription(
        key = "wwanLink",
        name="Cellular Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda span_panel: span_panel.is_cellular_connected(),
    ),
)

class SpanPanelBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Envoy inverter entity."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: SpanPanelBinarySensorEntityDescription,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        span_panel: SpanPanel = coordinator.data

        self.entity_description = description
        self._attr_name = f"{description.name}"
        self._attr_unique_id = f"span_{span_panel.serial_number}_{description.key}"
        self._attr_device_info = panel_to_device_info(span_panel)

        _LOGGER.debug("CREATE BINSENSOR [%s]" % self._attr_name)
        super().__init__(coordinator)

    @property
    def is_on(self):
        """Return the status of the sensor."""
        _LOGGER.debug("BINSENSOR [%s] IS_ON" % self._attr_name)
        span_panel: SpanPanel = self.coordinator.data
        return self.entity_description.value_fn(span_panel)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up envoy sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY BINARYSENSOR")

    data: dict = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: DataUpdateCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: list[SpanPanelBinarySensor] = []

    for description in BINARY_SENSORS:
        entities.append(
           SpanPanelBinarySensor(coordinator, description)
        )

    async_add_entities(entities)
