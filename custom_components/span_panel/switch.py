"""Control switches."""

import logging
from typing import Any, Literal

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

from .const import (
    COORDINATOR,
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    CircuitRelayState,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_switch_unique_id_for_entry,
)
from .span_panel import SpanPanel
from .util import panel_to_device_info

ICON: Literal["mdi:toggle-switch"] = "mdi:toggle-switch"

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Sentinel value to distinguish "never synced" from "circuit name is None"
_NAME_UNSET: object = object()


class SpanPanelCircuitsSwitch(CoordinatorEntity[SpanPanelCoordinator], SwitchEntity):
    """Represent a switch entity."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SpanPanelCoordinator, circuit_id: str, name: str, device_name: str
    ) -> None:
        """Initialize the values."""
        span_panel: SpanPanel = coordinator.data

        circuit = span_panel.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        self._circuit_id: str = circuit_id
        self._device_name = device_name
        self._attr_icon = "mdi:toggle-switch"
        self._attr_unique_id = self._construct_switch_unique_id(coordinator, span_panel, circuit_id)
        self._attr_device_info = panel_to_device_info(span_panel, device_name)

        # Check if entity already exists in registry
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(
            "switch", DOMAIN, self._attr_unique_id
        )

        if existing_entity_id:
            # Entity exists - always use panel name for sync
            circuit_identifier = circuit.name
            self._attr_name = f"{circuit_identifier} Breaker"
        else:
            # Initial install - use flag-based name for entity_id generation
            use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

            if use_circuit_numbers:
                # Use circuit number format: "Circuit 15 Breaker"
                if circuit.tabs and len(circuit.tabs) == 2:
                    sorted_tabs = sorted(circuit.tabs)
                    circuit_identifier = f"Circuit {sorted_tabs[0]} {sorted_tabs[1]}"
                elif circuit.tabs and len(circuit.tabs) == 1:
                    circuit_identifier = f"Circuit {circuit.tabs[0]}"
                else:
                    circuit_identifier = f"Circuit {circuit_id}"
            else:
                # Use friendly name format: "Kitchen Outlets Breaker"
                circuit_identifier = name

            self._attr_name = f"{circuit_identifier} Breaker"

        super().__init__(coordinator)

        self._update_is_on()

        # Use standard coordinator pattern - entities will update automatically
        # when coordinator data changes

        # Store initial circuit name for change detection in auto-sync
        # Use sentinel to distinguish "never synced" from "circuit name is None"
        if not existing_entity_id:
            self._previous_circuit_name: str | None | object = _NAME_UNSET
            _LOGGER.info("Switch entity not in registry, will sync on first update")
        else:
            self._previous_circuit_name = circuit.name
            _LOGGER.info(
                "Switch entity exists in registry, previous name set to '%s'", circuit.name
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        # Call parent cleanup
        await super().async_will_remove_from_hass()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check for circuit name changes
        span_panel: SpanPanel = self.coordinator.data
        circuit = span_panel.circuits.get(self._circuit_id)
        if circuit:
            current_circuit_name = circuit.name

            # Only request reload if the circuit name has actually changed
            if self._previous_circuit_name is _NAME_UNSET:
                # First update - sync to panel name
                _LOGGER.info(
                    "First update: syncing entity name to panel name '%s' for switch, requesting reload",
                    current_circuit_name,
                )
                # Update stored previous name for next comparison
                self._previous_circuit_name = current_circuit_name
                # Request integration reload to persist name change
                self.coordinator.request_reload()
            elif current_circuit_name != self._previous_circuit_name:
                _LOGGER.info(
                    "Name change detected: previous='%s', current='%s' for switch",
                    self._previous_circuit_name,
                    current_circuit_name,
                )
                _LOGGER.info(
                    "Auto-sync detected circuit name change from '%s' to '%s' for "
                    "switch, requesting integration reload",
                    self._previous_circuit_name,
                    current_circuit_name,
                )

                # Update stored previous name for next comparison
                self._previous_circuit_name = current_circuit_name

                # Request integration reload for next update cycle
                self.coordinator.request_reload()

        self._update_is_on()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return entity availability.

        Switches become unavailable when panel is offline since they can't control circuits.
        """
        if getattr(self.coordinator, "panel_offline", False):
            return False
        return super().available

    def _update_is_on(self) -> None:
        """Update the is_on state based on the circuit state."""
        span_panel: SpanPanel = self.coordinator.data
        # Get atomic snapshot of circuits data
        circuits: dict[str, SpanPanelCircuit] = span_panel.circuits
        circuit: SpanPanelCircuit | None = circuits.get(self._circuit_id)
        if circuit:
            # Use copy to ensure atomic state
            circuit = circuit.copy()
            self._attr_is_on = circuit.relay_state == CircuitRelayState.CLOSED.name
        else:
            self._attr_is_on = None

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""

        self.hass.create_task(self.async_turn_on(**kwargs))

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        self.hass.create_task(self.async_turn_off(**kwargs))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        span_panel: SpanPanel = self.coordinator.data
        circuits: dict[str, SpanPanelCircuit] = (
            span_panel.circuits
        )  # Get atomic snapshot of circuits
        if self._circuit_id in circuits:
            # Create a copy of the circuit for the operation
            curr_circuit: SpanPanelCircuit = circuits[self._circuit_id].copy()
            # Perform the state change
            await span_panel.api.set_relay(curr_circuit, CircuitRelayState.CLOSED)
            # Optimistically update local state to prevent UI bouncing
            self._attr_is_on = True
            if self.hass is not None:
                self.async_write_ha_state()
            # Request refresh to get the actual new state from panel
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        span_panel: SpanPanel = self.coordinator.data
        circuits: dict[str, SpanPanelCircuit] = (
            span_panel.circuits
        )  # Get atomic snapshot of circuits
        if self._circuit_id in circuits:
            # Create a copy of the circuit for the operation
            curr_circuit: SpanPanelCircuit = circuits[self._circuit_id].copy()
            # Perform the state change
            await span_panel.api.set_relay(curr_circuit, CircuitRelayState.OPEN)
            # Optimistically update local state to prevent UI bouncing
            self._attr_is_on = False
            # Only write state if hass is available
            if self.hass is not None:
                self.async_write_ha_state()
            # Request refresh to get the actual new state from panel
            await self.coordinator.async_request_refresh()

    def _construct_switch_unique_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        circuit_id: str,
    ) -> str:
        """Construct unique ID for switch entities."""
        return build_switch_unique_id_for_entry(
            coordinator, span_panel, circuit_id, self._device_name
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    # Get device name from config entry data
    _device_name = config_entry.data.get("device_name", config_entry.title)

    entities: list[SpanPanelCircuitsSwitch] = []

    for circuit_id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(
                SpanPanelCircuitsSwitch(coordinator, circuit_id, circuit_data.name, _device_name)
            )

    async_add_entities(entities)
