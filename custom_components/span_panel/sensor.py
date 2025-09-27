"""Support for Span Panel monitor."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    SpanSolarEnergySensor,
    SpanSolarSensor,
    SpanUnmappedCircuitSensor,
    create_native_sensors,
    enable_unmapped_tab_entities,
)
from .span_panel import SpanPanel

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
    "SpanSolarSensor",
    "SpanSolarEnergySensor",
]

import logging

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
        span_panel: SpanPanel = coordinator.data

        # Create all native sensors (now includes panel, circuit, and solar sensors)
        entities = create_native_sensors(coordinator, span_panel, config_entry)

        # Add all native sensor entities
        async_add_entities(entities)

        # Enable unmapped tab entities if they were disabled
        enable_unmapped_tab_entities(hass, entities)

        # Migration detection moved to coordinator update cycle

        # Force immediate coordinator refresh to ensure all sensors update right away
        await coordinator.async_request_refresh()

        _LOGGER.debug("Native sensor platform setup completed with %d entities", len(entities))
    except Exception as e:
        _LOGGER.error("Error in async_setup_entry: %s", e, exc_info=True)
        raise
