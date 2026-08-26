"""Control switches."""

from collections.abc import Mapping
import logging
from typing import Any, ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot
from span_panel_api.exceptions import SpanPanelServerError

from . import SpanPanelConfigEntry
from .adoption import AdoptedSwitch, create_adopted_switches
from .const import DOMAIN, USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX, CircuitRelayState
from .control_gate import (
    ControlLock,
    ControlMode,
    ControlPolicy,
    outcome_failure_reason,
    outcome_is_failure,
)
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .helpers import (
    build_switch_unique_id_for_entry,
    construct_circuit_identifier_from_tabs,
    construct_circuit_label,
    construct_tabs_attribute,
    construct_voltage_attribute,
)
from .naming import (
    circuit_object_id_base,
    legacy_preset_for_existing,
    release_registry_name_written_by_older_release,
)
from .util import snapshot_to_device_info

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
    # state (~258); `name` and `tabs` build its display name and its id base
    # (~106-122).
    _residual_field_paths: ClassVar[tuple[str, ...]] = (
        "circuit.relay_state",
        "circuit.name",
        "circuit.tabs",
    )

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        circuit_id: str,
        device_name: str,
    ) -> None:
        """Initialize the values.

        The circuit's name is not passed in: both the display name and the id
        base are read from the circuit in the current snapshot, so a circuit
        renamed on the panel reaches both on the next reload.
        """
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
        use_device_prefix: bool = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)
        circuit_name = circuit.name or _unnamed_switch_fallback(circuit, circuit_id)

        # One name path, in both naming modes: the panel's name, carried as
        # `original_name`. That field ranks below the object-id base below, so
        # what is displayed can no longer decide what "Recreate entity IDs"
        # proposes.
        self._attr_name = f"{circuit_name} Breaker"

        # The id itself is Home Assistant's to compose. This entity supplies only
        # its base -- the naming-flag half plus "breaker" -- and leaves
        # `entity_id` unset so Core assembles the rest from the user's
        # `entity_id_parts`. The suffix is named outright rather than mapped:
        # `get_user_friendly_suffix` has no entry for a switch at all.
        identifier = (
            construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)
            if use_circuit_numbers
            else circuit_name
        )
        preset = legacy_preset_for_existing(
            "switch",
            identifier=identifier,
            suffix="breaker",
            existing_entity_id=existing_entity_id,
            use_device_prefix=use_device_prefix,
            is_sub_device=False,
        )
        if preset is not None:
            self.entity_id = preset
        else:
            self._span_object_id_base = circuit_object_id_base(
                identifier, "breaker", existing_entity_id
            )

        # Circuit-numbers mode used to deliver the panel's name by writing the
        # registry's `name`. That field is the user's override, and Home Assistant
        # reads it ahead of `suggested_object_id` when generating an entity id, so
        # occupying it made "Recreate entity IDs" propose a friendly-name id for a
        # circuit-numbered entity. The name travels as `original_name` now, so all
        # that is left is to let go of what the old scheme wrote -- and only that:
        # any other name is the user's.
        if existing_entity_id:
            release_registry_name_written_by_older_release(
                entity_registry, existing_entity_id, circuit.name, ("Breaker",)
            )

        super().__init__(coordinator)

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
        await self._async_set_relay("CLOSED", is_on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_set_relay("OPEN", is_on=False)

    async def _async_set_relay(self, state: str, *, is_on: bool) -> None:
        """Operate the relay and show the result honestly.

        The optimistic write is deliberately *after* the publish, not before. A
        gate refusal or a debounce rejection raises out of the awaited call, and
        setting the state first would leave the switch showing a position the
        relay never took — with no coordinator update coming to correct it,
        because nothing on the panel changed.

        `FAILED` is the only outcome that means the command will never be
        delivered. `ACCEPTED` and `UNCONFIRMED` were both handed to the broker
        and may already have been acted on; `UNCONFIRMED` most often means the
        relay was already in the requested position. Neither is a failure and
        neither should discard the requested state.

        The two ways a command does not happen are reported the same way and
        worded differently, because they are different facts. A refusal never
        resolved an address -- the panel declares this relay non-commandable. A
        `FAILED` outcome resolved one and never handed it over, because the
        transport was closed or the broker was disconnected; the library
        refuses such a write rather than letting paho queue it, so it is a
        promise that nothing fires later against a panel nobody is watching.
        Both raise: a control that silently does nothing is the defect.
        """
        client = self.coordinator.client
        if not hasattr(client, "set_circuit_relay"):
            _LOGGER.warning("Client does not support relay control")
            return

        try:
            outcome = await self._async_guarded_control(
                client.set_circuit_relay(self._circuit_id, state)
            )
        except SpanPanelServerError as err:
            _LOGGER.warning(
                "SPAN panel did not accept a relay command for %s: %s", self.entity_id, err
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="circuit_relay_failed",
                translation_placeholders={
                    "circuit": construct_circuit_label(
                        self.coordinator.data.circuits.get(self._circuit_id),
                        self._circuit_id,
                    ),
                    "reason": str(err),
                },
            ) from err
        if outcome_is_failure(outcome):
            reason = outcome_failure_reason(outcome)
            _LOGGER.warning("Relay command for %s was not delivered: %s", self.entity_id, reason)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="circuit_relay_not_delivered",
                translation_placeholders={
                    "circuit": construct_circuit_label(
                        self.coordinator.data.circuits.get(self._circuit_id),
                        self._circuit_id,
                    ),
                    "reason": reason,
                },
            )

        self._attr_is_on = is_on
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


class SpanPanelControlLockSwitch(SpanPanelEntity, SwitchEntity):
    """Arms and disarms this panel's control lock.

    Not a control over the panel — it never publishes anything — so it is
    deliberately outside `_async_guarded_control` and is never seen by the
    interceptor. It is the thing the interceptor consults.

    The asymmetry is the whole design. Arming is permitted for anyone, including
    a contextless caller, because making a household safer should not require
    admin. Disarming requires an administrator, and is refused for a contextless
    caller regardless of `allow_contextless_control`: an automation that can
    unlock the panel is not a lock.
    """

    _attr_entity_category: EntityCategory | None = EntityCategory.CONFIG
    _attr_translation_key: str | None = "control_lock"

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        lock: ControlLock,
        policy: ControlPolicy,
        device_name: str,
    ) -> None:
        """Bind the entity to this entry's single lock object."""
        super().__init__(coordinator)
        snapshot: SpanPanelSnapshot = coordinator.data
        self._lock = lock
        self._policy = policy
        self._attr_device_info = snapshot_to_device_info(snapshot, device_name)
        self._attr_unique_id = f"span_{snapshot.serial_number}_control_lock"

    @property
    def is_on(self) -> bool:
        """Armed reads as on, matching an alarm panel rather than a door."""
        return self._lock.armed

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm the lock. Anyone may do this, including an automation."""
        self._lock.arm()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm the lock, for an administrator only."""
        context = self._context
        user_id = context.user_id if context is not None else None
        if user_id is None:
            raise ServiceValidationError(
                "The SPAN panel control lock can only be disarmed by a logged-in "
                "administrator, not by an automation or script.",
                translation_domain=DOMAIN,
                translation_key="control_lock_disarm_requires_user",
            )
        user = await self.hass.auth.async_get_user(user_id)
        if user is None or not user.is_admin:
            raise ServiceValidationError(
                "Only a Home Assistant administrator can disarm the SPAN panel control lock.",
                translation_domain=DOMAIN,
                translation_key="control_lock_disarm_requires_admin",
            )

        timeout = self._policy.lock_timeout_minutes
        self._lock.disarm(timeout if timeout is not None else 0)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator = config_entry.runtime_data.coordinator
    policy = config_entry.runtime_data.control_policy
    snapshot: SpanPanelSnapshot = coordinator.data

    # Get device name from config entry data
    _device_name = config_entry.data.get("device_name", config_entry.title)

    entities: list[SpanPanelCircuitsSwitch | AdoptedSwitch | SpanPanelControlLockSwitch] = []

    if policy.lock_enabled:
        entities.append(
            SpanPanelControlLockSwitch(
                coordinator,
                config_entry.runtime_data.control_lock,
                policy,
                _device_name,
            )
        )

    # Under `disabled` the control entities are not created, and their registry
    # entries are deliberately left in place. Removing them would risk a
    # regenerated entity_id on the way back — a `_2` suffix if anything else
    # claimed the slug meanwhile — and would discard the user's names, areas and
    # customizations. They read as unavailable instead, which is recoverable.
    if policy.mode is ControlMode.DISABLED:
        async_add_entities(entities)
        return

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
        entities.append(SpanPanelCircuitsSwitch(coordinator, circuit_id, _device_name))

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
