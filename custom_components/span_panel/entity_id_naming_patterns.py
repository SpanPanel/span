"""Entity ID naming pattern migration utilities for Span Panel integration."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .const import COORDINATOR, DOMAIN, USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX

_LOGGER = logging.getLogger(__name__)


class EntityIdMigrationManager:
    """Manages entity ID migrations when naming patterns change."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the migration manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id

    def _get_device_name_from_registry(self, active_config_entry_id: str) -> str | None:
        """Get the device name from the device registry.

        This gets the actual device name as shown in the UI, which may be different
        from the config entry data if the user has renamed the device.

        Args:
            active_config_entry_id: The config entry ID to find the device for

        Returns:
            Device name from registry or None if not found

        """
        try:
            device_registry = dr.async_get(self.hass)

            # Get all devices for this config entry
            devices = dr.async_entries_for_config_entry(device_registry, active_config_entry_id)

            if not devices:
                _LOGGER.warning("No devices found for config entry: %s", active_config_entry_id)
                return None

            # For SPAN panels, there should typically be one main device
            # Get the first device (main panel device)
            main_device = devices[0]

            # Use name_by_user if available (user-customized name), otherwise fall back to name
            device_name = main_device.name_by_user or main_device.name

            _LOGGER.debug("Retrieved device name from registry: %s (name_by_user: %s, name: %s)",
                         device_name, main_device.name_by_user, main_device.name)
            return device_name

        except Exception as e:
            _LOGGER.error("Failed to get device name from registry: %s", e)
            return None

    async def migrate_entity_ids(
        self, old_flags: dict[str, bool], new_flags: dict[str, bool]
    ) -> bool:
        """Migrate entity IDs when naming patterns change.

        Args:
            old_flags: Previous configuration flags
                {USE_CIRCUIT_NUMBERS: bool, USE_DEVICE_PREFIX: bool}
            new_flags: New configuration flags
                {USE_CIRCUIT_NUMBERS: bool, USE_DEVICE_PREFIX: bool}

        Handles multiple types of migrations:
        1. Legacy migration (no device prefix -> device prefix)
        2. Naming pattern changes (friendly names <-> circuit numbers)
        3. Combined migrations (legacy + naming pattern changes)

        Returns:
            True if migration was successful, False otherwise

        """
        _LOGGER.info(
            "Starting entity ID migration: old_flags=%s, new_flags=%s",
            old_flags,
            new_flags,
        )

        # Determine what type of migration is needed
        old_use_device_prefix = old_flags.get(USE_DEVICE_PREFIX, False)
        new_use_device_prefix = new_flags.get(USE_DEVICE_PREFIX, False)
        old_use_circuit_numbers = old_flags.get(USE_CIRCUIT_NUMBERS, False)
        new_use_circuit_numbers = new_flags.get(USE_CIRCUIT_NUMBERS, False)

        # Check if legacy migration is needed (no device prefix -> device prefix)
        needs_legacy_migration = not old_use_device_prefix and new_use_device_prefix

        # Check if naming pattern migration is needed (circuit numbers change)
        needs_naming_migration = old_use_circuit_numbers != new_use_circuit_numbers

        if needs_legacy_migration and needs_naming_migration:
            # Combined migration: legacy + naming pattern change
            _LOGGER.info("Performing combined migration: legacy + naming pattern change")
            legacy_success = await self._migrate_legacy_to_prefix(old_flags, new_flags)
            if legacy_success:
                # After legacy migration, do naming pattern migration
                return await self.migrate_entity_ids_with_flags(new_use_circuit_numbers, new_use_device_prefix)
            return False
        elif needs_legacy_migration:
            # Legacy migration only
            _LOGGER.info("Performing legacy migration: no device prefix -> device prefix")
            return await self._migrate_legacy_to_prefix(old_flags, new_flags)
        elif needs_naming_migration:
            # Naming pattern migration only
            _LOGGER.info("Performing naming pattern migration: circuit numbers %s -> %s",
                       old_use_circuit_numbers, new_use_circuit_numbers)
            return await self.migrate_entity_ids_with_flags(new_use_circuit_numbers, new_use_device_prefix)
        else:
            # No migration needed
            _LOGGER.info("No migration needed - flags unchanged")
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

            # Find the active config entry ID in hass.data (might be different from stored ID due to reloads)
            domain_data = self.hass.data.get(DOMAIN, {})
            if not domain_data:
                _LOGGER.error("No %s data found in hass.data - integration not loaded", DOMAIN)
                return False

            # Verify the config entry ID exists in the loaded data
            if self.config_entry_id not in domain_data:
                _LOGGER.error("Config entry ID %s not found in loaded domain data", self.config_entry_id)
                available_entries = list(domain_data.keys())
                _LOGGER.debug("Available config entry IDs: %s", available_entries)
                return False

            active_config_entry_id = self.config_entry_id
            _LOGGER.debug("Using config entry ID: %s", active_config_entry_id)

            # Get device name from device registry (this is the name shown in UI)
            device_name = self._get_device_name_from_registry(active_config_entry_id)
            if not device_name:
                _LOGGER.error("Could not get device name from registry - migration aborted to prevent incorrect entity renaming")
                return False

            sanitized_device_name = slugify(device_name)
            _LOGGER.info(
                "Using device name for migration: %s (sanitized: %s)",
                device_name,
                sanitized_device_name,
            )

            # Use the active config entry ID we found
            effective_config_entry_id = active_config_entry_id

            # Get entities for this config entry using HA helper
            _LOGGER.debug("Attempting to get entities for config_entry_id: %s", effective_config_entry_id)

            # Check if config entry exists first
            config_entry = self.hass.config_entries.async_get_entry(effective_config_entry_id)
            if config_entry is None:
                _LOGGER.error(
                    "Config entry %s not found in config entries registry - migration aborted",
                    effective_config_entry_id
                )
                available_entries = [entry.entry_id for entry in self.hass.config_entries.async_entries()]
                _LOGGER.debug("Available config entry IDs: %s", available_entries)
                return False

            try:
                config_entry_entities = er.async_entries_for_config_entry(
                    registry, effective_config_entry_id
                )
            except KeyError:
                _LOGGER.error(
                    "Config entry ID %s not found in entity registry - migration aborted",
                    effective_config_entry_id
                )
                return False

            _LOGGER.info(
                "Found %d entities for config_entry_id: %s",
                len(config_entry_entities),
                effective_config_entry_id,
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
                    "No entities found to migrate for config entry: %s", active_config_entry_id
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

    async def migrate_entity_ids_with_flags(
        self, use_circuit_numbers: bool, use_device_prefix: bool
    ) -> bool:
        """Migrate entity IDs based on provided naming flags.

        This method migrates circuit, switch, and select entities to use the specified
        naming pattern while preserving unique IDs for statistics continuity.

        Args:
            use_circuit_numbers: Whether to use circuit numbers in entity IDs
            use_device_prefix: Whether to include device prefix in entity IDs

        Returns:
            True if migration was successful, False otherwise

        """
        _LOGGER.info(
            "Starting entity ID migration with flags: use_circuit_numbers=%s, use_device_prefix=%s",
            use_circuit_numbers,
            use_device_prefix,
        )

        try:
            # Get entity registry
            registry = er.async_get(self.hass)

            # Find the active config entry ID in hass.data (might be different from stored ID due to reloads)
            domain_data = self.hass.data.get(DOMAIN, {})
            if not domain_data:
                _LOGGER.error("No %s data found in hass.data - integration not loaded", DOMAIN)
                return False

            # Verify the config entry ID exists in the loaded data
            if self.config_entry_id not in domain_data:
                _LOGGER.error("Config entry ID %s not found in loaded domain data", self.config_entry_id)
                available_entries = list(domain_data.keys())
                _LOGGER.debug("Available config entry IDs: %s", available_entries)
                return False

            active_config_entry_id = self.config_entry_id
            _LOGGER.debug("Using config entry ID: %s", active_config_entry_id)

            # Get device name from device registry (this is the name shown in UI)
            device_name = self._get_device_name_from_registry(active_config_entry_id)
            if not device_name:
                _LOGGER.error("Could not get device name from registry - migration aborted to prevent incorrect entity renaming")
                return False

            sanitized_device_name = slugify(device_name)
            _LOGGER.info(
                "Using device name for migration: %s (sanitized: %s)",
                device_name,
                sanitized_device_name,
            )

            # Get entities for this config entry
            config_entry_entities = er.async_entries_for_config_entry(
                registry, active_config_entry_id
            )

            _LOGGER.info(
                "Found %d entities for config_entry_id: %s",
                len(config_entry_entities),
                active_config_entry_id,
            )

            # Filter entities that need migration (circuits, switches, selects)
            entities_to_migrate = []
            for entity in config_entry_entities:
                if self._should_migrate_entity(entity, sanitized_device_name):
                    entities_to_migrate.append(entity)
                    _LOGGER.debug("Found entity to migrate: %s", entity.entity_id)

            if not entities_to_migrate:
                _LOGGER.warning(
                    "No entities found to migrate for config entry: %s", active_config_entry_id
                )
                return True

            _LOGGER.info("Found %d entities to migrate", len(entities_to_migrate))

            # Migrate each entity
            migrated_count = 0
            for entity in entities_to_migrate:
                new_entity_id = self._construct_new_entity_id(
                    entity, use_circuit_numbers, use_device_prefix, sanitized_device_name
                )

                if new_entity_id and new_entity_id != entity.entity_id:
                    _LOGGER.info(
                        "Entity migration: %s -> %s",
                        entity.entity_id,
                        new_entity_id,
                    )
                    registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)
                    migrated_count += 1
                else:
                    _LOGGER.debug("Skipping entity (no change needed): %s", entity.entity_id)

            _LOGGER.info("Migrated %d entities", migrated_count)
            return True

        except Exception as e:
            _LOGGER.error("Entity ID migration with flags failed: %s", e)
            return False

    def _should_migrate_entity(self, entity: er.RegistryEntry, sanitized_device_name: str) -> bool:
        """Determine if an entity should be migrated based on its unique ID pattern.

        Args:
            entity: The entity registry entry
            sanitized_device_name: The sanitized device name for prefix checking

        Returns:
            True if the entity should be migrated, False otherwise

        """
        unique_id = entity.unique_id
        if not unique_id:
            return False

        # Parse unique ID to determine entity type
        # Pattern: span_{serial}_{circuit_id}_{suffix} or span_{serial}_{suffix}
        parts = unique_id.split("_", 2)  # Split into max 3 parts
        if len(parts) < 3 or parts[0] != "span":
            return False

        # Exclude unmapped circuits (they should not be migrated)
        if "unmapped_tab" in unique_id:
            return False

        # Check if this is a circuit entity (has circuit_id in unique_id)
        # Circuit entities have pattern: span_{serial}_{circuit_id}_{suffix}
        # where circuit_id is typically a UUID (longer than 8 chars)
        if len(parts) >= 3:
            potential_circuit_id = parts[2].split("_")[0]  # Get first part after serial
            # Circuit IDs are typically UUIDs (32+ chars) or short identifiers
            if len(potential_circuit_id) > 8:  # Likely a UUID circuit_id
                return True

        # Check for switch entities: span_{serial}_relay_{circuit_id}
        if "relay" in unique_id:
            return True

        # Check for select entities: span_{serial}_select_{select_id}
        if "select" in unique_id:
            return True

        return False

    def _construct_new_entity_id(
        self,
        entity: er.RegistryEntry,
        use_circuit_numbers: bool,
        use_device_prefix: bool,
        sanitized_device_name: str,
    ) -> str | None:
        """Construct new entity ID based on naming flags.

        Args:
            entity: The entity registry entry
            use_circuit_numbers: Whether to use circuit numbers
            use_device_prefix: Whether to include device prefix
            sanitized_device_name: The sanitized device name

        Returns:
            New entity ID or None if construction fails

        """
        try:
            unique_id = entity.unique_id
            if not unique_id:
                return None

            # Parse unique ID to extract components
            parts = unique_id.split("_", 2)
            if len(parts) < 3 or parts[0] != "span":
                return None

            remaining = parts[2]

            # Determine entity type and extract circuit info
            if "relay" in unique_id:
                # Switch entity: span_{serial}_relay_{circuit_id}
                circuit_id = remaining.split("_", 1)[1] if "_" in remaining else remaining
                return self._construct_switch_entity_id(
                    entity.platform, circuit_id, use_circuit_numbers, use_device_prefix, sanitized_device_name, entity
                )
            elif "select" in unique_id and not any(x in unique_id for x in ["circuit", "priority"]):
                # Panel-level select entity: span_{serial}_select_{select_id}
                select_id = remaining.split("_", 1)[1] if "_" in remaining else remaining
                return self._construct_select_entity_id(
                    entity.platform, select_id, use_circuit_numbers, use_device_prefix, sanitized_device_name
                )
            else:
                # Circuit entity or circuit-related select: span_{serial}_{circuit_id}_{suffix}
                circuit_parts = remaining.split("_", 1)
                if len(circuit_parts) >= 2:
                    circuit_id = circuit_parts[0]
                    suffix = circuit_parts[1]

                    # Check if this is a circuit-related select entity
                    if "select" in suffix or "priority" in suffix:
                        # This is a circuit-related select entity
                        return self._construct_circuit_select_entity_id(
                            entity.platform, circuit_id, suffix, use_circuit_numbers, use_device_prefix, sanitized_device_name, entity
                        )
                    else:
                        # Regular circuit entity
                        return self._construct_circuit_entity_id(
                            entity.platform, circuit_id, suffix, use_circuit_numbers, use_device_prefix, sanitized_device_name, entity
                        )

            return None

        except Exception as e:
            _LOGGER.error("Failed to construct new entity ID for %s: %s", entity.entity_id, e)
            return None

    def _construct_circuit_entity_id(
        self,
        platform: str,
        circuit_id: str,
        suffix: str,
        use_circuit_numbers: bool,
        use_device_prefix: bool,
        sanitized_device_name: str,
        entity: er.RegistryEntry,
    ) -> str:
        """Construct entity ID for circuit entities.

        Args:
            platform: The platform name (sensor, switch, select)
            circuit_id: The circuit ID
            suffix: The entity suffix
            use_circuit_numbers: Whether to use circuit numbers
            use_device_prefix: Whether to include device prefix
            sanitized_device_name: The sanitized device name
            entity: The entity registry entry to get tabs attribute

        Returns:
            Constructed entity ID

        """
        parts = []

        if use_device_prefix:
            parts.append(sanitized_device_name)

        if use_circuit_numbers:
            # Get actual circuit numbers from coordinator data
            tabs = self._get_circuit_tabs_from_coordinator(circuit_id)
            if tabs:
                if len(tabs) == 2:
                    # 240V circuit - use both tab numbers
                    sorted_tabs = sorted(tabs)
                    parts.append(f"circuit_{sorted_tabs[0]}_{sorted_tabs[1]}")
                elif len(tabs) == 1:
                    # 120V circuit - use single tab number
                    parts.append(f"circuit_{tabs[0]}")
                else:
                    # Fallback to circuit_id
                    parts.append(f"circuit_{circuit_id}")
            else:
                # Fallback to circuit_id
                parts.append(f"circuit_{circuit_id}")
        else:
            # Use circuit friendly name - get from entity state or coordinator
            circuit_name = self._get_circuit_friendly_name(entity, circuit_id)
            if circuit_name:
                parts.append(slugify(circuit_name))
            else:
                # Fallback to circuit_id if we can't get the friendly name
                parts.append(circuit_id)

        if suffix and not parts[-1].endswith(f"_{suffix}"):
            parts.append(suffix)

        return f"{platform}.{'_'.join(parts)}"

    def _construct_circuit_select_entity_id(
        self,
        platform: str,
        circuit_id: str,
        suffix: str,
        use_circuit_numbers: bool,
        use_device_prefix: bool,
        sanitized_device_name: str,
        entity: er.RegistryEntry,
    ) -> str:
        """Construct entity ID for circuit-related select entities.

        Args:
            platform: The platform name
            circuit_id: The circuit ID
            suffix: The entity suffix (e.g., "circuit_priority")
            use_circuit_numbers: Whether to use circuit numbers
            use_device_prefix: Whether to include device prefix
            sanitized_device_name: The sanitized device name
            entity: The entity registry entry to get tabs attribute

        Returns:
            Constructed entity ID

        """
        parts = []

        if use_device_prefix:
            parts.append(sanitized_device_name)

        if use_circuit_numbers:
            # Get actual circuit numbers from coordinator data
            tabs = self._get_circuit_tabs_from_coordinator(circuit_id)
            if tabs:
                if len(tabs) == 2:
                    # 240V circuit - use both tab numbers
                    sorted_tabs = sorted(tabs)
                    parts.append(f"circuit_{sorted_tabs[0]}_{sorted_tabs[1]}")
                elif len(tabs) == 1:
                    # 120V circuit - use single tab number
                    parts.append(f"circuit_{tabs[0]}")
                else:
                    # Fallback to circuit_id
                    parts.append(f"circuit_{circuit_id}")
            else:
                # Fallback to circuit_id
                parts.append(f"circuit_{circuit_id}")
        else:
            # Use circuit friendly name
            circuit_name = self._get_circuit_friendly_name(entity, circuit_id)
            if circuit_name:
                parts.append(slugify(circuit_name))
            else:
                # Fallback to circuit_id if we can't get the friendly name
                parts.append(circuit_id)

        # Add the select type (e.g., "priority")
        if suffix and not parts[-1].endswith(f"_{suffix}"):
            parts.append(suffix)

        return f"{platform}.{'_'.join(parts)}"

    def _construct_switch_entity_id(
        self,
        platform: str,
        circuit_id: str,
        use_circuit_numbers: bool,
        use_device_prefix: bool,
        sanitized_device_name: str,
        entity: er.RegistryEntry,
    ) -> str:
        """Construct entity ID for switch entities.

        Args:
            platform: The platform name
            circuit_id: The circuit ID
            use_circuit_numbers: Whether to use circuit numbers
            use_device_prefix: Whether to include device prefix
            sanitized_device_name: The sanitized device name
            entity: The entity registry entry to get tabs attribute

        Returns:
            Constructed entity ID

        """
        parts = []

        if use_device_prefix:
            parts.append(sanitized_device_name)

        if use_circuit_numbers:
            # Get actual circuit numbers from coordinator data
            tabs = self._get_circuit_tabs_from_coordinator(circuit_id)
            if tabs:
                if len(tabs) == 2:
                    # 240V circuit - use both tab numbers
                    sorted_tabs = sorted(tabs)
                    parts.append(f"circuit_{sorted_tabs[0]}_{sorted_tabs[1]}")
                elif len(tabs) == 1:
                    # 120V circuit - use single tab number
                    parts.append(f"circuit_{tabs[0]}")
                else:
                    # Fallback to circuit_id
                    parts.append(f"circuit_{circuit_id}")
            else:
                # Fallback to circuit_id
                parts.append(f"circuit_{circuit_id}")
        else:
            # Use circuit friendly name
            circuit_name = self._get_circuit_friendly_name(entity, circuit_id)
            if circuit_name:
                parts.append(slugify(circuit_name))
            else:
                # Fallback to circuit_id if we can't get the friendly name
                parts.append(circuit_id)

        parts.append("relay")

        return f"{platform}.{'_'.join(parts)}"

    def _construct_select_entity_id(
        self,
        platform: str,
        select_id: str,
        use_circuit_numbers: bool,
        use_device_prefix: bool,
        sanitized_device_name: str,
    ) -> str:
        """Construct entity ID for select entities.

        Args:
            platform: The platform name
            select_id: The select ID
            use_circuit_numbers: Whether to use circuit numbers
            use_device_prefix: Whether to include device prefix
            sanitized_device_name: The sanitized device name

        Returns:
            Constructed entity ID

        """
        parts = []

        if use_device_prefix:
            parts.append(sanitized_device_name)

        # Check if this is a circuit-related select entity
        # Circuit-related selects have pattern: span_{serial}_{circuit_id}_select_{select_type}
        # or span_{serial}_{circuit_id}_{select_type}
        if "circuit" in select_id or "priority" in select_id:
            # This is a circuit-related select entity
            # Extract circuit_id from select_id if it contains one
            # For circuit-related selects, we need to get the circuit info
            # The select_id might be something like "0dad2f16cd514812ae1807b0457d473e_circuit_priority"
            if "_" in select_id:
                potential_circuit_id = select_id.split("_")[0]
                # Check if this looks like a circuit ID (UUID format)
                if len(potential_circuit_id) > 8:
                    # This is a circuit-related select, use circuit naming pattern
                    tabs = self._get_circuit_tabs_from_coordinator(potential_circuit_id)
                    if tabs:
                        if len(tabs) == 2:
                            # 240V circuit - use both tab numbers
                            sorted_tabs = sorted(tabs)
                            parts.append(f"circuit_{sorted_tabs[0]}_{sorted_tabs[1]}")
                        elif len(tabs) == 1:
                            # 120V circuit - use single tab number
                            parts.append(f"circuit_{tabs[0]}")
                        else:
                            # Fallback to circuit_id
                            parts.append(f"circuit_{potential_circuit_id}")
                    else:
                        # Fallback to circuit_id
                        parts.append(f"circuit_{potential_circuit_id}")

                    # Add the select type (e.g., "priority")
                    select_type = select_id.split("_", 1)[1] if "_" in select_id else select_id
                    parts.append(select_type)
                else:
                    # Not a circuit-related select, use simple naming
                    parts.append("select")
                    parts.append(select_id)
            else:
                # Not a circuit-related select, use simple naming
                parts.append("select")
                parts.append(select_id)
        else:
            # Not a circuit-related select, use simple naming
            parts.append("select")
            parts.append(select_id)

        return f"{platform}.{'_'.join(parts)}"

    def _get_circuit_tabs_from_coordinator(self, circuit_id: str) -> list[int] | None:
        """Get circuit tabs from coordinator data.

        Args:
            circuit_id: The circuit ID

        Returns:
            List of tab numbers or None if not found

        """
        try:
            # Find the active config entry ID in hass.data (might be different from stored ID due to reloads)
            domain_data = self.hass.data.get(DOMAIN, {})
            if not domain_data:
                _LOGGER.warning("No %s data found in hass.data - returning None for circuit tabs", DOMAIN)
                return None

            # Verify the config entry ID exists in the loaded data
            if self.config_entry_id not in domain_data:
                _LOGGER.warning("Config entry ID %s not found in loaded domain data - returning None for circuit tabs", self.config_entry_id)
                available_entries = list(domain_data.keys())
                _LOGGER.debug("Available config entry IDs: %s", available_entries)
                return None

            active_config_entry_id = self.config_entry_id

            # Get circuit tabs from coordinator data
            coordinator_data = self.hass.data[DOMAIN][active_config_entry_id]
            coordinator = coordinator_data[COORDINATOR]
            span_panel = coordinator.data

            # Look up circuit in span_panel data
            circuit = span_panel.circuits.get(circuit_id)
            if circuit and circuit.tabs:
                return circuit.tabs

            return None

        except Exception as e:
            _LOGGER.debug("Failed to get circuit tabs for %s: %s", circuit_id, e)
            return None

    def _get_circuit_friendly_name(self, entity: er.RegistryEntry, circuit_id: str) -> str | None:
        """Get the circuit friendly name from entity state or coordinator data.

        Args:
            entity: The entity registry entry
            circuit_id: The circuit ID

        Returns:
            Circuit friendly name or None if not found

        """
        try:
            # Find the active config entry ID in hass.data (might be different from stored ID due to reloads)
            domain_data = self.hass.data.get(DOMAIN, {})
            if not domain_data:
                _LOGGER.warning("No %s data found in hass.data - returning None for circuit name", DOMAIN)
                return None

            # Verify the config entry ID exists in the loaded data
            if self.config_entry_id not in domain_data:
                _LOGGER.warning("Config entry ID %s not found in loaded domain data - returning None for circuit name", self.config_entry_id)
                available_entries = list(domain_data.keys())
                _LOGGER.debug("Available config entry IDs: %s", available_entries)
                return None

            active_config_entry_id = self.config_entry_id

            # Get circuit name from coordinator data
            coordinator_data = self.hass.data[DOMAIN][active_config_entry_id]
            coordinator = coordinator_data[COORDINATOR]
            span_panel = coordinator.data

            # Look up circuit in span_panel data
            circuit = span_panel.circuits.get(circuit_id)
            if circuit and circuit.name:
                return circuit.name

            return None

        except Exception as e:
            _LOGGER.debug("Failed to get circuit friendly name for %s: %s", circuit_id, e)
            return None
