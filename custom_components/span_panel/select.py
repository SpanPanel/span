"""Select entity for the Span Panel."""

from collections.abc import Callable
import logging
from typing import Any, Final

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import httpx

from .const import COORDINATOR, DOMAIN, CircuitPriority
from .coordinator import SpanPanelCoordinator
from .helpers import construct_entity_id, get_user_friendly_suffix
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
    options_fn=lambda _: [
        e.value for e in CircuitPriority if e != CircuitPriority.UNKNOWN
    ],
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
        self.description_wrapper = (
            description  # Keep reference to wrapper for custom functions
        )
        self.id = circuit_id

        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_select_{self.id}"
        )
        self._attr_device_info = panel_to_device_info(span_panel)

        entity_suffix = get_user_friendly_suffix(description.entity_description.key)
        self.entity_id = construct_entity_id(  # type: ignore[assignment]
            coordinator, span_panel, "select", name, circuit_number, entity_suffix
        )

        friendly_name = f"{name} {description.entity_description.name}"

        self._attr_name = friendly_name

        circuit = self._get_circuit()
        self._attr_options = description.options_fn(circuit)
        self._attr_current_option = description.current_option_fn(circuit)

        _LOGGER.debug(
            "CREATE SELECT %s with options: %s", self._attr_name, self._attr_options
        )

        # Store initial circuit name for change detection in auto-sync of names
        self._previous_circuit_name = name

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
            async_create(
                self.hass,
                message="The requested service is not available in the SPAN API.",
                title="Service Not Found",
                notification_id=f"span_panel_service_not_found_{self.id}",
            )
        except httpx.HTTPStatusError:
            warning_msg = (
                f"SPAN API returned an HTTP Status Error attempting "
                f"to change the circuit priority for {self._attr_name}. "
                f"This typically indicates panel firmware doesn't support "
                f"this operation."
            )
            _LOGGER.warning("SPAN API may not support setting priority")
            async_create(
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

    entities: list[SpanPanelCircuitsSelect] = []

    for circuit_id, circuit_data in span_panel.circuits.items():
        if circuit_data.is_user_controllable:
            entities.append(
                SpanPanelCircuitsSelect(
                    coordinator,
                    CIRCUIT_PRIORITY_DESCRIPTION,
                    circuit_id,
                    circuit_data.name,
                )
            )

    async_add_entities(entities)
