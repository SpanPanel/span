"""Support for Span Panel monitor."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from span_panel_api import SpanPanelSnapshot

from .const import COORDINATOR, DOMAIN
from .coordinator import SpanPanelCoordinator
from .sensors import (
    SpanCircuitEnergySensor,
    SpanCircuitPowerSensor,
    SpanEnergySensorBase,
    SpanPanelBattery,
    SpanPanelEnergySensor,
    SpanPanelPanelStatus,
    SpanPanelPowerSensor,
    SpanPanelStatus,
    SpanSensorBase,
    SpanUnmappedCircuitSensor,
    create_native_sensors,
    enable_unmapped_tab_entities,
)

# Export the sensor classes for backward compatibility with tests
__all__ = [
    "SpanSensorBase",
    "SpanEnergySensorBase",
    "SpanPanelPanelStatus",
    "SpanPanelStatus",
    "SpanPanelBattery",
    "SpanPanelPowerSensor",
    "SpanPanelEnergySensor",
    "SpanCircuitPowerSensor",
    "SpanCircuitEnergySensor",
    "SpanUnmappedCircuitSensor",
]

_LOGGER: logging.Logger = logging.getLogger(__name__)

ICON = "mdi:flash"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    try:
        data = hass.data[DOMAIN][config_entry.entry_id]
        coordinator: SpanPanelCoordinator = data[COORDINATOR]
        snapshot: SpanPanelSnapshot = coordinator.data

        # Create all native sensors (panel, circuit, and battery sensors)
        entities = create_native_sensors(coordinator, snapshot, config_entry)

        # Add all native sensor entities
        async_add_entities(entities)

        # Enable unmapped tab entities if they were disabled
        enable_unmapped_tab_entities(hass, entities)

        # Force immediate coordinator refresh to ensure all sensors update right away
        await coordinator.async_request_refresh()

        _LOGGER.debug("Native sensor platform setup completed with %d entities", len(entities))
    except Exception as e:
        _LOGGER.error("Error in async_setup_entry: %s", e, exc_info=True)
        raise
