"""Control switches."""

import logging
from typing import Any, Literal

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

from .const import (
    COORDINATOR,
    DOMAIN,
    SIGNAL_STAGE_SWITCHES,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    CircuitRelayState,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_switch_unique_id_for_entry,
    construct_entity_id,
    get_circuit_number,
)
from .span_panel import SpanPanel
from .util import panel_to_device_info

ICON: Literal["mdi:toggle-switch"] = "mdi:toggle-switch"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelCircuitsSwitch(CoordinatorEntity[SpanPanelCoordinator], SwitchEntity):
    """Represent a switch entity."""

    def __init__(
        self, coordinator: SpanPanelCoordinator, circuit_id: str, name: str, device_name: str
    ) -> None:
        """Initialize the values."""
        span_panel: SpanPanel = coordinator.data

        circuit = span_panel.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        # Get the actual circuit number (tab position)
        circuit_number = get_circuit_number(circuit)

        self._circuit_id: str = circuit_id
        self._device_name = device_name
        self._attr_icon = "mdi:toggle-switch"
        self._attr_unique_id = self._construct_switch_unique_id(coordinator, span_panel, circuit_id)
        self._attr_device_info = panel_to_device_info(span_panel, device_name)

        # Use the common helper for entity_id construction
        entity_id = construct_entity_id(
            coordinator,
            span_panel,
            "switch",
            name,
            circuit_number,
            "breaker",
            None,
        )
        if entity_id is not None:
            self.entity_id = entity_id

        friendly_name = f"{name} Breaker"

        self._attr_name = friendly_name

        super().__init__(coordinator)

        self._update_is_on()

        # Subscribe to staged updates so switches run first each tick. Schedule
        # on the event loop to satisfy HA's thread-safety constraints.
        def _on_stage() -> None:
            # Schedule work onto the HA event loop thread explicitly.
            if self.hass is None:
                return

            def _run_on_loop() -> None:
                self._update_is_on()
                self.async_write_ha_state()

            self.hass.loop.call_soon_threadsafe(_run_on_loop)

        self._unsub_stage = async_dispatcher_connect(
            coordinator.hass, SIGNAL_STAGE_SWITCHES, _on_stage
        )

        # Store initial circuit name for change detection in auto-sync
        self._previous_circuit_name = name

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
            if current_circuit_name != self._previous_circuit_name:
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

    def _construct_switch_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        circuit_name: str,
        circuit_number: int | str,
        suffix: str,
        unique_id: str | None = None,
    ) -> str | None:
        """Construct entity ID for switch entities."""
        # Check registry first only if unique_id is provided
        if unique_id is not None:
            entity_registry = er.async_get(coordinator.hass)
            existing_entity_id = entity_registry.async_get_entity_id(
                "switch", "span_panel", unique_id
            )

            if existing_entity_id:
                return existing_entity_id

        # Construct default entity_id
        config_entry = coordinator.config_entry

        if not self._device_name:
            return None

        use_circuit_numbers = config_entry.options.get(USE_CIRCUIT_NUMBERS, True)
        use_device_prefix = config_entry.options.get(USE_DEVICE_PREFIX, True)

        # Build entity ID components
        parts = []

        if use_device_prefix:
            parts.append(self._device_name.lower().replace(" ", "_"))

        if use_circuit_numbers:
            parts.append(f"circuit_{circuit_number}")
        else:
            circuit_name_slug = circuit_name.lower().replace(" ", "_")
            parts.append(circuit_name_slug)

        # Only add suffix if it's different from the last word in the circuit name
        if suffix:
            circuit_name_words = circuit_name.lower().split()
            last_word = circuit_name_words[-1] if circuit_name_words else ""
            last_word_normalized = last_word.replace(" ", "_")

            if suffix != last_word_normalized:
                parts.append(suffix)

        entity_id = f"switch.{'_'.join(parts)}"
        return entity_id

    def __del__(self) -> None:
        """Ensure dispatcher subscription is released at GC time."""
        try:
            if hasattr(self, "_unsub_stage") and self._unsub_stage is not None:
                self._unsub_stage()
        except Exception as e:  # pragma: no cover – defensive
            _LOGGER.debug("Failed to cleanup dispatcher subscription: %s", e)


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
