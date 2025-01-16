# pyright: reportShadowedImports=false
import logging
from functools import cached_property
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COORDINATOR, DOMAIN, USE_DEVICE_PREFIX, CircuitPriority
from .coordinator import SpanPanelCoordinator
from .span_panel import SpanPanel
from .util import panel_to_device_info

ICON = "mdi:toggle-switch"

_LOGGER = logging.getLogger(__name__)


class SpanPanelCircuitsSelect(CoordinatorEntity[SpanPanelCoordinator], SelectEntity):
    """Represent a switch entity."""

    def __init__(self, coordinator: SpanPanelCoordinator, id: str, name: str) -> None:
        _LOGGER.debug("CREATE SELECT %s", name)
        span_panel: SpanPanel = coordinator.data

        self.id: str = id
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_select_{self.id}"
        )
        self._attr_device_info = panel_to_device_info(span_panel)
        super().__init__(coordinator)

    @cached_property
    def name(self) -> str:
        """Return the switch name."""
        span_panel: SpanPanel = self.coordinator.data
        base_name: str = f"{span_panel.circuits[self.id].name} Circuit Priority"
        if (
            self.coordinator.config_entry is not None
            and self.coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
        ):
            device_info: DeviceInfo | None = self._attr_device_info
            if device_info is not None and "name" in device_info:
                return f"{device_info['name']} {base_name}"
        return base_name

    @cached_property
    def options(self) -> list[str]:
        return [e.value for e in CircuitPriority if e != CircuitPriority.UNKNOWN]

    @cached_property
    def current_option(self) -> str | None:
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
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: list[SpanPanelCircuitsSelect] = []

    for circuit_id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(
                SpanPanelCircuitsSelect(coordinator, circuit_id, circuit_data.name)
            )

    async_add_entities(entities)
