# pyright: reportShadowedImports=false
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import COORDINATOR, DOMAIN
from .span_panel import SpanPanel
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

    def __init__(self, coordinator: DataUpdateCoordinator, id: str, name: str) -> None:
        _LOGGER.debug("CREATE SELECT %s", name)
        span_panel: SpanPanel = coordinator.data

        self.id = id
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_select_{self.id}"
        )
        self._attr_device_info = panel_to_device_info(span_panel)
        super().__init__(coordinator)

    @property
    def current_option(self) -> str:
        span_panel: SpanPanel = self.coordinator.data
        priority = span_panel.circuits[self.id].priority
        return PRIORITY_TO_HASS[priority]

    # async def async_select_option(self, option: str) -> None:
    #     _LOGGER.debug("SELECT - set option [%s] [%s]", option, HASS_TO_PRIORITY[option])
    #     span_panel: SpanPanel = self.coordinator.data
    #     priority = HASS_TO_PRIORITY[option]
    #     # TODO: Fix POST
    #     await span_panel.circuits.set_priority(self.id, priority)

    @property
    def name(self):
        """Return the switch name."""
        span_panel: SpanPanel = self.coordinator.data
        return f"{span_panel.circuits[self.id].name} Circuit Priority"


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

    entities: list[SpanPanelCircuitsSelect] = []

    for id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(SpanPanelCircuitsSelect(coordinator, id, circuit_data.name))

    async_add_entities(entities)
