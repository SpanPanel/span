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

from .const import COORDINATOR, DOMAIN, CircuitPriority
from .span_panel import SpanPanel
from .util import panel_to_device_info

ICON = "mdi:toggle-switch"

_LOGGER = logging.getLogger(__name__)


class SpanPanelCircuitsSelect(CoordinatorEntity, SelectEntity):
    """Represent a switch entity."""

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
    def name(self):
        """Return the switch name."""
        span_panel: SpanPanel = self.coordinator.data
        return f"{span_panel.circuits[self.id].name} Circuit Priority"

    @property
    def options(self) -> list[str]:
        return [e.value for e in CircuitPriority if e != CircuitPriority.UNKNOWN]

    @property
    def current_option(self) -> str:
        span_panel: SpanPanel = self.coordinator.data
        priority = span_panel.circuits[self.id].priority
        return CircuitPriority[priority].value

    async def async_select_option(self, option: str) -> None:
        span_panel: SpanPanel = self.coordinator.data
        priority = CircuitPriority(option)
        curr_circuit = span_panel.circuits[self.id]
        await span_panel.api.set_priority(curr_circuit, priority)
        await self.coordinator.async_request_refresh()


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
