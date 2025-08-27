"""Entity ID naming pattern migration utilities for Span Panel integration."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

from .const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from .helpers import (
    construct_multi_tab_entity_id_from_key,
    parse_tabs_attribute,
)

_LOGGER = logging.getLogger(__name__)


class EntityIdMigrationManager:
    """Manages entity ID migrations for synthetic sensors when naming patterns change."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the migration manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id

    async def migrate_synthetic_entities(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate synthetic sensor entity IDs when naming patterns change.

        This only handles legacy migration (no device prefix -> device prefix + friendly names).
        Non-legacy migrations are not supported since users choose their pattern during setup.

        Args:
            old_flags: Previous configuration flags {USE_CIRCUIT_NUMBERS: bool, USE_DEVICE_PREFIX: bool}
            new_flags: New configuration flags {USE_CIRCUIT_NUMBERS: bool, USE_DEVICE_PREFIX: bool}

        Returns:
            True if migration was successful, False otherwise

        """
        _LOGGER.info(
            "Starting synthetic entity migration: old_flags=%s, new_flags=%s",
            old_flags,
            new_flags,
        )

        # Only handle legacy migration: no device prefix -> device prefix + friendly names
        old_use_device_prefix = old_flags.get(USE_DEVICE_PREFIX, False)
        if not old_use_device_prefix:
            return await self._migrate_legacy_to_prefix(old_flags, new_flags)
        else:
            _LOGGER.info("Skipping migration: non-legacy patterns not supported")
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
            # Get sensor manager from hass data
            sensor_manager = (
                self.hass.data.get("ha_synthetic_sensors", {})
                .get("sensor_managers", {})
                .get(self.config_entry_id)
            )
            if not sensor_manager:
                _LOGGER.error("Sensor manager not found for config entry %s", self.config_entry_id)
                return False

            # Export current sensor configurations
            current_sensors = await sensor_manager.export()
            if not current_sensors or "sensors" not in current_sensors:
                _LOGGER.warning("No sensors found to migrate")
                return True

            # Get coordinator and span panel data
            coordinator = self.hass.data["span_panel"][self.config_entry_id]
            span_panel = coordinator.data

            # Migrate each sensor
            migrated_sensors = current_sensors.copy()
            entity_id_changes = {}

            for sensor_key, sensor_config in current_sensors["sensors"].items():
                current_entity_id = sensor_config.get("entity_id")
                if not current_entity_id:
                    continue

                # Generate new entity ID using helpers with new flags
                new_entity_id = await self._generate_new_entity_id(
                    sensor_key, sensor_config, coordinator, span_panel, new_flags
                )

                if new_entity_id and new_entity_id != current_entity_id:
                    _LOGGER.info(
                        "Legacy migration: %s -> %s",
                        current_entity_id,
                        new_entity_id,
                    )
                    migrated_sensors["sensors"][sensor_key]["entity_id"] = new_entity_id
                    entity_id_changes[current_entity_id] = new_entity_id

            # Update cross-references throughout the YAML
            if entity_id_changes:
                self._update_cross_references(migrated_sensors, entity_id_changes)

            # Store the updated configuration
            await sensor_manager.modify(migrated_sensors)

            _LOGGER.info("Legacy migration completed successfully")
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
                    migration_mode=True,
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
                migration_mode=True,
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

    def _update_cross_references(
        self, yaml_data: dict[str, Any], entity_id_changes: dict[str, str]
    ) -> None:
        """Update all cross-references to changed entity IDs throughout the YAML document.

        Used when migrating from no device prefix to device prefix friendly entity_id
        From pre 1.0.4 to 1.0.4+

        Args:
            yaml_data: Complete YAML configuration dictionary
            entity_id_changes: Dictionary mapping old entity IDs to new entity IDs

        """

        def update_recursive(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {key: update_recursive(value) for key, value in obj.items()}
            elif isinstance(obj, list):
                return [update_recursive(item) for item in obj]
            elif isinstance(obj, str):
                # Replace any old entity ID references with new ones (exact matches only)
                for old_id, new_id in entity_id_changes.items():
                    # Use word boundaries to ensure exact entity ID matches only
                    pattern = r"\b" + re.escape(old_id) + r"\b"
                    obj = re.sub(pattern, new_id, obj)
                return obj
            else:
                return obj

        # Update the entire YAML structure
        for key, value in yaml_data.items():
            yaml_data[key] = update_recursive(value)

        _LOGGER.debug("Updated cross-references for %d entity ID changes", len(entity_id_changes))
