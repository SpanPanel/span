"""Control switches."""

import logging
from typing import Any, Literal

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from .const import (
    CONF_API_VERSION,
    COORDINATOR,
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    CircuitRelayState,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_switch_unique_id_for_entry,
    construct_circuit_identifier_from_tabs,
)
from .util import snapshot_to_device_info

ICON: Literal["mdi:toggle-switch"] = "mdi:toggle-switch"

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Sentinel value to distinguish "never synced" from "circuit name is None"
_NAME_UNSET: object = object()

# Device types that use "Solar" as the fallback identifier when unnamed.
_SOLAR_DEVICE_TYPES: frozenset[str] = frozenset({"pv"})


def _unnamed_switch_fallback(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Return a descriptive identifier for an unnamed circuit switch."""
    if getattr(circuit, "device_type", "circuit") in _SOLAR_DEVICE_TYPES:
        return "Solar"
    return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)


class SpanPanelCircuitsSwitch(CoordinatorEntity[SpanPanelCoordinator], SwitchEntity):
    """Represent a switch entity."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SpanPanelCoordinator, circuit_id: str, name: str, device_name: str
    ) -> None:
        """Initialize the values."""
        snapshot: SpanPanelSnapshot = coordinator.data

        circuit = snapshot.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        self._circuit_id: str = circuit_id
        self._device_name = device_name
        self._attr_icon = "mdi:toggle-switch"
        self._attr_unique_id = self._construct_switch_unique_id(coordinator, snapshot, circuit_id)

        is_simulator = coordinator.config_entry.data.get(CONF_API_VERSION) == "simulation"
        host = coordinator.config_entry.data.get(CONF_HOST)
        self._attr_device_info = snapshot_to_device_info(snapshot, device_name, is_simulator, host)

        # Check if entity already exists in registry
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(
            "switch", DOMAIN, self._attr_unique_id
        )

        if existing_entity_id:
            # Entity exists - always use panel name for sync
            if circuit.name:
                self._attr_name = f"{circuit.name} Breaker"
            else:
                fallback = _unnamed_switch_fallback(circuit, circuit_id)
                self._attr_name = f"{fallback} Breaker"
        else:
            # Initial install - use flag-based name for entity_id generation
            use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

            if use_circuit_numbers:
                circuit_identifier = construct_circuit_identifier_from_tabs(
                    circuit.tabs, circuit_id
                )
                self._attr_name = f"{circuit_identifier} Breaker"
            elif name:
                self._attr_name = f"{name} Breaker"
            else:
                # v1 behavior: None lets HA handle default naming
                self._attr_name = None

        super().__init__(coordinator)

        self._update_is_on()

        # Store initial circuit name for change detection in auto-sync
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
        await super().async_will_remove_from_hass()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        snapshot: SpanPanelSnapshot = self.coordinator.data
        circuit = snapshot.circuits.get(self._circuit_id)
        if circuit:
            current_circuit_name = circuit.name

            # Check if user has customized the name in HA registry
            user_has_override = False
            if self.entity_id:
                entity_registry = er.async_get(self.hass)
                entity_entry = entity_registry.async_get(self.entity_id)
                if entity_entry and entity_entry.name:
                    user_has_override = True
                    _LOGGER.debug(
                        "User has customized name for %s, skipping sync",
                        self.entity_id,
                    )

            if user_has_override:
                self._previous_circuit_name = current_circuit_name
            elif self._previous_circuit_name is _NAME_UNSET:
                _LOGGER.info(
                    "First update: syncing entity name to panel name '%s' for switch, requesting reload",
                    current_circuit_name,
                )
                self._previous_circuit_name = current_circuit_name
                self.coordinator.request_reload()
            elif current_circuit_name != self._previous_circuit_name:
                _LOGGER.info(
                    "Auto-sync detected circuit name change from '%s' to '%s' for "
                    "switch, requesting integration reload",
                    self._previous_circuit_name,
                    current_circuit_name,
                )
                self._previous_circuit_name = current_circuit_name
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
        snapshot: SpanPanelSnapshot = self.coordinator.data
        circuit = snapshot.circuits.get(self._circuit_id)
        if circuit:
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
        client = self.coordinator.client
        if not hasattr(client, "set_circuit_relay"):
            _LOGGER.warning("Circuit relay control not available in simulation mode")
            return

        await client.set_circuit_relay(self._circuit_id, "CLOSED")
        # Optimistically update local state to prevent UI bouncing
        self._attr_is_on = True
        if self.hass is not None:
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        client = self.coordinator.client
        if not hasattr(client, "set_circuit_relay"):
            _LOGGER.warning("Circuit relay control not available in simulation mode")
            return

        await client.set_circuit_relay(self._circuit_id, "OPEN")
        # Optimistically update local state to prevent UI bouncing
        self._attr_is_on = False
        if self.hass is not None:
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _construct_switch_unique_id(
        self,
        coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
    ) -> str:
        """Construct unique ID for switch entities."""
        return build_switch_unique_id_for_entry(
            coordinator, snapshot, circuit_id, self._device_name
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    snapshot: SpanPanelSnapshot = coordinator.data

    # Get device name from config entry data
    _device_name = config_entry.data.get("device_name", config_entry.title)

    entities: list[SpanPanelCircuitsSwitch] = []

    for circuit_id, circuit_data in snapshot.circuits.items():
        if not circuit_data.is_user_controllable:
            continue
        # PV/EVSE circuits only get switches if they have a physical breaker
        # (relative_position == "DOWNSTREAM" means connected at a breaker slot)
        if (
            circuit_data.device_type in ("pv", "evse")
            and circuit_data.relative_position != "DOWNSTREAM"
        ):
            continue
        entities.append(
            SpanPanelCircuitsSwitch(coordinator, circuit_id, circuit_data.name, _device_name)
        )

    async_add_entities(entities)
