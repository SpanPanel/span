"""Named circuit synthetic sensor generation for SPAN Panel integration.

This module generates synthetic sensors for normal named circuits (non-unmapped circuits)
using YAML templates and virtual backing entities. These circuits benefit from the name
tracking and synchronization logic already in the integration.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.util import slugify

from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_120v_synthetic_entity_id,
    construct_240v_synthetic_entity_id,
    construct_backing_entity_id_for_entry,
    construct_synthetic_unique_id,
    construct_tabs_attribute,
    construct_voltage_attribute,
    get_circuit_number,
    get_user_friendly_suffix,
)
from .migration_utils import classify_sensor_from_unique_id
from .span_panel import SpanPanel
from .synthetic_utils import BackingEntity, combine_yaml_templates

_LOGGER = logging.getLogger(__name__)

# Named circuit sensor definitions - these match circuit sensor types
NAMED_CIRCUIT_SENSOR_DEFINITIONS = [
    {
        "key": "instantPowerW",
        "name": "Power",  # Will become "Fountain Power"
        "template": "circuit_power",
        "data_path": "instant_power",  # Match actual circuit attribute name
    },
    {
        "key": "producedEnergyWh",
        "name": "Produced Energy",  # Will become "Fountain Produced Energy"
        "template": "circuit_energy_produced",
        "data_path": "produced_energy",  # Match actual circuit attribute name
    },
    {
        "key": "consumedEnergyWh",
        "name": "Consumed Energy",  # Will become "Fountain Consumed Energy"
        "template": "circuit_energy_consumed",
        "data_path": "consumed_energy",  # Match actual circuit attribute name
    },
]


def get_circuit_data_value(circuit_data: Any, data_path: str) -> float:
    """Get circuit data value using attribute name.

    Args:
        circuit_data: The circuit data object
        data_path: Attribute name to get (e.g., "instant_power")

    Returns:
        The data value as float

    """
    try:
        value = getattr(circuit_data, data_path, None)
        return float(value) if value is not None else 0.0
    except (AttributeError, TypeError, ValueError) as e:
        _LOGGER.warning("Failed to get circuit data for path '%s': %s", data_path, e)
        return 0.0


def _match_existing_mapping(
    mappings: dict[str, str] | None, sensor_key: str, circuit: Any
) -> tuple[str | None, str | None]:
    """Try to match an existing mapping for this circuit and sensor key.

    Returns (sensor_unique_id, entity_id) if a match is found, otherwise (None, None).
    """
    if not mappings:
        return None, None
    for existing_unique_id, existing_entity_id in mappings.items():
        try:
            category, sensor_type, api_key = classify_sensor_from_unique_id(existing_unique_id)
            if category != "generic" or api_key != sensor_key:
                continue
            # For circuit sensors, verify circuit number matches
            if "_circuit_" in existing_entity_id:
                try:
                    parts = existing_entity_id.split("_circuit_")
                    if len(parts) > 1:
                        number_part = parts[1].split("_")[0]
                        circuit_number_from_entity = int(number_part)
                        current_circuit_number = get_circuit_number(circuit)
                        if circuit_number_from_entity != current_circuit_number:
                            continue
                except (ValueError, IndexError):
                    continue
            elif "_circuit_" not in existing_entity_id:
                # Panel-level sensor (no circuit number) - match by API key only
                pass
            else:
                continue
            return existing_unique_id, existing_entity_id
        except ValueError:
            continue
    return None, None


async def generate_named_circuit_sensors(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    device_name: str,
    existing_sensor_mappings: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[BackingEntity], dict[str, Any], dict[str, str]]:
    """Generate named circuit synthetic sensors and their backing entities.

    This function creates synthetic sensors for all normal named circuits
    (circuits that are not unmapped tab positions).

    Args:
        coordinator: The SpanPanelCoordinator instance
        span_panel: The SpanPanel data
        device_name: The device name to use for entity IDs and friendly names
        existing_sensor_mappings: Optional dict mapping unique_id to entity_id for migration.
                                 If None, generates new keys using helpers.

    Returns:
        Tuple of (sensor_configs_dict, list_of_backing_entities, global_settings, sensor_to_backing_mapping)

    """

    sensor_configs: dict[str, Any] = {}
    backing_entities: list[BackingEntity] = []
    global_settings: dict[str, Any] = {}
    sensor_to_backing_mapping: dict[str, str] = {}

    # Get display precision from options
    if coordinator is not None:
        power_precision = coordinator.config_entry.options.get("power_display_precision", 0)
        energy_precision = coordinator.config_entry.options.get("energy_display_precision", 2)
        is_simulator: bool = bool(coordinator.config_entry.data.get("simulation_mode", False))
    else:
        # During migration, use defaults
        power_precision = 0
        energy_precision = 2
        is_simulator = False
    if span_panel is not None:
        device_identifier_for_uniques: str = (
            slugify(device_name)
            if is_simulator and isinstance(device_name, str) and device_name
            else span_panel.status.serial_number
        )
    else:
        # During migration, device identifier is already provided via device_name
        # which is actually the device identifier passed from migration
        device_identifier_for_uniques = device_name

    # Create common placeholders for header template
    if coordinator is not None:
        energy_grace_period = coordinator.config_entry.options.get(
            "energy_reporting_grace_period", 15
        )
    else:
        # During migration, use default
        energy_grace_period = 15

    common_placeholders = {
        "device_identifier": device_identifier_for_uniques,
        "panel_id": device_identifier_for_uniques,
        "energy_grace_period_minutes": str(energy_grace_period),
        "power_display_precision": str(power_precision),
        "energy_display_precision": str(energy_precision),
    }

    # Filter to only normal named circuits (not unmapped)
    if span_panel is not None:
        named_circuits = {
            circuit_id: circuit_data
            for circuit_id, circuit_data in span_panel.circuits.items()
            if not circuit_id.startswith("unmapped_tab_")
        }
    else:
        # During migration, we don't generate new circuits
        # We only preserve existing sensor mappings
        named_circuits = {}

    # During migration with existing_sensor_mappings, we process those mappings
    # For fresh installs, we need circuits data
    if not named_circuits and not existing_sensor_mappings:
        _LOGGER.warning("GENERATE_NAMED_CIRCUITS: No named circuits found to process!")
        return sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping

    # During migration, if we have existing mappings but no circuits,
    # just preserve the mappings without generating new configs
    if existing_sensor_mappings and not named_circuits:
        _LOGGER.info(
            "MIGRATION: Processing %d existing sensor mappings without circuit data",
            len(existing_sensor_mappings),
        )
        # Return the existing mappings as sensor configs
        for unique_id, existing_entity_id in existing_sensor_mappings.items():
            sensor_configs[unique_id] = {"entity_id": existing_entity_id}
        return sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping

    for circuit_id, circuit_data in named_circuits.items():
        for sensor_def in NAMED_CIRCUIT_SENSOR_DEFINITIONS:
            # Get circuit number for helpers
            circuit_number = get_circuit_number(circuit_data)
            circuit_name = circuit_data.name or f"Circuit {circuit_number}"

            # Check if this sensor definition matches any existing sensor for this circuit
            sensor_unique_id: str | None = None
            entity_id: str | None = None

            match_uid, match_entity = _match_existing_mapping(
                existing_sensor_mappings, sensor_def["key"], circuit_data
            )
            if match_uid and match_entity:
                _LOGGER.info(
                    "MIGRATION: Using existing entity %s (unique_id: %s) for sensor key %s",
                    match_entity,
                    match_uid,
                    sensor_def["key"],
                )
                sensor_unique_id = match_uid
                entity_id = match_entity

            # If no existing sensor found, generate new keys (new installation)
            if sensor_unique_id is None:
                if existing_sensor_mappings:
                    _LOGGER.info(
                        "MIGRATION: No existing entity found for circuit %s, key %s. Generating new entity ID.",
                        circuit_id,
                        sensor_def["key"],
                    )
                # Generate entity ID using appropriate synthetic helper based on number of tabs
                entity_suffix = get_user_friendly_suffix(sensor_def["key"])

                # Check the number of tabs to determine which helper to use
                if len(circuit_data.tabs) == 2:
                    tmp_eid = construct_240v_synthetic_entity_id(
                        coordinator=coordinator,
                        span_panel=span_panel,
                        platform="sensor",
                        suffix=entity_suffix,
                        friendly_name=circuit_name,
                        tab1=circuit_data.tabs[0],
                        tab2=circuit_data.tabs[1],
                        unique_id=None,
                    )
                elif len(circuit_data.tabs) == 1:
                    tmp_eid = construct_120v_synthetic_entity_id(
                        coordinator=coordinator,
                        span_panel=span_panel,
                        platform="sensor",
                        suffix=entity_suffix,
                        friendly_name=circuit_name,
                        tab=circuit_data.tabs[0],
                        unique_id=None,
                    )
                else:
                    raise ValueError(
                        f"Circuit {circuit_id} ({circuit_name}) has {len(circuit_data.tabs)} tabs. "
                        f"US electrical systems require exactly 1 tab (120V) or 2 tabs (240V). "
                        f"Tabs: {circuit_data.tabs}"
                    )
                if tmp_eid is None:
                    raise ValueError("Failed to build entity_id for circuit sensor")
                entity_id = tmp_eid

                # Generate unique ID for synthetic sensor following documented pattern
                sensor_name = f"{circuit_id}_{entity_suffix}"
                sensor_unique_id = construct_synthetic_unique_id(
                    device_identifier_for_uniques, sensor_name
                )

            # Generate backing entity ID
            backing_suffix = get_user_friendly_suffix(sensor_def["key"])
            backing_entity_id = construct_backing_entity_id_for_entry(
                coordinator, span_panel, circuit_id, backing_suffix, device_name
            )

            # Create friendly name
            friendly_name = f"{circuit_name} {sensor_def['name']}"

            # Generate tabs and voltage attributes for this circuit
            tabs_attribute_full = construct_tabs_attribute(circuit_data)
            voltage_attribute = construct_voltage_attribute(circuit_data)

            # Use the full tabs attribute for template usage (template will add quotes)
            tabs_attribute = tabs_attribute_full if tabs_attribute_full else ""

            # Ensure non-None before use
            if entity_id is None:
                raise ValueError("Entity ID was not generated for circuit sensor")
            if sensor_unique_id is None:
                raise ValueError("Sensor unique_id was not generated for circuit sensor")

            # Create placeholders for this sensor
            sensor_placeholders = {
                "sensor_key": sensor_unique_id,
                "sensor_name": friendly_name,
                "entity_id": entity_id,
                "backing_entity_id": backing_entity_id,
                "tabs_attribute": tabs_attribute,
                "voltage_attribute": str(voltage_attribute),
            }

            # Combine common and sensor-specific placeholders
            all_placeholders = {**common_placeholders, **sensor_placeholders}

            # Ensure all placeholder values are strings, except voltage_attribute
            string_placeholders = {}
            for key, value in all_placeholders.items():
                if key == "voltage_attribute" and isinstance(value, int | float):
                    # Keep voltage as unquoted number for YAML
                    string_placeholders[key] = str(value)
                elif value is not None:
                    string_placeholders[key] = str(value)
                else:
                    string_placeholders[key] = ""

            # Use the combined YAML approach for this single sensor
            combined_result = await combine_yaml_templates(
                [sensor_def["template"]], string_placeholders
            )

            # Store global settings from first sensor (they should be the same for all)
            if not global_settings:
                global_settings = combined_result["global_settings"]

            # Add this sensor's config to the collection
            sensor_configs.update(combined_result["sensor_configs"])

            # Get the current data value
            data_value = get_circuit_data_value(circuit_data, sensor_def["data_path"])

            # Create backing entity
            backing_entity = BackingEntity(
                entity_id=backing_entity_id,
                value=data_value,
                data_path=f"circuits.{circuit_id}.{sensor_def['data_path']}",
            )
            backing_entities.append(backing_entity)

            # Create 1:1 mapping directly - sensor key to backing entity ID
            sensor_to_backing_mapping[sensor_unique_id] = backing_entity_id

    _LOGGER.debug(
        "Generated %d named circuit sensors with %d backing entities using global settings",
        len(sensor_configs),
        len(backing_entities),
    )

    return sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping
