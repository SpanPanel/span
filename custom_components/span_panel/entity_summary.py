"""Entity summary logging for Span Panel integration."""

from __future__ import annotations

import logging

from .coordinator import SpanPanelCoordinator
from .sensor_definitions import (
    PANEL_DATA_STATUS_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
)


def log_entity_summary(coordinator: SpanPanelCoordinator) -> None:
    """Log a comprehensive summary of entities that will be created.

    Uses debug level for detailed info, info level for basic summary.
    """
    # Check if any logging is enabled for the main span_panel module
    main_logger = logging.getLogger("custom_components.span_panel")
    use_debug_level = main_logger.isEnabledFor(logging.DEBUG)
    use_info_level = main_logger.isEnabledFor(logging.INFO)

    if not (use_debug_level or use_info_level):
        return

    span_panel_data = coordinator.data
    total_circuits = len(span_panel_data.circuits)

    # Count controllable circuits and identify non-controllable ones
    controllable_circuits = sum(
        1 for circuit in span_panel_data.circuits.values() if circuit.is_user_controllable
    )
    non_controllable_circuits = total_circuits - controllable_circuits

    # Identify non-controllable circuits for debugging
    non_controllable_circuit_names = [
        f"{circuit.name} (ID: {circuit.circuit_id})"
        for circuit in span_panel_data.circuits.values()
        if not circuit.is_user_controllable
    ]

    # Detect hardware capabilities from snapshot data
    bess_present = span_panel_data.battery.soe_percentage is not None
    pv_present = span_panel_data.power_flow_pv is not None or any(
        c.device_type == "pv" for c in span_panel_data.circuits.values()
    )
    power_flows_present = span_panel_data.power_flow_site is not None

    # Native sensors only - synthetic sensors now handled by template system
    unmapped_sensors = total_circuits * len(UNMAPPED_SENSORS)  # Invisible backing sensors
    panel_status_sensors = len(PANEL_DATA_STATUS_SENSORS)  # Panel status only
    status_sensors = len(STATUS_SENSORS)  # Hardware status

    # Battery sensors (auto-detected)
    native_battery_sensors = 0
    if bess_present:
        native_battery_sensors += 1  # Battery power
        if span_panel_data.battery.soe_percentage is not None:
            native_battery_sensors += 1  # Battery level

    # Power-flow sensors (conditional on hardware)
    power_flow_sensors = 0
    if pv_present:
        power_flow_sensors += 1  # PV power
    if power_flows_present:
        power_flow_sensors += 1  # Site power

    # Synthetic sensors (now handled by template system - counts are estimates)
    synthetic_circuit_sensors = (
        total_circuits * 3 if total_circuits > 0 else 0
    )  # Power, Produced, Consumed per circuit
    synthetic_panel_sensors = 6  # Panel power sensors (current power, feedthrough, energy sensors)

    total_native_sensors = (
        unmapped_sensors
        + panel_status_sensors
        + status_sensors
        + native_battery_sensors
        + power_flow_sensors
    )
    total_synthetic_sensors = synthetic_circuit_sensors + synthetic_panel_sensors
    total_sensors = total_native_sensors + total_synthetic_sensors
    total_switches = controllable_circuits  # Only controllable circuits get switches
    total_selects = controllable_circuits  # Only controllable circuits get selects

    # Choose logging level based on what's enabled
    log_func = main_logger.debug if use_debug_level else main_logger.info

    log_func("=== SPAN PANEL ENTITY SUMMARY ===")
    log_func(
        "Total circuits: %d (%d controllable, %d non-controllable)",
        total_circuits,
        controllable_circuits,
        non_controllable_circuits,
    )

    # Show non-controllable circuit info at both info and debug levels
    if non_controllable_circuit_names:
        # Force info level to ensure this shows up
        main_logger.info(
            "Non-controllable circuits: %s",
            ", ".join(non_controllable_circuit_names),
        )
    else:
        main_logger.info("Non-controllable circuits: None")

    log_func("=== NATIVE SENSORS ===")
    log_func(
        "Unmapped circuit sensors: %d (%d circuits x %d sensors per circuit) - invisible backing data",
        unmapped_sensors,
        total_circuits,
        len(UNMAPPED_SENSORS),
    )
    log_func("Panel status sensors: %d", panel_status_sensors)
    log_func("Hardware status sensors: %d", status_sensors)
    if bess_present:
        log_func("Battery sensors: %d (BESS detected)", native_battery_sensors)
    else:
        log_func("Battery sensors: 0 (no BESS)")
    if pv_present:
        log_func("PV sensors: 1 (PV detected)")
    else:
        log_func("PV sensors: 0 (no PV)")
    if power_flows_present:
        log_func("Site power: 1 (power-flows active)")
    log_func("Total native sensors: %d", total_native_sensors)

    log_func("=== SYNTHETIC SENSORS (Template-based) ===")
    log_func("Circuit synthetic sensors: %d", synthetic_circuit_sensors)
    log_func("Panel synthetic sensors: %d", synthetic_panel_sensors)
    log_func("Total synthetic sensors: %d", total_synthetic_sensors)
    log_func("Circuit switches: %d (controllable circuits only)", total_switches)
    log_func("Circuit selects: %d (controllable circuits only)", total_selects)
    log_func(
        "Total entities: %d sensors + %d switches + %d selects = %d",
        total_sensors,
        total_switches,
        total_selects,
        total_sensors + total_switches + total_selects,
    )
    log_func("=== END ENTITY SUMMARY ===")
