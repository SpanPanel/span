"""Control switches."""
from datetime import timedelta
import logging

from .span_panel import SpanPanel, CIRCUITS_POWER, CIRCUITS_ENERGY_PRODUCED, CIRCUITS_ENERGY_CONSUMED
import async_timeout

from homeassistant.components.select import SelectEntity, SelectEntityDescription
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

PRIORITY_TO_HASS = {
    "MUST_HAVE": "Must Have",
    "NICE_TO_HAVE": "Nice to Have",
    "NOT_ESSENTIAL": "Not Essential",
    "NON_ESSENTIAL": "Non Essential",
}
HASS_TO_PRIORITY = {v: k for k, v in PRIORITY_TO_HASS.items()}

class SpanPanelCircuitsSelect(CoordinatorEntity, SelectEntity):
    """Represent a switch entity."""

    _attr_options = list(PRIORITY_TO_HASS.values())

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        id: str,
        name: str,
    ) -> None:
        """Initialize the values."""
        _LOGGER.debug("CREATE SELECT %s" % name)
        span_panel: SpanPanel = coordinator.data

        self.id = id
        self._attr_unique_id = f"span_{span_panel.serial_number}_select_{id}"
        self._attr_device_info = panel_to_device_info(span_panel)
        super().__init__(coordinator)

    @property
    def current_option(self) -> str:
        """Return the current value."""
        span_panel: SpanPanel = self.coordinator.data
        priority = span_panel.circuits.get_priority(self.id)
        return PRIORITY_TO_HASS[priority]

    async def async_select_option(self, option: str) -> None:
        """Set the option."""
        _LOGGER.debug("SELECT - set option [%s] [%s]" % (option, HASS_TO_PRIORITY[option]))
        span_panel: SpanPanel = self.coordinator.data
        priority = HASS_TO_PRIORITY[option]
        await span_panel.circuits.set_priority(self.id, priority)


    @property
    def name(self):
        """Return the switch name."""
        span_panel: SpanPanel = self.coordinator.data
        return f"{span_panel.circuits.name(self.id)} Circuit Priority"


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
             SpanPanelCircuitsSelect(coordinator, id, name)
          )

    async_add_entities(entities)
