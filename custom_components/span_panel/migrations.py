"""Config entry migration logic for the Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_API_VERSION, CONF_HOP_PASSPHRASE, PANEL_CA_PENDING

if TYPE_CHECKING:
    from .runtime import SpanPanelConfigEntry

_LOGGER = logging.getLogger(__name__)

# Must match the storage version produced by the latest supported entry format.
CURRENT_CONFIG_VERSION = 7


async def async_migrate_entry(hass: HomeAssistant, config_entry: SpanPanelConfigEntry) -> bool:
    """Migrate config entry through successive versions.

    Supports upgrades from v1.3.1+ (config version 2) through to the
    current version 7. Each step mutates only the fields relevant to
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

    # --- v6 → v7: drop the stored passphrase, and queue the CA acquisition ---
    if config_entry.version < 7:
        updated_data = dict(config_entry.data)
        # The passphrase is a registration input only — nothing at runtime reads
        # it back. Holding it in `.storage` bought nothing and cost a credential
        # that re-registers any client against the panel.
        removed = updated_data.pop(CONF_HOP_PASSPHRASE, None) is not None

        # No I/O here, deliberately. This runs during startup, so a fetch would
        # delay boot whenever the panel is unreachable and a failure would have
        # nowhere to recover to. The flag defers it to the first successful
        # setup and is cleared there; the same shape as solar_migration_pending
        # above. Only v2 entries: v1 fails setup before it reaches a panel, and
        # a simulation entry has none to fetch from.
        if updated_data.get(CONF_API_VERSION) == "v2":
            updated_data[PANEL_CA_PENDING] = True

        hass.config_entries.async_update_entry(
            config_entry,
            data=updated_data,
            version=7,
        )
        if removed:
            _LOGGER.info(
                "Removed the stored panel passphrase from config entry %s",
                config_entry.entry_id,
            )
        _LOGGER.debug("Migrated config entry %s to version 7", config_entry.entry_id)

    return True
