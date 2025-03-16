"""Control switches."""

import logging
from typing import Any, Dict, Literal

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

from .const import COORDINATOR, DOMAIN, USE_DEVICE_PREFIX, CircuitRelayState
from .coordinator import SpanPanelCoordinator
from .span_panel import SpanPanel
from .util import panel_to_device_info

ICON: Literal["mdi:toggle-switch"] = "mdi:toggle-switch"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelCircuitsSwitch(CoordinatorEntity[SpanPanelCoordinator], SwitchEntity):
    """Represent a switch entity."""

    def __init__(
        self, coordinator: SpanPanelCoordinator, circuit_id: str, name: str
    ) -> None:
        """Initialize the values."""
        _LOGGER.debug("CREATE SWITCH %s", name)
        span_panel: SpanPanel = coordinator.data

        self.circuit_id: str = circuit_id
        self._attr_icon = "mdi:toggle-switch"
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_relay_{circuit_id}"
        )
        self._attr_device_info = panel_to_device_info(span_panel)

        # Set the name using _attr_name instead of property override
        base_name = f"{span_panel.circuits[self.circuit_id].name} Breaker"
        config_entry: ConfigEntry[Any] | None = coordinator.config_entry

        if (
            config_entry
            and config_entry.options.get(USE_DEVICE_PREFIX, False)
            and self._attr_device_info
            and "name" in self._attr_device_info
        ):
            device_name = self._attr_device_info["name"]
            self._attr_name = f"{device_name} {base_name}"
        else:
            self._attr_name = base_name

        super().__init__(coordinator)

        # Initialize is_on state
        self._update_is_on()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_is_on()
        super()._handle_coordinator_update()

    def _update_is_on(self) -> None:
        """Update the is_on state based on the circuit state."""
        span_panel: SpanPanel = self.coordinator.data
        # Get atomic snapshot of circuits data
        circuits: Dict[str, SpanPanelCircuit] = span_panel.circuits
        circuit: SpanPanelCircuit | None = circuits.get(self.circuit_id)
        if circuit:
            # Use copy to ensure atomic state
            circuit = circuit.copy()
            self._attr_is_on = circuit.relay_state == CircuitRelayState.CLOSED.name
        else:
            self._attr_is_on = None

    def turn_on(self, **kwargs: Any) -> None:
        """Synchronously turn the switch on."""
        self.hass.create_task(self.async_turn_on(**kwargs))

    def turn_off(self, **kwargs: Any) -> None:
        """Synchronously turn the switch off."""
        self.hass.create_task(self.async_turn_off(**kwargs))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        span_panel: SpanPanel = self.coordinator.data
        circuits: Dict[str, SpanPanelCircuit] = (
            span_panel.circuits
        )  # Get atomic snapshot of circuits
        if self.circuit_id in circuits:
            # Create a copy of the circuit for the operation
            curr_circuit: SpanPanelCircuit = circuits[self.circuit_id].copy()
            # Perform the state change
            await span_panel.api.set_relay(curr_circuit, CircuitRelayState.CLOSED)
            # Request refresh to get the new state
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        span_panel: SpanPanel = self.coordinator.data
        circuits: Dict[str, SpanPanelCircuit] = (
            span_panel.circuits
        )  # Get atomic snapshot of circuits
        if self.circuit_id in circuits:
            # Create a copy of the circuit for the operation
            curr_circuit: SpanPanelCircuit = circuits[self.circuit_id].copy()
            # Perform the state change
            await span_panel.api.set_relay(curr_circuit, CircuitRelayState.OPEN)
            # Request refresh to get the new state
            await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up envoy sensor platform.
    """

    _LOGGER.debug("ASYNC SETUP ENTRY SWITCH")
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: list[SpanPanelCircuitsSwitch] = []

    for circuit_id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(
                SpanPanelCircuitsSwitch(coordinator, circuit_id, circuit_data.name)
            )

    async_add_entities(entities)
