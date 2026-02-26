"""Factory functions for creating Span Panel sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import (
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.options import BATTERY_ENABLE
from custom_components.span_panel.sensor_definitions import (
    BATTERY_SENSOR,
    CIRCUIT_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_ENERGY_SENSORS,
    PANEL_POWER_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
)

from .circuit import SpanCircuitEnergySensor, SpanCircuitPowerSensor, SpanUnmappedCircuitSensor
from .panel import (
    SpanPanelBattery,
    SpanPanelEnergySensor,
    SpanPanelPanelStatus,
    SpanPanelPowerSensor,
    SpanPanelStatus,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)


def create_panel_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, config_entry: ConfigEntry
) -> list[SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor]:
    """Create panel-level sensors."""
    entities: list[
        SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor
    ] = []

    # Add panel data status sensors (DSM State, DSM Grid State, etc.)
    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, snapshot))

    # Add panel power sensors
    for description in PANEL_POWER_SENSORS:
        entities.append(SpanPanelPowerSensor(coordinator, description, snapshot))

    # Add panel energy sensors
    # Filter out net energy sensors if disabled
    panel_net_energy_enabled = config_entry.options.get(ENABLE_PANEL_NET_ENERGY_SENSORS, True)

    for description in PANEL_ENERGY_SENSORS:
        # Skip net energy sensors if disabled
        is_net_energy_sensor = "net_energy" in description.key or "NetEnergy" in description.key

        if not panel_net_energy_enabled and is_net_energy_sensor:
            continue
        entities.append(SpanPanelEnergySensor(coordinator, description, snapshot))

    # Add hardware status sensors (Door State, WiFi, Cellular, etc.)
    for description_ss in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description_ss, snapshot))

    return entities


def create_circuit_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, config_entry: ConfigEntry
) -> list[SpanCircuitPowerSensor | SpanCircuitEnergySensor]:
    """Create circuit-level sensors for named circuits."""
    entities: list[SpanCircuitPowerSensor | SpanCircuitEnergySensor] = []

    # Add circuit sensors for all named circuits
    named_circuits = [cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")]
    circuit_net_energy_enabled = config_entry.options.get(ENABLE_CIRCUIT_NET_ENERGY_SENSORS, True)

    for circuit_id in named_circuits:
        for circuit_description in CIRCUIT_SENSORS:
            # Skip net energy sensors if disabled
            is_net_energy_sensor = (
                "net_energy" in circuit_description.key or "energy_net" in circuit_description.key
            )

            if not circuit_net_energy_enabled and is_net_energy_sensor:
                continue

            if circuit_description.key == "circuit_power":
                # Use enhanced power sensor for power measurements
                entities.append(
                    SpanCircuitPowerSensor(coordinator, circuit_description, snapshot, circuit_id)
                )
            else:
                # Use energy sensor with grace period tracking for energy measurements
                entities.append(
                    SpanCircuitEnergySensor(coordinator, circuit_description, snapshot, circuit_id)
                )

    return entities


def create_unmapped_circuit_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanUnmappedCircuitSensor]:
    """Create unmapped circuit sensors for synthetic calculations."""
    entities: list[SpanUnmappedCircuitSensor] = []

    # Add unmapped circuit sensors (native sensors for synthetic calculations)
    # These are invisible sensors that provide stable entity IDs for solar synthetics
    unmapped_circuits = [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
    for circuit_id in unmapped_circuits:
        for unmapped_description in UNMAPPED_SENSORS:
            entities.append(
                SpanUnmappedCircuitSensor(coordinator, unmapped_description, snapshot, circuit_id)
            )

    return entities


def create_battery_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, config_entry: ConfigEntry
) -> list[SpanPanelBattery]:
    """Create battery sensors if enabled and data available."""
    entities: list[SpanPanelBattery] = []

    battery_enabled = config_entry.options.get(BATTERY_ENABLE, False)
    if battery_enabled and snapshot.battery.soe_percentage is not None:
        entities.append(SpanPanelBattery(coordinator, BATTERY_SENSOR, snapshot))

    return entities


def create_native_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, config_entry: ConfigEntry
) -> list[
    SpanPanelPanelStatus
    | SpanPanelStatus
    | SpanPanelPowerSensor
    | SpanPanelEnergySensor
    | SpanCircuitPowerSensor
    | SpanCircuitEnergySensor
    | SpanUnmappedCircuitSensor
    | SpanPanelBattery
]:
    """Create all native sensors for the platform."""
    entities: list[
        SpanPanelPanelStatus
        | SpanPanelStatus
        | SpanPanelPowerSensor
        | SpanPanelEnergySensor
        | SpanCircuitPowerSensor
        | SpanCircuitEnergySensor
        | SpanUnmappedCircuitSensor
        | SpanPanelBattery
    ] = []

    # Create different sensor types
    entities.extend(create_panel_sensors(coordinator, snapshot, config_entry))
    entities.extend(create_circuit_sensors(coordinator, snapshot, config_entry))
    entities.extend(create_unmapped_circuit_sensors(coordinator, snapshot))
    entities.extend(create_battery_sensors(coordinator, snapshot, config_entry))

    return entities


def enable_unmapped_tab_entities(hass: HomeAssistant, entities: list[Any]) -> None:
    """Enable unmapped tab entities in the entity registry if they were disabled."""
    entity_registry = er.async_get(hass)
    for entity in entities:
        # Check if this is an unmapped tab circuit sensor
        if (
            hasattr(entity, "unique_id")
            and entity.unique_id
            and "unmapped_tab_" in entity.unique_id
        ):
            entity_id = entity.entity_id
            registry_entry = entity_registry.async_get(entity_id)
            if registry_entry and registry_entry.disabled:
                _LOGGER.debug("Enabling previously disabled unmapped tab entity: %s", entity_id)
                entity_registry.async_update_entity(entity_id, disabled_by=None)
