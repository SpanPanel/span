# Version 2 Migration Testing Strategy

## Overview

This document outlines the testing strategy for validating the v2 migration process using real entity registry data from a 1.0.10 SPAN Panel installation. The
goal is to create a test harness that closely mimics the production migration process and validates that YAML generation produces the correct sensor
configurations with preserved entity IDs.

## Current State Analysis

### Available Test Data

Located in `/Users/bflood/projects/HA/span/tests/migration_storage/1_0_10/`:

- **`core.config_entries`**: Contains one SPAN Panel config entry (`01K2JBCXNSB9Q489RMG3XTMTJE`) with device identifier `nj-2316-005k6`
- **`core.entity_registry`**: Contains live entity registry with:
  - Panel sensors (e.g., `span_nj-2316-005k6_instantGridPowerW`)
  - Circuit sensors (22 circuits with power sensors using format `span_nj-2316-005k6_{circuit_id}_instantPowerW`)
  - Energy sensors with dotted notation (e.g., `span_nj-2316-005k6_mainMeterEnergy.producedEnergyWh`)
  - Solar and battery configuration enabled in config entry options
- **`core.device_registry`**: Device registry data for the SPAN Panel device

### Migration Challenge

The current v1.0.10 installation uses:

- Legacy unique_id format with dotted notation for energy sensors
- Circuit IDs that are UUIDs rather than circuit numbers
- Named circuits that should generate power values but currently show as "no power"

## Testing Strategy

### Phase 1: Registry-Based Test Infrastructure

#### 1.1 Test Registry Isolation

Create a test harness that:

- Copies the migration storage to a temporary test location for each test run
- Provides isolated entity and device registry instances for testing
- Avoids contaminating the base migration data

```python
# Proposed test structure
def setup_migration_test_registries():
    """Copy migration registries to temporary test storage."""
    test_storage_dir = tempfile.mkdtemp(prefix="span_migration_test_")
    source_dir = Path("tests/migration_storage/1_0_10")

    # Copy registries to test location
    shutil.copy(source_dir / "core.entity_registry", test_storage_dir)
    shutil.copy(source_dir / "core.device_registry", test_storage_dir)
    shutil.copy(source_dir / "core.config_entries", test_storage_dir)

    return test_storage_dir
```

#### 1.2 Mock Home Assistant Environment

Create a minimal Home Assistant environment that:

- Uses the copied registries
- Provides access to entity registry operations
- Supports the migration flag mechanics
- Enables storage manager operations

### Phase 2: Migration Process Testing

#### 2.1 Unique ID Normalization Test

**Objective**: Validate that Phase 1 of migration correctly normalizes unique_ids

**Test Steps**:

1. Load entity registry from test storage
2. Run unique_id normalization for the SPAN Panel config entry
3. Verify transformations:
   - `span_nj-2316-005k6_mainMeterEnergy.producedEnergyWh` → `span_nj-2316-005k6_main_meter_produced_energy`
   - `span_nj-2316-005k6_feedthroughEnergy.producedEnergyWh` → `span_nj-2316-005k6_feed_through_produced_energy`
   - Circuit power sensors: `span_nj-2316-005k6_{circuit_id}_instantPowerW` → `span_nj-2316-005k6_{circuit_id}_power`

**Expected Outcome**: All sensor unique_ids normalized to helper format while preserving entity_ids

#### 2.2 Migration Flag and Reload Simulation

**Objective**: Simulate the migration flag setting and reload process

**Test Steps**:

1. Set per-entry migration flag for the test config entry
2. Simulate integration reload
3. Verify migration flag is properly detected during setup

#### 2.3 YAML Generation Test (Migration Mode)

**Objective**: Validate complete YAML generation using real registry data

**Test Steps**:

1. Create modified version of `generate_complete_config.py` that:
   - Uses test registries instead of mock data
   - Operates in migration mode (flag set)
   - Performs entity_id resolution via registry lookup
   - Generates YAML for the actual device configuration

2. Mock the SPAN Panel coordinator and data:
   - Use real device identifier (`nj-2316-005k6`)
   - Create realistic panel data matching the entity count
   - Generate circuit data for all 22 circuits found in registry

3. Run YAML generation and validate:
   - All panel sensors are generated with correct sensor keys
   - All circuit sensors are generated with power data
   - Entity IDs are preserved from registry
   - Solar and battery sensors are included (enabled in config)

### Phase 3: Modified Test Script Design

#### 3.1 Enhanced generate_complete_config.py

Create `tests/scripts/generate_migration_test_config.py` that:

```python
#!/usr/bin/env python3
"""Generate complete sensor YAML configuration using real migration registry data.

This script tests the v2 migration process by:
1. Loading real entity registry data from v1.0.10 installation
2. Normalizing unique_ids as in migration Phase 1
3. Generating YAML in migration mode with registry entity_id lookups
4. Validating that named circuits get proper power values
"""

async def generate_migration_yaml():
    """Generate YAML using migration registry data."""

    # Setup test environment
    test_storage = setup_migration_test_registries()
    mock_hass = create_mock_hass_with_registries(test_storage)

    # Load actual config entry
    config_entry = load_test_config_entry()  # 01K2JBCXNSB9Q489RMG3XTMTJE

    # Set migration mode flag
    mock_hass.data[DOMAIN] = {config_entry.entry_id: {"migration_mode": True}}

    # Normalize unique_ids (Phase 1)
    await normalize_entity_unique_ids(mock_hass, config_entry)

    # Create realistic panel data from registry inspection
    panel_data = create_panel_data_from_registry(mock_hass, config_entry)

    # Generate YAML in migration mode
    yaml_content = await generate_yaml_migration_mode(
        mock_hass, config_entry, panel_data
    )

    # Validate results
    validate_migration_yaml(yaml_content, mock_hass, config_entry)

    return yaml_content
```

#### 3.2 Registry Data Mapping

Extract real circuit information from the registry:

```python
def extract_circuits_from_registry(entity_registry, config_entry_id):
    """Extract circuit information from existing entity registry."""
    circuits = {}

    # Find all power sensors for circuits
    for entity in entity_registry.entities.values():
        if (entity.config_entry_id == config_entry_id and
            entity.platform == "span_panel" and
            "_instantPowerW" in entity.unique_id):

            # Parse circuit info from unique_id
            circuit_id = extract_circuit_id_from_unique_id(entity.unique_id)
            circuit_name = extract_circuit_name_from_entity_name(entity.original_name)

            circuits[circuit_id] = {
                "name": circuit_name,
                "entity_id": entity.entity_id,
                "unique_id": entity.unique_id
            }

    return circuits
```

### Phase 4: Validation Framework

#### 4.1 YAML Content Validation

Validate the generated YAML contains:

- Correct sensor count matching registry entities
- Proper sensor keys in helper format
- Entity IDs preserved from original registry
- Named circuits with realistic power values (not zero)
- Solar sensors if configured
- Battery sensors if configured

#### 4.2 Entity ID Preservation Validation

```python
def validate_entity_id_preservation(yaml_content, original_registry):
    """Verify all entity_ids are preserved from original registry."""

    # Parse generated YAML
    yaml_data = yaml.safe_load(yaml_content)
    generated_sensors = yaml_data["sensors"]

    # Check each sensor has correct entity_id
    for sensor_key, sensor_config in generated_sensors.items():
        entity_id = sensor_config.get("entity_id")

        # Find corresponding original entity
        original_entity = find_original_entity_by_sensor_key(
            sensor_key, original_registry
        )

        assert entity_id == original_entity.entity_id, \
            f"Entity ID mismatch for {sensor_key}: {entity_id} != {original_entity.entity_id}"
```

#### 4.3 Power Data Validation

Ensure named circuits get realistic power values:

```python
def validate_circuit_power_generation(yaml_content):
    """Verify circuits have non-zero power capability."""

    yaml_data = yaml.safe_load(yaml_content)
    circuit_sensors = {k: v for k, v in yaml_data["sensors"].items()
                      if "_power" in k and "circuit" in k}

    for sensor_key, sensor_config in circuit_sensors.items():
        # Verify backing entity and template configuration
        assert "backing_entity" in sensor_config
        assert "state_template" in sensor_config

        # Check that template doesn't default to 0
        template = sensor_config["state_template"]
        assert "0" not in template or "|default(0)" not in template
```

## Implementation Checklist

### Test Infrastructure

- [ ] Create registry copying utilities
- [ ] Implement mock Home Assistant environment with real registries
- [ ] Create entity registry loading and manipulation functions

### Migration Logic Testing

- [ ] Test unique_id normalization with real registry data
- [ ] Test migration flag mechanics
- [ ] Test registry entity_id lookup functionality

### YAML Generation Testing

- [ ] Modify generate_complete_config.py for migration testing
- [ ] Implement registry-based panel data creation
- [ ] Test solar and battery sensor generation in migration mode

### Validation Framework

- [ ] Entity ID preservation validation
- [ ] Circuit power generation validation
- [ ] YAML structure and content validation
- [ ] Integration with existing test suite

## Expected Outcomes

After implementing this testing strategy:

1. **Migration Validation**: Confirm that the v2 migration process correctly handles real v1.0.10 installations
2. **Entity ID Preservation**: Verify that all existing entity IDs are maintained through the migration
3. **Power Data Fix**: Validate that named circuits generate proper power values instead of "no power"
4. **Solar/Battery Handling**: Ensure solar and battery sensors are correctly migrated when configured
5. **Production Readiness**: Provide confidence that the migration process will work correctly for real users

## Integration with Existing Tests

This migration testing strategy should:

- Complement existing simulation-based tests
- Provide real-world validation of migration logic
- Be runnable as part of the standard test suite
- Generate artifacts for manual inspection and validation

The test results will help ensure that the v2 migration process is reliable and preserves user configurations correctly 🧪
