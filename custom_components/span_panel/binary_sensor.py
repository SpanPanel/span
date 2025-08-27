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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    COORDINATOR,
    DOMAIN,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
    USE_DEVICE_PREFIX,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_binary_sensor_unique_id_for_entry,
    construct_panel_entity_id,
)
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
    SpanPanelBinarySensorEntityDescription,
] = (
    SpanPanelBinarySensorEntityDescription(
        key="doorState",
        name="Door State",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda status_data: (
            None
            if status_data.door_state not in [SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN]
            else not status_data.is_door_closed
        ),
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
    SpanPanelBinarySensorEntityDescription(
        key="panel_status",
        name="Panel Status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda status_data: True,  # Placeholder - actual logic handled in sensor class
    ),
)

T = TypeVar("T", bound=SpanPanelBinarySensorEntityDescription)


class SpanPanelBinarySensor(
    CoordinatorEntity[SpanPanelCoordinator], BinarySensorEntity, Generic[T]
):
    """Binary Sensor status entity."""

    _attr_icon: str | None = "mdi:flash"

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        span_panel: SpanPanel = data_coordinator.data

        # See developer_attrtribute_readme.md for why we use
        # entity_description instead of _attr_entity_description
        self.entity_description = description
        self._attr_device_class = description.device_class

        # Store direct reference to the value_fn so we don't need to access through
        # entity_description later, avoiding type issues
        self._value_fn = description.value_fn

        # Consistent device name logic (matches switch)
        self._device_name = data_coordinator.config_entry.data.get(
            "device_name", data_coordinator.config_entry.title
        )

        device_info: DeviceInfo = panel_to_device_info(span_panel, self._device_name)
        self._attr_device_info = device_info
        base_name: str | None = f"{description.name}"

        if (
            data_coordinator.config_entry is not None
            and data_coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
            and self._device_name
        ):
            self._attr_name = f"{self._device_name} {base_name or ''}"
        else:
            self._attr_name = base_name or ""

        self._attr_unique_id = self._construct_binary_sensor_unique_id(
            data_coordinator, span_panel, description.key
        )

        # Get the device prefix setting
        use_device_prefix = data_coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

        # Use the panel-level helper for entity_id construction (status sensor pattern)
        # panel_status is a new sensor in this release, so it should always use migration_mode=False
        # Other sensors should use migration mode based on existing entities
        if description.key == "panel_status":
            migration_mode = False  # panel_status is new, always create
        else:
            # Determine migration mode by checking if this is a fresh install
            # If there are existing entities for this config entry, we're in migration mode
            entity_registry = async_get_entity_registry(data_coordinator.hass)
            existing_entities = [
                entity_id
                for entity_id, entity_entry in entity_registry.entities.items()
                if entity_entry.config_entry_id == data_coordinator.config_entry.entry_id
            ]
            migration_mode = len(existing_entities) > 0

        entity_id = construct_panel_entity_id(
            data_coordinator,
            span_panel,
            "binary_sensor",
            description.key.lower(),
            self._device_name,
            unique_id=self._attr_unique_id,
            migration_mode=migration_mode,
            use_device_prefix=use_device_prefix,
        )
        _LOGGER.debug(
            "BINARY_SENSOR_DEBUG: key=%s, migration_mode=%s, unique_id=%s, entity_id=%s",
            description.key,
            migration_mode,
            self._attr_unique_id,
            entity_id,
        )
        if entity_id is not None:
            self.entity_id = entity_id
        else:
            _LOGGER.error(
                "BINARY_SENSOR_ERROR: construct_panel_entity_id returned None for key=%s, unique_id=%s",
                description.key,
                self._attr_unique_id,
            )

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Special handling for panel_status sensor
        if (
            hasattr(self.entity_description, "key")
            and self.entity_description.key == "panel_status"
        ):
            self._attr_is_on = not self.coordinator.panel_offline
            self._attr_available = True
            _LOGGER.debug(
                "PANEL_STATUS_DEBUG: Set is_on=%s, available=%s",
                self._attr_is_on,
                self._attr_available,
            )
            super()._handle_coordinator_update()
            _LOGGER.debug(
                "PANEL_STATUS_DEBUG: After super() is_on=%s, available=%s",
                self._attr_is_on,
                self._attr_available,
            )
            return

        # Check for panel offline status first to prevent accessing None data
        if self.coordinator.panel_offline or self.coordinator.data is None:
            # When panel is offline or data is None, keep hardware status sensors available but show as unknown
            # This prevents them from showing as 'unavailable' and allows them to show as 'unknown'
            self._attr_is_on = None  # Show as 'unknown' instead of a specific state
            self._attr_available = True  # Keep sensor available
            _LOGGER.debug(
                "Hardware status sensor %s: panel offline or no data - showing as unknown but keeping available",
                self.entity_id,
            )
            super()._handle_coordinator_update()
            return

        # Panel is online and data is available - use normal logic
        status_data = self.coordinator.data.status
        status_value = self._value_fn(status_data)

        self._attr_is_on = status_value
        self._attr_available = status_value is not None

        super()._handle_coordinator_update()

    def _construct_binary_sensor_unique_id(
        self,
        data_coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        description_key: str,
    ) -> str:
        """Construct unique ID for binary sensor entities."""
        return build_binary_sensor_unique_id_for_entry(
            data_coordinator, span_panel, description_key, self._device_name
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
