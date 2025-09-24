"""Factory functions for creating Span Panel sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.options import (
    BATTERY_ENABLE,
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)
from custom_components.span_panel.sensor_definitions import (
    BATTERY_SENSOR,
    CIRCUIT_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_ENERGY_SENSORS,
    PANEL_POWER_SENSORS,
    SOLAR_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
)
from custom_components.span_panel.span_panel import SpanPanel

from .circuit import SpanCircuitEnergySensor, SpanCircuitPowerSensor, SpanUnmappedCircuitSensor
from .panel import (
    SpanPanelBattery,
    SpanPanelEnergySensor,
    SpanPanelPanelStatus,
    SpanPanelPowerSensor,
    SpanPanelStatus,
)
from .solar import SpanSolarEnergySensor, SpanSolarSensor

_LOGGER: logging.Logger = logging.getLogger(__name__)


def create_panel_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel
) -> list[SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor]:
    """Create panel-level sensors."""
    entities: list[
        SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor
    ] = []

    # Add panel data status sensors (DSM State, DSM Grid State, etc.)
    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Add panel power sensors (replacing synthetic ones)
    for description in PANEL_POWER_SENSORS:
        entities.append(SpanPanelPowerSensor(coordinator, description, span_panel))

    # Add panel energy sensors (replacing synthetic ones)
    for description in PANEL_ENERGY_SENSORS:
        entities.append(SpanPanelEnergySensor(coordinator, description, span_panel))

    # Add hardware status sensors (Door State, WiFi, Cellular, etc.)
    for description_ss in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description_ss, span_panel))

    return entities


def create_circuit_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel
) -> list[SpanCircuitPowerSensor | SpanCircuitEnergySensor]:
    """Create circuit-level sensors for named circuits."""
    entities: list[SpanCircuitPowerSensor | SpanCircuitEnergySensor] = []

    # Add circuit sensors for all named circuits (replacing synthetic ones)
    named_circuits = [cid for cid in span_panel.circuits if not cid.startswith("unmapped_tab_")]
    for circuit_id in named_circuits:
        for circuit_description in CIRCUIT_SENSORS:
            if circuit_description.key == "circuit_power":
                # Use enhanced power sensor for power measurements
                entities.append(
                    SpanCircuitPowerSensor(coordinator, circuit_description, span_panel, circuit_id)
                )
            else:
                # Use energy sensor with grace period tracking for energy measurements
                entities.append(
                    SpanCircuitEnergySensor(
                        coordinator, circuit_description, span_panel, circuit_id
                    )
                )

    return entities


def create_unmapped_circuit_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel
) -> list[SpanUnmappedCircuitSensor]:
    """Create unmapped circuit sensors for synthetic calculations."""
    entities: list[SpanUnmappedCircuitSensor] = []

    # Add unmapped circuit sensors (native sensors for synthetic calculations)
    # These are invisible sensors that provide stable entity IDs for solar synthetics
    unmapped_circuits = [cid for cid in span_panel.circuits if cid.startswith("unmapped_tab_")]
    for circuit_id in unmapped_circuits:
        for unmapped_description in UNMAPPED_SENSORS:
            # UNMAPPED_SENSORS contains SpanPanelCircuitsSensorEntityDescription
            entities.append(
                SpanUnmappedCircuitSensor(coordinator, unmapped_description, span_panel, circuit_id)
            )

    return entities


def create_battery_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[SpanPanelBattery]:
    """Create battery sensors if enabled."""
    entities: list[SpanPanelBattery] = []

    # Add battery sensor if enabled
    battery_enabled = config_entry.options.get(BATTERY_ENABLE, False)
    if battery_enabled:
        entities.append(SpanPanelBattery(coordinator, BATTERY_SENSOR, span_panel))

    return entities


def create_solar_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[SpanSolarSensor | SpanSolarEnergySensor]:
    """Create solar sensors if enabled and configured."""
    entities: list[SpanSolarSensor | SpanSolarEnergySensor] = []

    # Add solar sensors if enabled
    solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
    if not solar_enabled:
        return entities

    # Get leg circuit IDs from options
    leg1_raw = config_entry.options.get(INVERTER_LEG1, 0)
    leg2_raw = config_entry.options.get(INVERTER_LEG2, 0)

    try:
        leg1_tab = int(leg1_raw)
        leg2_tab = int(leg2_raw)
    except (TypeError, ValueError):
        leg1_tab = 0
        leg2_tab = 0

    if leg1_tab <= 0 or leg2_tab <= 0:
        return entities

    # Find the circuit IDs for the specified tabs
    leg1_circuit_id = None
    leg2_circuit_id = None

    for circuit_id, circuit in span_panel.circuits.items():
        if hasattr(circuit, "tabs") and circuit.tabs:
            if leg1_tab in circuit.tabs:
                leg1_circuit_id = circuit_id
            if leg2_tab in circuit.tabs:
                leg2_circuit_id = circuit_id

    # Create solar sensors if both legs found
    if leg1_circuit_id and leg2_circuit_id:
        for solar_description in SOLAR_SENSORS:
            if solar_description.key == "solar_current_power":
                # Use regular solar sensor for power measurements
                entities.append(
                    SpanSolarSensor(
                        coordinator,
                        solar_description,
                        span_panel,
                        leg1_circuit_id,
                        leg2_circuit_id,
                    )
                )
            else:
                # Use energy sensor with grace period tracking for energy measurements
                entities.append(
                    SpanSolarEnergySensor(
                        coordinator,
                        solar_description,
                        span_panel,
                        leg1_circuit_id,
                        leg2_circuit_id,
                    )
                )

    return entities


def create_native_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[
    SpanPanelPanelStatus
    | SpanPanelStatus
    | SpanPanelPowerSensor
    | SpanPanelEnergySensor
    | SpanCircuitPowerSensor
    | SpanCircuitEnergySensor
    | SpanUnmappedCircuitSensor
    | SpanPanelBattery
    | SpanSolarSensor
    | SpanSolarEnergySensor
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
        | SpanSolarSensor
        | SpanSolarEnergySensor
    ] = []

    # Create different sensor types
    entities.extend(create_panel_sensors(coordinator, span_panel))
    entities.extend(create_circuit_sensors(coordinator, span_panel))
    entities.extend(create_unmapped_circuit_sensors(coordinator, span_panel))
    entities.extend(create_battery_sensors(coordinator, span_panel, config_entry))
    entities.extend(create_solar_sensors(coordinator, span_panel, config_entry))

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
