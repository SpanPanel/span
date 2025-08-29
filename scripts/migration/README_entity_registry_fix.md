# SPAN Panel Entity Registry Fix Script

This script fixes entity ID naming inconsistencies caused by the old 240V circuit bug where some entities got circuit number naming instead of friendly names.

## Problem

Due to a bug in older versions of the SPAN Panel integration, some entities were created with circuit number naming (e.g.,
`select.span_panel_circuit_16_priority`) instead of friendly names (e.g., `select.span_panel_outlets_kitchen_priority`). This typically happened after
processing 240V circuits.

## Solution

The `fix_entity_registry_naming.py` script can fix these naming inconsistencies by renaming entities in the registry to match your preferred naming strategy.

## Usage

### Prerequisites

1. **Stop Home Assistant** - The script must be run while Home Assistant is stopped
2. **Locate your entity registry** - Usually at `/config/.storage/core.entity_registry`

### Basic Usage

```bash
# First, do a dry run to see what would be changed
python fix_entity_registry_naming.py \
  --registry /path/to/config/.storage/core.entity_registry \
  --use-friendly-names \
  --dry-run

# If the dry run looks correct, run it for real
python fix_entity_registry_naming.py \
  --registry /path/to/config/.storage/core.entity_registry \
  --use-friendly-names
```

### Options

- `--registry <path>`: Path to the `core.entity_registry` file (required)
- `--use-friendly-names`: Convert circuit number naming to friendly names
- `--use-circuit-numbers`: Convert friendly names to circuit number naming
- `--dry-run`: Show what would be changed without making changes

### Examples

**Convert to friendly names (recommended):**

```bash
python fix_entity_registry_naming.py \
  --registry /config/.storage/core.entity_registry \
  --use-friendly-names
```

**Convert to circuit numbers:**

```bash
python fix_entity_registry_naming.py \
  --registry /config/.storage/core.entity_registry \
  --use-circuit-numbers
```

**Check what would be changed:**

```bash
python fix_entity_registry_naming.py \
  --registry /config/.storage/core.entity_registry \
  --use-friendly-names \
  --dry-run
```

## Safety Features

- **Automatic backup**: Creates a `.backup` file before making changes
- **Dry run mode**: Test changes without applying them
- **SPAN Panel only**: Only affects SPAN Panel entities
- **Validation**: Checks file format and validates changes

## What Gets Fixed

The script identifies and fixes entities that have inconsistent naming patterns:

**Before (circuit numbers):**

- `select.span_panel_circuit_16_priority`
- `sensor.span_panel_circuit_19_power`

**After (friendly names):**

- `select.span_panel_outlets_kitchen_priority`
- `sensor.span_panel_range_oven_power`

## After Running

1. **Start Home Assistant** - The changes will take effect
2. **Check your entities** - Verify the naming is now consistent
3. **Update automations** - If you have automations referencing the old entity IDs, update them

## Troubleshooting

**Script won't run:**

- Make sure Home Assistant is stopped
- Check the registry file path is correct
- Ensure you have Python 3.6+ installed

**No entities found:**

- Verify the registry file contains SPAN Panel entities
- Check that the file path is correct

**Wrong changes made:**

- Restore from the `.backup` file that was created
- Run with `--dry-run` first to preview changes

## Support

If you encounter issues:

1. Check the script output for error messages
2. Verify your registry file path
3. Ensure Home Assistant is completely stopped
4. Try the dry run mode first
