"""Migration logic for SPAN Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from ha_synthetic_sensors import StorageManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, USE_DEVICE_PREFIX

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
        "Migrating config entry %s from version %s to version 2 for synthetic sensors",
        config_entry.entry_id,
        config_entry.version,
    )

    try:
        # Analyze existing entities for this config entry
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

        # Extract device identifier from config entry
        device_identifier = extract_device_identifier_from_config_entry(config_entry, entities)

        # Group and analyze entities
        sensor_entities = []
        for entity in entities:
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

        # Generate YAML configuration for this device
        yaml_content = generate_device_yaml_from_entities(
            config_entry, device_identifier, sensor_entities
        )

        # Initialize or get existing storage manager
        storage_manager = StorageManager(hass, DOMAIN, integration_domain=DOMAIN)
        await storage_manager.async_load()

        # Create sensor set for this device
        sensor_set_id = f"{device_identifier}_sensors"

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


def extract_device_identifier_from_config_entry(
    config_entry: ConfigEntry,
    entities: list[er.RegistryEntry],
) -> str:
    """Extract device identifier from config entry and entities."""

    # Method 1: Try to get from existing entities' unique IDs
    for entity in entities:
        if entity.unique_id and entity.unique_id.startswith("span_"):
            # Extract serial number from unique ID pattern: span_{serial}_...
            parts = entity.unique_id.split("_")
            if len(parts) >= 2:
                potential_serial = parts[1]
                # Validate it looks like a serial number
                if len(potential_serial) > 3 and not potential_serial.isdigit():
                    return potential_serial

    # Method 2: Fall back to config entry data
    if "device_identifier" in config_entry.data:
        device_id = config_entry.data["device_identifier"]
        if isinstance(device_id, str):
            return device_id

    # Method 3: Use host as fallback
    host = config_entry.data.get(CONF_HOST, "unknown")
    return f"span_{host.replace('.', '_')}"


def generate_device_yaml_from_entities(
    config_entry: ConfigEntry,
    device_identifier: str,
    sensor_entities: list[dict[str, Any]],
) -> str:
    """Generate complete YAML configuration for a single device."""

    # Build global settings
    global_settings = {
        "device_identifier": device_identifier,
        "energy_grace_period": 300,  # Default 5 minutes
        "use_device_prefix": config_entry.options.get(USE_DEVICE_PREFIX, False),
    }

    # Generate sensor configurations using existing unique IDs as sensor keys
    sensor_configs = {}

    for sensor_entity in sensor_entities:
        sensor_key = sensor_entity["unique_id"]  # Existing unique ID becomes sensor key

        # Generate formula based on sensor type
        formula = generate_formula_for_existing_sensor(sensor_entity)

        # Generate backing entities based on sensor type
        backing_entities = generate_backing_entities_for_existing_sensor(sensor_entity)

        sensor_configs[sensor_key] = {
            "entity_id": sensor_entity["entity_id"],  # Preserve exact entity ID
            "name": sensor_entity["name"]
            or sensor_entity["original_name"]
            or sensor_entity["entity_id"],
            "unit_of_measurement": sensor_entity["unit_of_measurement"],
            "device_class": sensor_entity["device_class"],
            "formula": formula,
            "backing_entities": backing_entities,
        }

    # Use existing template system to construct complete YAML
    yaml_content = construct_complete_yaml_config(sensor_configs, global_settings)

    return yaml_content


def generate_formula_for_existing_sensor(sensor_entity: dict[str, Any]) -> str:
    """Generate appropriate formula for existing sensor based on its type."""

    entity_id = sensor_entity["entity_id"].lower()
    unique_id = sensor_entity["unique_id"].lower()

    # Analyze entity ID and unique ID to determine sensor type
    if "power" in entity_id or "power" in unique_id:
        return "backing_entities[0]"  # Direct mapping to backing entity
    elif "energy" in entity_id or "energy" in unique_id:
        return "integrate(backing_entities[0])"  # Integrate power to get energy
    elif "voltage" in entity_id or "voltage" in unique_id:
        return "backing_entities[0]"
    elif "current" in entity_id or "current" in unique_id:
        return "backing_entities[0]"
    else:
        return "backing_entities[0]"  # Default: direct mapping


def generate_backing_entities_for_existing_sensor(sensor_entity: dict[str, Any]) -> list[str]:
    """Generate backing entity IDs for existing sensor based on its unique ID pattern."""

    entity_id = sensor_entity["entity_id"]

    # For migration, we'll use self-mapping - the synthetic sensor will map to itself
    # This preserves the exact same behavior while transitioning to synthetic architecture
    return [entity_id]


def construct_complete_yaml_config(
    sensor_configs: dict[str, Any], global_settings: dict[str, Any]
) -> str:
    """Construct complete YAML configuration from sensor configs and global settings."""

    # Simple YAML construction for migration
    # This creates a basic YAML structure that preserves existing sensor behavior
    yaml_lines = []

    # Global settings section
    yaml_lines.append("global_settings:")
    for key, value in global_settings.items():
        if isinstance(value, bool):
            yaml_lines.append(f"  {key}: {str(value).lower()}")
        else:
            yaml_lines.append(f"  {key}: {value}")

    yaml_lines.append("")
    yaml_lines.append("sensors:")

    # Sensor configurations section
    for sensor_key, config in sensor_configs.items():
        yaml_lines.append(f"  {sensor_key}:")
        for key, value in config.items():
            if key == "backing_entities":
                yaml_lines.append(f"    {key}:")
                for entity in value:
                    yaml_lines.append(f"      - {entity}")
            elif isinstance(value, bool):
                yaml_lines.append(f"    {key}: {str(value).lower()}")
            elif value is not None:
                yaml_lines.append(f"    {key}: {value}")

    return "\n".join(yaml_lines)
