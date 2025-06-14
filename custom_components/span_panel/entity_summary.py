"""Entity summary logging for Span Panel integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry

from .coordinator import SpanPanelCoordinator
from .options import BATTERY_ENABLE, INVERTER_ENABLE
from .sensor import (
    CIRCUITS_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_SENSORS,
    STATUS_SENSORS,
    STORAGE_BATTERY_SENSORS,
    SYNTHETIC_SENSOR_TEMPLATES,
)

_LOGGER = logging.getLogger(__name__)


def log_entity_summary(coordinator: SpanPanelCoordinator, config_entry: ConfigEntry) -> None:
    """Log a comprehensive summary of entities that will be created.

    Uses debug level for detailed info, info level for basic summary.

    Args:
        coordinator: The SpanPanelCoordinator instance
        config_entry: The config entry with options

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

    solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
    battery_enabled = config_entry.options.get(BATTERY_ENABLE, False)

    # Circuit sensors are created for all circuits in the circuits collection
    # Solar legs are NOT in this collection - they're accessed via raw branch data
    circuit_sensors = total_circuits * len(CIRCUITS_SENSORS)
    synthetic_sensors = len(SYNTHETIC_SENSOR_TEMPLATES) if solar_enabled else 0
    panel_sensor_count = len(PANEL_SENSORS) + len(PANEL_DATA_STATUS_SENSORS)
    status_sensors = len(STATUS_SENSORS)
    battery_sensors = len(STORAGE_BATTERY_SENSORS) if battery_enabled else 0

    total_sensors = (
        circuit_sensors + synthetic_sensors + panel_sensor_count + status_sensors + battery_sensors
    )
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

    log_func(
        "Circuit sensors: %d (%d circuits x %d sensors per circuit)",
        circuit_sensors,
        total_circuits,
        len(CIRCUITS_SENSORS),
    )
    if solar_enabled:
        log_func("Synthetic sensors: %d (solar inverter)", synthetic_sensors)
    else:
        log_func("Synthetic sensors: 0 (solar disabled)")
    log_func("Panel sensors: %d", panel_sensor_count)
    log_func("Status sensors: %d", status_sensors)
    if battery_enabled:
        log_func("Battery sensors: %d", battery_sensors)
    else:
        log_func("Battery sensors: 0 (battery disabled)")
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
