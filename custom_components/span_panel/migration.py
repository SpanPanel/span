"""Migration logic for SPAN Panel integration.

Revised approach:
- Normalize unique_ids in the entity registry to helper-format per config entry
- Set a per-entry migration flag for first normal boot to generate YAML and perform registry lookups
- Fix entity registry naming inconsistencies caused by the old 240V circuit bug
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, USE_DEVICE_PREFIX
from .helpers import (
    build_binary_sensor_unique_id,
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

        # Check for binary sensor keys that should be preserved as-is
        binary_sensor_keys = {
            "doorState",
            "eth0Link",
            "wlanLink",
            "wwanLink",
        }

        if remainder in binary_sensor_keys:
            # Binary sensors: preserve the original description key
            return build_binary_sensor_unique_id(device_identifier, remainder)

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
    - Detect legacy config entries and set USE_DEVICE_PREFIX flag appropriately
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

        _LOGGER.debug(
            "MIGRATION: Using device_identifier=%s for entry %s (from config_entry.unique_id)",
            device_identifier,
            config_entry.entry_id,
        )

        for entity in entities:
            if entity.platform != DOMAIN:
                continue
            # Process both sensor and binary_sensor domains
            if entity.domain not in ["sensor", "binary_sensor"]:
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

        # Detect legacy config entry and set USE_DEVICE_PREFIX flag appropriately
        is_legacy_config = _detect_legacy_config_entry(entities)
        if is_legacy_config:
            _LOGGER.info(
                "MIGRATION: Detected legacy config entry %s (pre-1.0.4), setting USE_DEVICE_PREFIX=False",
                config_entry.entry_id,
            )
        else:
            _LOGGER.info(
                "MIGRATION: Detected modern config entry %s (1.0.4+), using USE_DEVICE_PREFIX=True (default)",
                config_entry.entry_id,
            )

        # Set per-entry migration flag for first normal boot (transient and persisted)
        hass.data.setdefault(DOMAIN, {}).setdefault(config_entry.entry_id, {})["migration_mode"] = (
            True
        )
        try:
            # Persist flags in options so they survive reboots
            new_options = dict(config_entry.options)
            new_options["migration_mode"] = True

            # Set USE_DEVICE_PREFIX flag based on legacy detection
            if is_legacy_config:
                new_options[USE_DEVICE_PREFIX] = False
                _LOGGER.info(
                    "MIGRATION: Set USE_DEVICE_PREFIX=False for legacy config entry %s",
                    config_entry.entry_id,
                )

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


def _detect_legacy_config_entry(entities: list[er.RegistryEntry]) -> bool:
    """Detect if a config entry is legacy based on entity IDs.

    Legacy config entries (pre-1.0.4) have entity IDs without the span_panel_ prefix.
    Modern config entries (1.0.4+) have entity IDs with the span_panel_ prefix.

    Args:
        entities: List of entity registry entries for this config entry

    Returns:
        True if this is a legacy config entry, False otherwise

    """
    # Check if any entity IDs have the span_panel_ prefix
    has_prefix = False
    no_prefix = False

    for entity in entities:
        if entity.platform != DOMAIN:
            continue

        entity_id = entity.entity_id
        if entity_id.startswith("sensor.span_panel_") or entity_id.startswith(
            "binary_sensor.span_panel_"
        ):
            has_prefix = True
        elif entity_id.startswith("sensor.") or entity_id.startswith("binary_sensor."):
            # Check if it's a SPAN Panel entity without the prefix
            # Look for SPAN Panel unique_id patterns (span_serial_*)
            if entity.unique_id.startswith("span_") and "_" in entity.unique_id:
                # Check for specific SPAN Panel patterns
                if any(
                    key in entity.unique_id
                    for key in [
                        "instantGridPowerW",
                        "feedthroughPowerW",
                        "doorState",
                        "eth0Link",
                        "wlanLink",
                        "wwanLink",
                    ]
                ):
                    no_prefix = True

    if has_prefix and no_prefix:
        _LOGGER.warning("Mixed entity ID patterns found, treating as modern config")
        return False
    elif has_prefix:
        return False  # Modern config
    elif no_prefix:
        return True  # Legacy config
    else:
        _LOGGER.warning("Cannot determine config type, treating as modern config")
        return False
