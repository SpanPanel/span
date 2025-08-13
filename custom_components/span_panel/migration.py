"""Migration logic for SPAN Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from ha_synthetic_sensors import StorageManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .helpers import construct_sensor_set_id
from .migration_utils import group_existing_sensors_by_category
from .synthetic_named_circuits import generate_named_circuit_sensors
from .synthetic_sensors import _construct_complete_yaml_config

_LOGGER = logging.getLogger(__name__)


def reconstruct_sensor_to_backing_mapping(
    device_identifier: str, sensor_mappings: dict[str, str]
) -> dict[str, str]:
    """Reconstruct sensor-to-backing entity ID mapping from existing sensors.

    This uses the deterministic pattern that backing entity IDs follow
    to recreate the mapping that would have been created during fresh install.
    """
    mapping = {}

    # API field to backing suffix mapping
    # Maps the old raw API field names (from entity registry) to new backing suffixes
    api_to_backing_suffix = {
        # Panel sensors (circuit_id = "0") - old raw API fields
        "instantGridPowerW": "current_power",
        "feedthroughPowerW": "feed_through_power",
        "mainMeterEnergy.producedEnergyWh": "main_meter_produced_energy",
        "mainMeterEnergy.consumedEnergyWh": "main_meter_consumed_energy",
        "feedthroughEnergy.producedEnergyWh": "feed_through_produced_energy",
        "feedthroughEnergy.consumedEnergyWh": "feed_through_consumed_energy",
        # Circuit sensors - old raw API fields
        "instantPowerW": "power",
        "producedEnergyWh": "energy_produced",
        "consumedEnergyWh": "energy_consumed",
    }

    for unique_id, _ in sensor_mappings.items():
        # Parse unique_id to extract components
        # Pattern: span_{device_id}_{circuit_id}_{api_field} or span_{device_id}_{api_field}
        parts = unique_id.split("_", 2)  # Split into at most 3 parts

        if len(parts) < 3:
            _LOGGER.warning("Cannot parse unique_id for backing mapping: %s", unique_id)
            continue

        # Check if this is the expected device
        if parts[1] != device_identifier:
            _LOGGER.warning(
                "Unexpected device identifier in unique_id: %s (expected %s)",
                parts[1],
                device_identifier,
            )
            continue

        # Remaining part after device_id
        remainder = parts[2]

        # Try to identify panel vs circuit sensor
        circuit_id = None
        api_field = None

        # Check if it's a panel sensor (direct API field)
        for field in api_to_backing_suffix:
            if remainder == field:
                # Panel sensor
                circuit_id = "0"
                api_field = field
                break

        if api_field is None:
            # Circuit sensor - split by last underscore to separate circuit_id and api_field
            last_underscore = remainder.rfind("_")
            if last_underscore > 0:
                potential_circuit_id = remainder[:last_underscore]
                potential_api_field = remainder[last_underscore + 1 :]

                # Validate it's a known API field
                if potential_api_field in api_to_backing_suffix:
                    circuit_id = potential_circuit_id
                    api_field = potential_api_field

        if circuit_id is None or api_field is None:
            _LOGGER.warning("Cannot determine circuit_id/api_field from unique_id: %s", unique_id)
            continue

        # Get backing suffix
        backing_suffix = api_to_backing_suffix.get(api_field)
        if backing_suffix is None:
            _LOGGER.warning("Unknown API field for backing mapping: %s", api_field)
            continue

        # Construct backing entity ID
        backing_entity_id = (
            f"sensor.span_{device_identifier.lower()}_{circuit_id}_backing_{backing_suffix}"
        )

        # Add to mapping
        mapping[unique_id] = backing_entity_id

        _LOGGER.debug("Mapped sensor %s -> backing %s", unique_id, backing_entity_id)

    return mapping


async def migrate_config_entry_to_synthetic_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> bool:
    """Migrate a single config entry to synthetic sensor configuration."""

    # Only migrate if version is less than 2
    if config_entry.version >= 2:
        return True

    _LOGGER.info(
        "MIGRATION STARTING: Migrating config entry %s from version %s to version 2 for synthetic sensors",
        config_entry.entry_id,
        config_entry.version,
    )

    try:
        # Analyze existing entities for this config entry
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

        _LOGGER.info(
            "MIGRATION DEBUG: Found %d total entities for config entry %s",
            len(entities),
            config_entry.entry_id,
        )

        # Extract device identifier from config entry unique_id (migration only deals with real panels)
        # During migration, coordinator hasn't been set up yet, so we use config entry data
        device_identifier_raw = config_entry.unique_id
        device_identifier: str = device_identifier_raw if device_identifier_raw is not None else ""
        device_name = config_entry.title

        _LOGGER.info("MIGRATION DEBUG: Config entry: %s", config_entry.entry_id)
        _LOGGER.info("MIGRATION DEBUG: Found %d total entities", len(entities))

        # Group and analyze entities
        sensor_entities = []
        for entity in entities:
            _LOGGER.debug(
                "MIGRATION DEBUG: Entity - domain=%s, entity_id=%s, unique_id=%s, platform=%s",
                entity.domain,
                entity.entity_id,
                entity.unique_id,
                entity.platform,
            )
            if entity.domain == "sensor":
                sensor_entities.append(
                    {
                        "entity_id": entity.entity_id,
                        "unique_id": entity.unique_id,
                        "name": entity.name,
                        "unit_of_measurement": entity.unit_of_measurement,
                        "device_class": entity.device_class,
                        "original_name": entity.original_name,
                    }
                )

        _LOGGER.debug(
            "Found %d sensor entities to migrate for device %s",
            len(sensor_entities),
            device_identifier,
        )

        # Classify existing sensors by category
        generic_mappings, solar_mappings = group_existing_sensors_by_category(sensor_entities)

        _LOGGER.info(
            "MIGRATION DEBUG: Classified sensors for device %s: %d generic, %d solar",
            device_identifier,
            len(generic_mappings),
            len(solar_mappings),
        )

        # Debug log some examples
        if generic_mappings:
            _LOGGER.info(
                "MIGRATION DEBUG: Generic mapping example: %s",
                list(generic_mappings.items())[0],
            )
        if solar_mappings:
            _LOGGER.info(
                "MIGRATION DEBUG: Solar mapping example: %s",
                list(solar_mappings.items())[0],
            )

        # Generate YAML configuration for this device using standard generation path
        yaml_content = await generate_device_yaml_from_classified_entities(
            config_entry,
            device_identifier,
            generic_mappings,
            solar_mappings,
            None,  # coordinator not available during migration
            None,  # span_panel not available during migration
            device_name,
        )

        # Initialize or get existing storage manager
        storage_manager = StorageManager(hass, DOMAIN, integration_domain=DOMAIN)
        await storage_manager.async_load()

        # Create sensor set for this device
        sensor_set_id = construct_sensor_set_id(device_identifier)

        if not storage_manager.sensor_set_exists(sensor_set_id):
            await storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier=device_identifier,
                name=f"SPAN Panel {device_identifier}",
                description=f"SPAN Panel synthetic sensors (migrated from v1) - {config_entry.title}",
            )
            _LOGGER.info("Created sensor set %s for device %s", sensor_set_id, device_identifier)

        # Import YAML configuration for this device
        await storage_manager.async_from_yaml(
            yaml_content=yaml_content,
            sensor_set_id=sensor_set_id,
            device_identifier=device_identifier,
            replace_existing=True,
        )

        _LOGGER.info(
            "Successfully migrated config entry %s to synthetic sensors with sensor set %s",
            config_entry.entry_id,
            sensor_set_id,
        )

        # Reconstruct sensor-to-backing mapping for synthetic sensors
        all_mappings = {**generic_mappings, **solar_mappings}
        sensor_to_backing_mapping = reconstruct_sensor_to_backing_mapping(
            device_identifier, all_mappings
        )

        _LOGGER.info(
            "Reconstructed %d sensor-to-backing mappings for migration",
            len(sensor_to_backing_mapping),
        )

        # Store the mapping in hass.data for sensor setup to retrieve
        # This is temporary storage that sensor.py will consume
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}
        if config_entry.entry_id not in hass.data[DOMAIN]:
            hass.data[DOMAIN][config_entry.entry_id] = {}

        hass.data[DOMAIN][config_entry.entry_id]["migration_sensor_to_backing_mapping"] = (
            sensor_to_backing_mapping
        )

        return True

    except Exception as e:
        _LOGGER.error(
            "Error during migration of config entry %s: %s",
            config_entry.entry_id,
            e,
            exc_info=True,
        )
        return False


async def generate_device_yaml_from_classified_entities(
    config_entry: ConfigEntry,
    device_identifier: str,
    generic_mappings: dict[str, str],
    solar_mappings: dict[str, str],
    coordinator: Any,
    span_panel: Any,
    device_name: str,
) -> str:
    """Generate YAML configuration using standard generation path with existing mappings.

    This leverages the same generation functions as fresh installs but passes existing
    sensor mappings to preserve entity_ids, ensuring identical YAML structure.
    """

    # Use standard generation functions with existing mappings

    # Generate panel sensors (skipped for migration - panels become generic circuits)
    panel_sensor_configs: dict[str, dict[str, Any]] = {}
    global_settings: dict[str, Any] = {}

    # Generate named circuit sensors (includes both panel and circuits from generic_mappings)
    (
        named_circuit_configs,
        named_circuit_backing_entities,
        named_global_settings,
        circuit_mappings,
    ) = await generate_named_circuit_sensors(
        coordinator, span_panel, device_identifier, existing_sensor_mappings=generic_mappings
    )

    # Generate solar sensors if any exist
    solar_sensor_configs: dict[str, dict[str, Any]] = {}

    if solar_mappings:
        try:
            # For migration, solar mappings preserve existing entity IDs as simple pass-through
            # Solar backing entities are determined by the actual solar options in config
            _LOGGER.info("Processing %d solar sensors for migration", len(solar_mappings))

            for unique_id, entity_id in solar_mappings.items():
                solar_sensor_configs[unique_id] = {
                    "entity_id": entity_id,
                    "name": entity_id.replace("sensor.", "").replace("_", " ").title(),
                    "formula": "state",  # Simple pass-through for migration
                }

            _LOGGER.info("Added %d solar sensors to migration", len(solar_sensor_configs))
        except Exception as e:
            _LOGGER.warning("Solar processing failed during migration (skipping): %s", e)
            # Solar failure is non-fatal for migration

    # Combine all sensor configs (same as standard path)
    all_sensor_configs = {**panel_sensor_configs, **named_circuit_configs, **solar_sensor_configs}

    # Use global settings from circuit generation (standard fallback logic)
    if not global_settings and named_global_settings:
        global_settings = named_global_settings

    # Use standard YAML construction with proper header/variables
    return await _construct_complete_yaml_config(all_sensor_configs, global_settings)
