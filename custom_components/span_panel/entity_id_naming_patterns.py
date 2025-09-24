"""Entity ID naming pattern migration utilities for Span Panel integration."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import COORDINATOR, DOMAIN, USE_DEVICE_PREFIX

_LOGGER = logging.getLogger(__name__)


class EntityIdMigrationManager:
    """Manages entity ID migrations when naming patterns change."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the migration manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id

    async def migrate_entity_ids(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate entity IDs when naming patterns change.

        Currently only supports legacy migration from no device prefix to device prefix.
        This handles renaming existing entities to include the device prefix.

        Args:
            old_flags: Previous configuration flags
                {USE_CIRCUIT_NUMBERS: bool, USE_DEVICE_PREFIX: bool}
            new_flags: New configuration flags {USE_CIRCUIT_NUMBERS: bool, USE_DEVICE_PREFIX: bool}

        Returns:
            True if migration was successful, False otherwise

        """
        _LOGGER.info(
            "Starting entity ID migration: old_flags=%s, new_flags=%s",
            old_flags,
            new_flags,
        )

        # Only perform legacy migration (no device prefix -> device prefix)
        old_use_device_prefix = old_flags.get(USE_DEVICE_PREFIX, False)
        new_use_device_prefix = new_flags.get(USE_DEVICE_PREFIX, False)

        if not old_use_device_prefix and new_use_device_prefix:
            # Legacy migration: no device prefix -> device prefix
            return await self._migrate_legacy_to_prefix(old_flags, new_flags)
        else:
            # No migration needed - only legacy prefix migration is supported
            _LOGGER.info("No migration needed - only legacy prefix migration is supported")
            return True

    async def _migrate_legacy_to_prefix(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate from legacy naming (no device prefix) to device prefix + friendly names.

        This migration includes ALL sensors (panel-level and circuit-level) since legacy
        installations need comprehensive migration to the new naming structure.

        Args:
            old_flags: {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: False}
            new_flags: {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        Returns:
            True if migration was successful, False otherwise

        """
        _LOGGER.info("Performing legacy to device prefix migration")

        try:
            _LOGGER.info("Starting legacy migration for config entry: %s", self.config_entry_id)

            # Get entity registry
            registry = er.async_get(self.hass)

            # Get device name for prefix
            coordinator_data = self.hass.data[DOMAIN][self.config_entry_id]
            coordinator = coordinator_data[COORDINATOR]
            device_name = coordinator.config_entry.data.get(
                "device_name", coordinator.config_entry.title
            )
            if not device_name:
                _LOGGER.error("No device name found for migration")
                return False

            sanitized_device_name = slugify(device_name)
            _LOGGER.info(
                "Using device name for migration: %s (sanitized: %s)",
                device_name,
                sanitized_device_name,
            )

            # Get entities for this config entry using HA helper
            config_entry_entities = er.async_entries_for_config_entry(
                registry, self.config_entry_id
            )

            _LOGGER.info(
                "Found %d entities for config_entry_id: %s",
                len(config_entry_entities),
                self.config_entry_id,
            )

            # Filter entities that need renaming (don't already have device prefix)
            entities_to_migrate = []
            for entity in config_entry_entities:
                object_id = entity.entity_id.split(".", 1)[1]
                _LOGGER.debug(
                    "Checking entity %s: object_id='%s', prefix='%s_', starts_with=%s",
                    entity.entity_id,
                    object_id,
                    sanitized_device_name,
                    object_id.startswith(f"{sanitized_device_name}_"),
                )
                if not object_id.startswith(f"{sanitized_device_name}_"):
                    entities_to_migrate.append(entity)
                    _LOGGER.debug("Found entity to migrate: %s", entity.entity_id)
                else:
                    _LOGGER.debug("Skipping entity (already has prefix): %s", entity.entity_id)

            if not entities_to_migrate:
                _LOGGER.warning(
                    "No entities found to migrate for config entry: %s", self.config_entry_id
                )
                return True

            _LOGGER.info("Found %d entities to migrate", len(entities_to_migrate))

            # Remove duplicates from the migration list
            seen_entity_ids = set()
            unique_entities_to_migrate = []
            for entity in entities_to_migrate:
                if entity.entity_id not in seen_entity_ids:
                    seen_entity_ids.add(entity.entity_id)
                    unique_entities_to_migrate.append(entity)
                else:
                    _LOGGER.debug("Removing duplicate entity: %s", entity.entity_id)

            entities_to_migrate = unique_entities_to_migrate
            _LOGGER.info(
                "After deduplication: %d unique entities to migrate", len(entities_to_migrate)
            )

            # Migrate each entity (remove from list after processing to avoid duplicates)
            migrated_count = 0

            while entities_to_migrate:
                entity = entities_to_migrate.pop(0)  # Take first entity and remove it from list
                current_entity_id = entity.entity_id
                platform, object_id = current_entity_id.split(".", 1)

                # Safety check: skip if entity already has the device prefix
                if object_id.startswith(f"{sanitized_device_name}_"):
                    _LOGGER.debug("Skipping entity (already has prefix): %s", current_entity_id)
                    continue

                # Generate new entity ID with device prefix
                new_object_id = f"{sanitized_device_name}_{object_id}"
                new_entity_id = f"{platform}.{new_object_id}"

                _LOGGER.info(
                    "Entity migration: %s -> %s",
                    current_entity_id,
                    new_entity_id,
                )

                # Update the entity registry (statistics should be transferred automatically in HA 2023.4+)
                registry.async_update_entity(current_entity_id, new_entity_id=new_entity_id)
                migrated_count += 1

            _LOGGER.info("Migrated %d entities", migrated_count)

            # Migration completed - reload will be handled by integration startup

            return True

        except Exception as e:
            _LOGGER.error("Legacy migration failed: %s", e)
            return False
