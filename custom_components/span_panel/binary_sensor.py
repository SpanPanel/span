"""Binary Sensors for status entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify
from span_panel_api import SpanEvseSnapshot, SpanPanelSnapshot

from . import SpanPanelConfigEntry
from .const import (
    CONF_API_VERSION,
    CONF_DEVICE_NAME,
    PANEL_STATUS,
    SYSTEM_DOOR_STATE,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
    SYSTEM_ETHERNET_LINK,
    SYSTEM_WIFI_LINK,
    USE_CIRCUIT_NUMBERS,
)
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .helpers import (
    build_binary_sensor_unique_id_for_entry,
    build_evse_unique_id_for_entry,
    has_bess,
    resolve_evse_display_suffix,
)
from .util import evse_device_info

# pylint: disable=invalid-overridden-method


_LOGGER: logging.Logger = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


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
        translation_key="door_state",
        device_class=BinarySensorDeviceClass.TAMPER,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: (
            None
            if s.door_state not in [SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN]
            else s.door_state != SYSTEM_DOOR_STATE_CLOSED
        ),
    ),
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_ETHERNET_LINK,
        translation_key="ethernet_link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.eth0_link,
    ),
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_WIFI_LINK,
        translation_key="wifi_link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.wlan_link,
    ),
    SpanPanelBinarySensorEntityDescription(
        key=PANEL_STATUS,
        translation_key="panel_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: True,  # Placeholder - actual logic handled in sensor class
    ),
)

BESS_CONNECTED_SENSOR = SpanPanelBinarySensorEntityDescription(
    key="bess_connected",
    translation_key="bess_connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda s: s.battery.connected,
)


class SpanPanelBinarySensor[T: SpanPanelBinarySensorEntityDescription](
    SpanPanelEntity, BinarySensorEntity
):
    """Binary Sensor status entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data

        self.entity_description = description
        self._attr_device_class = description.device_class
        self._value_fn = description.value_fn

        self._device_name = data_coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, data_coordinator.config_entry.title
        )

        self._attr_device_info = self._build_device_info(data_coordinator, snapshot)

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


# ---------------------------------------------------------------------------
# EVSE (EV Charger) binary sensors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanEvseBinarySensorRequiredKeysMixin:
    """Required keys mixin for EVSE binary sensors."""

    value_fn: Callable[[SpanEvseSnapshot], bool | None]


@dataclass(frozen=True)
class SpanEvseBinarySensorEntityDescription(
    BinarySensorEntityDescription, SpanEvseBinarySensorRequiredKeysMixin
):
    """Describes an EVSE binary sensor entity."""


_EV_CONNECTED_STATUSES: frozenset[str] = frozenset(
    {"PREPARING", "CHARGING", "SUSPENDED_EV", "SUSPENDED_EVSE", "FINISHING"}
)

EVSE_BINARY_SENSORS: tuple[
    SpanEvseBinarySensorEntityDescription,
    SpanEvseBinarySensorEntityDescription,
] = (
    SpanEvseBinarySensorEntityDescription(
        key="evse_charging",
        translation_key="evse_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda e: (e.status or "") == "CHARGING",
    ),
    SpanEvseBinarySensorEntityDescription(
        key="evse_ev_connected",
        translation_key="evse_ev_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda e: (e.status or "") in _EV_CONNECTED_STATUSES,
    ),
)

# Fallback EVSE snapshot used when the EVSE disappears mid-session
_EMPTY_EVSE = SpanEvseSnapshot(node_id="", feed_circuit_id="")


class SpanEvseBinarySensor(SpanPanelEntity, BinarySensorEntity):
    """EVSE (EV charger) binary sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanEvseBinarySensorEntityDescription,
        evse_id: str,
    ) -> None:
        """Initialize EVSE binary sensor."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data
        self._evse_id = evse_id
        self.entity_description = description
        self._attr_device_class = description.device_class
        self._value_fn = description.value_fn

        # Build EVSE sub-device info
        is_simulator = data_coordinator.config_entry.data.get(CONF_API_VERSION) == "simulation"
        panel_name = (
            data_coordinator.config_entry.data.get(
                CONF_DEVICE_NAME, data_coordinator.config_entry.title
            )
            or "Span Panel"
        )
        if is_simulator:
            panel_identifier = slugify(panel_name)
        else:
            panel_identifier = snapshot.serial_number

        evse = snapshot.evse.get(evse_id, _EMPTY_EVSE)
        use_circuit_numbers = data_coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        display_suffix = resolve_evse_display_suffix(evse, snapshot, use_circuit_numbers)
        self._attr_device_info = evse_device_info(
            panel_identifier, evse, panel_name, display_suffix
        )

        device_name = data_coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, data_coordinator.config_entry.title
        )
        self._attr_unique_id = build_evse_unique_id_for_entry(
            data_coordinator, snapshot, evse_id, description.key, device_name
        )

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.panel_offline or self.coordinator.data is None:
            self._attr_is_on = None
            super()._handle_coordinator_update()
            return

        snapshot = self.coordinator.data
        evse = snapshot.evse.get(self._evse_id, _EMPTY_EVSE)
        self._attr_is_on = self._value_fn(evse)
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up status sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY BINARYSENSOR")

    coordinator = config_entry.runtime_data.coordinator

    entities: list[
        SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription] | SpanEvseBinarySensor
    ] = []

    for description in BINARY_SENSORS:
        entities.append(SpanPanelBinarySensor(coordinator, description))

    # Add BESS connected sensor when battery is commissioned
    snapshot: SpanPanelSnapshot = coordinator.data
    if has_bess(snapshot):
        entities.append(SpanPanelBinarySensor(coordinator, BESS_CONNECTED_SENSOR))

    # Add EVSE binary sensors for each commissioned charger
    if snapshot.evse:
        for evse_id in snapshot.evse:
            for evse_desc in EVSE_BINARY_SENSORS:
                entities.append(SpanEvseBinarySensor(coordinator, evse_desc, evse_id))

    async_add_entities(entities)

    # Force immediate coordinator refresh to ensure hardware sensors update right away
    await coordinator.async_request_refresh()
