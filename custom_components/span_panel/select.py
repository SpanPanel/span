"""Select entity for the Span Panel."""

from collections.abc import Callable
import logging
from typing import Any, Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api.exceptions import SpanPanelServerError

from .const import (
    COORDINATOR,
    DOMAIN,
    SIGNAL_STAGE_SELECTS,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    CircuitPriority,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    async_create_span_notification,
    build_select_unique_id_for_entry,
    get_user_friendly_suffix,
)
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .util import panel_to_device_info

ICON = "mdi:chevron-down"

_LOGGER = logging.getLogger(__name__)


class SpanPanelSelectEntityDescriptionWrapper:
    """Wrapper class for Span Panel Select entities."""

    # The wrapper is required because the SelectEntityDescription is frozen
    # and we need to pass in the entity_description to the constructor
    # Using keyword arguments gives a warning about unexpected arguments
    # pylint: disable=R0903

    def __init__(
        self,
        key: str,
        name: str,
        icon: str,
        options_fn: Callable[[SpanPanelCircuit], list[str]] = lambda _: [],
        current_option_fn: Callable[[SpanPanelCircuit], str | None] = lambda _: None,
        select_option_fn: Callable[[SpanPanelCircuit, str], None] | None = None,
    ) -> None:
        """Initialize the select entity description wrapper."""
        self.entity_description = SelectEntityDescription(key=key, name=name, icon=icon)
        self.options_fn = options_fn
        self.current_option_fn = current_option_fn
        self.select_option_fn = select_option_fn


CIRCUIT_PRIORITY_DESCRIPTION: Final = SpanPanelSelectEntityDescriptionWrapper(
    key="circuit_priority",
    name="Circuit Priority",
    icon=ICON,
    options_fn=lambda _: [e.value for e in CircuitPriority if e != CircuitPriority.UNKNOWN],
    current_option_fn=lambda circuit: CircuitPriority[circuit.priority].value,
)


class SpanPanelCircuitsSelect(CoordinatorEntity[SpanPanelCoordinator], SelectEntity):
    """Represent a select entity for Span Panel circuits."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: SpanPanelSelectEntityDescriptionWrapper,
        circuit_id: str,
        name: str,
        device_name: str,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        span_panel: SpanPanel = coordinator.data

        # Get the circuit from the span_panel to access its properties
        circuit = span_panel.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        # Get the circuit number (tab position)
        circuit_number = circuit.tabs[0] if circuit.tabs else circuit_id

        self.entity_description = description.entity_description
        self.description_wrapper = description  # Keep reference to wrapper for custom functions
        self.id = circuit_id
        self._device_name = device_name

        self._attr_unique_id = self._construct_select_unique_id(coordinator, span_panel, self.id)
        self._attr_device_info = panel_to_device_info(span_panel, device_name)

        entity_suffix = get_user_friendly_suffix(description.entity_description.key)
        self.entity_id = self._construct_select_entity_id(  # type: ignore[assignment]
            coordinator, name, circuit_number, entity_suffix
        )

        friendly_name = f"{name} {description.entity_description.name}"

        self._attr_name = friendly_name

        circuit = self._get_circuit()
        self._attr_options = description.options_fn(circuit)
        self._attr_current_option = description.current_option_fn(circuit)

        # Store initial circuit name for change detection in auto-sync of names
        self._previous_circuit_name = name

        # Subscribe to staged updates so selects run after switches. We schedule
        # the update on the event loop to satisfy HA's thread-safety checks.
        def _on_stage() -> None:
            if self.hass is None:
                return

            def _run_on_loop() -> None:
                circuit = self._get_circuit()
                self._attr_options = self.description_wrapper.options_fn(circuit)
                self._attr_current_option = self.description_wrapper.current_option_fn(circuit)
                self.async_write_ha_state()

            self.hass.loop.call_soon_threadsafe(_run_on_loop)

        self._unsub_stage = async_dispatcher_connect(
            coordinator.hass, SIGNAL_STAGE_SELECTS, _on_stage
        )

    def _get_circuit(self) -> SpanPanelCircuit:
        """Get the circuit for this entity."""
        circuit = self.coordinator.data.circuits[self.id]
        if not isinstance(circuit, SpanPanelCircuit):
            raise TypeError(f"Expected SpanPanelCircuit, got {type(circuit)}")
        return circuit

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Selecting option: %s", option)
        span_panel: SpanPanel = self.coordinator.data
        priority = CircuitPriority(option)
        curr_circuit = self._get_circuit()

        try:
            await span_panel.api.set_priority(curr_circuit, priority)
            await self.coordinator.async_request_refresh()
        except ServiceNotFound as snf:
            _LOGGER.warning("Service not found when setting priority: %s", snf)
            await async_create_span_notification(
                self.hass,
                message="The requested service is not available in the SPAN API.",
                title="Service Not Found",
                notification_id=f"span_panel_service_not_found_{self.id}",
            )
        except SpanPanelServerError:
            warning_msg = (
                f"SPAN API returned a server error attempting "
                f"to change the circuit priority for {self._attr_name}. "
                f"This typically indicates panel firmware doesn't support "
                f"this operation."
            )
            _LOGGER.warning("SPAN API may not support setting priority")
            await async_create_span_notification(
                self.hass,
                message=warning_msg,
                title="SPAN API Error",
                notification_id=f"span_panel_api_error_{self.id}",
            )

    def select_option(self, option: str) -> None:
        """Select an option synchronously."""
        _LOGGER.debug("Selecting option synchronously: %s", option)
        self.hass.async_add_executor_job(self.async_select_option, option)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        span_panel: SpanPanel = self.coordinator.data
        circuit = span_panel.circuits.get(self.id)
        if circuit:
            current_circuit_name = circuit.name

            # Only request reload if the circuit name has actually changed
            if current_circuit_name != self._previous_circuit_name:
                _LOGGER.info(
                    "Auto-sync detected circuit name change from '%s' to '%s' for select, requesting integration reload",
                    self._previous_circuit_name,
                    current_circuit_name,
                )

                # Update stored previous name for next comparison
                self._previous_circuit_name = current_circuit_name

                # Request integration reload for next update cycle
                self.coordinator.request_reload()

        # Update options and current option based on coordinator data
        circuit = self._get_circuit()
        self._attr_options = self.description_wrapper.options_fn(circuit)
        self._attr_current_option = self.description_wrapper.current_option_fn(circuit)
        super()._handle_coordinator_update()

    def _construct_select_unique_id(
        self,
        coordinator: SpanPanelCoordinator,
        span_panel: SpanPanel,
        select_id: str,
    ) -> str:
        """Construct unique ID for select entities."""
        return build_select_unique_id_for_entry(
            coordinator, span_panel, select_id, self._device_name
        )

    def _construct_select_entity_id(
        self,
        coordinator: SpanPanelCoordinator,
        circuit_name: str,
        circuit_number: int | str,
        suffix: str,
        unique_id: str | None = None,
    ) -> str | None:
        """Construct entity ID for select entities."""
        # Check registry first only if unique_id is provided
        if unique_id is not None:
            entity_registry = er.async_get(coordinator.hass)
            existing_entity_id = entity_registry.async_get_entity_id(
                "select", "span_panel", unique_id
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

        entity_id = f"select.{'_'.join(parts)}"
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
    """Set up select entities for Span Panel."""

    _LOGGER.debug("ASYNC SETUP ENTRY SELECT")
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]

    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    # Get device name from config entry data
    device_name = config_entry.data.get("device_name", config_entry.title)

    entities: list[SpanPanelCircuitsSelect] = []

    for circuit_id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(
                SpanPanelCircuitsSelect(
                    coordinator,
                    CIRCUIT_PRIORITY_DESCRIPTION,
                    circuit_id,
                    circuit_data.name,
                    device_name,
                )
            )

    async_add_entities(entities)
