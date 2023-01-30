"""Control switches."""
from datetime import timedelta
import logging

from .span_panel import SpanPanel, CIRCUITS_POWER, CIRCUITS_ENERGY_PRODUCED, CIRCUITS_ENERGY_CONSUMED
import async_timeout

from homeassistant.components.switch import SwitchEntity
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

ICON = "mdi:toggle-switch"

_LOGGER = logging.getLogger(__name__)

class SpanPanelCircuitsSwitch(CoordinatorEntity, SwitchEntity):
    """Represent a switch entity."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        id: str,
        name: str,
    ) -> None:
        """Initialize the values."""
        _LOGGER.debug("CREATE SWITCH %s" % name)
        span_panel: SpanPanel = coordinator.data

        self.id = id
        self._attr_unique_id = f"span_{span_panel.serial_number}_relay_{id}"
        self._attr_device_info = panel_to_device_info(span_panel)
        super().__init__(coordinator)

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        _LOGGER.debug("TURN SWITCH ON")
        span_panel: SpanPanel = self.coordinator.data
        await span_panel.circuits.set_relay_closed(self.id)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        _LOGGER.debug("TURN SWITCH OFF")
        span_panel: SpanPanel = self.coordinator.data
        await span_panel.circuits.set_relay_open(self.id)
        await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        """Icon to use in the frontend."""
        return ICON

    @property
    def name(self):
        """Return the switch name."""
        span_panel: SpanPanel = self.coordinator.data
        return f"{span_panel.circuits.name(self.id)} Breaker"

    @property
    def is_on(self) -> bool:
        """Get switch state."""

        span_panel: SpanPanel = self.coordinator.data

        return span_panel.circuits.is_relay_closed(self.id)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up envoy sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY SWITCH")
    data: dict = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: DataUpdateCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data
    serial_number: str = config_entry.unique_id

    entities: list[SpanPanelCircuitsSwitch] = []

    for id in span_panel.circuits.keys():
       if span_panel.circuits.is_user_controllable(id):
          name = span_panel.circuits.name(id)
          entities.append(
             SpanPanelCircuitsSwitch(coordinator, id, name)
          )

    async_add_entities(entities)
