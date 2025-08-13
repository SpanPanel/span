"""Panel-level synthetic sensor generation for SPAN Panel integration.

This module generates synthetic sensors for panel-level measurements like
current power, feedthrough power, and energy sensors using YAML templates.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.util import slugify

from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_backing_entity_id_for_entry,
    construct_panel_friendly_name,
    construct_panel_synthetic_entity_id,
    construct_synthetic_unique_id,
    get_panel_entity_suffix,
    get_panel_voltage_attribute,
)
from .migration_utils import classify_sensor_from_unique_id
from .span_panel import SpanPanel
from .synthetic_utils import BackingEntity, combine_yaml_templates

_LOGGER = logging.getLogger(__name__)

# Panel sensor definitions - these match the old PANEL_SENSORS from sensor_definitions.py
PANEL_SENSOR_DEFINITIONS = [
    {
        "key": "instantGridPowerW",
        "name": "Current Power",
        "template": "panel_sensor",
        "data_path": "instantGridPowerW",  # Use camelCase property
    },
    {
        "key": "feedthroughPowerW",
        "name": "Feed Through Power",
        "template": "panel_sensor",
        "data_path": "feedthroughPowerW",  # Use camelCase property
    },
    {
        "key": "mainMeterEnergyProducedWh",
        "name": "Main Meter Produced Energy",
        "template": "panel_energy_produced",  # Use panel energy template for panel energy sensors
        "data_path": "mainMeterEnergyProducedWh",  # Use camelCase property
    },
    {
        "key": "mainMeterEnergyConsumedWh",
        "name": "Main Meter Consumed Energy",
        "template": "panel_energy_consumed",  # Use panel energy template for panel energy sensors
        "data_path": "mainMeterEnergyConsumedWh",  # Use camelCase property
    },
    {
        "key": "feedthroughEnergyProducedWh",
        "name": "Feed Through Produced Energy",
        "template": "panel_energy_produced",  # Use panel energy template for panel energy sensors
        "data_path": "feedthroughEnergyProducedWh",  # Use camelCase property
    },
    {
        "key": "feedthroughEnergyConsumedWh",
        "name": "Feed Through Consumed Energy",
        "template": "panel_energy_consumed",  # Use panel energy template for panel energy sensors
        "data_path": "feedthroughEnergyConsumedWh",  # Use camelCase property
    },
]


def get_panel_data_value(span_panel: SpanPanel, data_path: str) -> float:
    """Get panel data value using dot notation path.

    Args:
        span_panel: The SpanPanel instance
        data_path: Dot notation path to the data (e.g., "instant_grid_power")

    Returns:
        The data value as float

    """
    try:
        # Get the panel data object
        panel_data = span_panel.panel

        # Use getattr to get the value
        value = getattr(panel_data, data_path)

        # Convert to float for consistency
        return float(value) if value is not None else 0.0

    except (AttributeError, TypeError, ValueError) as e:
        _LOGGER.warning("Failed to get panel data for path '%s': %s", data_path, e)
        return 0.0


async def generate_panel_sensors(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    device_name: str,
    existing_sensor_mappings: dict[str, str] | None = None,
    migration_mode: bool = False,
) -> tuple[dict[str, Any], list[BackingEntity], dict[str, Any], dict[str, str]]:
    """Generate panel-level synthetic sensors and their backing entities.

    Args:
        coordinator: The SpanPanelCoordinator instance
        span_panel: The SpanPanel data
        device_name: The name of the device to use for sensor generation
        existing_sensor_mappings: Optional dict mapping unique_id to entity_id for migration.
                                 If None, generates new keys using helpers.
        migration_mode: When True, resolve entity_ids by registry lookup using helper-format unique_id

    Returns:
        Tuple of (sensor_configs_dict, list_of_backing_entities, global_settings, sensor_to_backing_mapping)

    """

    sensor_configs: dict[str, Any] = {}
    backing_entities: list[BackingEntity] = []
    global_settings: dict[str, Any] = {}
    sensor_to_backing_mapping: dict[str, str] = {}

    # Determine identifier to use for YAML/global_settings and unique IDs
    # - Live panels use the true serial number
    # - Simulator entries must be unique per config entry, so use slugified device name
    is_simulator: bool = bool(coordinator.config_entry.data.get("simulation_mode", False))
    device_identifier_for_uniques: str = (
        slugify(device_name)
        if is_simulator and isinstance(device_name, str) and device_name
        else span_panel.status.serial_number
    )

    # Get display precision from options
    power_precision = coordinator.config_entry.options.get("power_display_precision", 0)
    energy_precision = coordinator.config_entry.options.get("energy_display_precision", 2)

    # Create common placeholders for header template
    common_placeholders = {
        "device_identifier": device_identifier_for_uniques,
        "panel_id": device_identifier_for_uniques,
        "device_name": device_name,
        "energy_grace_period_minutes": str(
            coordinator.config_entry.options.get("energy_reporting_grace_period", 15)
        ),
        "power_display_precision": str(power_precision),
        "energy_display_precision": str(energy_precision),
        # Panel sensors don't have circuit data, so use appropriate defaults
        "tabs_attribute": "panel",  # Panel-level identifier
        "voltage_attribute": str(get_panel_voltage_attribute()),  # Standard panel voltage
    }

    for sensor_def in PANEL_SENSOR_DEFINITIONS:
        # Generate entity ID using new helper for synthetic sensors
        entity_suffix = get_panel_entity_suffix(sensor_def["key"])
        device_name = coordinator.config_entry.data.get(
            "device_name", coordinator.config_entry.title
        )
        # Build helper-format unique_id (used for registry lookup when migration_mode is True)
        sensor_unique_id = construct_synthetic_unique_id(
            device_identifier_for_uniques, entity_suffix
        )
        entity_id = construct_panel_synthetic_entity_id(
            coordinator,
            span_panel,
            "sensor",
            entity_suffix,
            device_name,
            unique_id=sensor_unique_id if migration_mode else None,
        )

        # Generate friendly name using existing helper
        friendly_name = construct_panel_friendly_name(sensor_def["name"])

        # Generate backing entity ID using existing helper - use user-friendly suffix
        backing_entity_suffix = get_panel_entity_suffix(sensor_def["key"])
        backing_entity_id = construct_backing_entity_id_for_entry(
            coordinator,
            span_panel,
            "0",  # Use "0" for panel-level circuit_id
            backing_entity_suffix,  # Use user-friendly suffix, not raw API key
            device_name,
        )

        # Get the current data value
        data_value = get_panel_data_value(span_panel, sensor_def["data_path"])

        # Generate the sensor key following documented pattern
        # Pattern: span_{identifier}_{sensor_key} where identifier is per-device
        # For migration mode we already computed sensor_unique_id above
        if not migration_mode:
            # In non-migration scenarios, we maintain legacy support for existing mappings
            if existing_sensor_mappings:
                for existing_unique_id, existing_entity_id in existing_sensor_mappings.items():
                    try:
                        category, sensor_type, api_key = classify_sensor_from_unique_id(
                            existing_unique_id
                        )
                        if category == "generic" and api_key == sensor_def["key"]:
                            sensor_unique_id = existing_unique_id
                            entity_id = existing_entity_id
                            break
                    except ValueError:
                        continue
            # If still not set, build helper-format unique_id (fresh install)
            if "sensor_unique_id" not in locals() or sensor_unique_id is None:
                sensor_unique_id = construct_synthetic_unique_id(
                    device_identifier_for_uniques, entity_suffix
                )

        # Create placeholders for this specific sensor
        sensor_placeholders = {
            "sensor_key": sensor_unique_id,
            "sensor_name": friendly_name,
            "entity_id": entity_id,
            "backing_entity_id": backing_entity_id,
        }

        # Combine common and sensor-specific placeholders
        all_placeholders = {**common_placeholders, **sensor_placeholders}

        # Ensure all placeholder values are strings
        string_placeholders = {
            key: str(value) if value is not None else "" for key, value in all_placeholders.items()
        }

        # Use the new combined YAML approach for this single sensor
        combined_result = await combine_yaml_templates(
            [sensor_def["template"]], string_placeholders
        )

        # Store global settings from first sensor (they should be the same for all)
        if not global_settings:
            global_settings = combined_result["global_settings"]

        # Add this sensor's config to the collection
        sensor_configs.update(combined_result["sensor_configs"])

        # Create backing entity
        backing_entity = BackingEntity(
            entity_id=backing_entity_id, value=data_value, data_path=sensor_def["data_path"]
        )
        backing_entities.append(backing_entity)

        # Create 1:1 mapping directly - sensor key to backing entity ID
        sensor_to_backing_mapping[sensor_unique_id] = backing_entity_id

        _LOGGER.debug(
            "Generated panel sensor: %s -> %s (value: %s)", sensor_def["key"], entity_id, data_value
        )

    return sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping
