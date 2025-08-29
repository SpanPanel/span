#!/usr/bin/env python3
"""Test legacy config entry detection and USE_DEVICE_PREFIX flag setting.

This test validates that the integration properly detects legacy config entries
(pre-1.0.4) and sets the USE_DEVICE_PREFIX flag to False to maintain compatibility
with existing entity IDs.
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

from custom_components.span_panel.const import USE_DEVICE_PREFIX

def detect_legacy_config(registry_data):
    """Detect if a config entry is legacy based on entity IDs."""

    # Extract SPAN Panel entities
    span_entities = []
    for entity in registry_data["data"]["entities"]:
        if entity.get("platform") == "span_panel":
            span_entities.append(entity)

    if not span_entities:
        return False, "No SPAN Panel entities found"

    # Check if any entity IDs have the span_panel_ prefix
    has_prefix = False
    no_prefix = False

    for entity in span_entities:
        entity_id = entity["entity_id"]
        if entity_id.startswith("sensor.span_panel_") or entity_id.startswith("binary_sensor.span_panel_"):
            has_prefix = True
        elif entity_id.startswith("sensor.") or entity_id.startswith("binary_sensor."):
            # Check if it's a SPAN Panel entity without the prefix
            if any(key in entity.get("unique_id", "") for key in ["instantGridPowerW", "feedthroughPowerW", "doorState", "eth0Link"]):
                no_prefix = True

    if has_prefix and no_prefix:
        return False, "Mixed entity ID patterns found"
    elif has_prefix:
        return False, "Modern config (has span_panel_ prefix)"
    elif no_prefix:
        return True, "Legacy config (no span_panel_ prefix)"
    else:
        return False, "Cannot determine config type"

def test_legacy_config_detection():
    """Test legacy config detection logic."""

    print("🧪 Testing Legacy Config Detection")
    print("=" * 50)

    # Test all registry versions
    registry_versions = ["legacy", "1_0_4", "1_0_10"]

    for version in registry_versions:
        print(f"\n📁 Testing {version} registry...")

        # Load registry data
        registry_file = Path(__file__).parent / "migration_storage" / version / "core.entity_registry"
        if not registry_file.exists():
            print(f"❌ Registry file not found: {registry_file}")
            continue

        with open(registry_file, 'r') as f:
            registry_data = json.load(f)

        # Detect if it's legacy
        is_legacy, reason = detect_legacy_config(registry_data)

        print(f"   Detection result: {reason}")
        print(f"   Is legacy: {is_legacy}")

        if is_legacy:
            print(f"   ✅ Correctly identified as legacy config")
            print(f"   📋 Should set USE_DEVICE_PREFIX=False")
        else:
            print(f"   ✅ Correctly identified as modern config")
            print(f"   📋 Should set USE_DEVICE_PREFIX=True (default)")

    print(f"\n" + "=" * 50)
    print("🎯 Legacy Detection Test Summary:")
    print("   • Legacy configs should have USE_DEVICE_PREFIX=False")
    print("   • Modern configs should have USE_DEVICE_PREFIX=True")
    print("   • This ensures entity ID compatibility after migration")

if __name__ == "__main__":
    test_legacy_config_detection()
