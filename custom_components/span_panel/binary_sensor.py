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


class SpanPanelBinarySensor(
    CoordinatorEntity[SpanPanelCoordinator], BinarySensorEntity, Generic[T]
):
    """Binary Sensor status entity."""

    _attr_icon = "mdi:flash"

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        span_panel: SpanPanel = data_coordinator.data

        self._attr_entity_description = description
        # HA (2025.3.3) has a base class inconsistency where sensors produce
        # warnings if we explicitly set the device class but binary sensors
        # require setting the device class attribute for specific state
        # conversions from boolean (for example cleared, connected, etc.)
        self._attr_device_class = description.device_class
        device_info: DeviceInfo = panel_to_device_info(span_panel)
        self._attr_device_info = device_info
        base_name: str = f"{description.name}"

        if (
            data_coordinator.config_entry is not None
            and data_coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
            and "name" in device_info
        ):
            self._attr_name = f"{device_info['name']} {base_name}"
        else:
            self._attr_name = base_name

        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_{description.key}"
        )

        _LOGGER.debug("CREATE BINSENSOR [%s]", self._attr_name)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Get the raw status value from the device
        status_data = self.coordinator.data.status
        # Use the value_fn to get the binary state
        status_value = self._attr_entity_description.value_fn(status_data)

        self._attr_is_on = status_value

        self._attr_available = status_value is not None

        _LOGGER.debug(
            "BINSENSOR [%s] updated: is_on=%s, available=%s",
            self._attr_name,
            self._attr_is_on,
            self._attr_available,
        )

        # Call parent method to notify HA of the update
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up status sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY BINARYSENSOR")

    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SpanPanelCoordinator = data[COORDINATOR]

    entities: list[SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription]] = []

    for description in BINARY_SENSORS:
        entities.append(SpanPanelBinarySensor(coordinator, description))

    async_add_entities(entities)
