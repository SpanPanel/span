"""Select entity for the Span Panel."""

from collections.abc import Callable, Mapping
import logging
from typing import Any, ClassVar, Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from .adoption import AdoptedSelect, create_adopted_selects
from .const import DOMAIN, USE_CIRCUIT_NUMBERS, CircuitPriority
from .control_gate import ControlMode
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .helpers import (
    build_select_unique_id_for_entry,
    circuit_has_a_priority_select,
    construct_circuit_identifier_from_tabs,
    construct_circuit_label,
    construct_tabs_attribute,
    construct_voltage_attribute,
)
from .naming import (
    circuit_object_id_base,
    release_registry_name_written_by_older_release,
)
from .runtime import SpanPanelConfigEntry

# Device types that use "Solar" as the fallback identifier when unnamed.
_SOLAR_DEVICE_TYPES: frozenset[str] = frozenset({"pv"})


def _unnamed_select_fallback(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Return a descriptive identifier for an unnamed circuit select."""
    if getattr(circuit, "device_type", "circuit") in _SOLAR_DEVICE_TYPES:
        return "Solar"
    return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)


_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Sentinel value to distinguish "never synced" from "circuit name is None"
_NAME_UNSET: object = object()


class SpanPanelSelectEntityDescriptionWrapper:
    """Wrapper class for Span Panel Select entities."""

    # The wrapper is required because the SelectEntityDescription is frozen
    # and we need to pass in the entity_description to the constructor
    # Using keyword arguments gives a warning about unexpected arguments
    # pylint: disable=too-few-public-methods

    def __init__(
        self,
        key: str,
        name: str,
        options_fn: Callable[[SpanCircuitSnapshot], list[str]] = lambda _: [],
        current_option_fn: Callable[[SpanCircuitSnapshot], str | None] = lambda _: None,
        select_option_fn: Callable[[SpanCircuitSnapshot, str], None] | None = None,
    ) -> None:
        """Initialize the select entity description wrapper."""
        self.name: str = name
        """The description's label, kept as the `str` it is.

        `SelectEntityDescription.name` is typed `str | UndefinedType | None`,
        being optional for a description that declares a `translation_key`
        instead. This one always declares a label, and the registry-name release
        wants a `str`, so the wrapper holds on to what it was handed rather than
        narrowing the frozen description's field back down at every reader.
        """
        self.entity_description = SelectEntityDescription(
            key=key,
            name=name,
            translation_key=key,
            entity_category=EntityCategory.CONFIG,
        )
        self.options_fn = options_fn
        self.current_option_fn = current_option_fn
        self.select_option_fn = select_option_fn


CIRCUIT_PRIORITY_DESCRIPTION: Final = SpanPanelSelectEntityDescriptionWrapper(
    key="circuit_priority",
    name="Circuit Priority",
    options_fn=lambda _: [e.value for e in CircuitPriority if e != CircuitPriority.UNKNOWN],
    current_option_fn=lambda circuit: CircuitPriority[circuit.priority].value,
)


class SpanPanelCircuitsSelect(SpanPanelEntity, SelectEntity):
    """Represent a select entity for Span Panel circuits."""

    # Read in entity code rather than through a description: the wrapper
    # class is not a frozen dataclass description, so it cannot carry the
    # declaration. `priority` is the selected option (~96); `name` and
    # `tabs` build the display name and the id base (~153-171).
    _residual_field_paths: ClassVar[tuple[str, ...]] = (
        "circuit.priority",
        "circuit.name",
        "circuit.tabs",
    )

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: SpanPanelSelectEntityDescriptionWrapper,
        circuit_id: str,
        device_name: str,
    ) -> None:
        """Initialize the select.

        The circuit's name is not passed in: both the display name and the id
        base are read from the circuit in the current snapshot, so a circuit
        renamed on the panel reaches both on the next reload.
        """
        super().__init__(coordinator)
        snapshot: SpanPanelSnapshot = coordinator.data

        circuit = snapshot.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        self.entity_description = description.entity_description
        self.description_wrapper = description
        self.id = circuit_id
        self._device_name = device_name

        self._attr_unique_id = self._construct_select_unique_id(coordinator, snapshot, self.id)

        self._attr_device_info = self._build_device_info(coordinator, snapshot)

        # Check if entity already exists in registry
        entity_registry = er.async_get(coordinator.hass)
        existing_entity_id = entity_registry.async_get_entity_id(
            "select", DOMAIN, self._attr_unique_id
        )

        use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        desc_name = description.name
        circuit_name = circuit.name or _unnamed_select_fallback(circuit, circuit_id)

        # One name path, in both naming modes: the panel's name, carried as
        # `original_name`. That field ranks below the object-id base below, so
        # what is displayed can no longer decide what "Recreate entity IDs"
        # proposes.
        self._attr_name = f"{circuit_name} {desc_name}"

        # The id itself is Home Assistant's to compose. This entity supplies only
        # its base -- the naming-flag half plus the description's key -- and
        # leaves `entity_id` unset so Core assembles the rest from the user's
        # `entity_id_parts`. The key is used as the suffix verbatim:
        # `get_user_friendly_suffix` maps `circuit_priority` to `priority`, which
        # is the unique_id's spelling and not this entity's.
        suffix = description.entity_description.key
        identifier = (
            construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)
            if use_circuit_numbers
            else circuit_name
        )
        self._span_object_id_base = circuit_object_id_base(identifier, suffix, existing_entity_id)

        # Circuit-numbers mode used to deliver the panel's name by writing the
        # registry's `name`. That field is the user's override, and Home Assistant
        # reads it ahead of `suggested_object_id` when generating an entity id, so
        # occupying it made "Recreate entity IDs" propose a friendly-name id for a
        # circuit-numbered entity. The name travels as `original_name` now, so all
        # that is left is to let go of what the old scheme wrote -- and only that:
        # any other name is the user's.
        if existing_entity_id:
            release_registry_name_written_by_older_release(
                entity_registry, existing_entity_id, circuit.name, (desc_name,)
            )

        self._attr_options = description.options_fn(circuit)
        self._attr_current_option = description.current_option_fn(circuit)

        # Store initial circuit name for change detection in auto-sync
        if not existing_entity_id:
            self._previous_circuit_name: str | None | object = _NAME_UNSET
            _LOGGER.info("Select entity not in registry, will sync on first update")
        else:
            self._previous_circuit_name = circuit.name
            _LOGGER.info(
                "Select entity exists in registry, previous name set to '%s'",
                circuit.name,
            )

    def _get_circuit(self) -> SpanCircuitSnapshot | None:
        """Get the circuit for this entity, or None if temporarily missing."""
        snapshot: SpanPanelSnapshot = self.coordinator.data
        return snapshot.circuits.get(self.id)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        await super().async_will_remove_from_hass()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option.

        A refusal is raised at the caller rather than filed as a persistent
        notification. The library refuses a priority the panel declares
        unsettable before anything is published, so there is nothing to correct
        later and nobody to tell but the person who just made the choice -- and
        a notification keyed on the circuit would outlive the failure and sit in
        the sidebar until someone dismissed it by hand.

        A `FAILED` outcome is raised too, and separately worded: that command
        resolved an address and was never handed to the broker, which the
        library promises means it will not arrive later either. Both live in
        `_async_control`, which every control in this integration shares.
        """
        _LOGGER.debug("Selecting option: %s", option)
        client = self.coordinator.client
        priority = CircuitPriority(option)

        await self._async_control(
            client.set_circuit_priority(self.id, priority.name),
            command=f"a priority change for {self.entity_id}",
            failed_key="circuit_priority_failed",
            not_delivered_key="circuit_priority_not_delivered",
            placeholders={"circuit": construct_circuit_label(self._get_circuit(), self.id)},
        )

        await self.coordinator.async_request_refresh()

    def select_option(self, option: str) -> None:
        """Select an option synchronously."""
        _LOGGER.debug("Selecting option synchronously: %s", option)
        self.hass.create_task(self.async_select_option(option))

    @property
    def available(self) -> bool:
        """Return entity availability.

        Selects become unavailable when panel is offline since they can't change settings.
        """
        if not self._transport_available:
            return False
        if self.coordinator.panel_offline:
            return False
        return super().available

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return panel position attributes for this circuit."""
        if not self.coordinator.data:
            return None

        circuit = self.coordinator.data.circuits.get(self.id)
        if not circuit:
            return None

        attributes: dict[str, Any] = {}

        tabs_result = construct_tabs_attribute(circuit)
        if tabs_result is not None:
            attributes["tabs"] = tabs_result

        voltage = construct_voltage_attribute(circuit)
        if voltage is not None:
            attributes["voltage"] = voltage

        if circuit.priority_target is not None:
            attributes["priority_target"] = circuit.priority_target

        return attributes or None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        The circuit's name is watched here: the entity carries it as
        `original_name`, which can only be refreshed by being rebuilt, so a
        rename earns a reload. Whether the panel still lets this circuit's shed
        priority be set -- the answer that decided this entity exists -- is
        watched by `SpanPanelCoordinator._check_settability_change` instead,
        because the opposite edge happens on circuits that have no entity here
        to see it.
        """
        snapshot: SpanPanelSnapshot = self.coordinator.data
        circuit = snapshot.circuits.get(self.id)
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
                    _LOGGER.debug(
                        "User has customized name for %s, skipping sync",
                        self.entity_id,
                    )

            if user_has_override:
                self._previous_circuit_name = current_circuit_name
            elif self._previous_circuit_name is _NAME_UNSET:
                _LOGGER.info(
                    "First update: syncing entity name to panel name '%s' for select, requesting reload",
                    current_circuit_name,
                )
                self._previous_circuit_name = current_circuit_name
                self.coordinator.request_reload()
            elif current_circuit_name != self._previous_circuit_name:
                _LOGGER.info(
                    "Auto-sync detected circuit name change from '%s' to '%s' for select, requesting integration reload",
                    self._previous_circuit_name,
                    current_circuit_name,
                )
                self._previous_circuit_name = current_circuit_name
                self.coordinator.request_reload()

        # Update options and current option based on coordinator data
        circuit = self._get_circuit()
        if circuit is None:
            _LOGGER.debug(
                "Circuit %s temporarily missing from snapshot, skipping select update",
                self.id,
            )
            return
        self._attr_options = self.description_wrapper.options_fn(circuit)
        self._attr_current_option = self.description_wrapper.current_option_fn(circuit)
        super()._handle_coordinator_update()

    def _construct_select_unique_id(
        self,
        coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
        select_id: str,
    ) -> str:
        """Construct unique ID for select entities."""
        return build_select_unique_id_for_entry(coordinator, snapshot, select_id, self._device_name)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities for Span Panel."""

    _LOGGER.debug("ASYNC SETUP ENTRY SELECT")

    # Under `disabled` no control entity is created and no registry entry is
    # removed. See `switch.async_setup_entry` for why the registry entries stay.
    if config_entry.runtime_data.control_policy.mode is ControlMode.DISABLED:
        return

    coordinator = config_entry.runtime_data.coordinator
    snapshot: SpanPanelSnapshot = coordinator.data

    # Get device name from config entry data
    device_name = config_entry.data.get("device_name", config_entry.title)

    entities: list[SpanPanelCircuitsSelect | AdoptedSelect] = []

    for circuit_id, circuit_data in snapshot.circuits.items():
        if not circuit_has_a_priority_select(circuit_data):
            continue
        entities.append(
            SpanPanelCircuitsSelect(
                coordinator,
                CIRCUIT_PRIORITY_DESCRIPTION,
                circuit_id,
                device_name,
            )
        )

    # Settable properties on devices this integration models nothing for.
    # Disabled and diagnostic like every adopted entity: the panel authorises the
    # write, and the user decides whether the control is one they want.
    entities.extend(
        create_adopted_selects(
            coordinator,
            coordinator.data,
            dr.async_get(hass),
            panel_device_id=config_entry.runtime_data.panel_device_id,
            overlay=config_entry.runtime_data.curation,
        )
    )

    async_add_entities(entities)
