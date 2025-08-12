"""Utilities for migration from v1 to v2 synthetic sensors."""

import logging
import re
from typing import Any


def classify_sensor_from_unique_id(unique_id: str) -> tuple[str, str, str]:
    """Classify sensor from unique ID for migration purposes.

    Args:
        unique_id: The existing unique ID from the entity registry

    Returns:
        (category, sensor_type, api_key)
        category: 'panel', 'circuit', or 'solar'
        sensor_type: 'power', 'energy_produced', 'energy_consumed'
        api_key: the API field key for finding the right sensor definition

    """
    unique_id_lower = unique_id.lower()

    # Panel circuit patterns (specific and stable)
    panel_patterns = {
        "instantgridpowerw": "instantGridPowerW",
        "feedthroughpowerw": "feedthroughPowerW",
        "mainmeterenergy.*produced": "mainMeterEnergyProducedWh",
        "mainmeterenergy.*consumed": "mainMeterEnergyConsumedWh",
        "feedthroughenergy.*produced": "feedthroughEnergyProducedWh",
        "feedthroughenergy.*consumed": "feedthroughEnergyConsumedWh",
    }

    # Check if it's a panel circuit first (most specific)
    for pattern, api_key in panel_patterns.items():
        if re.search(pattern, unique_id_lower):
            if "power" in api_key.lower():
                return "panel", "power", api_key
            elif "produced" in api_key.lower():
                return "panel", "energy_produced", api_key
            elif "consumed" in api_key.lower():
                return "panel", "energy_consumed", api_key

    # Check if it's solar (includes both 'solar' and 'inverter' patterns)
    if "solar" in unique_id_lower or "inverter" in unique_id_lower:
        if "power" in unique_id_lower:
            return "solar", "power", "solar_power"
        elif "produced" in unique_id_lower or "energy" in unique_id_lower:
            # Solar energy is typically produced energy by default
            if "consumed" in unique_id_lower:
                return "solar", "energy_consumed", "solar_energy_consumed"
            else:
                return "solar", "energy_produced", "solar_energy_produced"

    # Must be a circuit sensor (everything else)
    if "power" in unique_id_lower:
        return "circuit", "power", "instantPowerW"
    elif "produced" in unique_id_lower:
        return "circuit", "energy_produced", "producedEnergyWh"
    elif "consumed" in unique_id_lower:
        return "circuit", "energy_consumed", "consumedEnergyWh"
    elif "energy" in unique_id_lower:
        # Default energy to produced if not specified
        return "circuit", "energy_produced", "producedEnergyWh"

    raise ValueError(f"Cannot classify sensor type from unique_id: {unique_id}")


def group_existing_sensors_by_category(
    sensor_entities: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Group existing sensor entities by category for migration.

    Args:
        sensor_entities: List of sensor entity dicts from entity registry

    Returns:
        (panel_mappings, circuit_mappings, solar_mappings)
        Each mapping is {unique_id: entity_id}

    """
    panel_mappings: dict[str, str] = {}
    circuit_mappings: dict[str, str] = {}
    solar_mappings: dict[str, str] = {}

    for sensor_entity in sensor_entities:
        unique_id = sensor_entity["unique_id"]
        entity_id = sensor_entity["entity_id"]

        try:
            category, sensor_type, api_key = classify_sensor_from_unique_id(unique_id)

            if category == "panel":
                panel_mappings[unique_id] = entity_id
            elif category == "circuit":
                circuit_mappings[unique_id] = entity_id
            elif category == "solar":
                solar_mappings[unique_id] = entity_id

        except ValueError as e:
            # Log but don't fail - skip sensors we can't classify
            logger = logging.getLogger(__name__)
            logger.warning("Failed to classify sensor for migration: %s", e)
            continue

    return panel_mappings, circuit_mappings, solar_mappings
