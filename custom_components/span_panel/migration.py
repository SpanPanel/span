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
from .helpers import (
    build_circuit_unique_id,
    build_panel_unique_id,
    construct_synthetic_unique_id,
    get_panel_entity_suffix,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_panel_description_key(raw_key: str) -> str:
    """Normalize legacy/dotted panel keys to helper description keys.

    Examples:
      mainMeterEnergy.producedEnergyWh -> mainMeterEnergyProducedWh
      feedthroughEnergy.consumedEnergyWh -> feedthroughEnergyConsumedWh

    """

    if "." in raw_key:
        # Convert dotted to camel-cased, removing duplicate parts for energy sensors
        if raw_key.endswith(".producedEnergyWh"):
            base = raw_key.replace(".producedEnergyWh", "")
            return f"{base}ProducedWh"
        elif raw_key.endswith(".consumedEnergyWh"):
            base = raw_key.replace(".consumedEnergyWh", "")
            return f"{base}ConsumedWh"
        else:
            # General dot normalization for other cases
            left, right = raw_key.split(".", 1)
            return f"{left}{right[0].upper()}{right[1:]}"
    return raw_key


def _compute_normalized_unique_id(raw_unique_id: str) -> str | None:
    """Compute helper-format unique_id from an existing raw unique_id.

    This is a simplified version that extracts the device identifier from the unique_id.
    For more control, use _compute_normalized_unique_id_with_device.
    """
    try:
        parts = raw_unique_id.split("_", 2)
        if len(parts) < 3 or parts[0] != "span":
            return None
        device_identifier = parts[1]
        return _compute_normalized_unique_id_with_device(raw_unique_id, device_identifier)
    except Exception:
        return None


def _compute_normalized_unique_id_with_device(
    raw_unique_id: str, device_identifier: str
) -> str | None:
    """Compute helper-format unique_id from an existing raw unique_id using provided device identifier.

    Handles both panel and circuit sensors. Case-insensitive for legacy circuit
    API suffixes and correctly distinguishes circuit vs panel forms.
    Returns None if the unique_id cannot be parsed.
    """
    try:
        parts = raw_unique_id.split("_", 2)
        if len(parts) < 3 or parts[0] != "span":
            return None
        # Use the provided device_identifier instead of parsing from the unique_id
        remainder = parts[2]

        # Check for solar sensor patterns (any solar sensor → canonical solar format)
        if "solar" in remainder:
            # Check for power vs energy
            if "power" in remainder:
                return construct_synthetic_unique_id(device_identifier, "solar_current_power")
            if "energy" in remainder:
                # Check for produced vs consumed
                if "produced" in remainder:
                    return construct_synthetic_unique_id(device_identifier, "solar_produced_energy")
                if "consumed" in remainder:
                    return construct_synthetic_unique_id(device_identifier, "solar_consumed_energy")

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

        # Check for native sensor keys (camelCase/legacy → snake_case mapping)
        native_sensor_map = {
            "currentRunConfig": "current_run_config",
            "dsmGridState": "dsm_grid_state",
            "dsmState": "dsm_state",
            "mainRelayState": "main_relay_state",
            "softwareVersion": "software_version",
            "softwareVer": "software_version",  # Legacy mapping
            "batteryPercentage": "storage_battery_percentage",
        }

        if remainder in native_sensor_map:
            # Native sensor: use the snake_case key directly with build_panel_unique_id
            snake_case_key = native_sensor_map[remainder]
            return build_panel_unique_id(device_identifier, snake_case_key)

        # Panel case: normalize dotted/camel variants to API keys, then use helper
        # First normalize dots to camelCase API format
        normalized_api_key = _normalize_panel_description_key(remainder)
        # Then get the entity suffix using the helper
        entity_suffix = get_panel_entity_suffix(normalized_api_key)
        # Finally construct the unique_id using the same helper as synthetic sensors
        return construct_synthetic_unique_id(device_identifier, entity_suffix)

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
        # Get the correct device identifier from config entry
        device_identifier = config_entry.unique_id
        if not device_identifier:
            _LOGGER.error("Config entry %s has no unique_id, cannot migrate", config_entry.entry_id)
            return False

        for entity in entities:
            if entity.domain != "sensor" or entity.platform != DOMAIN:
                continue
            raw_uid = entity.unique_id
            new_uid = _compute_normalized_unique_id_with_device(raw_uid, device_identifier)
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
