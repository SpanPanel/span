"""Binary Sensors for status entities."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    COORDINATOR,
    DOMAIN,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
    USE_DEVICE_PREFIX,
)
from .coordinator import SpanPanelCoordinator
from .span_panel import SpanPanel
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .util import panel_to_device_info

# pylint: disable=invalid-overridden-method



_LOGGER: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpanPanelRequiredKeysMixin:
    """Required keys mixin for Span Panel binary sensors."""
    value_fn: Callable[[SpanPanelHardwareStatus], bool | None]


@dataclass(frozen=True)
class SpanPanelBinarySensorEntityDescription(
    BinarySensorEntityDescription, SpanPanelRequiredKeysMixin
):
    """Describes an SpanPanelCircuits sensor entity."""


# Door state has benn observed to return UNKNOWN if the door
# has not been operated recently so we check for invalid values
# pylint: disable=unexpected-keyword-arg
BINARY_SENSORS: tuple[
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
] = (
    SpanPanelBinarySensorEntityDescription(
        key="doorState",
        name="Door State",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda status_data: None
        if status_data.door_state
        not in [SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN]
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

T = TypeVar("T", bound=SpanPanelBinarySensorEntityDescription)


# pylint: disable=invalid-overridden-method
class SpanPanelBinarySensor(
    CoordinatorEntity[SpanPanelCoordinator], BinarySensorEntity, Generic[T]
):
    """Binary Sensor status entity."""

    _entity_description: T

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        span_panel: SpanPanel = data_coordinator.data

        self._entity_description = description
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

        _LOGGER.debug("CREATE BINSENSOR [%s]", self._attr_name)

    @property
    def entity_description(self) -> T:
        """Return the entity description."""
        return self._entity_description

    @property
    def is_on(self) -> bool | None:
        """Return the status of the sensor."""
        span_panel: SpanPanel = self.coordinator.data
        description = self._entity_description
        status: SpanPanelHardwareStatus = span_panel.status
        status_is_on: bool | None = description.value_fn(status)
        _LOGGER.debug("BINSENSOR [%s] is_on:[%s]", self._attr_name, status_is_on)
        return status_is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        span_panel: SpanPanel = self.coordinator.data
        description = self._entity_description
        status: SpanPanelHardwareStatus = span_panel.status
        return description.value_fn(status) is not None


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
