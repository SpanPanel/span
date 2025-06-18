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
from .helpers import sanitize_name_for_entity_id
from .span_panel import SpanPanel
from .util import panel_to_device_info

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
                if "unmapped_tab_" in entity.entity_id:
                    _LOGGER.debug("Skipping unmapped_tab entity: %s", entity.entity_id)
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
                    entity.entity_id, entity_name, from_enum, to_enum
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

            # Get device name for prefix logic
            device_name = "span_panel"  # Default fallback
            if hasattr(self, "data") and self.data:  # self.data is the SpanPanel object
                try:
                    device_info = panel_to_device_info(self.data)
                    device_name_raw = device_info.get("name")
                    if device_name_raw:
                        device_name = sanitize_name_for_entity_id(device_name_raw)
                except (AttributeError, KeyError, TypeError) as exc:
                    # Log the specific error but continue with fallback
                    _LOGGER.debug(
                        "Unable to get device name for entity ID migration, using fallback: %s", exc
                    )
                    # device_name remains the default "span_panel"

            # Use the entity's actual name to create the new object ID
            name_based_object_id = sanitize_name_for_entity_id(entity_name)

            # Apply device prefix based on target pattern
            if to_pattern == EntityNamingPattern.CIRCUIT_NUMBERS:
                # Circuit numbers pattern: device_circuit_N_suffix
                new_object_id = f"{device_name}_{name_based_object_id}"
            elif to_pattern == EntityNamingPattern.FRIENDLY_NAMES:
                # Friendly names pattern: device_friendly_name_suffix
                # Friendly names are just base names, so we add device prefix for entity_id
                new_object_id = f"{device_name}_{name_based_object_id}"
            else:
                # Legacy pattern, no device prefix
                new_object_id = name_based_object_id

            return f"{domain}.{new_object_id}"

        except Exception as e:
            _LOGGER.warning("Failed to generate new entity ID for %s: %s", current_entity_id, e)
            return None
