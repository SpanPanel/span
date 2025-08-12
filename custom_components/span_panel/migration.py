"""Migration logic for SPAN Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from ha_synthetic_sensors import StorageManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import COORDINATOR, DOMAIN
from .helpers import construct_sensor_set_id
from .migration_utils import group_existing_sensors_by_category
from .synthetic_named_circuits import generate_named_circuit_sensors
from .synthetic_sensors import _construct_complete_yaml_config

_LOGGER = logging.getLogger(__name__)


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
        
        _LOGGER.info("MIGRATION DEBUG: Found %d total entities for config entry %s", 
                     len(entities), config_entry.entry_id)

        # Get coordinator and panel data to extract proper device identifier
        data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
        coordinator = data[COORDINATOR]
        span_panel = coordinator.data
        device_name = config_entry.data.get("device_name", config_entry.title)

        # Extract device identifier from panel serial number (migration only deals with real panels)
        device_identifier: str = span_panel.status.serial_number

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
            coordinator,
            span_panel,
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
        coordinator, span_panel, device_name, existing_sensor_mappings=generic_mappings
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
