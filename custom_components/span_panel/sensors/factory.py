"""Factory functions for creating Span Panel sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_DEVICE_NAME,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    USE_CIRCUIT_NUMBERS,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.helpers import resolve_evse_display_suffix
from custom_components.span_panel.sensor_definitions import (
    BATTERY_POWER_SENSOR,
    BATTERY_SENSOR,
    CIRCUIT_SENSORS,
    EVSE_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_ENERGY_SENSORS,
    PANEL_POWER_SENSORS,
    PV_POWER_SENSOR,
    SITE_POWER_SENSOR,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
)
from custom_components.span_panel.util import evse_device_info

from .circuit import SpanCircuitEnergySensor, SpanCircuitPowerSensor, SpanUnmappedCircuitSensor
from .evse import SpanEvseSensor
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

    # Add panel data status sensors (grid state, run config, relay, dominant power source, vendor cloud)
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


def _build_evse_device_info_map(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> dict[str, DeviceInfo]:
    """Build a mapping of EVSE feed circuit IDs to their EVSE DeviceInfo.

    Circuit sensors for EVSE feed circuits are assigned to the EVSE sub-device
    instead of the panel device, keeping all charger-related entities together.
    """
    if not snapshot.evse:
        return {}

    is_simulator = coordinator.config_entry.data.get(CONF_API_VERSION) == "simulation"
    panel_name = (
        coordinator.config_entry.data.get(CONF_DEVICE_NAME, coordinator.config_entry.title)
        or "Span Panel"
    )
    if is_simulator:
        panel_identifier = slugify(panel_name)
    else:
        panel_identifier = snapshot.serial_number

    use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    mapping: dict[str, DeviceInfo] = {}
    for _evse_id, evse in snapshot.evse.items():
        display_suffix = resolve_evse_display_suffix(evse, snapshot, use_circuit_numbers)
        info = evse_device_info(panel_identifier, evse, panel_name, display_suffix)
        mapping[evse.feed_circuit_id] = info

    return mapping


def create_circuit_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, config_entry: ConfigEntry
) -> list[SpanCircuitPowerSensor | SpanCircuitEnergySensor]:
    """Create circuit-level sensors for named circuits."""
    entities: list[SpanCircuitPowerSensor | SpanCircuitEnergySensor] = []

    # Build EVSE device info so feed circuit sensors land on the charger device
    evse_device_map = _build_evse_device_info_map(coordinator, snapshot)

    # Add circuit sensors for all named circuits
    named_circuits = [cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")]
    circuit_net_energy_enabled = config_entry.options.get(ENABLE_CIRCUIT_NET_ENERGY_SENSORS, True)

    for circuit_id in named_circuits:
        device_override = evse_device_map.get(circuit_id)

        for circuit_description in CIRCUIT_SENSORS:
            # Skip net energy sensors if disabled
            is_net_energy_sensor = (
                "net_energy" in circuit_description.key or "energy_net" in circuit_description.key
            )

            if not circuit_net_energy_enabled and is_net_energy_sensor:
                continue

            if circuit_description.key == "circuit_power":
                entities.append(
                    SpanCircuitPowerSensor(
                        coordinator,
                        circuit_description,
                        snapshot,
                        circuit_id,
                        device_info_override=device_override,
                    )
                )
            else:
                entities.append(
                    SpanCircuitEnergySensor(
                        coordinator,
                        circuit_description,
                        snapshot,
                        circuit_id,
                        device_info_override=device_override,
                    )
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


def has_bess(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether a BESS (battery energy storage system) is commissioned.

    Only soe_percentage is a reliable signal — the power-flows node publishes
    battery=0.0 even on panels without a commissioned BESS.
    """
    return snapshot.battery.soe_percentage is not None


def has_pv(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether PV (solar) is commissioned."""
    return snapshot.power_flow_pv is not None or any(
        c.device_type == "pv" for c in snapshot.circuits.values()
    )


def has_power_flows(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the power-flows node is publishing data."""
    return snapshot.power_flow_site is not None


def has_evse(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether an EVSE (EV charger) is commissioned."""
    return len(snapshot.evse) > 0


def detect_capabilities(snapshot: SpanPanelSnapshot) -> frozenset[str]:
    """Derive the set of optional capabilities present in the snapshot.

    Used by the coordinator to detect when new hardware (BESS, PV, EVSE) appears
    and trigger a reload so new sensors are created.
    """
    caps: set[str] = set()
    if has_bess(snapshot):
        caps.add("bess")
    if has_pv(snapshot):
        caps.add("pv")
    if has_power_flows(snapshot):
        caps.add("power_flows")
    if has_evse(snapshot):
        caps.add("evse")
    return frozenset(caps)


def create_battery_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanPanelBattery | SpanPanelPowerSensor]:
    """Create battery sensors when BESS is commissioned.

    Auto-detected from soe_percentage — only a commissioned BESS reports SoE.
    """
    if not has_bess(snapshot):
        return []

    return [
        SpanPanelPowerSensor(coordinator, BATTERY_POWER_SENSOR, snapshot),
        SpanPanelBattery(coordinator, BATTERY_SENSOR, snapshot),
    ]


def create_power_flow_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanPanelPowerSensor]:
    """Create power-flow sensors that are conditional on hardware presence.

    PV Power — only when PV is commissioned.
    Site Power — only when the power-flows node is publishing.
    """
    entities: list[SpanPanelPowerSensor] = []

    if has_pv(snapshot):
        entities.append(SpanPanelPowerSensor(coordinator, PV_POWER_SENSOR, snapshot))

    if has_power_flows(snapshot):
        entities.append(SpanPanelPowerSensor(coordinator, SITE_POWER_SENSOR, snapshot))

    return entities


def create_evse_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanEvseSensor]:
    """Create EVSE sensors for each commissioned charger."""
    if not has_evse(snapshot):
        return []
    entities: list[SpanEvseSensor] = []
    for evse_id in snapshot.evse:
        for desc in EVSE_SENSORS:
            entities.append(SpanEvseSensor(coordinator, desc, snapshot, evse_id))
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
    | SpanEvseSensor
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
        | SpanEvseSensor
    ] = []

    # Create different sensor types
    entities.extend(create_panel_sensors(coordinator, snapshot, config_entry))
    entities.extend(create_circuit_sensors(coordinator, snapshot, config_entry))
    entities.extend(create_unmapped_circuit_sensors(coordinator, snapshot))
    entities.extend(create_battery_sensors(coordinator, snapshot))
    entities.extend(create_power_flow_sensors(coordinator, snapshot))
    entities.extend(create_evse_sensors(coordinator, snapshot))

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
