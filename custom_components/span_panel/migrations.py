"""Config entry migration logic for the Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_API_VERSION

if TYPE_CHECKING:
    from . import SpanPanelConfigEntry

_LOGGER = logging.getLogger(__name__)

# Must match the storage version produced by the latest supported entry format.
CURRENT_CONFIG_VERSION = 6


async def async_migrate_entry(hass: HomeAssistant, config_entry: SpanPanelConfigEntry) -> bool:
    """Migrate config entry through successive versions.

    Supports upgrades from v1.3.1+ (config version 2) through to the
    current version 6. Each step mutates only the fields relevant to
    that version boundary.
    """
    if config_entry.version >= CURRENT_CONFIG_VERSION:
        return True

    _LOGGER.debug(
        "Migrating config entry %s from version %s to %s",
        config_entry.entry_id,
        config_entry.version,
        CURRENT_CONFIG_VERSION,
    )

    # --- v2 → v3: add api_version field ---
    if config_entry.version < 3:
        updated_data = dict(config_entry.data)

        if updated_data.get("simulation_mode", False):
            updated_data[CONF_API_VERSION] = "simulation"
        else:
            updated_data[CONF_API_VERSION] = "v1"

        hass.config_entries.async_update_entry(
            config_entry,
            data=updated_data,
            options=config_entry.options,
            title=config_entry.title,
            version=3,
        )
        _LOGGER.debug("Migrated config entry %s to version 3", config_entry.entry_id)

    # --- v3 → v4: solar migration flag + remove legacy solar/retry options ---
    if config_entry.version < 4:
        updated_options = dict(config_entry.options)
        updated_data = dict(config_entry.data)

        # Check if user had solar configured under v1 options layout
        solar_was_enabled = updated_options.pop("enable_solar_circuit", False)
        updated_options.pop("leg1", None)
        updated_options.pop("leg2", None)

        if solar_was_enabled:
            # PV circuit UUID is only known at runtime (from MQTT data),
            # so defer entity registry update to first coordinator refresh.
            updated_data["solar_migration_pending"] = True
            _LOGGER.info(
                "Solar was configured — setting solar_migration_pending flag "
                "for runtime entity registry migration"
            )

        # Remove v1 REST retry options (no longer applicable)
        for key in ("api_retries", "api_retry_timeout", "api_retry_backoff_multiplier"):
            updated_options.pop(key, None)

        hass.config_entries.async_update_entry(
            config_entry,
            data=updated_data,
            options=updated_options,
            version=4,
        )
        _LOGGER.debug("Migrated config entry %s to version 4", config_entry.entry_id)

    # --- v4 → v5: remove wwanLink binary sensor ---
    if config_entry.version < 5:
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

        removed = 0
        for entity in entities:
            if entity.domain == "binary_sensor" and entity.unique_id.endswith("_wwanLink"):
                entity_registry.async_remove(entity.entity_id)
                _LOGGER.info("Removed deprecated wwanLink binary sensor: %s", entity.entity_id)
                removed += 1

        if removed:
            _LOGGER.info("v4→v5 migration: removed %d deprecated entities", removed)

        hass.config_entries.async_update_entry(
            config_entry,
            version=5,
        )
        _LOGGER.debug("Migrated config entry %s to version 5", config_entry.entry_id)

    # --- v5 → v6: bump version ---
    if config_entry.version < 6:
        if config_entry.data.get(CONF_API_VERSION) == "simulation" or config_entry.data.get(
            "simulation_mode", False
        ):
            _LOGGER.warning(
                "Config entry '%s' is a built-in simulation entry which is no "
                "longer supported. Please remove it manually from Settings > "
                "Devices & Services",
                config_entry.title,
            )

        hass.config_entries.async_update_entry(
            config_entry,
            version=6,
        )
        _LOGGER.debug("Migrated config entry %s to version 6", config_entry.entry_id)

    return True
