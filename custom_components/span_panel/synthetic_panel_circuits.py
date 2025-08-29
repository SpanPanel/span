"""Panel-level synthetic sensor generation for SPAN Panel integration.

This module generates synthetic sensors for panel-level measurements like
current power, feedthrough power, and energy sensors using YAML templates.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import USE_DEVICE_PREFIX
from .coordinator import SpanPanelCoordinator
from .helpers import (
    NEW_SENSOR,
    build_binary_sensor_unique_id_for_entry,
    construct_backing_entity_id_for_entry,
    construct_panel_entity_id,
    construct_panel_friendly_name,
    construct_panel_synthetic_entity_id,
    construct_synthetic_unique_id_for_entry,
    get_panel_entity_suffix,
    get_panel_voltage_attribute,
)
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


def get_panel_data_value(span_panel: SpanPanel, data_path: str) -> float | None:
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
        return float(value) if value is not None else None

    except (AttributeError, TypeError, ValueError) as e:
        _LOGGER.warning("Failed to get panel data for path '%s': %s", data_path, e)
        return None


async def generate_panel_sensors(
    hass: HomeAssistant,
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    device_name: str,
    migration_mode: bool = False,
) -> tuple[dict[str, Any], list[BackingEntity], dict[str, Any], dict[str, str]]:
    """Generate panel-level synthetic sensors and their backing entities.

    Args:
        hass: The HomeAssistant instance
        coordinator: The SpanPanelCoordinator instance
        span_panel: The SpanPanel data
        device_name: The name of the device to use for sensor generation
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

    # Construct panel status entity ID
    # panel_status is a new sensor, so always use migration_mode=False when looking up its entity ID
    use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)
    _LOGGER.debug(
        "SYNTHETIC_PANEL_DEBUG: USE_DEVICE_PREFIX lookup - key=%s, value=%s, migration_mode=%s",
        USE_DEVICE_PREFIX,
        use_device_prefix,
        migration_mode,
    )
    # WFF Again, the panel_status is a new sensor, so it is a one time effort to build the YAML.
    panel_status_unique_id = build_binary_sensor_unique_id_for_entry(
        coordinator, span_panel, "panel_status", device_name
    )
    panel_status_entity_id = construct_panel_entity_id(
        coordinator,
        span_panel,
        "binary_sensor",
        "panel_status",
        device_name,
        unique_id=NEW_SENSOR if migration_mode else panel_status_unique_id,
        migration_mode=migration_mode,
        use_device_prefix=use_device_prefix,
    )
    _LOGGER.debug(
        "SYNTHETIC_PANEL_DEBUG: panel_status lookup - unique_id=%s, entity_id=%s",
        panel_status_unique_id,
        panel_status_entity_id,
    )
    if panel_status_entity_id is None:
        _LOGGER.error(
            "SYNTHETIC_PANEL_ERROR: panel_status_entity_id is None! unique_id=%s, device_name=%s, use_device_prefix=%s",
            panel_status_unique_id,
            device_name,
            use_device_prefix,
        )

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
        "panel_status_entity_id": panel_status_entity_id
        or construct_panel_entity_id(
            coordinator,
            span_panel,
            "binary_sensor",
            "panel_status",
            device_name,
            unique_id=NEW_SENSOR,
            migration_mode=False,
            use_device_prefix=use_device_prefix,
        ),
    }

    _LOGGER.debug(
        "SYNTHETIC_PANEL_DEBUG: final panel_status_entity_id=%s",
        common_placeholders["panel_status_entity_id"],
    )

    for sensor_def in PANEL_SENSOR_DEFINITIONS:
        # Generate entity ID using new helper for synthetic sensors
        entity_suffix = get_panel_entity_suffix(sensor_def["key"])
        device_name = coordinator.config_entry.data.get(
            "device_name", coordinator.config_entry.title
        )
        # Generate unique_id using helper (consistent with migration expectations)
        sensor_unique_id = construct_synthetic_unique_id_for_entry(
            coordinator, span_panel, entity_suffix, device_name
        )
        entity_id = construct_panel_synthetic_entity_id(
            coordinator,
            span_panel,
            "sensor",
            entity_suffix,
            device_name,
            unique_id=NEW_SENSOR if migration_mode else sensor_unique_id,
            migration_mode=migration_mode,
        )
        _LOGGER.debug(
            "GEN_PANEL_DEBUG: migration_mode=%s, unique_id=%s, resolved_entity_id=%s",
            migration_mode,
            sensor_unique_id,
            entity_id,
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
            hass, [sensor_def["template"]], string_placeholders
        )

        # Store global settings from first sensor (they should be the same for all)
        if not global_settings:
            global_settings = combined_result["global_settings"]

        # Add this sensor's config to the collection
        sensor_configs.update(combined_result["sensor_configs"])

        # Only create backing entities for non-net energy sensors
        # Net energy sensors are pure calculations that reference other sensors
        if not sensor_def["key"].endswith("NetEnergyWh"):
            # Create backing entity
            backing_entity = BackingEntity(
                entity_id=backing_entity_id, value=data_value, data_path=sensor_def["data_path"]
            )
            backing_entities.append(backing_entity)

            # Create 1:1 mapping directly - sensor key to backing entity ID
            sensor_to_backing_mapping[sensor_unique_id] = backing_entity_id
        else:
            _LOGGER.debug(
                "Skipping backing entity creation for net energy sensor: %s",
                sensor_unique_id,
            )

        _LOGGER.debug(
            "Generated panel sensor: %s -> %s (value: %s)",
            sensor_def["key"],
            entity_id,
            data_value,
        )

    # After producing consumed/produced, add Net sensors for main meter and feedthrough
    try:
        # Build suffixes and entity_ids using helpers
        device_name = coordinator.config_entry.data.get(
            "device_name", coordinator.config_entry.title
        )

        # Helper to add one net sensor from two existing entity_ids
        async def _add_panel_net(
            description_key_consumed: str,
            description_key_produced: str,
            net_description_key: str,
            friendly_name: str,
        ) -> None:
            consumed_suffix = get_panel_entity_suffix(description_key_consumed)
            produced_suffix = get_panel_entity_suffix(description_key_produced)
            net_suffix = get_panel_entity_suffix(net_description_key)

            consumed_entity_id = construct_panel_synthetic_entity_id(
                coordinator,
                span_panel,
                "sensor",
                consumed_suffix,
                device_name,
                unique_id=construct_synthetic_unique_id_for_entry(
                    coordinator, span_panel, consumed_suffix, device_name
                ),
                migration_mode=False,  # Synthetic sensors are new, not subject to migration
            )
            produced_entity_id = construct_panel_synthetic_entity_id(
                coordinator,
                span_panel,
                "sensor",
                produced_suffix,
                device_name,
                unique_id=construct_synthetic_unique_id_for_entry(
                    coordinator, span_panel, produced_suffix, device_name
                ),
                migration_mode=False,  # Synthetic sensors are new, not subject to migration
            )
            net_unique_id = construct_synthetic_unique_id_for_entry(
                coordinator, span_panel, net_suffix, device_name
            )
            net_entity_id = construct_panel_synthetic_entity_id(
                coordinator,
                span_panel,
                "sensor",
                net_suffix,
                device_name,
                unique_id=net_unique_id,
                migration_mode=False,  # Synthetic sensors are new, not subject to migration
            )

            # Create placeholders for this specific sensor (following main loop pattern)
            sensor_placeholders = {
                "sensor_key": net_unique_id,
                "sensor_name": friendly_name,
                "entity_id": net_entity_id or "",
                "net_consumed_entity_id": consumed_entity_id or "",
                "net_produced_entity_id": produced_entity_id or "",
            }

            # Combine common and sensor-specific placeholders
            all_placeholders = {**common_placeholders, **sensor_placeholders}

            # Ensure all placeholder values are strings
            string_placeholders = {
                key: str(value) if value is not None else ""
                for key, value in all_placeholders.items()
            }

            # Use the same pattern as the main loop - combine template and update collection
            net_result = await combine_yaml_templates(
                hass, ["panel_energy_net"], string_placeholders
            )

            # Add this sensor's config to the collection
            sensor_configs.update(net_result["sensor_configs"])

        # Main meter net
        await _add_panel_net(
            "mainMeterEnergyConsumedWh",
            "mainMeterEnergyProducedWh",
            "mainMeterNetEnergyWh",
            construct_panel_friendly_name("Main Meter Net Energy"),
        )

        # Feedthrough net
        await _add_panel_net(
            "feedthroughEnergyConsumedWh",
            "feedthroughEnergyProducedWh",
            "feedthroughNetEnergyWh",
            construct_panel_friendly_name("Feed Through Net Energy"),
        )
    except Exception as e:
        _LOGGER.warning("Failed to generate panel net energy sensors: %s", e)

    return sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping
