#!/usr/bin/env python3
"""Simple test script for synthetic entity detection."""

import re


def _is_synthetic_entity_id(object_id: str) -> bool:
    """Check if an entity ID belongs to a synthetic entity (solar inverter, etc.)."""
    # Synthetic entities have these patterns:
    # 1. Multi-circuit patterns: circuit_30_32_suffix (circuit numbers mode)
    # 2. Named patterns: solar_inverter_suffix (friendly names mode)

    # Pattern for multi-circuit entities with circuit numbers: circuit_30_32_suffix
    if re.search(r"circuit_\d+_\d+_", object_id):
        print(f"Detected multi-circuit synthetic entity (circuit numbers): {object_id}")
        return True

    # Pattern for named synthetic entities: solar_inverter_, battery_bank_, etc.
    synthetic_name_patterns = [
        "solar_inverter_",
        "battery_bank_",
        "circuit_group_",
    ]

    for pattern in synthetic_name_patterns:
        if pattern in object_id:
            print(f"Detected named synthetic entity: {object_id}")
            return True

    return False


def test_synthetic_detection():
    """Test synthetic entity detection."""
    test_cases = [
        # Solar sensor entity IDs
        ("span_panel_circuit_30_32_energy_consumed", True),
        ("span_panel_circuit_30_32_energy_produced", True),
        ("span_panel_circuit_30_32_instant_power", True),
        ("span_panel_solar_inverter_energy_consumed", True),
        ("span_panel_solar_inverter_energy_produced", True),
        ("span_panel_solar_inverter_instant_power", True),
        # Regular circuit entities (should not be synthetic)
        ("span_panel_circuit_15_power", False),
        ("span_panel_kitchen_outlets_power", False),
        ("span_panel_circuit_15_breaker", False),
        # Panel-level entities (should not be synthetic)
        ("span_panel_current_power", False),
        ("span_panel_main_meter_produced_energy", False),
        ("span_panel_software_version", False),
    ]

    print("Testing synthetic entity detection:")
    for entity_id, expected in test_cases:
        result = _is_synthetic_entity_id(entity_id)
        status = "✓" if result == expected else "✗"
        print(f"  {status} {entity_id}: {result} (expected {expected})")


if __name__ == "__main__":
    test_synthetic_detection()
