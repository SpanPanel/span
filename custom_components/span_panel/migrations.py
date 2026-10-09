"""Config entry migration logic for the Span Panel integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_API_VERSION, CONF_HOP_PASSPHRASE, PANEL_CA_PENDING
from .options import ALLOW_CONTEXTLESS_CONTROL

if TYPE_CHECKING:
    from .runtime import SpanPanelConfigEntry

_LOGGER = logging.getLogger(__name__)

# Must match the storage version produced by the latest supported entry format.
CURRENT_CONFIG_VERSION = 7
CURRENT_CONFIG_MINOR_VERSION = 3

_UNMAPPED_TAB_SENSORS_OPTION = "enable_unmapped_circuit_sensors"
"""The retired option's key, spelled out because nothing else reads it any more."""


async def async_migrate_entry(hass: HomeAssistant, config_entry: SpanPanelConfigEntry) -> bool:
    """Migrate config entry through successive versions.

    Supports upgrades from v1.3.1+ (config version 2) through to the
    current version 7.3. Each step mutates only the fields relevant to
    that version boundary.

    Core also calls this for an entry a newer release has already moved past
    the current minor version, which is a downgrade it allows; such an entry is
    left as it is.
    """
    if (config_entry.version, config_entry.minor_version) >= (
        CURRENT_CONFIG_VERSION,
        CURRENT_CONFIG_MINOR_VERSION,
    ):
        return True

    _LOGGER.debug(
        "Migrating config entry %s from version %s.%s to %s.%s",
        config_entry.entry_id,
        config_entry.version,
        config_entry.minor_version,
        CURRENT_CONFIG_VERSION,
        CURRENT_CONFIG_MINOR_VERSION,
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

    # --- v7.1 → v7.2: pin control without a logged-in user to what it was ---
    if config_entry.version == 7 and config_entry.minor_version < 2:
        updated_options = dict(config_entry.options)
        # The default for an entry that never stored this became off. Every entry
        # reaching this step was created before that, when anything but a stored
        # bool resolved to on, so that is written down here rather than letting
        # an upgrade start refusing a household's automations. A stored bool is
        # the user's own choice and is kept.
        if not isinstance(updated_options.get(ALLOW_CONTEXTLESS_CONTROL), bool):
            updated_options[ALLOW_CONTEXTLESS_CONTROL] = True

        hass.config_entries.async_update_entry(
            config_entry,
            options=updated_options,
            minor_version=2,
        )
        _LOGGER.debug("Migrated config entry %s to version 7.2", config_entry.entry_id)

    # --- v7.2 → v7.3: retire the Unmapped Tab sensors option ---
    if config_entry.version == 7 and config_entry.minor_version < 3:
        # The panel publishes nothing for an empty breaker position, so these
        # sensors could only ever report the library's filler. Removed rather
        # than left unavailable, and the option with them, so no stored value
        # outlives the form that set it.
        _remove_unmapped_tab_sensors(hass, config_entry)
        updated_options = dict(config_entry.options)
        updated_options.pop(_UNMAPPED_TAB_SENSORS_OPTION, None)

        hass.config_entries.async_update_entry(
            config_entry,
            options=updated_options,
            minor_version=3,
        )
        _LOGGER.debug("Migrated config entry %s to version 7.3", config_entry.entry_id)

    return True


def _remove_unmapped_tab_sensors(hass: HomeAssistant, config_entry: SpanPanelConfigEntry) -> None:
    """Remove the sensors the Unmapped Tab option created for this entry.

    Each had the unique id `span_{serial}_unmapped_tab_{n}_{suffix}`, built by
    `build_circuit_unique_id` from the library's `unmapped_tab_{n}` circuit id
    and one of three description keys: `instantPowerW`, `producedEnergyWh` and
    `consumedEnergyWh`, whose suffixes are `power`, `energy_produced` and
    `energy_consumed`. The match is exact on every segment, so it takes nothing
    else: no other id this integration builds has `unmapped_tab_` after the
    serial, because every other circuit id is the panel's own. Only this
    entry's sensors are considered.

    An entry with no unique id has no serial to match on, and is left alone.
    """
    serial = config_entry.unique_id
    if not serial:
        return
    pattern = re.compile(
        rf"span_{re.escape(serial.lower())}_unmapped_tab_\d+_"
        r"(?:power|energy_produced|energy_consumed)"
    )
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        if entity.domain == "sensor" and pattern.fullmatch(entity.unique_id):
            entity_registry.async_remove(entity.entity_id)
            _LOGGER.info("Removed Unmapped Tab sensor %s: the option is retired", entity.entity_id)
