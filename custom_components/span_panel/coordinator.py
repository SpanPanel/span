"""Coordinator for Span Panel."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

from .const import API_TIMEOUT, EntityNamingPattern
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelCoordinator(DataUpdateCoordinator[SpanPanel]):
    """Coordinator for Span Panel."""

    def __init__(
        self,
        hass: HomeAssistant,
        span_panel: SpanPanel,
        name: str,
        update_interval: int,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"span panel {name}",
            update_interval=timedelta(seconds=update_interval),
            always_update=True,
        )
        self.span_panel_api = span_panel
        self.config_entry: ConfigEntry | None = config_entry

        # Flag for panel name auto-sync integration reload
        self._needs_reload = False

    def request_reload(self) -> None:
        """Request an integration reload for the next update cycle."""
        self._needs_reload = True
        _LOGGER.debug("Integration reload requested for next update cycle")

    async def _async_update_data(self) -> SpanPanel:
        """Fetch data from API endpoint."""
        # Check if reload is needed before updating (auto-sync)
        if self._needs_reload:
            self._needs_reload = False
            _LOGGER.info("Auto-sync triggering integration reload")

            # Schedule reload outside of the coordinator's update cycle to avoid conflicts
            async def schedule_reload() -> None:
                """Schedule the reload after the current update cycle completes."""
                try:
                    # Wait for current operations to complete
                    await self.hass.async_block_till_done()

                    if self.config_entry is None:
                        _LOGGER.error(
                            "Cannot reload: config_entry is None - integration incorrectly initialized"
                        )
                        return

                    _LOGGER.info("Auto-sync performing scheduled integration reload")
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    _LOGGER.info("Auto-sync integration reload completed")

                except (ConfigEntryNotReady, HomeAssistantError) as e:
                    _LOGGER.error("Auto-sync failed to reload integration: %s", e)
                except Exception as e:
                    _LOGGER.error("Unexpected error during auto-sync reload: %s", e, exc_info=True)

            # Schedule the reload to run outside the current update cycle
            self.hass.async_create_task(schedule_reload())

            # Return current data and continue with normal operation until reload completes
            return self.span_panel_api

        try:
            _LOGGER.debug("Starting coordinator update")
            await asyncio.wait_for(self.span_panel_api.update(), timeout=API_TIMEOUT)
            return self.span_panel_api
        except SpanPanelAuthError as err:
            _LOGGER.error("Authentication failed while updating Span data: %s", str(err))
            raise ConfigEntryAuthFailed from err
        except (SpanPanelConnectionError, SpanPanelTimeoutError) as err:
            _LOGGER.error("Connection/timeout error while updating Span data: %s", str(err))
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except SpanPanelRetriableError as err:
            _LOGGER.warning(
                "Retriable error occurred while updating Span data (will retry): %s",
                str(err),
            )
            raise UpdateFailed(f"Temporary SPAN Panel error: {err}") from err
        except SpanPanelServerError as err:
            _LOGGER.error("SPAN Panel server error (will not retry): %s", str(err))
            raise UpdateFailed(f"SPAN Panel server error: {err}") from err
        except SpanPanelAPIError as err:
            _LOGGER.error("API error while updating Span data: %s", str(err))
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except TimeoutError as err:
            _LOGGER.error(
                "An asyncio.TimeoutError occurred while updating Span data: %s",
                str(err),
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def migrate_entities(self, from_pattern: str, to_pattern: str) -> bool:
        """Migrate entity IDs from one naming pattern to another.

        REGULAR CIRCUIT SENSORS: Migrate entity IDs to new naming pattern
           - Apply standard entity ID migration logic
           - Use proper construct_entity_id helpers for correct prefixing

        SYNTHETIC SENSORS (Solar): Remove and recreate via YAML regeneration
           - Integration-owned solar sensors in span-ha-synthetic.yaml
           - Remove from entity registry completely during migration
           - YAML regeneration will recreate with correct naming patterns
           - Prevents double prefixing and entity ID collisions

        UNMAPPED TAB SENSORS: Skip migration entirely
           - Used as variables in solar YAML configuration
           - Always use simple pattern regardless of user naming preferences
           - Entity IDs like: sensor.span_panel_unmapped_tab_30_energy_produced
           - Must remain stable for YAML variable references

        USER-CREATED SYNTHETICS: Not applicable (future feature)
           - Would be in separate user-managed YAML files
           - Integration never touches user-controlled synthetic sensors
           - User manages naming and migration of their own sensors

        This method uses the actual entity objects to get their names and
        perform generic entity migration without hardcoded entity types.

        Args:
            from_pattern: The current entity naming pattern
            to_pattern: The target entity naming pattern

        Returns:
            True if migration was successful, False otherwise

        """
        if from_pattern == to_pattern:
            _LOGGER.debug("No migration needed - patterns are the same")
            return True

        _LOGGER.info("Starting entity migration from %s to %s", from_pattern, to_pattern)

        try:
            if self.config_entry is None:
                _LOGGER.error("Cannot migrate entities: config_entry is None")
                return False

            entity_registry = er.async_get(self.hass)

            # Get all entities for this integration
            entities = [
                entity
                for entity in entity_registry.entities.values()
                if entity.config_entry_id == self.config_entry.entry_id
            ]

            _LOGGER.debug("Found %d entities to potentially migrate", len(entities))

            # Convert patterns to enum values
            from_enum = EntityNamingPattern(from_pattern)
            to_enum = EntityNamingPattern(to_pattern)

            migration_count = 0
            removal_count = 0

            for entity in entities:
                # Skip unmapped_tab entities - they should never be renamed as they are used as
                # variables in solar YAML configuration
                if "unmapped_tab_" in entity.entity_id or self._is_panel_level_entity(entity):
                    _LOGGER.debug("Skipping entity: %s", entity.entity_id)
                    continue

                # Check if this is a synthetic sensor (solar inverter, etc.)
                if self._is_integration_synthetic_sensor(entity.entity_id):
                    _LOGGER.debug("Removing synthetic sensor for recreation: %s", entity.entity_id)
                    entity_registry.async_remove(entity.entity_id)
                    removal_count += 1
                    continue

                # Get the actual entity object to access its name
                state = self.hass.states.get(entity.entity_id)
                if not state:
                    _LOGGER.debug("Skipping %s - no state available", entity.entity_id)
                    continue

                # Use the entity's actual name attribute
                entity_name = state.attributes.get("friendly_name")
                if not entity_name:
                    _LOGGER.debug("Skipping %s - no friendly_name attribute", entity.entity_id)
                    continue

                # Generate new entity ID based on the entity's actual name
                new_entity_id = self._generate_new_entity_id_from_name(
                    entity.entity_id, entity_name, from_enum, to_enum, entity.unique_id
                )

                if new_entity_id and new_entity_id != entity.entity_id:
                    try:
                        entity_registry.async_update_entity(
                            entity.entity_id, new_entity_id=new_entity_id
                        )
                        _LOGGER.debug("Migrated: %s -> %s", entity.entity_id, new_entity_id)
                        migration_count += 1
                    except Exception as e:
                        _LOGGER.error(
                            "Failed to migrate %s to %s: %s", entity.entity_id, new_entity_id, e
                        )

            _LOGGER.info(
                "Entity migration completed: %d entities migrated, %d synthetic sensors removed",
                migration_count,
                removal_count,
            )
            return True

        except Exception as e:
            _LOGGER.error("Entity migration failed: %s", e)
            return False

    def _is_integration_synthetic_sensor(self, entity_id: str) -> bool:
        """Check if entity is an integration-managed synthetic sensor.

        These are synthetic sensors created by the integration (like solar inverters)
        that should be removed during migration and recreated via YAML regeneration.
        """
        # Solar inverter sensors created by the integration
        synthetic_patterns = [
            "solar_inverter_instant_power",
            "solar_inverter_energy_produced",
            "solar_inverter_energy_consumed",
            # Add other integration-created synthetic sensor patterns here
        ]

        return any(pattern in entity_id for pattern in synthetic_patterns)

    def _generate_new_entity_id_from_name(
        self,
        current_entity_id: str,
        entity_name: str,
        from_pattern: EntityNamingPattern,
        to_pattern: EntityNamingPattern,
        unique_id: str | None = None,
    ) -> str | None:
        """Generate new entity ID based on entity's actual name and target pattern.

        This is completely generic - it works for any entity type by using the
        entity's actual name to derive the new entity ID.

        The friendly name is just the base name (e.g., "Air Conditioner Power") without
        any device prefix. Device prefixes are only applied to entity_id based on the
        target naming pattern.
        """
        # Skip unmapped_tab entities ID's - they should never be renamed by the integration
        # A user could rename the entity id but then they own its management
        if "unmapped_tab_" in current_entity_id:
            return None

        try:
            # Extract domain from current entity ID
            domain = current_entity_id.split(".", 1)[0]

            # For circuit entities, use the same logic as fresh install
            if unique_id:
                # Set target pattern for simulation
                self._migration_target_pattern = to_pattern
                try:
                    circuit_entity_id = self._generate_circuit_entity_id_for_migration(
                        domain, unique_id, entity_name
                    )
                    if circuit_entity_id:
                        return circuit_entity_id
                finally:
                    # Clean up the target pattern
                    if hasattr(self, "_migration_target_pattern"):
                        delattr(self, "_migration_target_pattern")

            # For non-circuit entities (panel-level), construct using appropriate helpers
            from .helpers import sanitize_name_for_entity_id, panel_to_device_info

            # Fallback: manual construction for non-circuit entities
            device_name = "span_panel"  # Default fallback
            if hasattr(self, "data") and self.data:
                try:
                    device_info = panel_to_device_info(self.data)
                    device_name_raw = device_info.get("name")
                    if device_name_raw:
                        device_name = sanitize_name_for_entity_id(device_name_raw)
                except (AttributeError, KeyError, TypeError):
                    pass  # Use fallback

            name_based_object_id = sanitize_name_for_entity_id(entity_name)

            # Apply device prefix based on target pattern (simplified logic for non-circuit entities)
            if to_pattern == EntityNamingPattern.LEGACY_NAMES:
                new_object_id = name_based_object_id
            else:
                # Both CIRCUIT_NUMBERS and FRIENDLY_NAMES use device prefix for non-circuit entities
                new_object_id = f"{device_name}_{name_based_object_id}"

            return f"{domain}.{new_object_id}"

        except Exception as e:
            _LOGGER.warning("Failed to generate new entity ID for %s: %s", current_entity_id, e)
            return None

    def _is_panel_level_entity(self, entity: er.RegistryEntry) -> bool:
        """Check if entity is a panel-level entity that should not be migrated.

        Panel-level entities represent the state of the panel itself (not circuits)
        and should have stable entity IDs regardless of naming pattern changes.

        Examples:
        - binary_sensor.span_panel_door_state
        - binary_sensor.span_panel_cellular_link
        - sensor.current_power (or sensor.span_panel_current_power)
        - sensor.dsm_state

        """
        if not entity.unique_id:
            return False

        # Panel-level binary sensors have unique_id pattern: span_{serial}_{key}
        # where key is doorState, eth0Link, wlanLink, wwanLink
        panel_binary_sensor_keys = [
            "doorState",
            "eth0Link",
            "wlanLink",
            "wwanLink",
        ]

        for key in panel_binary_sensor_keys:
            if entity.unique_id.endswith(f"_{key}"):
                return True

        # Panel-level sensors have unique_id pattern: span_{serial}_{key}
        # where key is instantGridPowerW, feedthroughPowerW, dsmState, etc.
        panel_sensor_keys = [
            "instantGridPowerW",
            "feedthroughPowerW",
            "mainMeterEnergy.producedEnergyWh",
            "mainMeterEnergy.consumedEnergyWh",
            "feedthroughEnergy.producedEnergyWh",
            "feedthroughEnergy.consumedEnergyWh",
            "currentRunConfig",
            "dsmGridState",
            "dsmState",
            "mainRelayState",
            "softwareVer",
        ]

        for key in panel_sensor_keys:
            if entity.unique_id.endswith(f"_{key}"):
                return True

        return False

    def _generate_circuit_entity_id_for_migration(
        self, domain: str, unique_id: str, entity_name: str
    ) -> str | None:
        """Generate circuit entity ID for migration by simulating fresh install logic.

        This reuses the exact same entity creation logic that would run on a fresh install,
        ensuring migration produces identical entity IDs.

        Args:
            domain: Entity domain (sensor, switch, etc.)
            unique_id: Current unique ID from entity registry
            entity_name: Current entity name (friendly name of the circuit)

        Returns:
            Complete entity ID (with domain prefix) or None if not a circuit entity

        """
        if not self.data or not hasattr(self.data, "circuits"):
            _LOGGER.debug("No data or circuits available for migration")
            return None

        _LOGGER.debug(
            "Attempting migration for unique_id: %s, entity_name: %s", unique_id, entity_name
        )

        # Find which circuit this unique_id belongs to by checking all circuits
        for circuit_id, circuit in self.data.circuits.items():
            _LOGGER.debug("Checking circuit %s against unique_id %s", circuit_id, unique_id)
            # Check if this unique_id matches what would be generated for this circuit
            serial = self.data.status.serial_number
            _LOGGER.debug("Panel serial: %s", serial)

            # Try sensor pattern: span_{serial}_{circuit_id}_{suffix}
            if unique_id.startswith(f"span_{serial}_{circuit_id}_"):
                # This is a sensor - simulate sensor entity creation
                return self._simulate_circuit_entity_creation(
                    domain, circuit_id, circuit, entity_name, unique_id
                )

            # Try switch pattern: span_{serial}_relay_{circuit_id}
            elif unique_id == f"span_{serial}_relay_{circuit_id}":
                # This is a switch - simulate switch entity creation
                return self._simulate_circuit_entity_creation(
                    domain, circuit_id, circuit, entity_name
                )

        # If no circuit matched by unique_id, try to find circuit by friendly name
        # This handles migration FROM friendly names TO circuit numbers
        for circuit_id, circuit in self.data.circuits.items():
            if circuit.name == entity_name.replace(" Breaker", "").replace(" Power", "").replace(
                " Energy Consumed", ""
            ).replace(" Energy Produced", "").replace(" Energy Imported", "").replace(
                " Energy Exported", ""
            ).replace(" Priority", ""):
                return self._simulate_circuit_entity_creation(
                    domain, circuit_id, circuit, circuit.name, unique_id
                )

        return None

    def _simulate_circuit_entity_creation(
        self,
        domain: str,
        circuit_id: str,
        circuit: "SpanPanelCircuit",
        entity_name: str,
        unique_id: str | None = None,
    ) -> str | None:
        """Simulate circuit entity creation using the same logic as fresh install."""
        from .helpers import (
            get_circuit_number,
            get_user_friendly_suffix,
            sanitize_name_for_entity_id,
        )
        from .util import panel_to_device_info

        # Get circuit number using the same helper that sensor.py and switch.py use
        circuit_number = get_circuit_number(circuit)

        # Use the circuit's actual friendly name from panel data (not the passed entity_name)
        circuit_friendly_name = circuit.name

        # For migration, we need to construct entity ID for the TARGET pattern, not current config
        # Determine target pattern from the migration context
        if hasattr(self, "_migration_target_pattern"):
            target_pattern = self._migration_target_pattern
        else:
            # Fallback to using construct_entity_id with current config
            from .helpers import construct_entity_id

            if domain == "switch":
                return construct_entity_id(
                    self, self.data, "switch", circuit_friendly_name, circuit_number, "breaker"
                )
            else:
                if unique_id is None:
                    return None
                serial = self.data.status.serial_number
                prefix = f"span_{serial}_{circuit_id}_"
                suffix = unique_id[len(prefix) :]
                entity_suffix = get_user_friendly_suffix(suffix)
                return construct_entity_id(
                    self, self.data, "sensor", circuit_friendly_name, circuit_number, entity_suffix
                )

        # Get device name for prefix
        device_info = panel_to_device_info(self.data)
        device_name_raw = device_info.get("name")
        if device_name_raw:
            device_name = sanitize_name_for_entity_id(device_name_raw)
        else:
            device_name = "span_panel"

        # Construct entity ID based on target pattern
        if domain == "switch":
            entity_suffix = "breaker"
        else:
            if unique_id is None:
                return None
            serial = self.data.status.serial_number
            prefix = f"span_{serial}_{circuit_id}_"
            suffix = unique_id[len(prefix) :]
            entity_suffix = get_user_friendly_suffix(suffix)

        # Apply the target pattern format
        from .const import EntityNamingPattern

        if target_pattern == EntityNamingPattern.CIRCUIT_NUMBERS:
            # Circuit numbers format: device_circuit_N_suffix
            return f"{domain}.{device_name}_circuit_{circuit_number}_{entity_suffix}"
        elif target_pattern == EntityNamingPattern.FRIENDLY_NAMES:
            # Friendly names format: device_friendly_name_suffix
            circuit_name_sanitized = sanitize_name_for_entity_id(circuit_friendly_name)
            return f"{domain}.{device_name}_{circuit_name_sanitized}_{entity_suffix}"
        else:
            # Legacy format: friendly_name_suffix
            circuit_name_sanitized = sanitize_name_for_entity_id(circuit_friendly_name)
            return f"{domain}.{circuit_name_sanitized}_{entity_suffix}"
