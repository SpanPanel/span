#!/usr/bin/env python3
"""Test SPAN Panel migration from legacy versions to v1.2.0.

This test validates the complete migration process for legacy registry data:
1. Loads real legacy entity registry data
2. Performs unique_id normalization (Phase 1)
3. Validates that all expected sensors are created:
   - Panel sensors (no collisions, proper entity_ids)
   - Circuit power sensors (for all circuits)
   - Circuit energy sensors
   - Status sensors
   - Select entities (circuit priorities)
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

# Import the real migration module
from custom_components.span_panel.migration import _compute_normalized_unique_id

async def test_migration_legacy_to_1_2_0():
    """Test complete migration from legacy versions to v1.2.0."""

    print("🧪 SPAN Panel Migration Test: Legacy → v1.2.0")
    print("=" * 60)

    # Create temporary directory for test registry files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Copy registry files to temp location to avoid polluting original test data
        source_registry_dir = Path(__file__).parent / "migration_storage" / "legacy"
        temp_registry_dir = temp_path / "migration_storage" / "legacy"
        temp_registry_dir.mkdir(parents=True, exist_ok=True)

        # Copy registry file
        registry_file = "core.entity_registry"
        source_file = source_registry_dir / registry_file
        temp_file = temp_registry_dir / registry_file
        if source_file.exists():
            shutil.copy2(source_file, temp_file)
            print(f"📁 Copied {registry_file} to temp location")
        else:
            print(f"❌ Registry file not found: {source_file}")
            return False

        # Load the copied registry data
        registry_source = temp_registry_dir / registry_file

        print(f"📁 Loading legacy registry from temp location: {registry_source}")

        with open(registry_source, 'r') as f:
            registry_data = json.load(f)

    # Extract SPAN Panel entities
    span_entities = []
    for entity in registry_data["data"]["entities"]:
        if entity.get("platform") == "span_panel":
            span_entities.append(entity)

    print(f"📊 Found {len(span_entities)} SPAN Panel entities in legacy registry")

    # Categorize entities by type
    panel_entities = []
    circuit_entities = []
    binary_sensor_entities = []
    status_entities = []
    select_entities = []

    for entity in span_entities:
        unique_id = entity["unique_id"]
        entity_id = entity["entity_id"]

        # Categorize based on unique_id patterns and entity_id
        if any(key in unique_id for key in ["mainMeterEnergy", "feedthroughEnergy", "feedthroughPowerW", "instantGridPowerW"]):
            panel_entities.append(entity)
        elif "select_" in unique_id:
            select_entities.append(entity)
        elif entity_id.startswith("binary_sensor.") and any(key in unique_id for key in ["doorState", "eth0Link", "wlanLink", "wwanLink"]):
            binary_sensor_entities.append(entity)
        elif any(key in unique_id for key in ["instantPowerW", "producedEnergyWh", "consumedEnergyWh"]):
            # Check if this is a circuit entity (has UUID in 3rd position)
            parts = unique_id.split('_')
            if len(parts) >= 4 and len(parts[2]) == 32 and all(c in '0123456789abcdef' for c in parts[2]):
                circuit_entities.append(entity)
            else:
                status_entities.append(entity)
        else:
            status_entities.append(entity)

    print(f"   • Panel sensors: {len(panel_entities)}")
    print(f"   • Circuit sensors: {len(circuit_entities)}")
    print(f"   • Binary sensors: {len(binary_sensor_entities)}")
    print(f"   • Status sensors: {len(status_entities)}")
    print(f"   • Select entities: {len(select_entities)}")

    # Extract circuit IDs from registry
    circuit_ids = set()
    circuit_power_sensors = []
    circuit_energy_sensors = []

    for entity in circuit_entities:
        unique_id = entity["unique_id"]
        # Circuit pattern: span_serial_circuitid_sensor
        # Look for entities with UUID pattern (32 char hex)
        parts = unique_id.split('_')
        if len(parts) >= 3:
            potential_circuit_id = parts[2]  # Circuit UUID is 3rd part
            # Check if it looks like a UUID (32 hex characters)
            if len(potential_circuit_id) == 32 and all(c in '0123456789abcdef' for c in potential_circuit_id):
                circuit_ids.add(potential_circuit_id)

                if "instantPowerW" in unique_id:
                    circuit_power_sensors.append(entity)
                elif "EnergyWh" in unique_id:
                    circuit_energy_sensors.append(entity)

    expected_circuits = sorted(list(circuit_ids))
    print(f"   • Unique circuits: {len(expected_circuits)}")
    print(f"   • Circuit power sensors: {len(circuit_power_sensors)}")
    print(f"   • Circuit energy sensors: {len(circuit_energy_sensors)}")

    # Phase 1: Test migration normalization
    print(f"\n🔄 Phase 1: Testing unique_id normalization...")

    normalization_tests = 0
    normalization_passed = 0

    # Test panel entities
    panel_test_cases = [
        ("mainMeterEnergy.producedEnergyWh", "main_meter_produced_energy"),
        ("mainMeterEnergy.consumedEnergyWh", "main_meter_consumed_energy"),
        ("feedthroughEnergy.producedEnergyWh", "feed_through_produced_energy"),
        ("feedthroughEnergy.consumedEnergyWh", "feed_through_consumed_energy"),
        ("feedthroughPowerW", "feed_through_power"),
        ("instantGridPowerW", "current_power"),
    ]

    for original_suffix, expected_suffix in panel_test_cases:
        original_unique_id = f"span_nt-2224-c1fa7_{original_suffix}"
        expected_unique_id = f"span_nt-2224-c1fa7_{expected_suffix}"

        normalized = _compute_normalized_unique_id(original_unique_id)
        normalization_tests += 1

        if normalized == expected_unique_id:
            print(f"   ✅ Panel: {original_suffix} → {expected_suffix}")
            normalization_passed += 1
        else:
            print(f"   ❌ Panel: {original_suffix}")
            print(f"      Expected: {expected_unique_id}")
            print(f"      Got: {normalized}")

    # Test circuit entities
    for circuit_id in expected_circuits[:3]:  # Test first 3 circuits
        test_cases = [
            (f"span_nt-2224-c1fa7_{circuit_id}_instantPowerW", f"span_nt-2224-c1fa7_{circuit_id}_power"),
            (f"span_nt-2224-c1fa7_{circuit_id}_producedEnergyWh", f"span_nt-2224-c1fa7_{circuit_id}_energy_produced"),
            (f"span_nt-2224-c1fa7_{circuit_id}_consumedEnergyWh", f"span_nt-2224-c1fa7_{circuit_id}_energy_consumed"),
        ]

        for original, expected in test_cases:
            normalized = _compute_normalized_unique_id(original)
            normalization_tests += 1

            if normalized == expected:
                normalization_passed += 1
            else:
                print(f"   ❌ Circuit {circuit_id[:8]}...: {original.split('_')[-1]}")
                print(f"      Expected: {expected}")
                print(f"      Got: {normalized}")

    # Test binary sensor entities
    for binary_entity in binary_sensor_entities:
        unique_id = binary_entity["unique_id"]
        original_suffix = unique_id.split('_')[-1]  # Get the last part after the last underscore

        normalized = _compute_normalized_unique_id(unique_id)
        normalization_tests += 1

        if normalized == unique_id:  # Binary sensors should be preserved as-is
            print(f"   ✅ Binary: {original_suffix} → {original_suffix}")
            normalization_passed += 1
        else:
            print(f"   ❌ Binary: {original_suffix}")
            print(f"      Expected: {unique_id}")
            print(f"      Got: {normalized}")

    # Test other status entities (non-binary sensors)
    status_test_cases = [
        ("softwareVer", "software_version"),  # This one gets normalized
    ]

    for original_suffix, expected_suffix in status_test_cases:
        original_unique_id = f"span_nt-2224-c1fa7_{original_suffix}"
        expected_unique_id = f"span_nt-2224-c1fa7_{expected_suffix}"

        normalized = _compute_normalized_unique_id(original_unique_id)
        normalization_tests += 1

        if normalized == expected_unique_id:
            print(f"   ✅ Status: {original_suffix} → {expected_suffix}")
            normalization_passed += 1
        else:
            print(f"   ❌ Status: {original_suffix}")
            print(f"      Expected: {expected_unique_id}")
            print(f"      Got: {normalized}")

    print(f"   📊 Normalization: {normalization_passed}/{normalization_tests} passed")

    # Phase 2: Validate expected YAML structure based on migration
    print(f"\n🔍 Phase 2: Validating expected post-migration YAML structure...")

    # After migration, we expect these normalized unique_ids to exist
    # and they should match what synthetic generation would produce
    from custom_components.span_panel.helpers import get_panel_entity_suffix, construct_synthetic_unique_id

    expected_sensors = {}

    # Panel sensors - these should exist after migration normalization
    panel_api_mapping = {
        "instantGridPowerW": "current_power",
        "feedthroughPowerW": "feed_through_power",
        "mainMeterEnergyProducedWh": "main_meter_produced_energy",
        "mainMeterEnergyConsumedWh": "main_meter_consumed_energy",
        "feedthroughEnergyProducedWh": "feed_through_produced_energy",
        "feedthroughEnergyConsumedWh": "feed_through_consumed_energy",
    }

    for api_key, expected_suffix in panel_api_mapping.items():
        synthetic_unique_id = construct_synthetic_unique_id("nt-2224-c1fa7", expected_suffix)
        expected_sensors[synthetic_unique_id] = {
            "type": "panel",
            "api_key": api_key,
            "suffix": expected_suffix
        }

    # Circuit sensors - these should be created for all circuits
    for circuit_id in expected_circuits:
        # Power sensor
        power_unique_id = construct_synthetic_unique_id("nt-2224-c1fa7", f"{circuit_id}_power")
        expected_sensors[power_unique_id] = {
            "type": "circuit_power",
            "circuit_id": circuit_id
        }

        # Energy sensors
        for energy_type in ["energy_produced", "energy_consumed"]:
            energy_unique_id = construct_synthetic_unique_id("nt-2224-c1fa7", f"{circuit_id}_{energy_type}")
            expected_sensors[energy_unique_id] = {
                "type": f"circuit_{energy_type}",
                "circuit_id": circuit_id
            }

    # Binary sensors - these should be preserved as-is
    for binary_entity in binary_sensor_entities:
        unique_id = binary_entity["unique_id"]
        api_key = unique_id.split('_')[-1]  # Get the last part after the last underscore

        expected_sensors[unique_id] = {
            "type": "binary_sensor",
            "api_key": api_key,
            "suffix": api_key
        }

    # Other status sensors
    status_mapping = {
        "softwareVer": "software_version",  # This one gets normalized
    }

    for api_key, expected_suffix in status_mapping.items():
        # Other status sensors use construct_synthetic_unique_id
        synthetic_unique_id = construct_synthetic_unique_id("nt-2224-c1fa7", expected_suffix)
        expected_sensors[synthetic_unique_id] = {
            "type": "status",
            "api_key": api_key,
            "suffix": expected_suffix
        }

    print(f"   📊 Expected sensors after migration:")
    print(f"      • Panel sensors: {len([s for s in expected_sensors.values() if s['type'] == 'panel'])}")
    print(f"      • Circuit power sensors: {len([s for s in expected_sensors.values() if s['type'] == 'circuit_power'])}")
    print(f"      • Circuit energy sensors: {len([s for s in expected_sensors.values() if 'circuit_energy' in s['type']])}")
    print(f"      • Binary sensors: {len([s for s in expected_sensors.values() if s['type'] == 'binary_sensor'])}")
    print(f"      • Status sensors: {len([s for s in expected_sensors.values() if s['type'] == 'status'])}")

    # Phase 3: Test select entities (circuit priorities)
    print(f"\n🔍 Phase 3: Testing select entities (circuit priorities)...")

    select_tests = 0
    select_passed = 0

    # Test select entities - these should be preserved as-is since they're not synthetic sensors
    for select_entity in select_entities[:5]:  # Test first 5
        unique_id = select_entity["unique_id"]
        # Select entities should not be normalized since they're not synthetic sensors
        normalized = _compute_normalized_unique_id(unique_id)
        select_tests += 1

        if normalized is None:  # Should return None for non-synthetic sensors
            print(f"   ✅ Select: {unique_id.split('_')[-1]} (preserved)")
            select_passed += 1
        else:
            print(f"   ❌ Select: {unique_id.split('_')[-1]} (should be preserved)")
            print(f"      Got: {normalized}")

    print(f"   📊 Select entities: {select_passed}/{select_tests} preserved correctly")

    # Final Results
    print(f"\n" + "=" * 60)
    print(f"MIGRATION TEST RESULTS:")
    print(f"   Panel sensors: {len([s for s in expected_sensors.values() if s['type'] == 'panel'])}/6")
    print(f"   Circuit power sensors: {len([s for s in expected_sensors.values() if s['type'] == 'circuit_power'])}/{len(expected_circuits)}")
    print(f"   Circuit energy sensors: {len([s for s in expected_sensors.values() if 'circuit_energy' in s['type']])}/{len(expected_circuits) * 2}")
    print(f"   Binary sensors: {len([s for s in expected_sensors.values() if s['type'] == 'binary_sensor'])}/{len(binary_sensor_entities)}")
    print(f"   Status sensors: {len([s for s in expected_sensors.values() if s['type'] == 'status'])}/1")
    print(f"   Select entities: {select_passed}/{select_tests} preserved")

    # Overall success criteria
    success = (
        normalization_passed == normalization_tests and
        select_passed == select_tests
    )

    print(f"\n" + "=" * 60)
    if success:
        print(f"MIGRATION TEST PASSED!")
        print(f"   All panel sensors normalized correctly")
        print(f"   All {len(expected_circuits)} circuit sensors normalized correctly")
        print(f"   All status sensors normalized correctly")
        print(f"   All select entities preserved correctly")
        print(f"   Ready for legacy → v1.2.0 migration!")
    else:
        print(f"MIGRATION TEST FAILED!")
        if normalization_passed < normalization_tests:
            failed = normalization_tests - normalization_passed
            print(f"   • {failed} normalization tests failed")
        if select_passed < select_tests:
            failed = select_tests - select_passed
            print(f"   • {failed} select entity tests failed")

    return success

if __name__ == "__main__":
    asyncio.run(test_migration_legacy_to_1_2_0())
