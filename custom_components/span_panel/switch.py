"""Control switches."""

from collections.abc import Mapping
import logging
from typing import Any, ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from . import SpanPanelConfigEntry
from .adoption import AdoptedSwitch, create_adopted_switches
from .const import DOMAIN, USE_CIRCUIT_NUMBERS, CircuitRelayState
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .helpers import (
    build_switch_unique_id_for_entry,
    construct_circuit_identifier_from_tabs,
    construct_single_circuit_entity_id,
    construct_tabs_attribute,
    construct_voltage_attribute,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Sentinel value to distinguish "never synced" from "circuit name is None"
_NAME_UNSET: object = object()

# Device types that use "Solar" as the fallback identifier when unnamed.
_SOLAR_DEVICE_TYPES: frozenset[str] = frozenset({"pv"})


def _unnamed_switch_fallback(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Return a descriptive identifier for an unnamed circuit switch."""
    if getattr(circuit, "device_type", "circuit") in _SOLAR_DEVICE_TYPES:
        return "Solar"
    return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)


class SpanPanelCircuitsSwitch(SpanPanelEntity, SwitchEntity):
    """Represent a switch entity."""

    # Read in entity code rather than through a description: this platform
    # has no entity description at all. `relay_state` is the switch's own
    # state (line ~262); `name` and `tabs` build its display name (~77-101).
    _residual_field_paths: ClassVar[tuple[str, ...]] = (
        "circuit.relay_state",
        "circuit.name",
        "circuit.tabs",
    )

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        circuit_id: str,
        name: str,
        device_name: str,
    ) -> None:
        """Initialize the values."""
        snapshot: SpanPanelSnapshot = coordinator.data

        circuit = snapshot.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        self._circuit_id: str = circuit_id
        self._device_name = device_name
        self._attr_unique_id = self._construct_switch_unique_id(coordinator, snapshot, circuit_id)

        self._attr_device_info = self._build_device_info(coordinator, snapshot)

        # Check if entity already exists in registry
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(
            "switch", DOMAIN, self._attr_unique_id
        )

        use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

        if existing_entity_id:
            # Phase 2: the panel's name, in both modes. It reaches the UI as
            # `original_name`, which ranks below `suggested_object_id` and so
            # cannot decide what "Recreate entity IDs" proposes.
            if circuit.name:
                self._attr_name = f"{circuit.name} Breaker"
            else:
                fallback = _unnamed_switch_fallback(circuit, circuit_id)
                self._attr_name = f"{fallback} Breaker"

        # Circuit-numbers mode used to deliver the panel's name by writing the
        # registry's `name`. That field is the user's override, and Home Assistant
        # reads it ahead of `suggested_object_id` when generating an entity id, so
        # occupying it made "Recreate entity IDs" propose a friendly-name id for a
        # circuit-numbered entity. The name travels as `original_name` now, so all
        # that is left is to let go of what the old scheme wrote -- and only that:
        # any other name is the user's.
        if existing_entity_id and circuit.name:
            entity_entry = entity_registry.async_get(existing_entity_id)
            if entity_entry and entity_entry.name == f"{circuit.name} Breaker":
                entity_registry.async_update_entity(existing_entity_id, name=None)

        if not existing_entity_id:
            # Initial install - use flag-based name for entity_id generation
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

        # Explicitly set entity_id using construct_single_circuit_entity_id
        # which correctly handles 240V two-tab circuits. For an entity already
        # in the registry this is a suggestion HA records and does not act on --
        # the stored entity_id stands. See the helper's docstring.
        constructed_id = construct_single_circuit_entity_id(
            coordinator,
            snapshot,
            "switch",
            "breaker",
            circuit,
            existing_entity_id=existing_entity_id,
        )
        if constructed_id:
            self.entity_id = constructed_id

        self._update_is_on()

        # Store initial circuit name for change detection in auto-sync
        if not existing_entity_id:
            self._previous_circuit_name: str | None | object = _NAME_UNSET
            _LOGGER.info("Switch entity not in registry, will sync on first update")
        else:
            self._previous_circuit_name = circuit.name
            _LOGGER.info(
                "Switch entity exists in registry, previous name set to '%s'",
                circuit.name,
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

            # One path for both modes: the name is carried by `original_name`,
            # which is written when the entity is added, so a reload is what
            # refreshes it. A name in the registry is one the user set.
            user_has_override = False
            if self.entity_id:
                entity_registry = er.async_get(self.hass)
                entity_entry = entity_registry.async_get(self.entity_id)
                if entity_entry and entity_entry.name:
                    user_has_override = True

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

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return panel position attributes for this circuit."""
        if not self.coordinator.data:
            return None

        circuit = self.coordinator.data.circuits.get(self._circuit_id)
        if not circuit:
            return None

        attributes: dict[str, Any] = {}

        tabs_result = construct_tabs_attribute(circuit)
        if tabs_result is not None:
            attributes["tabs"] = tabs_result

        voltage = construct_voltage_attribute(circuit)
        if voltage is not None:
            attributes["voltage"] = voltage

        if circuit.relay_state_target is not None:
            attributes["relay_state_target"] = circuit.relay_state_target

        return attributes or None

    def _update_is_on(self) -> None:
        """Update the is_on state based on the circuit state.

        Uses the panel's ``relay_state_target`` (from Homie ``$target``) to
        show the desired state while a relay command is pending. When the
        target differs from the actual relay state, the panel has not yet
        confirmed the command and we display the target to prevent UI bounce.
        """
        snapshot: SpanPanelSnapshot = self.coordinator.data
        circuit = snapshot.circuits.get(self._circuit_id)
        if not circuit:
            self._attr_is_on = None
            return

        actual_is_on = circuit.relay_state == CircuitRelayState.CLOSED.name

        # When the panel publishes a $target that differs from the confirmed
        # relay state, a command is pending — show the target to avoid bounce.
        if (
            circuit.relay_state_target is not None
            and circuit.relay_state_target != circuit.relay_state
        ):
            self._attr_is_on = circuit.relay_state_target == CircuitRelayState.CLOSED.name
        else:
            self._attr_is_on = actual_is_on

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
            _LOGGER.warning("Client does not support relay control")
            return

        await client.set_circuit_relay(self._circuit_id, "CLOSED")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        client = self.coordinator.client
        if not hasattr(client, "set_circuit_relay"):
            _LOGGER.warning("Client does not support relay control")
            return

        await client.set_circuit_relay(self._circuit_id, "OPEN")
        self._attr_is_on = False
        self.async_write_ha_state()

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
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator = config_entry.runtime_data.coordinator
    snapshot: SpanPanelSnapshot = coordinator.data

    # Get device name from config entry data
    _device_name = config_entry.data.get("device_name", config_entry.title)

    entities: list[SpanPanelCircuitsSwitch | AdoptedSwitch] = []

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

    # Settable properties on devices this integration models nothing for.
    # Disabled and diagnostic like every adopted entity: the panel authorises the
    # write, and the user decides whether the control is one they want.
    entities.extend(
        create_adopted_switches(
            coordinator,
            coordinator.data,
            dr.async_get(hass),
            panel_device_id=config_entry.runtime_data.panel_device_id,
        )
    )

    async_add_entities(entities)
