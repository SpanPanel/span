"""Migration logic for SPAN Panel integration.

Revised approach:
- Normalize unique_ids in the entity registry to helper-format per config entry
- Set a per-entry migration flag for first normal boot to generate YAML and perform registry lookups
"""

from __future__ import annotations

import logging
from typing import Any  # noqa: F401 (retained for future type hints)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .helpers import build_circuit_unique_id, build_panel_unique_id

_LOGGER = logging.getLogger(__name__)


def _normalize_panel_description_key(raw_key: str) -> str:
    """Normalize legacy/dotted panel keys to helper description keys.

    Examples:
      mainMeterEnergy.producedEnergyWh -> mainMeterEnergyProducedWh
      feedthroughEnergy.consumedEnergyWh -> feedthroughEnergyConsumedWh

    """

    if "." in raw_key:
        # Convert dotted to camel-cased without the dot
        left, right = raw_key.split(".", 1)
        return f"{left}{right[0].upper()}{right[1:]}"
    return raw_key


def _compute_normalized_unique_id(raw_unique_id: str) -> str | None:
    """Compute helper-format unique_id from an existing raw unique_id.

    Handles both panel and circuit sensors. Case-insensitive for legacy circuit
    API suffixes and correctly distinguishes circuit vs panel forms.
    Returns None if the unique_id cannot be parsed.
    """
    try:
        parts = raw_unique_id.split("_", 2)
        if len(parts) < 3 or parts[0] != "span":
            return None
        device_identifier = parts[1]
        remainder = parts[2]

        # If remainder contains an underscore, treat as circuit: {circuit_id}_{api_field}
        last_underscore = remainder.rfind("_")
        if last_underscore > 0:
            circuit_id = remainder[:last_underscore]
            raw_api_field = remainder[last_underscore + 1 :]
            # Normalize legacy variants in a case-insensitive way
            api_key_lc = raw_api_field.replace(".", "").lower()
            circuit_map: dict[str, str] = {
                "instantpowerw": "instantPowerW",
                "power": "instantPowerW",  # tolerate already-normalized suffix
                "producedenergywh": "producedEnergyWh",
                "consumedenergywh": "consumedEnergyWh",
            }
            canonical_api = circuit_map.get(api_key_lc)
            if canonical_api is None:
                return None
            return build_circuit_unique_id(device_identifier, circuit_id, canonical_api)

        # Panel case: normalize dotted/camel variants to helper description keys
        normalized_panel_key = _normalize_panel_description_key(remainder)
        return build_panel_unique_id(device_identifier, normalized_panel_key)

    except Exception:
        return None


async def migrate_config_entry_to_synthetic_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> bool:
    """Migrate a single config entry to v2 by normalizing unique_ids and flagging migration.

    - Normalize unique_ids for span_panel sensor entities to helper-format
    - Set a per-entry migration flag for first normal boot YAML generation
    """

    # Only migrate if version is less than 2
    if config_entry.version >= 2:
        return True

    _LOGGER.info(
        "MIGRATION: Normalizing unique_ids for entry %s (version %s) to helper format",
        config_entry.entry_id,
        config_entry.version,
    )

    try:
        # Analyze existing entities for this config entry
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

        updated = 0
        skipped = 0
        for entity in entities:
            if entity.domain != "sensor" or entity.platform != DOMAIN:
                continue
            raw_uid = entity.unique_id
            new_uid = _compute_normalized_unique_id(raw_uid)
            if not new_uid:
                skipped += 1
                continue
            if new_uid != raw_uid:
                try:
                    entity_registry.async_update_entity(entity.entity_id, new_unique_id=new_uid)
                    updated += 1
                except Exception as e:
                    _LOGGER.warning("Failed to update unique_id for %s: %s", entity.entity_id, e)

        _LOGGER.info(
            "MIGRATION: Normalized %d unique_ids (skipped %d) for entry %s",
            updated,
            skipped,
            config_entry.entry_id,
        )

        # Set per-entry migration flag for first normal boot (transient and persisted)
        hass.data.setdefault(DOMAIN, {}).setdefault(config_entry.entry_id, {})["migration_mode"] = (
            True
        )
        try:
            # Persist flag in options so it survives reboots
            new_options = dict(config_entry.options)
            new_options["migration_mode"] = True
            hass.config_entries.async_update_entry(config_entry, options=new_options)
            _LOGGER.info(
                "MIGRATION: Set per-entry migration_mode option for entry %s",
                config_entry.entry_id,
            )
        except Exception as opt_err:
            _LOGGER.warning(
                "MIGRATION: Failed to persist migration_mode option for entry %s: %s",
                config_entry.entry_id,
                opt_err,
            )

        return True

    except Exception as e:
        _LOGGER.error(
            "Migration error for entry %s: %s",
            config_entry.entry_id,
            e,
            exc_info=True,
        )
        return False
