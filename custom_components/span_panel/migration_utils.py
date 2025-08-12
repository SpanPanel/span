"""Utilities for migration from v1 to v2 synthetic sensors."""

import logging
from typing import Any


def classify_sensor_from_unique_id(unique_id: str) -> tuple[str, str, str]:
    """Classify sensor from unique ID for migration purposes.

    Args:
        unique_id: The existing unique ID from the entity registry

    Returns:
        (category, sensor_type, api_key)
        category: 'generic' or 'solar'
        sensor_type: 'power', 'energy_produced', 'energy_consumed'
        api_key: the API field key for finding the right sensor definition

    """
    unique_id_lower = unique_id.lower()

    # Check if it's solar (includes both 'solar' and 'inverter' patterns)
    if "solar" in unique_id_lower or "inverter" in unique_id_lower:
        if "power" in unique_id_lower:
            return "solar", "power", "solar_power"
        elif "produced" in unique_id_lower or "energy" in unique_id_lower:
            if "consumed" in unique_id_lower:
                return "solar", "energy_consumed", "solar_energy_consumed"
            else:
                return "solar", "energy_produced", "solar_energy_produced"

    # Generic (non-solar): determine type by keywords only
    if "power" in unique_id_lower:
        return "generic", "power", "instantPowerW"
    if "consumed" in unique_id_lower:
        return "generic", "energy_consumed", "consumedEnergyWh"
    if "produced" in unique_id_lower or "energy" in unique_id_lower:
        # Default energy to produced when unclear
        return "generic", "energy_produced", "producedEnergyWh"

    raise ValueError(f"Cannot classify sensor type from unique_id: {unique_id}")


def group_existing_sensors_by_category(
    sensor_entities: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Group existing sensor entities by category for migration.

    Args:
        sensor_entities: List of sensor entity dicts from entity registry

    Returns:
        (generic_mappings, solar_mappings)
        Each mapping is {unique_id: entity_id}

    """
    generic_mappings: dict[str, str] = {}
    solar_mappings: dict[str, str] = {}

    for sensor_entity in sensor_entities:
        unique_id = sensor_entity["unique_id"]
        entity_id = sensor_entity["entity_id"]

        # Skip unmapped tab entities - these are native backing entities, not synthetic sensors
        if "unmapped_tab_" in unique_id:
            logger = logging.getLogger(__name__)
            logger.debug("Skipping unmapped tab entity (native backing entity): %s", entity_id)
            continue

        try:
            category, sensor_type, api_key = classify_sensor_from_unique_id(unique_id)

            if category == "generic":
                generic_mappings[unique_id] = entity_id
            elif category == "solar":
                solar_mappings[unique_id] = entity_id

        except ValueError as e:
            # Log but don't fail - skip sensors we can't classify
            logger = logging.getLogger(__name__)
            logger.warning("Failed to classify sensor for migration: %s", e)
            continue

    return generic_mappings, solar_mappings
