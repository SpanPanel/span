"""Support for Span Panel monitor."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import SpanPanelSnapshot

from . import SpanPanelConfigEntry
from .const import (
    CONF_DEVICE_NAME,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    ENABLE_PANEL_NET_ENERGY_SENSORS,
    ENABLE_UNMAPPED_CIRCUIT_SENSORS,
    USE_CIRCUIT_NUMBERS,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    has_bess,
    has_bess_telemetry,
    has_evse,
    has_mid,
    has_pcs,
    has_power_flows,
    has_pv,
    has_shed_forecast,
    resolve_evse_display_suffix,
)
from .sensor_base import SpanEnergySensorBase, SpanSensorBase
from .sensor_circuit import (
    SpanCircuitEnergySensor,
    SpanCircuitPowerSensor,
    SpanUnmappedCircuitSensor,
)
from .sensor_definitions import (
    BATTERY_POWER_SENSOR,
    BATTERY_SENSOR,
    BESS_METADATA_SENSORS,
    BESS_TELEMETRY_SENSORS,
    CIRCUIT_BREAKER_RATING_SENSOR,
    CIRCUIT_CURRENT_SENSOR,
    CIRCUIT_SENSORS,
    DOWNSTREAM_L1_CURRENT_SENSOR,
    DOWNSTREAM_L2_CURRENT_SENSOR,
    EVSE_SENSORS,
    GRID_POWER_FLOW_SENSOR,
    L1_VOLTAGE_SENSOR,
    L2_VOLTAGE_SENSOR,
    MAIN_BREAKER_RATING_SENSOR,
    MID_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_ENERGY_SENSORS,
    PANEL_POWER_SENSORS,
    PCS_SENSORS,
    PV_METADATA_SENSORS,
    PV_POWER_SENSOR,
    SHED_FORECAST_SENSORS,
    SITE_POWER_SENSOR,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
    UPSTREAM_L1_CURRENT_SENSOR,
    UPSTREAM_L2_CURRENT_SENSOR,
)
from .sensor_evse import SpanEvseSensor
from .sensor_panel import (
    SpanBessMetadataSensor,
    SpanMidSensor,
    SpanPanelBattery,
    SpanPanelEnergySensor,
    SpanPanelPanelStatus,
    SpanPanelPowerSensor,
    SpanPanelStatus,
    SpanPcsSensor,
    SpanPVMetadataSensor,
    SpanShedForecastSensor,
)
from .util import bess_device_info, evse_device_info, mid_device_info, pv_device_info

# Export the sensor classes for backward compatibility with tests
__all__ = [
    "SpanBessMetadataSensor",
    "SpanMidSensor",
    "SpanCircuitEnergySensor",
    "SpanCircuitPowerSensor",
    "SpanEnergySensorBase",
    "SpanPVMetadataSensor",
    "SpanPanelBattery",
    "SpanPanelEnergySensor",
    "SpanPanelPanelStatus",
    "SpanPanelPowerSensor",
    "SpanPanelStatus",
    "SpanPcsSensor",
    "SpanSensorBase",
    "SpanShedForecastSensor",
    "SpanUnmappedCircuitSensor",
]

_LOGGER: logging.Logger = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    try:
        coordinator = config_entry.runtime_data.coordinator
        snapshot: SpanPanelSnapshot = coordinator.data

        # Create all native sensors (panel, circuit, and battery sensors)
        entities = create_native_sensors(coordinator, snapshot, config_entry)

        # Add all native sensor entities
        async_add_entities(entities)

        # Force immediate coordinator refresh to ensure all sensors update right away
        await coordinator.async_request_refresh()

        _LOGGER.debug("Native sensor platform setup completed with %d entities", len(entities))
    except Exception:
        _LOGGER.exception("Error in async_setup_entry")
        raise


def create_panel_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    config_entry: ConfigEntry,
) -> list[SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor]:
    """Create panel-level sensors."""
    entities: list[
        SpanPanelPanelStatus | SpanPanelStatus | SpanPanelPowerSensor | SpanPanelEnergySensor
    ] = [
        SpanPanelPanelStatus(coordinator, description, snapshot)
        for description in PANEL_DATA_STATUS_SENSORS
    ]

    # Add panel power sensors
    entities.extend(
        SpanPanelPowerSensor(coordinator, description, snapshot)
        for description in PANEL_POWER_SENSORS
    )

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
    entities.extend(
        SpanPanelStatus(coordinator, description, snapshot) for description in STATUS_SENSORS
    )

    # Add v2 diagnostic sensors (conditionally created when data is available)
    if snapshot.l1_voltage is not None:
        entities.append(SpanPanelPanelStatus(coordinator, L1_VOLTAGE_SENSOR, snapshot))
    if snapshot.l2_voltage is not None:
        entities.append(SpanPanelPanelStatus(coordinator, L2_VOLTAGE_SENSOR, snapshot))
    if snapshot.upstream_l1_current_a is not None:
        entities.append(SpanPanelPanelStatus(coordinator, UPSTREAM_L1_CURRENT_SENSOR, snapshot))
    if snapshot.upstream_l2_current_a is not None:
        entities.append(SpanPanelPanelStatus(coordinator, UPSTREAM_L2_CURRENT_SENSOR, snapshot))
    if snapshot.downstream_l1_current_a is not None:
        entities.append(SpanPanelPanelStatus(coordinator, DOWNSTREAM_L1_CURRENT_SENSOR, snapshot))
    if snapshot.downstream_l2_current_a is not None:
        entities.append(SpanPanelPanelStatus(coordinator, DOWNSTREAM_L2_CURRENT_SENSOR, snapshot))
    if snapshot.main_breaker_rating_a is not None:
        entities.append(SpanPanelPanelStatus(coordinator, MAIN_BREAKER_RATING_SENSOR, snapshot))

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

    panel_name = (
        coordinator.config_entry.data.get(CONF_DEVICE_NAME, coordinator.config_entry.title)
        or "Span Panel"
    )
    panel_identifier = snapshot.serial_number

    use_circuit_numbers = coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

    mapping: dict[str, DeviceInfo] = {}
    for evse in snapshot.evse.values():
        display_suffix = resolve_evse_display_suffix(evse, snapshot, use_circuit_numbers)
        info = evse_device_info(
            panel_identifier,
            evse,
            panel_name,
            display_suffix,
            panel_device_id=coordinator.config_entry.runtime_data.panel_device_id,
        )
        mapping[evse.feed_circuit_id] = info

    return mapping


def create_circuit_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    config_entry: ConfigEntry,
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
        circuit_data = snapshot.circuits.get(circuit_id)

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

        # Per-circuit current sensor (v2 only)
        if circuit_data and circuit_data.current_a is not None:
            entities.append(
                SpanCircuitPowerSensor(
                    coordinator,
                    CIRCUIT_CURRENT_SENSOR,
                    snapshot,
                    circuit_id,
                    device_info_override=device_override,
                )
            )

        # Per-circuit breaker rating sensor (v2 only)
        if circuit_data and circuit_data.breaker_rating_a is not None:
            entities.append(
                SpanCircuitPowerSensor(
                    coordinator,
                    CIRCUIT_BREAKER_RATING_SENSOR,
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
        entities.extend(
            SpanUnmappedCircuitSensor(coordinator, unmapped_description, snapshot, circuit_id)
            for unmapped_description in UNMAPPED_SENSORS
        )

    return entities


def _build_bess_device_info(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> DeviceInfo:
    """Build BESS sub-device info, resolving the panel identifier."""
    panel_name = (
        coordinator.config_entry.data.get(CONF_DEVICE_NAME, coordinator.config_entry.title)
        or "Span Panel"
    )

    return bess_device_info(
        snapshot.serial_number,
        snapshot.battery,
        panel_name,
        panel_device_id=coordinator.config_entry.runtime_data.panel_device_id,
    )


def _build_mid_device_info(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> DeviceInfo:
    """DeviceInfo for the Microgrid Interconnect sub-device."""
    panel_name = (
        coordinator.config_entry.data.get(CONF_DEVICE_NAME, coordinator.config_entry.title)
        or "Span Panel"
    )
    mid = snapshot.mid
    if mid is None:
        raise ValueError("cannot build MID device info for a snapshot with no MID")
    return mid_device_info(
        snapshot.serial_number,
        mid,
        panel_name,
        panel_device_id=coordinator.config_entry.runtime_data.panel_device_id,
    )


def create_mid_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanMidSensor]:
    """Create the Microgrid Interconnect sub-device and its sensors.

    v1.0 only, and purely additive: no flat panel publishes a MID, so `has_mid` is false
    everywhere today and nothing a user has changes. Presence is the library's optional
    `mid` field rather than a sentinel value, so there is nothing to infer.

    DUAL-SCHEMA: gated on what the snapshot carries, never on a version or a config flag. A
    panel that hot-loads parent/child mid-life gains the MID as a capability change, the
    coordinator reloads, and the device appears; one that never does simply never sees
    it. When the flat path retires, this gate can go and the sensors become
    unconditional.
    """
    if not has_mid(snapshot):
        return []

    mid_info = _build_mid_device_info(coordinator, snapshot)
    return [SpanMidSensor(coordinator, desc, snapshot, mid_info) for desc in MID_SENSORS]


def create_shed_forecast_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanShedForecastSensor]:
    """Create the backup-planning forecast sensors the panel can actually fill.

    Two gates, not one, because absence has two shapes here. `has_shed_forecast`
    answers whether the panel publishes the capability at all — false on every
    flat panel and on any v1.0 firmware that omits the node, and the reason a
    reload creates these when a panel gains it mid-life. The per-description
    check then answers whether *this* estimate is among what the node publishes:
    the catalog marks all four times SHOULD rather than MUST, so a partial
    node is legal and the half it omits must produce no entity rather than one
    permanently unknown.

    The presence test is the description's own `value_fn`. The field a sensor
    reads is exactly the field whose absence should suppress it, so asking the
    reader is what keeps the gate from drifting away from the read.

    DUAL-SCHEMA: gated on what the snapshot carries, never on a version or a
    config flag. When the flat path retires, the first gate goes and the second
    stays — a v1.0 panel may still publish a partial node.
    """
    if not has_shed_forecast(snapshot):
        return []

    return [
        SpanShedForecastSensor(coordinator, description, snapshot)
        for description in SHED_FORECAST_SENSORS
        if description.value_fn(snapshot) is not None
    ]


def create_pcs_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanPcsSensor]:
    """Create the Power Control System sensors, where the panel runs one.

    **One gate, not two, and that is the difference from every other capability
    here.** The shed forecast and the BESS telemetry gate a second time on each
    description's own `value_fn`, because a property that arrives unpublished
    would otherwise become a permanently-unknown entity. That reasoning does not
    transfer: `pcs` publishes properties that are legally `0.0`, `false` and
    `UNCONFIGURED`, and the reference capture is exactly that — a PCS which
    exists and is switched off. A per-reading gate would create the entities on a
    configured panel and delete them the moment somebody turned the PCS off,
    which is the state a user most wants to see reported.

    So presence is the node, which `has_pcs` reads off the library's `None`
    contract, and a property the node omits degrades to unknown on an entity that
    stays. Both results the capability marks `SHOULD`, so a panel publishing the
    node without them is unusual firmware rather than an expected shape.

    DUAL-SCHEMA: gated on what the snapshot carries, never on a version. No flat
    panel publishes the capability, and a v1.0 panel that gains it reaches
    `detect_capabilities` and picks the entities up on the reload.
    """
    if not has_pcs(snapshot):
        return []

    return [SpanPcsSensor(coordinator, description, snapshot) for description in PCS_SENSORS]


def create_battery_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanPanelBattery | SpanPanelPowerSensor | SpanBessMetadataSensor]:
    """Create battery sensors when BESS is commissioned.

    Auto-detected from soe_percentage — only a commissioned BESS reports SoE.
    All BESS sensors live on the BESS sub-device.
    """
    if not has_bess(snapshot):
        return []

    bess_info = _build_bess_device_info(coordinator, snapshot)

    entities: list[SpanPanelBattery | SpanPanelPowerSensor | SpanBessMetadataSensor] = [
        SpanPanelPowerSensor(
            coordinator, BATTERY_POWER_SENSOR, snapshot, device_info_override=bess_info
        ),
        SpanPanelBattery(coordinator, BATTERY_SENSOR, snapshot, device_info_override=bess_info),
    ]

    # Add BESS metadata sensors
    entities.extend(
        SpanBessMetadataSensor(coordinator, desc, snapshot, bess_info)
        for desc in BESS_METADATA_SENSORS
    )

    # What the BESS reports about itself, gated per description because it comes
    # from capability nodes a BESS may not have. `has_bess_telemetry` answers
    # whether it publishes either node at all -- false on every flat panel, and
    # the reason a reload creates these when a BESS gains them mid-life. The
    # per-description check then asks whether *this* reading is among what it
    # publishes: a BESS with a `meter` node and no `status` node is legal, and the
    # half it omits must produce no entity rather than one permanently unknown.
    #
    # The presence test is the description's own `value_fn`, so the gate cannot
    # drift away from the read it is gating.
    if has_bess_telemetry(snapshot):
        entities.extend(
            SpanBessMetadataSensor(coordinator, desc, snapshot, bess_info)
            for desc in BESS_TELEMETRY_SENSORS
            if desc.value_fn(snapshot.battery) is not None
        )

    return entities


def _build_pv_device_info(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> DeviceInfo:
    """DeviceInfo for the solar inverter sub-device."""
    panel_name = (
        coordinator.config_entry.data.get(CONF_DEVICE_NAME, coordinator.config_entry.title)
        or "Span Panel"
    )
    return pv_device_info(
        snapshot.serial_number,
        snapshot.pv,
        panel_name,
        panel_device_id=coordinator.config_entry.runtime_data.panel_device_id,
    )


def create_power_flow_sensors(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot
) -> list[SpanPanelPowerSensor | SpanPVMetadataSensor]:
    """Create power-flow sensors that are conditional on hardware presence.

    PV Power — only when PV is commissioned.
    Site Power — only when the power-flows node is publishing.
    PV metadata sensors — only when PV is commissioned.

    The PV sensors land on the inverter's own sub-device, matching what the BESS
    has done since v1.0: `battery_power` is the enclosure's reading of the
    battery and it sits on the battery's card, so `pv_power` -- the enclosure's
    reading of the inverter -- belongs on the inverter's.

    Nothing pins an entity_id. An installation that already has these five keeps
    the ids it has, because the registry never renames an entity it already
    knows; a new one gets whatever Home Assistant derives from the inverter's
    device name, which is the standard assignment and what every other
    sub-device entity gets. The two shapes differ by install date and that is
    deliberate -- see `test_pv_device.py`.
    """
    entities: list[SpanPanelPowerSensor | SpanPVMetadataSensor] = []

    if has_pv(snapshot):
        pv_info = _build_pv_device_info(coordinator, snapshot)
        entities.append(
            SpanPanelPowerSensor(
                coordinator,
                PV_POWER_SENSOR,
                snapshot,
                device_info_override=pv_info,
            )
        )

        entities.extend(
            SpanPVMetadataSensor(coordinator, desc, snapshot, pv_info)
            for desc in PV_METADATA_SENSORS
        )

    if has_power_flows(snapshot):
        entities.append(SpanPanelPowerSensor(coordinator, GRID_POWER_FLOW_SENSOR, snapshot))
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
        entities.extend(
            SpanEvseSensor(coordinator, desc, snapshot, evse_id) for desc in EVSE_SENSORS
        )
    return entities


def create_native_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    config_entry: ConfigEntry,
) -> list[
    SpanPanelPanelStatus
    | SpanPanelStatus
    | SpanPanelPowerSensor
    | SpanPanelEnergySensor
    | SpanCircuitPowerSensor
    | SpanCircuitEnergySensor
    | SpanUnmappedCircuitSensor
    | SpanPanelBattery
    | SpanBessMetadataSensor
    | SpanPVMetadataSensor
    | SpanEvseSensor
    | SpanMidSensor
    | SpanShedForecastSensor
    | SpanPcsSensor
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
        | SpanBessMetadataSensor
        | SpanPVMetadataSensor
        | SpanEvseSensor
        | SpanMidSensor
        | SpanShedForecastSensor
        | SpanPcsSensor
    ] = []

    # Create different sensor types
    entities.extend(create_panel_sensors(coordinator, snapshot, config_entry))
    entities.extend(create_circuit_sensors(coordinator, snapshot, config_entry))
    if config_entry.options.get(ENABLE_UNMAPPED_CIRCUIT_SENSORS, False):
        entities.extend(create_unmapped_circuit_sensors(coordinator, snapshot))
    entities.extend(create_battery_sensors(coordinator, snapshot))
    entities.extend(create_mid_sensors(coordinator, snapshot))
    entities.extend(create_shed_forecast_sensors(coordinator, snapshot))
    entities.extend(create_pcs_sensors(coordinator, snapshot))
    entities.extend(create_power_flow_sensors(coordinator, snapshot))
    entities.extend(create_evse_sensors(coordinator, snapshot))

    return entities
