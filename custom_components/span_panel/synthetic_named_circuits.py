"""Named circuit synthetic sensor generation for SPAN Panel integration.

This module generates synthetic sensors for normal named circuits (non-unmapped circuits)
using YAML templates and virtual backing entities. These circuits benefit from the name
tracking and synchronization logic already in the integration.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_120v_synthetic_entity_id,
    construct_240v_synthetic_entity_id,
    construct_backing_entity_id_for_entry,
    construct_circuit_unique_id_for_entry,
    construct_panel_entity_id,
    construct_tabs_attribute,
    construct_voltage_attribute,
    get_circuit_number,
    get_user_friendly_suffix,
)
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


def get_circuit_data_value(circuit_data: Any, data_path: str) -> float | None:
    """Get circuit data value using attribute name.

    Args:
        circuit_data: The circuit data object
        data_path: Attribute name to get (e.g., "instant_power")

    Returns:
        The data value as float

    """
    try:
        value = getattr(circuit_data, data_path, None)
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError) as e:
        _LOGGER.warning("Failed to get circuit data for path '%s': %s", data_path, e)
        return None


async def generate_named_circuit_sensors(
    hass: HomeAssistant,
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    device_name: str,
    migration_mode: bool = False,
) -> tuple[dict[str, Any], list[BackingEntity], dict[str, Any], dict[str, str]]:
    """Generate named circuit synthetic sensors and their backing entities.

    This function creates synthetic sensors for all normal named circuits
    (circuits that are not unmapped tab positions).

    Args:
        hass: Home Assistant instance
        coordinator: The SpanPanelCoordinator instance
        span_panel: The SpanPanel data
        device_name: The device name to use for entity IDs and friendly names
        migration_mode: When True, resolve entity_ids by registry lookup using helper-format unique_id

    Returns:
        Tuple of (sensor_configs_dict, list_of_backing_entities, global_settings, sensor_to_backing_mapping)

    """

    sensor_configs: dict[str, Any] = {}
    backing_entities: list[BackingEntity] = []
    global_settings: dict[str, Any] = {}
    sensor_to_backing_mapping: dict[str, str] = {}

    # Get display precision from options - coordinator should always be available during YAML generation
    if coordinator is None:
        raise ValueError("Coordinator is required for YAML generation but was None")

    if span_panel is None:
        raise ValueError("span_panel is None")

    power_precision = coordinator.config_entry.options.get("power_display_precision", 0)
    energy_precision = coordinator.config_entry.options.get("energy_display_precision", 2)
    is_simulator: bool = bool(coordinator.config_entry.data.get("simulation_mode", False))

    # Initialize device_identifier_for_uniques with a default value
    device_identifier_for_uniques: str = (
        slugify(device_name) if isinstance(device_name, str) and device_name else "unknown"
    )

    if span_panel is not None:
        device_identifier_for_uniques = (
            slugify(device_name)
            if is_simulator and isinstance(device_name, str) and device_name
            else span_panel.status.serial_number
        )

    # Create common placeholders for header template
    energy_grace_period = coordinator.config_entry.options.get("energy_reporting_grace_period", 15)

    # Construct panel status entity ID
    use_device_prefix = coordinator.config_entry.options.get("USE_DEVICE_PREFIX", True)
    panel_status_entity_id = construct_panel_entity_id(
        coordinator,
        span_panel,
        "binary_sensor",
        "panel_status",
        device_name,
        use_device_prefix=use_device_prefix,
    )

    common_placeholders = {
        "device_identifier": device_identifier_for_uniques,
        "panel_id": device_identifier_for_uniques,
        "energy_grace_period_minutes": str(energy_grace_period),
        "power_display_precision": str(power_precision),
        "energy_display_precision": str(energy_precision),
        "panel_status_entity_id": panel_status_entity_id or "binary_sensor.span_panel_panel_status",
    }

    # Filter to only normal named circuits (not unmapped)
    named_circuits = {
        circuit_id: circuit_data
        for circuit_id, circuit_data in span_panel.circuits.items()
        if not circuit_id.startswith("unmapped_tab_")
    }

    # For fresh installs or normal boot after migration, we need circuits data
    if not named_circuits:
        raise ValueError(
            f"No named circuits found to process (span_panel available: {span_panel is not None}). Cannot generate synthetic sensors without circuit data."
        )

    for circuit_id, circuit_data in named_circuits.items():
        # Track produced/consumed entity_ids to build Net Energy after both exist
        produced_entity_id_for_circuit: str | None = None
        consumed_entity_id_for_circuit: str | None = None
        for sensor_def in NAMED_CIRCUIT_SENSOR_DEFINITIONS:
            # Get circuit number for helpers
            circuit_number = get_circuit_number(circuit_data)
            circuit_name = circuit_data.name or f"Circuit {circuit_number}"

            # Generate unique_id using helper (consistent with migration expectations)
            entity_suffix = get_user_friendly_suffix(sensor_def["key"])
            sensor_unique_id = construct_circuit_unique_id_for_entry(
                coordinator, span_panel, circuit_id, sensor_def["key"], device_name
            )

            # In migration mode, look up existing entity_id directly from registry
            entity_id: str | None = None
            if migration_mode:
                entity_registry = er.async_get(hass)
                existing_entity_id = entity_registry.async_get_entity_id(
                    "sensor", DOMAIN, sensor_unique_id
                )
                if existing_entity_id:
                    # Use existing entity ID as-is - no sanitization needed during migration
                    entity_id = existing_entity_id
                    _LOGGER.debug(
                        "MIGRATION: Using existing entity %s for unique_id %s",
                        existing_entity_id,
                        sensor_unique_id,
                    )
                else:
                    # FATAL ERROR: Migration mode but migrated key not found in registry
                    raise ValueError(
                        f"MIGRATION ERROR: Expected migrated unique_id '{sensor_unique_id}' not found in registry. "
                        f"This indicates migration failed for circuit {circuit_id} sensor {sensor_def['key']}."
                    )
            else:
                # Non-migration mode: generate new entity_id
                if len(circuit_data.tabs) == 2:
                    entity_id = construct_240v_synthetic_entity_id(
                        coordinator=coordinator,
                        span_panel=span_panel,
                        platform="sensor",
                        suffix=entity_suffix,
                        friendly_name=circuit_name,
                        tab1=circuit_data.tabs[0],
                        tab2=circuit_data.tabs[1],
                    )
                elif len(circuit_data.tabs) == 1:
                    entity_id = construct_120v_synthetic_entity_id(
                        coordinator=coordinator,
                        span_panel=span_panel,
                        platform="sensor",
                        suffix=entity_suffix,
                        friendly_name=circuit_name,
                        tab=circuit_data.tabs[0],
                    )
                else:
                    raise ValueError(
                        f"Circuit {circuit_id} ({circuit_name}) has {len(circuit_data.tabs)} tabs. "
                        f"US electrical systems require exactly 1 tab (120V) or 2 tabs (240V). "
                        f"Tabs: {circuit_data.tabs}"
                    )
                if entity_id is None:
                    raise ValueError("Failed to build entity_id for circuit sensor")
                _LOGGER.debug(
                    "GEN_CKT_DEBUG: migration_mode=%s, circuit_id=%s, unique_id=%s, resolved_entity_id=%s",
                    migration_mode,
                    circuit_id,
                    sensor_unique_id,
                    entity_id,
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
                hass, [sensor_def["template"]], string_placeholders
            )

            # Store global settings from first sensor (they should be the same for all)
            if not global_settings:
                global_settings = combined_result["global_settings"]

            # Add this sensor's config to the collection
            sensor_configs.update(combined_result["sensor_configs"])

            # Get the current data value
            data_value = get_circuit_data_value(circuit_data, sensor_def["data_path"])

            # Only create backing entities for non-net energy sensors
            # Net energy sensors are pure calculations that reference other sensors
            if not sensor_def["key"].endswith("netEnergyWh"):
                # Create backing entity
                backing_entity = BackingEntity(
                    entity_id=backing_entity_id,
                    value=data_value,
                    data_path=f"circuits.{circuit_id}.{sensor_def['data_path']}",
                )
                backing_entities.append(backing_entity)

                # Create 1:1 mapping directly - sensor key to backing entity ID
                sensor_to_backing_mapping[sensor_unique_id] = backing_entity_id
            else:
                _LOGGER.debug(
                    "Skipping backing entity creation for net energy sensor: %s",
                    sensor_unique_id,
                )

            # Record produced/consumed entity_ids for Net Energy construction
            if sensor_def["key"] == "producedEnergyWh":
                produced_entity_id_for_circuit = entity_id
            elif sensor_def["key"] == "consumedEnergyWh":
                consumed_entity_id_for_circuit = entity_id

        # After creating produced and consumed, add Net Energy sensor referencing them
        if produced_entity_id_for_circuit and consumed_entity_id_for_circuit:
            # Generate tabs and voltage attributes for this circuit (same as main loop)
            tabs_attribute_full = construct_tabs_attribute(circuit_data)
            voltage_attribute = construct_voltage_attribute(circuit_data)
            tabs_attribute = tabs_attribute_full if tabs_attribute_full else ""

            await _add_circuit_net_energy_sensor(
                hass=hass,
                coordinator=coordinator,
                span_panel=span_panel,
                circuit_id=circuit_id,
                circuit_data=circuit_data,
                circuit_name=circuit_name,
                consumed_entity_id=consumed_entity_id_for_circuit,
                produced_entity_id=produced_entity_id_for_circuit,
                device_identifier_for_uniques=device_identifier_for_uniques,
                common_placeholders=common_placeholders,
                tabs_attribute=tabs_attribute,
                voltage_attribute=voltage_attribute,
                sensor_configs=sensor_configs,
            )

    _LOGGER.debug(
        "Generated %d named circuit sensors with %d backing entities using global settings",
        len(sensor_configs),
        len(backing_entities),
    )

    return sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping


""


async def _add_circuit_net_energy_sensor(
    hass: HomeAssistant,
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    circuit_id: str,
    circuit_data: Any,
    circuit_name: str,
    consumed_entity_id: str,
    produced_entity_id: str,
    device_identifier_for_uniques: str,
    common_placeholders: dict[str, Any],
    tabs_attribute: str,
    voltage_attribute: int | float | None,
    sensor_configs: dict[str, Any],
) -> None:
    """Add a net energy sensor for a circuit that references consumed and produced sensors.

    Args:
        hass: The HomeAssistant instance
        coordinator: The SpanPanelCoordinator instance
        span_panel: The SpanPanel data
        circuit_id: The circuit ID
        circuit_data: The circuit data
        circuit_name: The friendly name for the circuit
        consumed_entity_id: Entity ID of the consumed energy sensor
        produced_entity_id: Entity ID of the produced energy sensor
        device_identifier_for_uniques: Device identifier for unique IDs
        common_placeholders: Common placeholders for templates
        tabs_attribute: Tabs attribute string for the circuit
        voltage_attribute: Voltage attribute value for the circuit
        sensor_configs: Dictionary to update with new sensor configs

    """
    # Build net sensor identifiers
    net_suffix = get_user_friendly_suffix("netEnergyWh")  # -> energy_net
    # Use proper helper to get device identifier for this entry
    net_unique_id = construct_circuit_unique_id_for_entry(
        coordinator, span_panel, circuit_id, "netEnergyWh", circuit_data.name
    )

    # Build entity_id for circuit (respect 120/240V)
    if len(circuit_data.tabs) == 2:
        net_entity_id = construct_240v_synthetic_entity_id(
            coordinator=coordinator,
            span_panel=span_panel,
            platform="sensor",
            suffix=net_suffix,
            friendly_name=circuit_name,
            tab1=circuit_data.tabs[0],
            tab2=circuit_data.tabs[1],
        )
    else:
        net_entity_id = construct_120v_synthetic_entity_id(
            coordinator=coordinator,
            span_panel=span_panel,
            platform="sensor",
            suffix=net_suffix,
            friendly_name=circuit_name,
            tab=circuit_data.tabs[0],
        )

    if net_entity_id is None:
        raise ValueError("Failed to build entity_id for circuit net energy sensor")

    # Friendly name for net
    net_friendly_name = f"{circuit_name} Net Energy"

    # Create placeholders for this specific sensor (following main loop pattern)
    sensor_placeholders = {
        "sensor_key": net_unique_id,
        "sensor_name": net_friendly_name,
        "entity_id": net_entity_id,
        "net_consumed_entity_id": consumed_entity_id,
        "net_produced_entity_id": produced_entity_id,
        "tabs_attribute": tabs_attribute,
        "voltage_attribute": voltage_attribute or 0,
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

    # Use the same pattern as the main loop - combine template and update collection
    net_result = await combine_yaml_templates(hass, ["circuit_energy_net"], string_placeholders)

    # Add this sensor's config to the collection
    sensor_configs.update(net_result["sensor_configs"])
