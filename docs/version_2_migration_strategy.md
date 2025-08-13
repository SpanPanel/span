# Version 2 Migration Strategy

## Overview

This document outlines the migration strategy for upgrading existing SPAN Panel installations to version 2, which introduces synthetic sensors with YAML-based
configuration. The primary goal is to preserve existing entity IDs and user configurations while seamlessly transitioning to the new synthetic sensor
architecture.

## Current Migration Issues

The existing migration logic in `__init__.py` attempts to normalize unique IDs to a new format, but this approach has several problems:

1. **Unnecessary Changes**: Existing unique IDs are already unique and functional
2. **UI Disruption**: Entity IDs are the primary means of identifying circuits in the UI and must remain unchanged
3. **Missing YAML Generation**: The migration doesn't address the core need to generate YAML configurations from existing installations

## Proposed Migration Strategy

### Core Principles

1. **Config Entry-Driven Migration**: Start with existing config entries as the only known data source
2. **Per-Device YAML Generation**: Generate complete YAML configuration for each device/config entry independently
3. **Preserve All Identifiers**: Keep existing unique IDs as sensor keys and preserve all entity IDs
4. **Storage-Based Persistence**: Store generated YAML in ha-synthetic-sensors storage for each device
5. **Seamless Boot Transition**: After YAML generation, boot normally as if it were a pre-configured installation

### Migration Reality

Existing installations have:

- **One or more config entries** (each representing a SPAN Panel device)
- **Device, host, and token information** in each config entry
- **Existing entities** in the entity registry associated with each config entry
- **No knowledge of YAML/synthetic sensor process**

The migration must:

- **Discover all config entries** for the SPAN Panel domain
- **For each config entry**: Generate complete YAML configuration for that device's sensor set
- **Store YAML configurations** in ha-synthetic-sensors storage using device identifiers
- **Preserve all existing identifiers** (unique IDs become sensor keys, entity IDs preserved)
- **Continue normal boot** as if the installation had always been configured with synthetic sensors

### Migration Process

#### Phase 1: Multi-Device Discovery and Analysis

The migration happens during the `async_migrate_entry` call for each config entry. However, we need to coordinate across all config entries to set up the
ha-synthetic-sensors storage properly.

```python
async def migrate_config_entry_to_synthetic_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry
) -> bool:
    """Migrate a single config entry to synthetic sensor configuration."""

    # Only migrate if version is less than 2
    if config_entry.version >= 2:
        return True

    _LOGGER.info(
        "Migrating config entry %s from version %s to version 2 for synthetic sensors",
        config_entry.entry_id,
        config_entry.version
    )

    # Analyze existing entities for this config entry
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

    # Extract device identifier from config entry
    device_identifier = extract_device_identifier_from_config_entry(config_entry, entities)

    # Group and analyze entities
    sensor_entities = []
    for entity in entities:
        if entity.domain == "sensor":
            sensor_entities.append({
                "entity_id": entity.entity_id,
                "unique_id": entity.unique_id,
                "name": entity.name,
                "unit_of_measurement": entity.unit_of_measurement,
                "device_class": entity.device_class,
                "original_name": entity.original_name
            })

    # Generate YAML configuration for this device
    yaml_content = await generate_device_yaml_from_entities(
        config_entry, device_identifier, sensor_entities
    )

    # Initialize or get existing storage manager
    storage_manager = StorageManager(hass, DOMAIN, integration_domain=DOMAIN)
    await storage_manager.async_load()

    # Create sensor set for this device
    sensor_set_id = f"{device_identifier}_sensors"

    if not storage_manager.sensor_set_exists(sensor_set_id):
        await storage_manager.async_create_sensor_set(
            sensor_set_id=sensor_set_id,
            device_identifier=device_identifier,
            name=f"SPAN Panel {device_identifier}",
            description=f"SPAN Panel synthetic sensors (migrated from v1) - {config_entry.title}"
        )
        _LOGGER.info("Created sensor set %s for device %s", sensor_set_id, device_identifier)

    # Import YAML configuration for this device
    await storage_manager.async_from_yaml(
        yaml_content=yaml_content,
        sensor_set_id=sensor_set_id,
        device_identifier=device_identifier,
        replace_existing=True
    )

    # Update config entry version
    config_entry.version = 2

    _LOGGER.info(
        "Successfully migrated config entry %s to synthetic sensors with sensor set %s",
        config_entry.entry_id,
        sensor_set_id
    )

    return True
```

#### Phase 2: Device Identifier Extraction

```python
def extract_device_identifier_from_config_entry(
    config_entry: ConfigEntry,
    entities: list[er.RegistryEntry]
) -> str:
    """Extract device identifier from config entry and entities."""

    # Method 1: Try to get from existing entities' unique IDs
    for entity in entities:
        if entity.unique_id and entity.unique_id.startswith("span_"):
            # Extract serial number from unique ID pattern: span_{serial}_...
            parts = entity.unique_id.split("_")
            if len(parts) >= 2:
                potential_serial = parts[1]
                # Validate it looks like a serial number
                if len(potential_serial) > 3 and not potential_serial.isdigit():
                    return potential_serial

    # Method 2: Fall back to config entry data
    if "device_identifier" in config_entry.data:
        return config_entry.data["device_identifier"]

    # Method 3: Use host as fallback
    host = config_entry.data.get(CONF_HOST, "unknown")
    return f"span_{host.replace('.', '_')}"
```

#### Phase 3: YAML Generation for Each Device

```python
async def generate_device_yaml_from_entities(
    config_entry: ConfigEntry,
    device_identifier: str,
    sensor_entities: list[dict[str, Any]]
) -> str:
    """Generate complete YAML configuration for a single device."""

    # Build global settings
    global_settings = {
        "device_identifier": device_identifier,
        "energy_grace_period": 300,  # Default 5 minutes
        "use_device_prefix": config_entry.options.get(USE_DEVICE_PREFIX, False)
    }

    # Generate sensor configurations using existing unique IDs as sensor keys
    sensor_configs = {}

    for sensor_entity in sensor_entities:
        sensor_key = sensor_entity["unique_id"]  # Existing unique ID becomes sensor key

        # Generate formula based on sensor type
        formula = generate_formula_for_existing_sensor(sensor_entity)

        # Generate backing entities based on sensor type
        backing_entities = generate_backing_entities_for_existing_sensor(sensor_entity)

        sensor_configs[sensor_key] = {
            "entity_id": sensor_entity["entity_id"],  # Preserve exact entity ID
            "name": sensor_entity["name"] or sensor_entity["original_name"] or sensor_entity["entity_id"],
            "unit_of_measurement": sensor_entity["unit_of_measurement"],
            "device_class": sensor_entity["device_class"],
            "formula": formula,
            "backing_entities": backing_entities
        }

    # Use existing template system to construct complete YAML
    yaml_content = construct_complete_yaml_config(sensor_configs, global_settings)

    return yaml_content
```

After migration, the normal boot process takes over. The key is that after migration, each config entry will find that:

1. **Storage manager exists and is populated** with sensor sets for each device
2. **YAML configurations are already stored** for each device identifier
3. **Boot process proceeds normally** as if it were a pre-configured installation

```python
# In async_setup_entry (modified)
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry (works for both fresh and migrated installations)."""

    # ... existing setup code (API client, coordinator, etc.) ...

    # Set up synthetic sensors - this works the same for fresh and migrated installations
    # The migration will have already populated the storage if this was a v1 upgrade
    storage_manager = await setup_synthetic_configuration(hass, entry, coordinator)
    sensor_manager = await setup_synthetic_sensors(
        hass=hass,
        config_entry=entry,
        async_add_entities=async_add_entities,
        coordinator=coordinator,
        storage_manager=storage_manager,
    )

    # ... continue with normal setup ...
```

The beauty of this approach is that `setup_synthetic_configuration` will:

- Find existing sensor sets in storage (created during migration)
- Use the stored YAML configurations (generated during migration)
- Create synthetic sensors with preserved entity IDs and names

### Multi-Device Migration Strategy

For installations with multiple SPAN Panels, each config entry is migrated independently:

```python
# In async_migrate_entry (called for each config entry)
async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry for synthetic sensor YAML generation."""

    return await migrate_config_entry_to_synthetic_sensors(hass, config_entry)
```

Each device gets its own:

- **Device identifier** (extracted from existing entities or config)
- **Sensor set** in storage (`{device_identifier}_sensors`)
- **YAML configuration** with preserved entity IDs and unique IDs

### Boot Process After Migration

1. **Migration completes** for all config entries before any setup begins
2. **Each config entry boots normally** using `async_setup_entry`
3. **Synthetic sensor setup finds existing storage** for each device
4. **Sensors are created with preserved identifiers** from the stored YAML
5. **Users see no difference** in their dashboards or automations

### Version-Specific Handling

#### Pre-1.0.4 Installations (Legacy)

```python
def handle_legacy_installation(migration_info: MigrationInfo) -> dict[str, Any]:
    """Handle installations prior to version 1.0.4."""

    return {
        "use_device_prefix": False,  # Preserve legacy naming
        "use_circuit_numbers": False,
        "migration_flags": {
            "legacy_naming": True,
            "preserve_entity_ids": True
        }
    }
```

#### Post-1.0.4 Installations

```python
def handle_modern_installation(migration_info: MigrationInfo) -> dict[str, Any]:
    """Handle installations after version 1.0.4."""

    return {
        "use_device_prefix": migration_info.use_device_prefix,
        "use_circuit_numbers": migration_info.use_circuit_numbers,
        "migration_flags": {
            "legacy_naming": False,
            "preserve_entity_ids": True
        }
    }
```

### Integration with Existing Code

#### Modified `async_setup_entry`

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry with migration support."""

    # ... existing setup code ...

    # Check if migration is needed
    migration_info = await detect_migration_needs(entry)

    if migration_info.needs_migration:
        _LOGGER.info("Detected version 2 migration needed for entry %s", entry.entry_id)

        # Analyze existing entities
        entity_analysis = await analyze_existing_entities(hass, entry)

        # Generate YAML configuration
        yaml_content = await generate_yaml_from_existing(
            hass, entry, entity_analysis, migration_info
        )

        # Set up synthetic sensors with generated configuration
        storage_manager = await setup_synthetic_sensors_for_migration(
            hass, entry, coordinator, yaml_content
        )

        # Store migration info for future reference
        entry.data["migration_completed"] = True
        entry.data["migration_version"] = 2

    else:
        # Normal setup for fresh installations or already migrated
        storage_manager = await setup_synthetic_configuration(hass, entry, coordinator)

    # ... continue with existing setup ...
```

### Template System Integration

The migration process leverages the existing template system used for new installations:

1. **Global Settings Template**: Uses existing device identifier and default energy grace period
2. **Sensor Configuration Template**: Uses existing unique IDs as sensor keys and entity IDs
3. **Formula Generation**: Generates appropriate formulas based on sensor type and existing configuration
4. **Backing Entity Mapping**: Creates backing entities that map to existing SPAN Panel data paths

### Validation and Testing

#### Migration Validation

```python
async def validate_migration(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    storage_manager: StorageManager
) -> MigrationValidationResult:
    """Validate that migration was successful."""

    # Check that all existing entities are preserved
    entity_registry = er.async_get(hass)
    original_entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

    # Verify synthetic sensors are working
    sensor_set = storage_manager.get_sensor_set(f"{device_identifier}_sensors")
    synthetic_sensors = sensor_set.list_sensors()

    # Compare entity counts and names
    validation_result = MigrationValidationResult(
        entities_preserved=len(original_entities) == len(synthetic_sensors),
        yaml_generated=sensor_set is not None,
        migration_successful=True
    )

    return validation_result
```

### Rollback Strategy

If migration fails, the system should:

1. **Preserve Original State**: Never modify existing entities during migration
2. **Log Detailed Errors**: Provide comprehensive error information
3. **Allow Manual Recovery**: Enable users to manually configure synthetic sensors
4. **Maintain Functionality**: Ensure the integration continues to work with native sensors

### Implementation Timeline

1. **Phase 1**: Implement migration detection and analysis
2. **Phase 2**: Develop YAML generation from existing entities
3. **Phase 3**: Integrate with existing synthetic sensor setup
4. **Phase 4**: Add validation and rollback mechanisms
5. **Phase 5**: Comprehensive testing with various legacy installations

### Benefits of This Approach

1. **Zero UI Disruption**: Entity IDs remain unchanged
2. **Seamless Upgrade**: Users see no difference in their dashboards
3. **Preserved Configuration**: All existing sensor names and settings are maintained
4. **Version Compatibility**: Handles different legacy versions appropriately
5. **Future-Proof**: Sets up proper synthetic sensor architecture for future enhancements

### Critical Implementation Findings

#### sensor_to_backing_mapping Must Be Provided Every Boot

**Key Discovery**: The `sensor_to_backing_mapping` is NOT persisted by the synthetic sensors package. It's stored as an in-memory instance variable in `SensorManager` and must be recreated on every Home Assistant restart.

**Evidence**:
- `SensorManager._sensor_to_backing_mapping` starts empty on each boot
- Fresh install debug shows mapping like: `{'span_nj-2316-005k6_current_power': 'sensor.span_nj-2316-005k6_0_backing_current_power', ...}`
- All YAML templates use `formula: "state"` which requires the mapping to resolve backing entities
- Synthetic sensors package calls `register_sensor_to_backing_mapping()` automatically when mapping is provided

**Implications**:
- **Fresh installs**: Must recreate mapping every boot (likely through generation process)
- **Migration**: Must reconstruct mapping from entity registry data and provide it every boot
- **Storage**: Only YAML is persisted; mapping must be regenerated from available data

#### Migration Mapping Reconstruction

**Pattern Analysis** from real fresh install:
```python
# Panel sensors (circuit_id = "0")
'span_nj-2316-005k6_current_power' → 'sensor.span_nj-2316-005k6_0_backing_current_power'
'span_nj-2316-005k6_feed_through_power' → 'sensor.span_nj-2316-005k6_0_backing_feed_through_power'

# Circuit sensors (using UUID as circuit_id)  
'span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_power' → 'sensor.span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_backing_power'
```

**Old Entity Registry Patterns** (what migration will find):
```python
# Panel sensors use raw API field names
'span_nj-2316-005k6_instantGridPowerW' → 'sensor.span_nj-2316-005k6_0_backing_current_power'
'span_nj-2316-005k6_feedthroughPowerW' → 'sensor.span_nj-2316-005k6_0_backing_feed_through_power'

# Circuit sensors use raw API field names
'span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_instantPowerW' → 'sensor.span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_backing_power'
```

**Mapping Function** (implemented in `migration.py`):
```python
def reconstruct_sensor_to_backing_mapping(device_identifier: str, sensor_mappings: dict[str, str]) -> dict[str, str]:
    # Maps old raw API field names to new backing suffixes
    api_to_backing_suffix = {
        # Panel sensors (circuit_id = "0")
        "instantGridPowerW": "current_power",
        "feedthroughPowerW": "feed_through_power", 
        "mainMeterEnergy.producedEnergyWh": "main_meter_produced_energy",
        "mainMeterEnergy.consumedEnergyWh": "main_meter_consumed_energy",
        "feedthroughEnergy.producedEnergyWh": "feed_through_produced_energy", 
        "feedthroughEnergy.consumedEnergyWh": "feed_through_consumed_energy",
        
        # Circuit sensors
        "instantPowerW": "power",
        "producedEnergyWh": "energy_produced",
        "consumedEnergyWh": "energy_consumed",
    }
    # Parse unique_id patterns and construct backing entity IDs deterministically
```

#### Fresh Install Boot Sequence Question

**Current Mystery**: How do fresh installs recreate the mapping on subsequent boots when:
1. Sensor sets exist in storage → generation is skipped
2. `SyntheticSensorCoordinator.sensor_to_backing_mapping` starts empty  
3. No migration mapping exists
4. Templates require `formula: "state"` which needs the mapping

**Possible Solutions** (needs investigation):
1. Fresh installs DO run generation every time (our assumption about skipping was wrong)
2. There's another mechanism that recreates mapping from stored YAML
3. Fresh installs are actually broken on subsequent boots (contradicts user statement)

#### Implementation Status

**Completed**:
- Migration trigger mechanism (ConfigFlow.VERSION = 2)
- Sensor classification from entity registry 
- Deterministic mapping reconstruction
- Integration with synthetic sensors package
- Temporary mapping storage in hass.data

**Remaining Questions**:
- How fresh installs handle mapping persistence
- Whether generation should run every boot vs. only once

### Conclusion

This migration strategy prioritizes user experience and data preservation while enabling the transition to the new synthetic sensor architecture. The critical insight is that `sensor_to_backing_mapping` must be provided every boot, requiring either generation or reconstruction from persisted data.

**Next Steps**:
1. Investigate fresh install boot sequence to understand mapping recreation
2. Ensure migration provides mapping every boot (not just during migration)
3. Test complete migration flow with real data

## Specific Code Changes Required

### 1. Remove Unique ID Migration Logic

The current `migrate_unique_ids_for_consistency` function should be **removed entirely** as it provides no benefit and risks breaking existing installations:

```python
# REMOVE this function - it's unnecessary and potentially harmful
async def migrate_unique_ids_for_consistency(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Migrate existing unique IDs to consistent pattern."""
    # This entire function should be removed
```

### 2. Modify `async_migrate_entry`

Replace the current migration logic with per-device YAML generation:

```python
async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry for synthetic sensor YAML generation."""
    _LOGGER.debug("Checking config entry version: %s", config_entry.version)

    if config_entry.version < CURRENT_CONFIG_VERSION:
        _LOGGER.debug(
            "Migrating config entry %s from version %s to %s for synthetic sensor setup",
            config_entry.entry_id,
            config_entry.version,
            CURRENT_CONFIG_VERSION,
        )

        # Migrate this config entry to synthetic sensors
        success = await migrate_config_entry_to_synthetic_sensors(hass, config_entry)

        if not success:
            _LOGGER.error("Failed to migrate config entry %s", config_entry.entry_id)
            return False

        # Update config entry version
        config_entry.version = CURRENT_CONFIG_VERSION
        _LOGGER.debug("Successfully migrated config entry %s to version %s",
                     config_entry.entry_id, CURRENT_CONFIG_VERSION)

    return True
```

### 3. Add Device-Specific YAML Generation Functions

The functions defined in Phase 1-3 above replace the old approach:

- `migrate_config_entry_to_synthetic_sensors()`: Main migration function per config entry
- `extract_device_identifier_from_config_entry()`: Device identifier extraction
- `generate_device_yaml_from_entities()`: YAML generation for each device

### 4. Modify `async_setup_entry` - No Changes Needed

The beauty of this approach is that `async_setup_entry` requires **no modifications**:

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry (works for fresh and migrated installations)."""

    # ... existing setup code (API client, coordinator, device registration) ...

    # Set up synthetic sensors - this works identically for fresh and migrated installations
    # For migrated installations: finds existing sensor sets in storage
    # For fresh installations: generates new sensor sets
    storage_manager = await setup_synthetic_configuration(hass, entry, coordinator)
    sensor_manager = await setup_synthetic_sensors(
        hass=hass,
        config_entry=entry,
        async_add_entities=async_add_entities,
        coordinator=coordinator,
        storage_manager=storage_manager,
    )

    # ... continue with existing setup ...
```

The existing `setup_synthetic_configuration` function will:

- **For migrated installations**: Find the existing sensor set in storage (created during migration)
- **For fresh installations**: Generate and create new sensor sets

This means the migration is completely transparent to the normal boot process!

### 5. Add Helper Functions

```python
def generate_formula_for_existing_sensor(sensor_entity: dict[str, Any]) -> str:
    """Generate appropriate formula for existing sensor based on its type."""

    entity_id = sensor_entity["entity_id"].lower()
    unique_id = sensor_entity["unique_id"].lower()

    # Analyze entity ID and unique ID to determine sensor type
    if "power" in entity_id or "power" in unique_id:
        return "backing_entities[0]"  # Direct mapping to backing entity
    elif "energy" in entity_id or "energy" in unique_id:
        return "integrate(backing_entities[0])"  # Integrate power to get energy
    elif "voltage" in entity_id or "voltage" in unique_id:
        return "backing_entities[0]"
    elif "current" in entity_id or "current" in unique_id:
        return "backing_entities[0]"
    else:
        return "backing_entities[0]"  # Default: direct mapping

def generate_backing_entities_for_existing_sensor(sensor_entity: dict[str, Any]) -> list[str]:
    """Generate backing entity IDs for existing sensor based on its unique ID pattern."""

    unique_id = sensor_entity["unique_id"]
    entity_id = sensor_entity["entity_id"]

    # Extract circuit/device information from unique ID
    # Pattern examples:
    # - span_ABC123_circuit_uuid_power -> circuit power sensor
    # - span_ABC123_panel_instant_power -> panel power sensor
    # - span_ABC123_status_wifi_strength -> status sensor

    if "circuit" in unique_id:
        # Circuit-level sensor
        # Map to the native circuit entity that provides the data
        return [entity_id.replace("sensor.", "sensor.")]  # Self-mapping for now
    elif "panel" in unique_id or "instant" in unique_id:
        # Panel-level sensor
        return [entity_id.replace("sensor.", "sensor.")]  # Self-mapping for now
    else:
        # Status or other sensor
        return [entity_id.replace("sensor.", "sensor.")]  # Self-mapping for now

def extract_circuit_id_from_entity_id(entity_id: str) -> str:
    """Extract circuit identifier from entity ID for backing entity generation."""

    # Remove domain prefix
    base_id = entity_id.replace("sensor.", "")

    # Extract circuit part
    if "circuit" in base_id:
        parts = base_id.split("_")
        if "circuit" in parts:
            circuit_idx = parts.index("circuit")
            if circuit_idx + 1 < len(parts):
                return f"circuit_{parts[circuit_idx + 1]}"

    return base_id
```

### 6. Update Constants

Add new constants for migration:

```python
# In const.py
MIGRATION_COMPLETED = "migration_completed"
GENERATED_YAML = "generated_yaml"
MIGRATION_VERSION = "migration_version"
```

### 7. Testing Strategy

Create comprehensive tests for migration scenarios:

```python
# In tests/test_migration.py
async def test_migration_preserves_entity_ids():
    """Test that migration preserves existing entity IDs."""

async def test_migration_generates_yaml():
    """Test that migration generates appropriate YAML configuration."""

async def test_legacy_installation_migration():
    """Test migration of pre-1.0.4 installations."""

async def test_modern_installation_migration():
    """Test migration of post-1.0.4 installations."""
```

### 8. Rollback Considerations

The migration should be designed to be completely reversible:

- **No entity modifications**: Existing entities are never changed
- **YAML generation only**: Only creates new synthetic sensor configurations
- **Fallback to native sensors**: If synthetic sensors fail, native sensors continue working
- **Clear logging**: Comprehensive logging for troubleshooting

### Implementation Priority

1. **High Priority**: Remove unique ID migration logic
2. **High Priority**: Implement YAML generation from existing entities
3. **Medium Priority**: Add migration detection and validation
4. **Medium Priority**: Create comprehensive tests
5. **Low Priority**: Add rollback mechanisms (if needed)

This approach ensures that existing installations can be upgraded to version 2 without any disruption to their current setup while gaining the benefits of the
new synthetic sensor architecture.
