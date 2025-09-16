"""Entity ID naming pattern migration utilities for Span Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import COORDINATOR, DOMAIN, USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from .helpers import (
    construct_multi_tab_entity_id_from_key,
    is_panel_level_sensor_key,
    parse_tabs_attribute,
)

_LOGGER = logging.getLogger(__name__)


class EntityIdMigrationManager:
    """Manages entity ID migrations for sensors when naming patterns change."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the migration manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id

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

    async def _generate_new_entity_id(
        self,
        sensor_key: str,
        sensor_config: dict[str, Any],
        coordinator: Any,
        span_panel: Any,
        flags: dict[str, bool],
    ) -> str | None:
        """Generate new entity ID using helpers with specified flags.

        Args:
            sensor_key: Sensor key like "solar_inverter_instant_power"
            sensor_config: Sensor configuration dictionary
            coordinator: Coordinator instance
            span_panel: Span panel data
            flags: Configuration flags to use

        Returns:
            New entity ID or None if generation failed

        """
        # Check if sensor has tabs attribute that we can use for circuit-based naming
        tabs_attr = sensor_config.get("attributes", {}).get("tabs")
        if tabs_attr and flags.get(USE_CIRCUIT_NUMBERS, False):
            # Parse tabs attribute to get tab numbers
            tab_numbers = parse_tabs_attribute(tabs_attr)
            if tab_numbers:
                _LOGGER.debug(
                    "Using tabs attribute '%s' for entity ID construction: %s",
                    tabs_attr,
                    tab_numbers,
                )
                # Use the existing helper with tab numbers
                return construct_multi_tab_entity_id_from_key(
                    coordinator=coordinator,
                    span_panel=span_panel,
                    platform="sensor",
                    sensor_key=sensor_key,
                    sensor_config=sensor_config,
                    unique_id=None,  # Don't check registry during migration
                )

        # Temporarily update coordinator flags for helper functions
        original_flags = {}
        config_entry = coordinator.config_entry

        try:
            # Save original flags
            for flag_key in [USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX]:
                original_flags[flag_key] = config_entry.options.get(flag_key, False)

            # Set new flags temporarily
            config_entry.options.update(flags)

            # Use the new helper to generate entity ID
            new_entity_id = construct_multi_tab_entity_id_from_key(
                coordinator=coordinator,
                span_panel=span_panel,
                platform="sensor",
                sensor_key=sensor_key,
                sensor_config=sensor_config,
                unique_id=None,  # Don't check registry during migration
            )

            return new_entity_id

        finally:
            # Restore original flags
            config_entry.options.update(original_flags)

    async def _is_entity_id_customized(
        self,
        sensor_key: str,
        sensor_config: dict[str, Any],
        coordinator: Any,
        span_panel: Any,
        old_flags: dict[str, bool],
    ) -> bool:
        """Check if entity ID has been customized by user.

        Args:
            sensor_key: Sensor key
            sensor_config: Sensor configuration dictionary
            coordinator: Coordinator instance
            span_panel: Span panel data
            old_flags: Previous configuration flags

        Returns:
            True if entity ID appears to be customized by user

        """
        current_entity_id = sensor_config.get("entity_id")
        if not current_entity_id:
            return False

        # Generate what the entity ID should be under old flags
        expected_entity_id = await self._generate_new_entity_id(
            sensor_key, sensor_config, coordinator, span_panel, old_flags
        )

        # If expected_entity_id is None, we can't compare properly
        if expected_entity_id is None:
            return False

        # If current doesn't match expected, it's likely customized
        # Ensure we're comparing strings explicitly for type safety
        current_id_str = str(current_entity_id)
        expected_id_str = str(expected_entity_id)
        return current_id_str != expected_id_str

    async def _migrate_non_legacy_patterns(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate between non-legacy naming patterns.

        This handles migrations between different non-legacy patterns, such as:
        - Device prefix without circuit numbers -> Device prefix with circuit numbers
        - Device prefix with circuit numbers -> Device prefix without circuit numbers

        Args:
            old_flags: Configuration flags before the change
            new_flags: Configuration flags after the change

        Returns:
            True if migration was successful, False otherwise

        """
        _LOGGER.info("Performing non-legacy pattern migration")

        try:
            # Get the synthetic sensor manager for this config entry
            if "ha_synthetic_sensors" not in self.hass.data:
                _LOGGER.warning("No synthetic sensors integration found")
                return True

            sensor_managers = self.hass.data["ha_synthetic_sensors"].get("sensor_managers", {})
            sensor_manager = sensor_managers.get(self.config_entry_id)

            if not sensor_manager:
                _LOGGER.warning(
                    "No sensor manager found for config entry: %s", self.config_entry_id
                )
                return False

            # Get coordinator and span_panel data
            coordinator_data = self.hass.data[DOMAIN][self.config_entry_id]
            coordinator = coordinator_data[COORDINATOR]
            span_panel = coordinator.data

            # Get all sensors from the manager
            sensors_data = sensor_manager.sensors
            if not sensors_data:
                _LOGGER.info("No sensors found to migrate")
                return True

            migrated_count = 0
            skipped_count = 0

            for sensor_key, sensor_config in sensors_data.items():
                # Skip panel-level sensors (they don't change with circuit number patterns)
                if is_panel_level_sensor_key(sensor_key):
                    _LOGGER.debug("Skipping panel-level sensor: %s", sensor_key)
                    skipped_count += 1
                    continue

                # Check if entity ID is customized (user has manually changed it)
                current_entity_id = sensor_config.get("entity_id")
                if not current_entity_id:
                    _LOGGER.debug("No entity_id found for sensor: %s", sensor_key)
                    continue

                if await self._is_entity_id_customized(
                    sensor_key, sensor_config, coordinator, span_panel, old_flags
                ):
                    _LOGGER.debug("Skipping customized entity ID: %s", current_entity_id)
                    skipped_count += 1
                    continue

                # Generate new entity ID based on new flags
                new_entity_id = await self._generate_new_entity_id(
                    sensor_key, sensor_config, coordinator, span_panel, new_flags
                )
                if not new_entity_id or new_entity_id == current_entity_id:
                    _LOGGER.debug("No change needed for sensor: %s", sensor_key)
                    continue

                # Update the sensor configuration
                _LOGGER.info("Migrating sensor: %s -> %s", current_entity_id, new_entity_id)
                sensor_config["entity_id"] = new_entity_id
                migrated_count += 1

            # Save the changes
            if migrated_count > 0:
                await sensor_manager.modify()
                _LOGGER.info(
                    "Non-legacy migration completed: %d migrated, %d skipped",
                    migrated_count,
                    skipped_count,
                )
            else:
                _LOGGER.info("Non-legacy migration completed: no changes needed")

            return True

        except Exception as e:
            _LOGGER.error("Non-legacy migration failed: %s", e, exc_info=True)
            return False

    async def migrate_synthetic_entities(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate synthetic sensor entity IDs based on old and new configuration flags.

        This method determines the appropriate migration strategy based on the flag changes
        and delegates to the specific migration method.

        Args:
            old_flags: Configuration flags before the change
            new_flags: Configuration flags after the change

        Returns:
            bool: True if migration succeeded, False otherwise

        """
        _LOGGER.info("Starting entity migration with flags: %s -> %s", old_flags, new_flags)

        # Check if this is a legacy to device prefix migration
        old_use_device_prefix = old_flags.get(USE_DEVICE_PREFIX, False)
        new_use_device_prefix = new_flags.get(USE_DEVICE_PREFIX, False)

        _LOGGER.info(
            "Migration check: old_use_device_prefix=%s, new_use_device_prefix=%s",
            old_use_device_prefix,
            new_use_device_prefix,
        )

        if not old_use_device_prefix and new_use_device_prefix:
            # This is a legacy to device prefix migration
            _LOGGER.info("Performing legacy to device prefix migration")
            return await self._migrate_legacy_to_prefix(old_flags, new_flags)
        elif old_use_device_prefix and new_use_device_prefix:
            # This is a non-legacy pattern migration (e.g., changing circuit number usage)
            _LOGGER.info("Performing non-legacy pattern migration")
            return await self._migrate_non_legacy_patterns(old_flags, new_flags)
        else:
            # For other migration types, we would add additional methods here
            _LOGGER.info(
                "No specific migration needed for flag change: %s -> %s", old_flags, new_flags
            )
            return True
