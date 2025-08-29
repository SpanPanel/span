#!/usr/bin/env python3
"""Offline script to fix SPAN Panel entity registry naming issues.

This script fixes entity ID naming inconsistencies caused by the old 240V circuit bug
where some entities got circuit number naming instead of friendly names.

Usage:
    python fix_entity_registry_naming.py --registry /path/to/core.entity_registry --use-friendly-names
    python fix_entity_registry_naming.py --registry /path/to/core.entity_registry --use-circuit-numbers
    python fix_entity_registry_naming.py --registry /path/to/core.entity_registry --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_entity_registry(registry_path: Path) -> Dict[str, Any]:
    """Load the entity registry JSON file."""
    try:
        with open(registry_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading entity registry: {e}")
        sys.exit(1)


def save_entity_registry(registry_path: Path, data: Dict[str, Any]) -> None:
    """Save the entity registry JSON file."""
    try:
        # Create backup
        backup_path = registry_path.with_suffix('.backup')
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Backup created: {backup_path}")

        # Save updated registry
        with open(registry_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Updated registry saved: {registry_path}")
    except Exception as e:
        print(f"Error saving entity registry: {e}")
        sys.exit(1)


def is_span_panel_entity(entity: Dict[str, Any]) -> bool:
    """Check if entity belongs to SPAN Panel integration."""
    return entity.get('platform') == 'span_panel'


def extract_circuit_info_from_entity_id(entity_id: str) -> Tuple[str, str, str]:
    """Extract platform, circuit info, and suffix from entity ID.

    Returns:
        (platform, circuit_part, suffix)
    """
    # Handle patterns like:
    # sensor.span_panel_circuit_16_power -> (sensor, circuit_16, power)
    # select.span_panel_outlets_kitchen_priority -> (select, outlets_kitchen, priority)
    # sensor.span_panel_current_power -> (sensor, current, power)

    # Split by dots first to get platform
    dot_parts = entity_id.split('.')
    if len(dot_parts) != 2:
        return None, None, None

    platform = dot_parts[0]
    entity_part = dot_parts[1]

    # Check if it's a SPAN Panel entity
    if not entity_part.startswith('span_panel_'):
        return None, None, None

    # Remove span_panel_ prefix
    entity_part = entity_part[12:]  # len('span_panel_') = 12

    # Split by underscores to find suffix
    parts = entity_part.split('_')
    if len(parts) < 2:
        return None, None, None

    # Last part is usually the suffix
    suffix = parts[-1]

    # Everything else is the circuit part
    circuit_parts = parts[:-1]
    circuit_part = '_'.join(circuit_parts)

    return platform, circuit_part, suffix


def should_rename_entity(entity_id: str, use_friendly_names: bool) -> bool:
    """Determine if entity should be renamed based on naming strategy."""
    platform, circuit_part, suffix = extract_circuit_info_from_entity_id(entity_id)

    if not platform or not circuit_part or not suffix:
        return False

    # Check if this is a circuit entity that might need renaming
    if circuit_part.startswith('circuit_'):
        # Currently using circuit numbers, but should use friendly names
        return use_friendly_names
    else:
        # Currently using friendly names, but should use circuit numbers
        return not use_friendly_names


def get_circuit_number_from_entity_id(entity_id: str) -> str:
    """Extract circuit number from entity ID like 'circuit_16'."""
    platform, circuit_part, suffix = extract_circuit_info_from_entity_id(entity_id)

    if circuit_part and circuit_part.startswith('circuit_'):
        # Extract number from circuit_16 -> 16
        match = re.match(r'circuit_(\d+)', circuit_part)
        if match:
            return match.group(1)

    return None


def get_friendly_name_from_entity_id(entity_id: str) -> str:
    """Extract friendly name from entity ID."""
    platform, circuit_part, suffix = extract_circuit_info_from_entity_id(entity_id)

    if circuit_part and not circuit_part.startswith('circuit_'):
        return circuit_part

    return None


# Circuit number to friendly name mapping based on the registry analysis
CIRCUIT_TO_FRIENDLY_MAPPING = {
    "16": "outlets_kitchen",
    "19": "range_oven",
    # Add more mappings as needed based on your specific installation
}

# Reverse mapping for friendly names to circuit numbers
FRIENDLY_TO_CIRCUIT_MAPPING = {v: k for k, v in CIRCUIT_TO_FRIENDLY_MAPPING.items()}


def construct_new_entity_id(platform: str, circuit_part: str, suffix: str,
                          use_friendly_names: bool, circuit_number: str = None,
                          friendly_name: str = None) -> str:
    """Construct new entity ID based on naming strategy."""
    if use_friendly_names:
        if friendly_name:
            new_circuit_part = friendly_name
        elif circuit_number and circuit_number in CIRCUIT_TO_FRIENDLY_MAPPING:
            new_circuit_part = CIRCUIT_TO_FRIENDLY_MAPPING[circuit_number]
        else:
            # Fallback to circuit number if no mapping exists
            new_circuit_part = f"circuit_{circuit_number}" if circuit_number else circuit_part
    else:
        if circuit_number:
            new_circuit_part = f"circuit_{circuit_number}"
        elif friendly_name and friendly_name in FRIENDLY_TO_CIRCUIT_MAPPING:
            new_circuit_part = f"circuit_{FRIENDLY_TO_CIRCUIT_MAPPING[friendly_name]}"
        else:
            # Fallback to friendly name if no mapping exists
            new_circuit_part = circuit_part

    return f"{platform}.span_panel_{new_circuit_part}_{suffix}"


def fix_entity_registry(data: Dict[str, Any], use_friendly_names: bool,
                       dry_run: bool = False) -> List[Tuple[str, str]]:
    """Fix entity registry naming inconsistencies.

    Returns:
        List of (old_entity_id, new_entity_id) tuples for renamed entities
    """
    renamed_entities = []

    for entity_id, entity_data in data.get('data', {}).get('entities', {}).items():
        if not is_span_panel_entity(entity_data):
            continue

        if not should_rename_entity(entity_id, use_friendly_names):
            continue

        # Extract current naming info
        platform, circuit_part, suffix = extract_circuit_info_from_entity_id(entity_id)

        if not platform or not circuit_part or not suffix:
            continue

        # Determine new entity ID
        if use_friendly_names and circuit_part.startswith('circuit_'):
            # Convert circuit_16 to friendly name
            circuit_number = get_circuit_number_from_entity_id(entity_id)
            if circuit_number:
                # This would need a mapping from circuit numbers to friendly names
                # For now, we'll use a placeholder
                friendly_name = f"circuit_{circuit_number}_friendly"  # Placeholder
                new_entity_id = construct_new_entity_id(platform, circuit_part, suffix,
                                                       True, circuit_number, friendly_name)
            else:
                continue
        elif not use_friendly_names and not circuit_part.startswith('circuit_'):
            # Convert friendly name to circuit number
            # This would need a mapping from friendly names to circuit numbers
            # For now, we'll use a placeholder
            circuit_number = "unknown"  # Placeholder
            new_entity_id = construct_new_entity_id(platform, circuit_part, suffix,
                                                   False, circuit_number)
        else:
            continue

        if new_entity_id != entity_id:
            if dry_run:
                print(f"Would rename: {entity_id} -> {new_entity_id}")
            else:
                # Update the entity registry
                data['data']['entities'][new_entity_id] = entity_data
                del data['data']['entities'][entity_id]
                print(f"Renamed: {entity_id} -> {new_entity_id}")

            renamed_entities.append((entity_id, new_entity_id))

    return renamed_entities


def main():
    parser = argparse.ArgumentParser(
        description="Fix SPAN Panel entity registry naming issues"
    )
    parser.add_argument(
        '--registry',
        type=Path,
        required=True,
        help='Path to core.entity_registry file'
    )
    parser.add_argument(
        '--use-friendly-names',
        action='store_true',
        help='Convert circuit number naming to friendly names'
    )
    parser.add_argument(
        '--use-circuit-numbers',
        action='store_true',
        help='Convert friendly names to circuit number naming'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without making changes'
    )

    args = parser.parse_args()

    if not args.registry.exists():
        print(f"Registry file not found: {args.registry}")
        sys.exit(1)

    if not args.use_friendly_names and not args.use_circuit_numbers:
        print("Must specify either --use-friendly-names or --use-circuit-numbers")
        sys.exit(1)

    if args.use_friendly_names and args.use_circuit_numbers:
        print("Cannot specify both --use-friendly-names and --use-circuit-numbers")
        sys.exit(1)

    print(f"Loading entity registry: {args.registry}")
    data = load_entity_registry(args.registry)

    print(f"Fixing SPAN Panel entities...")
    print(f"Strategy: {'Friendly names' if args.use_friendly_names else 'Circuit numbers'}")
    print(f"Mode: {'Dry run' if args.dry_run else 'Live'}")

    renamed_entities = fix_entity_registry(data, args.use_friendly_names, args.dry_run)

    if renamed_entities:
        print(f"\nRenamed {len(renamed_entities)} entities:")
        for old_id, new_id in renamed_entities:
            print(f"  {old_id} -> {new_id}")

        if not args.dry_run:
            print(f"\nSaving updated registry...")
            save_entity_registry(args.registry, data)
            print("Done!")
        else:
            print(f"\nDry run completed. No changes made.")
    else:
        print("No entities need to be renamed.")


if __name__ == '__main__':
    main()
