"""Binary Sensors for status entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any, Generic, TypeVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api import SpanPanelSnapshot

from .const import (
    CONF_API_VERSION,
    CONF_DEVICE_NAME,
    COORDINATOR,
    DOMAIN,
    PANEL_STATUS,
    SYSTEM_DOOR_STATE,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
    SYSTEM_ETHERNET_LINK,
    SYSTEM_WIFI_LINK,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_binary_sensor_unique_id_for_entry,
)
from .util import snapshot_to_device_info

# pylint: disable=invalid-overridden-method


_LOGGER: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpanPanelRequiredKeysMixin:
    """Required keys mixin for Span Panel binary sensors."""

    value_fn: Callable[[SpanPanelSnapshot], bool | None]


@dataclass(frozen=True)
class SpanPanelBinarySensorEntityDescription(
    BinarySensorEntityDescription, SpanPanelRequiredKeysMixin
):
    """Describes an SpanPanelCircuits sensor entity."""


# Door state has been observed to return UNKNOWN if the door
# has not been operated recently so we check for invalid values
# pylint: disable=unexpected-keyword-arg
BINARY_SENSORS: tuple[
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
] = (
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_DOOR_STATE,
        name="Door State",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda s: (
            None
            if s.door_state not in [SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN]
            else s.door_state != SYSTEM_DOOR_STATE_CLOSED
        ),
    ),
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_ETHERNET_LINK,
        name="Ethernet Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda s: s.eth0_link,
    ),
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_WIFI_LINK,
        name="Wi-Fi Link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda s: s.wlan_link,
    ),
    SpanPanelBinarySensorEntityDescription(
        key=PANEL_STATUS,
        name="Panel Status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda s: True,  # Placeholder - actual logic handled in sensor class
    ),
)

T = TypeVar("T", bound=SpanPanelBinarySensorEntityDescription)


class SpanPanelBinarySensor(
    CoordinatorEntity[SpanPanelCoordinator], BinarySensorEntity, Generic[T]
):
    """Binary Sensor status entity."""

    _attr_has_entity_name = True
    _attr_icon: str | None = "mdi:flash"

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data

        # See developer_attrtribute_readme.md for why we use
        # entity_description instead of _attr_entity_description
        self.entity_description = description
        self._attr_device_class = description.device_class

        # Store direct reference to the value_fn so we don't need to access through
        # entity_description later, avoiding type issues
        self._value_fn = description.value_fn

        # Consistent device name logic (matches switch)
        self._device_name = data_coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, data_coordinator.config_entry.title
        )

        is_simulator = data_coordinator.config_entry.data.get(CONF_API_VERSION) == "simulation"
        host = data_coordinator.config_entry.data.get(CONF_HOST)
        device_info: DeviceInfo = snapshot_to_device_info(
            snapshot, self._device_name, is_simulator, host
        )
        self._attr_device_info = device_info
        base_name: str | None = f"{description.name}"

        # Set entity name for HA automatic naming
        self._attr_name = base_name or ""

        self._attr_unique_id = self._construct_binary_sensor_unique_id(
            data_coordinator, snapshot, description.key
        )

    @property
    def available(self) -> bool:
        """Return entity availability.

        - Panel status sensor: always available to show online/offline state
        - Hardware status sensors: remain available when offline to show Unknown state
        - Other binary sensors (switches): become unavailable when panel is offline
        """
        # Panel status sensor should always be available to show online/offline state
        if hasattr(self.entity_description, "key") and self.entity_description.key == PANEL_STATUS:
            return True

        # Hardware status sensors should remain available when offline to show Unknown
        hardware_status_sensors = {
            SYSTEM_DOOR_STATE,
            SYSTEM_ETHERNET_LINK,
            SYSTEM_WIFI_LINK,
        }

        if (
            hasattr(self.entity_description, "key")
            and self.entity_description.key in hardware_status_sensors
        ):
            if getattr(self.coordinator, "panel_offline", False):
                return True

        return super().available

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Special handling for panel_status sensor
        if hasattr(self.entity_description, "key") and self.entity_description.key == PANEL_STATUS:
            self._attr_is_on = not self.coordinator.panel_offline
            self._attr_available = True
            _LOGGER.debug(
                "PANEL_STATUS_DEBUG: Set is_on=%s, available=%s",
                self._attr_is_on,
                self._attr_available,
            )
            super()._handle_coordinator_update()
            return

        # Check for panel offline status first to prevent accessing None data
        if self.coordinator.panel_offline or self.coordinator.data is None:
            hardware_status_sensors = {
                SYSTEM_DOOR_STATE,
                SYSTEM_ETHERNET_LINK,
                SYSTEM_WIFI_LINK,
            }

            if (
                hasattr(self.entity_description, "key")
                and self.entity_description.key in hardware_status_sensors
            ):
                self._attr_is_on = None
                _LOGGER.debug(
                    "Hardware status sensor %s: panel offline or no data - showing as unknown",
                    self.entity_id,
                )
            else:
                _LOGGER.debug(
                    "Binary sensor %s: panel offline or no data - will be unavailable",
                    self.entity_id,
                )

            super()._handle_coordinator_update()
            return

        # Panel is online and data is available — snapshot provides status fields directly
        snapshot = self.coordinator.data
        status_value = self._value_fn(snapshot)

        self._attr_is_on = status_value
        self._attr_available = status_value is not None

        super()._handle_coordinator_update()

    def _construct_binary_sensor_unique_id(
        self,
        data_coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
        description_key: str,
    ) -> str:
        """Construct unique ID for binary sensor entities."""
        return build_binary_sensor_unique_id_for_entry(
            data_coordinator, snapshot, description_key, self._device_name
        )


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

    # Force immediate coordinator refresh to ensure hardware sensors update right away
    await coordinator.async_request_refresh()
