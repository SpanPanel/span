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

    Handles both panel and circuit sensors.
    Returns None if the unique_id cannot be parsed.
    """
    try:
        parts = raw_unique_id.split("_", 2)
        if len(parts) < 3 or parts[0] != "span":
            return None
        device_identifier = parts[1]
        remainder = parts[2]

        # Panel case: remainder matches a panel key (including dotted variants)
        normalized_panel_key = _normalize_panel_description_key(remainder)
        # Try building via helper (returns span_{id}_{entity_suffix})
        if normalized_panel_key.endswith("W") or normalized_panel_key.endswith("Wh"):
            return build_panel_unique_id(device_identifier, normalized_panel_key)

        # Circuit case: split into circuit_id and api_field by last underscore
        last_underscore = remainder.rfind("_")
        if last_underscore > 0:
            circuit_id = remainder[:last_underscore]
            api_field = remainder[last_underscore + 1 :]
            # Use helper to construct with consistent suffix mapping
            if api_field in ("instantPowerW", "producedEnergyWh", "consumedEnergyWh"):
                return build_circuit_unique_id(device_identifier, circuit_id, api_field)

        return None
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

        # Set per-entry migration flag for first normal boot
        hass.data.setdefault(DOMAIN, {}).setdefault(config_entry.entry_id, {})["migration_mode"] = (
            True
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
