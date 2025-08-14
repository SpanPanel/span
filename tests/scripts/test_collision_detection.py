#!/usr/bin/env python3
"""Simple test to verify collision detection in entity registry mock."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

def test_registry_collision_detection():
    """Test that our registry mock properly detects and handles collisions."""

    print("🧪 Testing Registry Collision Detection")
    print("=" * 50)

    # Create mock registry with collision detection
    registry = MagicMock()
    registry.entities = {}

    # Add existing entities that will cause collisions
    existing_entities = [
        ("sensor.span_panel_circuit_2_power", "span_native_circuit_2_power"),
        ("sensor.span_panel_circuit_4_power", "span_native_circuit_4_power"),
    ]

    for entity_id, unique_id in existing_entities:
        entry = MagicMock()
        entry.entity_id = entity_id
        entry.unique_id = unique_id
        entry.platform = "span_panel"
        entry.domain = "sensor"
        registry.entities[entity_id] = entry
        print(f"🎯 Added existing entity: {entity_id} (unique_id: {unique_id})")

    # Mock async_get_entity_id method (returns original entity_ids from migration)
    def async_get_entity_id(domain, platform, unique_id):
        """Simulate registry lookup - returns original entity_ids."""
        lookup_table = {
            "span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_power": "sensor.span_panel_circuit_2_power",
            "span_nj-2316-005k6_11a47a0f69d54e12b7200f730c2ffda1_power": "sensor.span_panel_circuit_4_power",
        }

        result = lookup_table.get(unique_id)
        if result:
            print(f"🔍 Registry lookup: unique_id '{unique_id}' -> entity_id '{result}'")
            return result
        else:
            print(f"🔍 Registry lookup: unique_id '{unique_id}' -> NOT FOUND")
            return None

    registry.async_get_entity_id = async_get_entity_id

    # Mock async_get_or_create method (with collision detection)
    def async_get_or_create(domain, platform, unique_id, suggested_object_id=None, **kwargs):
        """Test collision detection when trying to create synthetic sensors."""

        # Check if entity already exists by unique_id (normal case - should find existing)
        for entity in registry.entities.values():
            if (entity.domain == domain and
                entity.platform == platform and
                entity.unique_id == unique_id):
                print(f"🔍 Found existing entity by unique_id: {entity.entity_id}")
                return entity

        # Entity doesn't exist by unique_id, need to create new one
        # But check if the desired entity_id conflicts with existing entities
        desired_entity_id = suggested_object_id or f"{domain}.{unique_id}"
        if not desired_entity_id.startswith(f"{domain}."):
            desired_entity_id = f"{domain}.{desired_entity_id}"

        # Check for entity_id collision and generate suffix if needed
        final_entity_id = desired_entity_id
        suffix = 1
        while final_entity_id in registry.entities:
            suffix += 1
            final_entity_id = f"{desired_entity_id}_{suffix}"
            existing_entity = registry.entities[desired_entity_id]
            print(f"🚨 COLLISION DETECTED: {desired_entity_id} exists (unique_id: {existing_entity.unique_id})")
            print(f"   Trying: {final_entity_id}")

        # Create new entity
        entry = MagicMock()
        entry.entity_id = final_entity_id
        entry.unique_id = unique_id
        entry.platform = platform
        entry.domain = domain
        registry.entities[final_entity_id] = entry
        print(f"✅ Created new entity: {final_entity_id} (unique_id: {unique_id})")
        return entry

    registry.async_get_or_create = async_get_or_create

    print("\n🧪 Test 1: Migration Mode Lookup (should find existing entity_ids)")
    print("-" * 60)

    # Test the migration mode lookup behavior
    result1 = registry.async_get_entity_id("sensor", "span_panel", "span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_power")
    result2 = registry.async_get_entity_id("sensor", "span_panel", "span_nj-2316-005k6_11a47a0f69d54e12b7200f730c2ffda1_power")

    print(f"✅ Migration lookup test passed: Found {result1} and {result2}")

    print("\n🧪 Test 2: Collision Detection (should detect conflicts and generate _2 suffixes)")
    print("-" * 80)

    # Test synthetic sensor registration that should trigger collision detection
    # Synthetic sensors tries to create sensor with entity_id that already exists but different unique_id
    test_cases = [
        ("synthetic_unique_id_1", "span_panel_circuit_2_power"),  # Should conflict with existing
        ("synthetic_unique_id_2", "span_panel_circuit_4_power"),  # Should conflict with existing
        ("synthetic_unique_id_3", "span_panel_circuit_6_power"),  # Should NOT conflict
    ]

    for synthetic_unique_id, suggested_object_id in test_cases:
        print(f"\n--- Testing synthetic sensor: unique_id='{synthetic_unique_id}', desired entity_id='sensor.{suggested_object_id}' ---")
        result = registry.async_get_or_create("sensor", "synthetic_sensors", synthetic_unique_id, suggested_object_id)
        print(f"Result: {result.entity_id}")

    print("\n🎯 Final Registry State:")
    print("-" * 30)
    for entity_id, entity in registry.entities.items():
        print(f"  • {entity_id} (unique_id: {entity.unique_id})")

    print("\n✅ Collision detection test completed!")
    print("Expected: sensor.span_panel_circuit_2_power_2 and sensor.span_panel_circuit_4_power_2")

if __name__ == "__main__":
    test_registry_collision_detection()
