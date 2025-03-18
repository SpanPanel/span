"""Select entity for the Span Panel."""

# pyright: reportShadowedImports=false
import logging
from typing import Any, Callable, Final

import httpx

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import ServiceNotFound
from homeassistant.components.persistent_notification import async_create

from .const import COORDINATOR, DOMAIN, CircuitPriority
from .coordinator import SpanPanelCoordinator
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

        self.entity_description = description.entity_description
        self.id = circuit_id

        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_select_{self.id}"
        )
        self._attr_device_info = panel_to_device_info(span_panel)

        circuit = self._get_circuit()
        self._attr_options = description.options_fn(circuit)

        self._attr_current_option = description.current_option_fn(circuit)

        # Set the name using the description's name
        self._attr_name = f"{name} {description.entity_description.name}"

        _LOGGER.debug(
            "CREATE SELECT %s with options: %s", self._attr_name, self._attr_options
        )

    def _get_circuit(self) -> SpanPanelCircuit:
        """Get the circuit for this entity."""
        return self.coordinator.data.circuits[self.id]

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
            _LOGGER.error("Service not found when setting priority: %s", snf)
            self.hass.components.persistent_notification.create(
                message="The requested service is not available in the SPAN API.",
                title="Service Not Found",
                notification_id=f"span_panel_service_not_found_{self.id}",
            )
        except httpx.HTTPStatusError:
            error_msg = (
                f"SPAN API returned an HTTP Status Error attempting "
                f"to change the circuit priority for {self._attr_name}. "
                f"This typically indicates panel firmware doesn't support "
                f"this operation."
            )
            _LOGGER.error("SPAN API may not support setting priority")
            async_create(
                self.hass,
                message=error_msg,
                title="SPAN API Error",
                notification_id=f"span_panel_api_error_{self.id}",
            )

    def select_option(self, option: str) -> None:
        """Select an option synchronously."""
        _LOGGER.debug("Selecting option synchronously: %s", option)
        self.hass.async_add_executor_job(self.async_select_option, option)


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
