"""Control switches."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (CoordinatorEntity,
                                                      DataUpdateCoordinator)

from .const import COORDINATOR, DOMAIN, CircuitRelayState
from .span_panel import SpanPanel
from .util import panel_to_device_info

ICON = "mdi:toggle-switch"

_LOGGER = logging.getLogger(__name__)


class SpanPanelCircuitsSwitch(CoordinatorEntity, SwitchEntity):
    """Represent a switch entity."""

    def __init__(self, coordinator: DataUpdateCoordinator, id: str, name: str) -> None:
        """Initialize the values."""
        _LOGGER.debug("CREATE SWITCH %s" % name)
        span_panel: SpanPanel = coordinator.data

        self.id = id
        self._attr_unique_id = f"span_{span_panel.status.serial_number}_relay_{id}"
        self._attr_device_info = panel_to_device_info(span_panel)
        super().__init__(coordinator)

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        span_panel: SpanPanel = self.coordinator.data
        curr_circuit = span_panel.circuits[self.id]
        await span_panel.api.set_relay(curr_circuit, CircuitRelayState.CLOSED)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        span_panel: SpanPanel = self.coordinator.data
        curr_circuit = span_panel.circuits[self.id]
        await span_panel.api.set_relay(curr_circuit, CircuitRelayState.OPEN)
        await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        """Icon to use in the frontend."""
        return ICON

    @property
    def name(self):
        """Return the switch name."""
        span_panel: SpanPanel = self.coordinator.data
        return f"{span_panel.circuits[self.id].name} Breaker"

    @property
    def is_on(self) -> bool:
        """Get switch state."""
        span_panel: SpanPanel = self.coordinator.data
        return span_panel.circuits[self.id].is_relay_closed


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up envoy sensor platform.
    """

    _LOGGER.debug("ASYNC SETUP ENTRY SWITCH")
    data: dict = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: DataUpdateCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: list[SpanPanelCircuitsSwitch] = []

    for id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(SpanPanelCircuitsSwitch(coordinator, id, circuit_data.name))

    async_add_entities(entities)
