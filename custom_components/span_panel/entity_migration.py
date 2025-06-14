"""Entity registry migration utilities for SPAN Panel integration."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import EntityNamingPattern

_LOGGER = logging.getLogger(__name__)


class EntityMigrationManager:
    """Manages entity registry migrations for naming pattern changes."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the migration manager."""
        self._hass = hass
        self._config_entry_id = config_entry_id
        self._entity_registry = er.async_get(hass)
        self._circuit_data: dict[str, tuple[str, int]] = {}  # circuit_id -> (name, number)

    async def migrate_entities(
        self,
        from_pattern: EntityNamingPattern,
        to_pattern: EntityNamingPattern,
    ) -> bool:
        """Migrate entities from one naming pattern to another.

        This method renames existing entities in the registry to match
        what the integration would create with the new naming pattern flags.
        After this, the integration will reload and create entities with
        the correct naming pattern.

        Args:
            from_pattern: The current entity naming pattern
            to_pattern: The target entity naming pattern

        Returns:
            True if migration was successful, False otherwise

        """
        if from_pattern == to_pattern:
            _LOGGER.debug("No migration needed - patterns are the same")
            return True

        _LOGGER.info(
            "Starting entity migration from %s to %s",
            from_pattern.value,
            to_pattern.value,
        )

        try:
            # Get current circuit data for transformation
            await self._load_circuit_data()

            # Get all entities for this integration
            integration_entities = self._get_integration_entities()

            if not integration_entities:
                _LOGGER.warning("No entities found for migration")
                return True

            _LOGGER.debug(
                "Found %d circuit-level entities for migration:",
                len(integration_entities),
            )
            synthetic_count = 0
            for entity in integration_entities:
                is_synthetic = entity.unique_id and "synthetic" in entity.unique_id
                if is_synthetic:
                    synthetic_count += 1
                    _LOGGER.info(
                        "  - SYNTHETIC: %s (unique_id: %s)",
                        entity.entity_id,
                        entity.unique_id,
                    )
                else:
                    _LOGGER.debug("  - %s (unique_id: %s)", entity.entity_id, entity.unique_id)

            _LOGGER.info(
                "Found %d synthetic entities out of %d total entities",
                synthetic_count,
                len(integration_entities),
            )

            # Build mapping from old to new entity IDs
            entity_mapping = self._build_entity_mapping(
                integration_entities, from_pattern, to_pattern
            )

            if not entity_mapping:
                _LOGGER.info("No entity renames needed for pattern change")
                return True

            # Log synthetic entity mappings specifically
            synthetic_mappings = {
                old: new
                for old, new in entity_mapping.items()
                if "circuit_" in old
                and "_" in old.split("circuit_")[1]
                and "_" in old.split("circuit_")[1].split("_", 1)[1]
            }
            if synthetic_mappings:
                _LOGGER.info("Synthetic entity mappings:")
                for old_id, new_id in synthetic_mappings.items():
                    _LOGGER.info("  %s -> %s", old_id, new_id)

            # Apply the entity ID changes
            success_count = 0
            total_count = len(entity_mapping)

            for old_entity_id, new_entity_id in entity_mapping.items():
                if await self._update_entity_id(old_entity_id, new_entity_id):
                    success_count += 1
                    _LOGGER.debug(
                        "Migrated entity: %s -> %s",
                        old_entity_id,
                        new_entity_id,
                    )

            success = success_count == total_count

            if success:
                _LOGGER.info(
                    "Entity migration completed successfully: %d/%d entities renamed",
                    success_count,
                    total_count,
                )
            else:
                _LOGGER.error(
                    "Some entity migrations failed: %d/%d successful",
                    success_count,
                    total_count,
                )

            return success

        except Exception as e:
            _LOGGER.error("Entity migration failed with error: %s", e)
            return False

    async def _load_circuit_data(self) -> None:
        """Load current circuit data from the coordinator."""
        try:
            # Get the coordinator data to access circuit information
            from .const import COORDINATOR, DOMAIN

            domain_data = self._hass.data.get(DOMAIN, {})
            entry_data = domain_data.get(self._config_entry_id, {})
            coordinator = entry_data.get(COORDINATOR)

            if coordinator and coordinator.data:
                span_panel = coordinator.data
                for circuit_id, circuit in span_panel.circuits.items():
                    # Get the circuit number (tab position)
                    circuit_number = circuit.tabs[0] if circuit.tabs else circuit_id
                    self._circuit_data[circuit_id] = (circuit.name, circuit_number)

                _LOGGER.debug("Loaded circuit data for %d circuits", len(self._circuit_data))
            else:
                _LOGGER.warning("Could not load circuit data - coordinator not available")

        except Exception as e:
            _LOGGER.warning("Failed to load circuit data: %s", e)

    def _get_integration_entities(self) -> list[er.RegistryEntry]:
        """Get circuit-level entities belonging to this SPAN Panel integration.

        Panel-level entities (like current power, status sensors) are excluded
        since naming patterns only apply to circuit-level entities.
        """
        all_entities = [
            entity
            for entity in self._entity_registry.entities.values()
            if entity.config_entry_id == self._config_entry_id
        ]

        # Filter to only include circuit-level entities
        circuit_entities: list[er.RegistryEntry] = []
        for entity in all_entities:
            if self._is_circuit_level_entity(entity):
                circuit_entities.append(entity)

        return circuit_entities

    def _is_circuit_level_entity(self, entity: er.RegistryEntry) -> bool:
        """Determine if an entity is a circuit-level entity that should be affected by naming patterns.

        Panel-level entities are excluded, including:
        - Panel power/energy sensors (current power, feed through power, main meter energy)
        - Panel status sensors (DSM state, relay state, run config, software version)
        - Hardware status sensors (door state, connectivity status)
        - Storage battery sensors (battery percentage)

        Circuit-level entities that ARE affected include:
        - Individual circuit sensors (power, energy_produced, energy_consumed)
        - Circuit switches (breaker control)
        - Circuit selects (priority)
        - Synthetic entities (solar inverters, etc.)
        """
        entity_id = entity.entity_id

        # Panel-level entity patterns to exclude
        panel_level_patterns = [
            # Panel power and energy sensors
            "_current_power",
            "_feed_through_power",
            "_main_meter_produced_energy",
            "_main_meter_consumed_energy",
            "_feed_through_produced_energy",
            "_feed_through_consumed_energy",
            # Panel status sensors
            "_current_run_config",
            "_dsm_grid_state",
            "_dsm_state",
            "_main_relay_state",
            "_software_version",
            # Storage battery sensors
            "_span_storage_battery_percentage",
            "_battery_percentage",
            # Binary sensors (connectivity and hardware status)
            "_door_state",
            "_ethernet_link",
            "_wi_fi_link",
            "_cellular_link",
        ]

        # Check if this is a panel-level entity
        for pattern in panel_level_patterns:
            if entity_id.endswith(pattern):
                return False

        # Additional check for panel-level entities that might have different naming
        # These are based on sensor keys from sensor.py and binary_sensor.py
        panel_sensor_keys = [
            # Panel power and energy sensors (PANEL_SENSORS)
            "instantgridpowerw",
            "feedthroughpowerw",
            "mainmeterenergy_producedenergywh",
            "mainmeterenergy_consumedenergywh",
            "feedthroughenergy_producedenergywh",
            "feedthroughenergy_consumedenergywh",
            # Panel status sensors (PANEL_DATA_STATUS_SENSORS)
            "currentrunconfig",
            "dsmgridstate",
            "dsmstate",
            "mainrelaystate",
            # Hardware status sensors (STATUS_SENSORS)
            "softwarever",
            # Storage battery sensors (STORAGE_BATTERY_SENSORS)
            "batterypercentage",
            # Binary sensors (BINARY_SENSORS)
            "doorstate",
            "eth0link",
            "wlanlink",
            "wwanlink",
        ]

        # Normalize entity ID for comparison (remove domain and convert to lowercase)
        object_id = entity_id.split(".", 1)[1].lower().replace("_", "").replace(".", "")

        for key in panel_sensor_keys:
            normalized_key = key.lower().replace("_", "").replace(".", "")
            if normalized_key in object_id:
                return False

        # Check for synthetic entities (solar inverters, etc.)
        # These have "synthetic" in their unique ID
        if entity.unique_id and "synthetic" in entity.unique_id:
            _LOGGER.debug(
                "Found synthetic entity: %s (unique_id: %s)",
                entity_id,
                entity.unique_id,
            )
            return True

        # If we get here, it's likely a circuit-level entity
        # Circuit-level entities typically include:
        # - Entities with circuit names or numbers in them
        # - Entities ending with _power, _energy_produced, _energy_consumed (but not panel-level ones)
        # - Switch entities for breaker control
        # - Select entities for priority
        # - Synthetic entities (solar inverters, etc.)

        return True

    def _build_entity_mapping(
        self,
        entities: list[er.RegistryEntry],
        from_pattern: EntityNamingPattern,
        to_pattern: EntityNamingPattern,
    ) -> dict[str, str]:
        """Build a mapping of old entity IDs to new entity IDs based on pattern change.

        This method analyzes the naming pattern differences and generates the
        new entity IDs that would be created with the target pattern.
        """
        entity_mapping: dict[str, str] = {}

        for entity in entities:
            new_entity_id = self._generate_new_entity_id(entity, from_pattern, to_pattern)

            if new_entity_id and new_entity_id != entity.entity_id:
                entity_mapping[entity.entity_id] = new_entity_id

        return entity_mapping

    def _generate_new_entity_id(
        self,
        entity: er.RegistryEntry,
        from_pattern: EntityNamingPattern,
        to_pattern: EntityNamingPattern,
    ) -> str | None:
        """Generate the new entity ID for an entity based on pattern transformation.

        This method transforms the existing entity ID based on the differences
        between the old and new naming patterns.
        """
        try:
            # Get the pattern transformation rules
            from_prefix, from_numbers = self._get_pattern_flags(from_pattern)
            to_prefix, to_numbers = self._get_pattern_flags(to_pattern)

            # If patterns are the same, no change needed
            if from_prefix == to_prefix and from_numbers == to_numbers:
                return None

            # Transform the entity ID based on pattern differences
            return self._transform_entity_id(
                entity.entity_id, from_prefix, to_prefix, from_numbers, to_numbers
            )

        except Exception as e:
            _LOGGER.warning(
                "Failed to generate new entity ID for %s: %s",
                entity.entity_id,
                e,
            )
            return None

    def _get_pattern_flags(self, pattern: EntityNamingPattern) -> tuple[bool, bool]:
        """Get the use_device_prefix and use_circuit_numbers flags for a pattern."""
        if pattern == EntityNamingPattern.FRIENDLY_NAMES:
            return True, False  # device prefix, no circuit numbers
        elif pattern == EntityNamingPattern.CIRCUIT_NUMBERS:
            return True, True  # device prefix, circuit numbers
        elif pattern == EntityNamingPattern.LEGACY_NAMES:
            return False, False  # no device prefix, no circuit numbers
        else:
            # Default to friendly names
            return True, False

    def _transform_entity_id(
        self,
        entity_id: str,
        from_prefix: bool,
        to_prefix: bool,
        from_numbers: bool,
        to_numbers: bool,
    ) -> str | None:
        """Transform an entity ID from one naming pattern to another.

        This handles:
        - Device prefix transformations (adding/removing span_panel_)
        - Circuit naming transformations (numbers ↔ friendly names)
        - Special handling for synthetic entities (solar inverters, etc.)
        """
        try:
            # Parse the current entity ID to understand its structure
            parts = entity_id.split(".")
            if len(parts) != 2:
                _LOGGER.warning("Unexpected entity ID format: %s", entity_id)
                return None

            domain = parts[0]
            object_id = parts[1]

            # Check if this is a synthetic entity (solar inverter, etc.)
            if self._is_synthetic_entity_id(object_id):
                return self._transform_synthetic_entity_id(
                    entity_id,
                    domain,
                    object_id,
                    from_prefix,
                    to_prefix,
                    from_numbers,
                    to_numbers,
                )

            # Handle device prefix changes
            if from_prefix and not to_prefix:
                # Remove device prefix
                if object_id.startswith("span_panel_"):
                    object_id = object_id[len("span_panel_") :]
            elif not from_prefix and to_prefix:
                # Add device prefix
                if not object_id.startswith("span_panel_"):
                    object_id = f"span_panel_{object_id}"

            # Handle circuit naming transformations
            if from_numbers != to_numbers:
                object_id = self._transform_circuit_naming(object_id, from_numbers, to_numbers)

            return f"{domain}.{object_id}"

        except Exception as e:
            _LOGGER.warning("Failed to transform entity ID %s: %s", entity_id, e)
            return None

    def _transform_circuit_naming(
        self, object_id: str, from_numbers: bool, to_numbers: bool
    ) -> str:
        """Transform circuit naming in entity object ID.

        Args:
            object_id: The entity object ID (without domain)
            from_numbers: Whether the source uses circuit numbers
            to_numbers: Whether the target uses circuit numbers

        Returns:
            Transformed object ID

        """
        try:
            if from_numbers and not to_numbers:
                # Transform from circuit numbers to friendly names
                # Example: span_panel_circuit_15_breaker -> span_panel_kitchen_outlets_breaker
                return self._circuit_numbers_to_friendly_names(object_id)
            elif not from_numbers and to_numbers:
                # Transform from friendly names to circuit numbers
                # Example: span_panel_kitchen_outlets_breaker -> span_panel_circuit_15_breaker
                return self._friendly_names_to_circuit_numbers(object_id)
            else:
                # No transformation needed
                return object_id

        except Exception as e:
            _LOGGER.warning("Failed to transform circuit naming for %s: %s", object_id, e)
            return object_id

    def _circuit_numbers_to_friendly_names(self, object_id: str) -> str:
        """Transform circuit number format to friendly name format."""
        import re

        # Pattern to match circuit number format: circuit_15_breaker
        pattern = r"circuit_(\d+)_(.+)$"
        match = re.search(pattern, object_id)

        if match:
            circuit_number = int(match.group(1))
            suffix = match.group(2)

            # Find the circuit with this number
            for _, (circuit_name, circuit_num) in self._circuit_data.items():
                if circuit_num == circuit_number:
                    # Sanitize the circuit name for entity ID
                    sanitized_name = circuit_name.lower().replace(" ", "_").replace("-", "_")
                    # Replace the circuit number part with the friendly name
                    return object_id.replace(
                        f"circuit_{circuit_number}_{suffix}",
                        f"{sanitized_name}_{suffix}",
                    )

        # If no match or circuit not found, return original
        return object_id

    def _friendly_names_to_circuit_numbers(self, object_id: str) -> str:
        """Transform friendly name format to circuit number format."""
        # This is more complex since we need to identify which part is the circuit name
        # For now, we'll use a heuristic approach

        # Look for known suffixes to identify the circuit name part
        suffixes = [
            "_breaker",
            "_power",
            "_energy_produced",
            "_energy_consumed",
            "_priority",
        ]

        for suffix in suffixes:
            if object_id.endswith(suffix):
                # Extract the part before the suffix
                prefix_part = object_id[: -len(suffix)]

                # Remove device prefix if present
                if prefix_part.startswith("span_panel_"):
                    circuit_part = prefix_part[len("span_panel_") :]
                else:
                    circuit_part = prefix_part

                # Find matching circuit by name
                for _, (circuit_name, circuit_num) in self._circuit_data.items():
                    sanitized_name = circuit_name.lower().replace(" ", "_").replace("-", "_")
                    if circuit_part == sanitized_name:
                        # Replace with circuit number format
                        new_circuit_part = f"circuit_{circuit_num}"
                        if prefix_part.startswith("span_panel_"):
                            return f"span_panel_{new_circuit_part}{suffix}"
                        else:
                            return f"{new_circuit_part}{suffix}"

        # If no transformation possible, return original
        return object_id

    def _is_synthetic_entity_id(self, object_id: str) -> bool:
        """Check if an entity ID belongs to a synthetic entity (solar inverter, etc.)."""
        # Synthetic entities have these patterns:
        # 1. Multi-circuit patterns: circuit_30_32_suffix (circuit numbers mode)
        # 2. Named patterns: solar_inverter_suffix (friendly names mode)
        # 3. Single-circuit solar inverter patterns: circuit_30_suffix when 30 is a solar leg

        import re

        # Pattern for multi-circuit entities with circuit numbers: circuit_30_32_suffix
        if re.search(r"circuit_\d+_\d+_", object_id):
            _LOGGER.debug(
                "Detected multi-circuit synthetic entity (circuit numbers): %s",
                object_id,
            )
            return True

        # Check for single-circuit solar inverter entities: circuit_30_suffix
        single_circuit_match = re.search(r"circuit_(\d+)_", object_id)
        if single_circuit_match:
            circuit_num = int(single_circuit_match.group(1))
            leg1, leg2 = self._get_solar_inverter_circuits()

            # Check if this circuit is one of the configured solar inverter legs
            if circuit_num == leg1 or circuit_num == leg2:
                _LOGGER.debug(
                    "Detected single-circuit solar inverter entity: %s (circuit %d matches solar leg)",
                    object_id,
                    circuit_num,
                )
                return True

        # Pattern for named synthetic entities: solar_inverter_, battery_bank_, etc.
        synthetic_name_patterns = [
            "solar_inverter_",
            "battery_bank_",
            "circuit_group_",
        ]

        for pattern in synthetic_name_patterns:
            if pattern in object_id:
                _LOGGER.debug("Detected named synthetic entity: %s", object_id)
                return True

        return False

    def _transform_synthetic_entity_id(
        self,
        entity_id: str,
        domain: str,
        object_id: str,
        from_prefix: bool,
        to_prefix: bool,
        from_numbers: bool,
        to_numbers: bool,
    ) -> str | None:
        """Transform synthetic entity IDs (solar inverters, etc.)."""
        try:
            # Handle device prefix changes first
            if from_prefix and not to_prefix:
                # Remove device prefix
                if object_id.startswith("span_panel_"):
                    object_id = object_id[len("span_panel_") :]
            elif not from_prefix and to_prefix:
                # Add device prefix
                if not object_id.startswith("span_panel_"):
                    object_id = f"span_panel_{object_id}"

            # Handle synthetic entity naming pattern changes
            if from_numbers != to_numbers:
                object_id = self._transform_synthetic_circuit_naming(
                    object_id, from_numbers, to_numbers
                )

            return f"{domain}.{object_id}"

        except Exception as e:
            _LOGGER.warning("Failed to transform synthetic entity ID %s: %s", entity_id, e)
            return None

    def _transform_synthetic_circuit_naming(
        self, object_id: str, from_numbers: bool, to_numbers: bool
    ) -> str:
        """Transform synthetic entity circuit naming patterns."""
        import re

        if from_numbers and not to_numbers:
            # Transform from circuit numbers to friendly names
            # Example: span_panel_circuit_30_32_energy_consumed -> span_panel_solar_inverter_energy_consumed

            # Check if the object_id has a device prefix
            has_prefix = object_id.startswith("span_panel_")
            prefix = "span_panel_" if has_prefix else ""

            # Pattern for multi-circuit synthetic entities
            multi_pattern = r"circuit_(\d+)_(\d+)_(.+)$"
            multi_match = re.search(multi_pattern, object_id)

            if multi_match:
                circuit1 = int(multi_match.group(1))
                circuit2 = int(multi_match.group(2))
                suffix = multi_match.group(3)

                # Check if this matches the solar inverter configuration
                if self._is_solar_inverter_circuits(circuit1, circuit2):
                    _LOGGER.debug(
                        "Transforming solar inverter entity: %s -> %ssolar_inverter_%s",
                        object_id,
                        prefix,
                        suffix,
                    )
                    return f"{prefix}solar_inverter_{suffix}"
                else:
                    # Unknown multi-circuit entity - use generic naming
                    _LOGGER.debug(
                        "Unknown multi-circuit entity, using generic naming: %s",
                        object_id,
                    )
                    return f"{prefix}circuit_group_{circuit1}_{circuit2}_{suffix}"

            # Check for single-circuit solar inverter patterns: circuit_30_suffix
            single_circuit_pattern = r"circuit_(\d+)_(.+)$"
            single_match = re.search(single_circuit_pattern, object_id)

            if single_match:
                circuit_num = int(single_match.group(1))
                suffix = single_match.group(2)

                # Check if this circuit is one of the configured solar inverter legs
                leg1, leg2 = self._get_solar_inverter_circuits()
                if circuit_num == leg1 or circuit_num == leg2:
                    _LOGGER.debug(
                        "Transforming single-circuit solar inverter entity: %s -> %ssolar_inverter_%s",
                        object_id,
                        prefix,
                        suffix,
                    )
                    return f"{prefix}solar_inverter_{suffix}"

        elif not from_numbers and to_numbers:
            # Transform from friendly names to circuit numbers
            # Example: span_panel_solar_inverter_energy_consumed -> span_panel_circuit_30_32_energy_consumed

            # Check if the object_id has a device prefix
            has_prefix = object_id.startswith("span_panel_")
            prefix = "span_panel_" if has_prefix else ""

            # Check for solar inverter pattern
            solar_pattern = r"solar_inverter_(.+)$"
            match = re.search(solar_pattern, object_id)

            if match:
                suffix = match.group(1)
                # Get the solar inverter circuit configuration
                circuit1, circuit2 = self._get_solar_inverter_circuits()
                if circuit1 and circuit2:
                    _LOGGER.debug(
                        "Transforming solar inverter entity: %s -> %scircuit_%d_%d_%s",
                        object_id,
                        prefix,
                        circuit1,
                        circuit2,
                        suffix,
                    )
                    return f"{prefix}circuit_{circuit1}_{circuit2}_{suffix}"
                elif circuit1:
                    _LOGGER.debug(
                        "Transforming single-leg solar inverter entity: %s -> %scircuit_%d_%s",
                        object_id,
                        prefix,
                        circuit1,
                        suffix,
                    )
                    return f"{prefix}circuit_{circuit1}_{suffix}"

            # For other synthetic entities, let the integration recreate them
            _LOGGER.debug(
                "Synthetic entity %s will be recreated with new naming pattern",
                object_id,
            )

        return object_id

    def _is_solar_inverter_circuits(self, circuit1: int, circuit2: int) -> bool:
        """Check if the given circuits match the solar inverter configuration."""
        try:
            # Get the config entry directly from the registry
            config_entry = self._hass.config_entries.async_get_entry(self._config_entry_id)
            if not config_entry:
                return False

            from .options import INVERTER_LEG1, INVERTER_LEG2

            leg1: int = config_entry.options.get(INVERTER_LEG1, 0)
            leg2: int = config_entry.options.get(INVERTER_LEG2, 0)

            # Check if circuits match (order doesn't matter)
            return bool(
                (circuit1 == leg1 and circuit2 == leg2) or (circuit1 == leg2 and circuit2 == leg1)
            )

        except Exception as e:
            _LOGGER.warning("Failed to check solar inverter circuits: %s", e)

        return False

    def _get_solar_inverter_circuits(self) -> tuple[int, int]:
        """Get the solar inverter circuit configuration."""
        try:
            # Get the config entry directly from the registry
            config_entry = self._hass.config_entries.async_get_entry(self._config_entry_id)
            if not config_entry:
                return 0, 0

            from .options import INVERTER_LEG1, INVERTER_LEG2

            leg1: int = config_entry.options.get(INVERTER_LEG1, 0)
            leg2: int = config_entry.options.get(INVERTER_LEG2, 0)
            return leg1, leg2

        except Exception as e:
            _LOGGER.warning("Failed to get solar inverter circuits: %s", e)

        return 0, 0

    async def _remove_entity(self, entity_id: str) -> bool:
        """Remove an entity from the entity registry.

        Args:
            entity_id: Entity ID to remove

        Returns:
            True if successful, False otherwise

        """
        try:
            # Get the current entity
            entity = self._entity_registry.async_get(entity_id)
            if entity is None:
                _LOGGER.warning("Cannot remove %s: entity not found in registry", entity_id)
                return False

            # Remove the entity
            self._entity_registry.async_remove(entity_id)

            _LOGGER.debug("Successfully removed entity: %s", entity_id)
            return True

        except Exception as e:
            _LOGGER.error("Failed to remove entity %s: %s", entity_id, e)
            return False

    async def _update_entity_id(
        self,
        old_entity_id: str,
        new_entity_id: str,
    ) -> bool:
        """Update an entity's entity_id in the registry."""
        try:
            # Check if the new entity ID already exists
            existing_entity = self._entity_registry.async_get(new_entity_id)
            if existing_entity and existing_entity.entity_id != old_entity_id:
                _LOGGER.error(
                    "Cannot migrate %s to %s - target entity ID already exists",
                    old_entity_id,
                    new_entity_id,
                )
                return False

            # Update the entity ID
            self._entity_registry.async_update_entity(
                old_entity_id,
                new_entity_id=new_entity_id,
            )

            _LOGGER.debug(
                "Successfully updated entity ID: %s -> %s",
                old_entity_id,
                new_entity_id,
            )
            return True

        except Exception as e:
            _LOGGER.error(
                "Failed to update entity ID %s -> %s: %s",
                old_entity_id,
                new_entity_id,
                e,
            )
            return False
