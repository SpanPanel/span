"""Factory functions for creating Span Panel sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from span_panel_api import PanelCapability

from custom_components.span_panel.const import (
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    ENABLE_SOLAR_NET_ENERGY_SENSORS,
)
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
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor]:
    """Create panel-level sensors."""
    entities: list[
        SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor
    ] = []

    capabilities = span_panel.api.capabilities

    # Add panel data status sensors (DSM State, DSM Grid State, etc.)
    # These are Gen2-only; Gen3 has no DSM state data.
    if PanelCapability.DSM_STATE in capabilities:
        for description in PANEL_DATA_STATUS_SENSORS:
            entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Add panel power sensors — available for all panel generations.
    for description in PANEL_POWER_SENSORS:
        entities.append(SpanPanelPowerSensor(coordinator, description, span_panel))

    # Add panel energy sensors — Gen2-only (energy history requires OpenAPI).
    if PanelCapability.ENERGY_HISTORY in capabilities:
        panel_net_energy_enabled = config_entry.options.get(ENABLE_PANEL_NET_ENERGY_SENSORS, True)
        for description in PANEL_ENERGY_SENSORS:
            is_net_energy_sensor = "net_energy" in description.key or "NetEnergy" in description.key
            if not panel_net_energy_enabled and is_net_energy_sensor:
                continue
            entities.append(SpanPanelEnergySensor(coordinator, description, span_panel))

    # Add hardware status sensors (Door State, WiFi, Cellular, etc.) — Gen2-only.
    if PanelCapability.HARDWARE_STATUS in capabilities:
        for description_ss in STATUS_SENSORS:
            entities.append(SpanPanelStatus(coordinator, description_ss, span_panel))

    return entities


def create_circuit_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[SpanCircuitPowerSensor | SpanCircuitEnergySensor]:
    """Create circuit-level sensors for named circuits."""
    entities: list[SpanCircuitPowerSensor | SpanCircuitEnergySensor] = []

    capabilities = span_panel.api.capabilities
    has_energy_history = PanelCapability.ENERGY_HISTORY in capabilities

    # Add circuit sensors for all named circuits (replacing synthetic ones)
    named_circuits = [cid for cid in span_panel.circuits if not cid.startswith("unmapped_tab_")]
    circuit_net_energy_enabled = config_entry.options.get(ENABLE_CIRCUIT_NET_ENERGY_SENSORS, True)

    for circuit_id in named_circuits:
        for circuit_description in CIRCUIT_SENSORS:
            is_energy_sensor = circuit_description.key != "circuit_power"

            # Energy sensors require energy history capability (Gen2-only).
            if is_energy_sensor and not has_energy_history:
                continue

            # Skip net energy sensors if disabled via user option.
            if is_energy_sensor:
                is_net_energy_sensor = (
                    "net_energy" in circuit_description.key
                    or "energy_net" in circuit_description.key
                )
                if not circuit_net_energy_enabled and is_net_energy_sensor:
                    continue

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
    """Create battery sensors if enabled and the panel supports battery data."""
    entities: list[SpanPanelBattery] = []

    # Battery data is only available on Gen2 panels.
    if PanelCapability.BATTERY not in span_panel.api.capabilities:
        return entities

    battery_enabled = config_entry.options.get(BATTERY_ENABLE, False)
    if battery_enabled:
        entities.append(SpanPanelBattery(coordinator, BATTERY_SENSOR, span_panel))

    return entities


def create_solar_sensors(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, config_entry: ConfigEntry
) -> list[SpanSolarSensor | SpanSolarEnergySensor]:
    """Create solar sensors if enabled and the panel supports solar data."""
    entities: list[SpanSolarSensor | SpanSolarEnergySensor] = []

    # Solar/feedthrough data is only available on Gen2 panels.
    if PanelCapability.SOLAR not in span_panel.api.capabilities:
        return entities

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
        solar_net_energy_enabled = config_entry.options.get(ENABLE_SOLAR_NET_ENERGY_SENSORS, True)

        for solar_description in SOLAR_SENSORS:
            # Skip net energy sensors if disabled
            if not solar_net_energy_enabled and "net_energy" in solar_description.key:
                continue

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
    entities.extend(create_panel_sensors(coordinator, span_panel, config_entry))
    entities.extend(create_circuit_sensors(coordinator, span_panel, config_entry))
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
