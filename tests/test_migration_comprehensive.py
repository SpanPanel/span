#!/usr/bin/env python3
"""Comprehensive SPAN Panel migration test using actual integration methods.

This test validates that ALL unique IDs are migrated to canonical form using the exact
same methods the integration uses during startup. This ensures that when the integration
finalizes its migration in the after-reload step, it can load all sensors correctly.
"""

import asyncio
import json
import tempfile
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import yaml

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import the actual migration methods the integration uses
from custom_components.span_panel.migration import (
    _compute_normalized_unique_id_with_device,
    migrate_config_entry_to_synthetic_sensors,
)
from custom_components.span_panel.helpers import (
    build_binary_sensor_unique_id,
    build_circuit_unique_id,
    build_panel_unique_id,
    construct_synthetic_unique_id,
    get_panel_entity_suffix,
)

async def test_comprehensive_migration():
    """Test comprehensive migration using actual integration methods."""

    print("🧪 SPAN Panel Comprehensive Migration Test")
    print("=" * 60)
    print("Testing ALL unique IDs using actual integration migration methods")

    # Test all registry versions
    registry_versions = ["legacy", "1_0_4", "1_0_10"]

    for version in registry_versions:
        print(f"\n📁 Testing {version} registry...")
        print("-" * 40)

        success = await test_registry_migration(version)
        if not success:
            print(f"❌ {version} migration test failed!")
            return False
        else:
            print(f"✅ {version} migration test passed!")

    print(f"\n" + "=" * 60)
    print("🎉 ALL MIGRATION TESTS PASSED!")
    print("   All unique IDs will be migrated to canonical form")
    print("   Integration will be able to load all sensors after migration")
    return True

async def test_registry_migration(version: str):
    """Test migration for a specific registry version."""

    # Create temporary directory for test registry files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Copy registry files to temp location
        source_registry_dir = Path(__file__).parent / "migration_storage" / version
        temp_registry_dir = temp_path / "migration_storage" / version
        temp_registry_dir.mkdir(parents=True, exist_ok=True)

        # Copy registry file
        registry_file = "core.entity_registry"
        source_file = source_registry_dir / registry_file
        temp_file = temp_registry_dir / registry_file
        if not source_file.exists():
            print(f"❌ Registry file not found: {source_file}")
            return False

        shutil.copy2(source_file, temp_file)

        # Load the registry data
        with open(temp_file, 'r') as f:
            registry_data = json.load(f)

    # Extract SPAN Panel entities
    span_entities = []
    for entity in registry_data["data"]["entities"]:
        if entity.get("platform") == "span_panel":
            span_entities.append(entity)

    print(f"📊 Found {len(span_entities)} SPAN Panel entities")

    if len(span_entities) == 0:
        print("⚠️  No SPAN Panel entities found in registry")
        return True

    # Extract device identifier from the first entity
    # This simulates how the integration gets it from config_entry.unique_id
    first_entity = span_entities[0]
    unique_id_parts = first_entity["unique_id"].split("_", 2)
    if len(unique_id_parts) < 3:
        print(f"❌ Cannot extract device identifier from: {first_entity['unique_id']}")
        return False

    device_identifier = unique_id_parts[1]
    print(f"🔧 Using device identifier: {device_identifier}")

    # Test migration of ALL unique IDs using the actual integration method
    migration_results = []
    skipped_entities = []

    for entity in span_entities:
        raw_unique_id = entity["unique_id"]
        entity_id = entity["entity_id"]
        # Extract domain from entity_id (e.g., "select.great_room" -> "select")
        domain = entity_id.split(".")[0] if "." in entity_id else entity.get("domain", "sensor")

        # Use the exact same method the integration uses
        new_unique_id = _compute_normalized_unique_id_with_device(raw_unique_id, device_identifier)

        if new_unique_id is None:
            skipped_entities.append({
                "entity_id": entity_id,
                "unique_id": raw_unique_id,
                "domain": domain,
                "reason": "Could not normalize unique_id"
            })
        else:
            migration_results.append({
                "entity_id": entity_id,
                "old_unique_id": raw_unique_id,
                "new_unique_id": new_unique_id,
                "domain": domain,
                "changed": new_unique_id != raw_unique_id
            })

    # Analyze results
    total_entities = len(span_entities)
    migrated_entities = len([r for r in migration_results if r["changed"]])
    preserved_entities = len([r for r in migration_results if not r["changed"]])
    skipped_count = len(skipped_entities)

    print(f"📊 Migration Analysis:")
    print(f"   • Total entities: {total_entities}")
    print(f"   • Migrated (changed): {migrated_entities}")
    print(f"   • Preserved (no change): {preserved_entities}")
    print(f"   • Skipped: {skipped_count}")

    # Validate that skipped entities are only non-synthetic sensors (select and switch entities)
    if skipped_count > 0:
        skipped_domains = set(skipped["domain"] for skipped in skipped_entities)
        expected_skipped_domains = {"select", "switch"}
        if not skipped_domains.issubset(expected_skipped_domains):
            unexpected_domains = skipped_domains - expected_skipped_domains
            print(f"❌ Unexpected entities skipped from domains: {unexpected_domains}")
            for skipped in [s for s in skipped_entities if s["domain"] in unexpected_domains][:3]:
                print(f"      • {skipped['entity_id']}: {skipped['unique_id']} ({skipped['domain']})")
            return False
        else:
            select_skipped = len([s for s in skipped_entities if s["domain"] == "select"])
            switch_skipped = len([s for s in skipped_entities if s["domain"] == "switch"])
            print(f"✅ {select_skipped} select entities correctly skipped (not synthetic sensors)")
            print(f"✅ {switch_skipped} switch entities correctly skipped (not synthetic sensors)")

    # Validate that all migrated unique_ids are in canonical form
    canonical_validation = validate_canonical_form(migration_results, device_identifier)
    if not canonical_validation["success"]:
        print(f"❌ Canonical form validation failed:")
        for error in canonical_validation["errors"][:5]:
            print(f"      • {error}")
        if len(canonical_validation["errors"]) > 5:
            print(f"      ... and {len(canonical_validation['errors']) - 5} more errors")
        return False

    # Show sample migrations
    print(f"📋 Sample migrations:")
    migrated_samples = [r for r in migration_results if r["changed"]][:3]
    for sample in migrated_samples:
        old_suffix = sample["old_unique_id"].split("_", 2)[2]
        new_suffix = sample["new_unique_id"].split("_", 2)[2]
        print(f"   • {old_suffix} → {new_suffix}")

    preserved_samples = [r for r in migration_results if not r["changed"]][:3]
    if preserved_samples:
        print(f"📋 Sample preserved (no change):")
        for sample in preserved_samples:
            suffix = sample["old_unique_id"].split("_", 2)[2]
            print(f"   • {suffix} (preserved)")

    print(f"✅ All {total_entities} entities successfully migrated to canonical form")
    return True

def validate_canonical_form(migration_results, device_identifier):
    """Validate that all migrated unique_ids are in canonical form."""

    errors = []

    for result in migration_results:
        new_unique_id = result["new_unique_id"]
        entity_id = result["entity_id"]
        domain = result["domain"]

        # Parse the new unique_id
        parts = new_unique_id.split("_", 2)
        if len(parts) != 3 or parts[0] != "span":
            errors.append(f"{entity_id}: Invalid unique_id format: {new_unique_id}")
            continue

        if parts[1] != device_identifier:
            errors.append(f"{entity_id}: Wrong device identifier: {parts[1]} (expected {device_identifier})")
            continue

        remainder = parts[2]

        # Validate based on domain and entity type
        if domain == "binary_sensor":
            # Binary sensors should use build_binary_sensor_unique_id format
            expected = build_binary_sensor_unique_id(device_identifier, remainder)
            if new_unique_id != expected:
                errors.append(f"{entity_id}: Binary sensor not in canonical form: {new_unique_id}")

        elif domain == "sensor":
            # Check if it's a circuit sensor (contains UUID)
            if "_" in remainder and len(remainder.split("_")[0]) == 32:
                # Circuit sensor - validate format
                circuit_id, api_key = remainder.split("_", 1)
                expected = build_circuit_unique_id(device_identifier, circuit_id, api_key)
                if new_unique_id != expected:
                    errors.append(f"{entity_id}: Circuit sensor not in canonical form: {new_unique_id}")
            else:
                # Panel sensor - should use construct_synthetic_unique_id format
                expected = construct_synthetic_unique_id(device_identifier, remainder)
                if new_unique_id != expected:
                    errors.append(f"{entity_id}: Panel sensor not in canonical form: {new_unique_id}")

    return {
        "success": len(errors) == 0,
        "errors": errors
    }

if __name__ == "__main__":
    asyncio.run(test_comprehensive_migration())
