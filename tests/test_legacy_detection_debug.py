#!/usr/bin/env python3
"""Debug test for legacy detection."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from custom_components.span_panel.migration import _detect_legacy_config_entry

def test_legacy_detection_debug():
    """Debug the legacy detection function."""

    print("🧪 Debugging Legacy Detection")
    print("=" * 40)

    # Load legacy registry data
    registry_file = Path(__file__).parent / "migration_storage" / "legacy" / "core.entity_registry"
    with open(registry_file, 'r') as f:
        registry_data = json.load(f)

    # Get SPAN Panel entities
    span_entities = [e for e in registry_data["data"]["entities"] if e.get("platform") == "span_panel"]
    print(f"📊 Found {len(span_entities)} SPAN Panel entities")

    # Create mock entities
    mock_entities = []
    for entity_data in span_entities[:10]:  # Test first 10
        mock_entity = MagicMock()
        mock_entity.platform = "span_panel"
        mock_entity.entity_id = entity_data["entity_id"]
        mock_entity.unique_id = entity_data["unique_id"]
        mock_entities.append(mock_entity)

    print(f"🔍 Created {len(mock_entities)} mock entities")
    print("Sample entities:")
    for i, entity in enumerate(mock_entities[:5]):
        print(f"  {i+1}. {entity.entity_id} -> {entity.unique_id}")

    # Test legacy detection
    result = _detect_legacy_config_entry(mock_entities)
    print(f"🎯 Legacy detection result: {result}")

    # Test with just one entity
    single_result = _detect_legacy_config_entry([mock_entities[0]])
    print(f"🎯 Single entity result: {single_result}")

if __name__ == "__main__":
    test_legacy_detection_debug()
