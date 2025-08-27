# Entity ID Change Architecture

## Overview

The SPAN Panel integration handles entity ID changes by leveraging the ha-synthetic-sensors package's sophisticated entity management system. This includes per-SensorSet entity indexes, bulk modification operations, and registry event storm protection. The primary goal is to prevent event thrashing when we initiate entity ID changes that would otherwise trigger cascading update cycles.

## Architecture Relationship

### SPAN Panel Integration
- **Role**: Consumer of ha-synthetic-sensors package
- **Entity Management**: Delegates to ha-synthetic-sensors StorageManager and SensorSet classes
- **Migration**: Implements custom migration logic for legacy naming patterns
- **Configuration**: Uses ha-synthetic-sensors for sensor storage and entity tracking

### ha-synthetic-sensors Package
- **Role**: Provider of entity management infrastructure
- **Components**: EntityIndex, EntityRegistryListener, StorageManager, SensorSet
- **Storm Protection**: Handles registry event filtering and self-change detection
- **Bulk Operations**: Provides atomic modification capabilities

## Core Components (ha-synthetic-sensors)

### Per-SensorSet Entity Index

- **Purpose**: Tracks which entity IDs are actively referenced by sensors within a specific SensorSet for event filtering
- **Scope**: Per-SensorSet, completely rebuilt whenever that SensorSet is modified
- **Content**: Set of ALL entity IDs referenced in sensor configurations, formula variables, and global settings within the SensorSet
- **No Distinction**: Contains any entity_id reference - no distinction between synthetic and external entities
- **Exclusions**: Dynamic patterns (device_class, regex, label) are excluded since they're resolved at runtime
- **Location**: Owned by each SensorSet, not global
- **Update Strategy**: Always rebuild entire index from final SensorSet state - no incremental updates

### Entity Registry Event Listener

- **Purpose**: Monitors Home Assistant entity registry changes globally
- **Filtering**: Checks all SensorSet indexes to see which ones track the changed entity
- **Efficiency**: Only SensorSets that track the changed entity process the event
- **Storm Protection**: Ignores events for entities we're currently changing (self-change detection)

### Bulk Modification System

- **Purpose**: Handles coordinated changes to sensors, global settings, and entity ID renames within a SensorSet
- **Operations**: Add/remove/update sensors, change entity IDs, update global settings
- **Atomicity**: All changes within a SensorSet happen together
- **Index Strategy**: Pre-update EntityIndex for storm protection, then rebuild from final state

## Key Principles

### 1. Per-SensorSet Entity Tracking

- Each SensorSet maintains its own EntityIndex of referenced entities
- Event listener checks all SensorSet indexes to find which ones care about a changed entity
- No global entity tracking needed - aggregation happens at query time if needed
- SensorSets are isolated from each other's entity changes

### 2. Registry Event Storm Protection

- **The Problem**: When we rename entities in HA registry, each rename triggers an event
- **The Solution**: Pre-update our SensorSet's EntityIndex before registry changes
- **Result**: Our own registry changes get ignored, preventing infinite loops and thrashing

### 3. Two Types of Changes

#### Type 1: Configuration Changes (No Storm Risk)

- Modifying formulas, adding/removing sensors, changing global variables
- These don't trigger HA registry events
- EntityIndex rebuilt from final SensorSet state
- No event storm protection needed

#### Type 2: Entity ID Registry Changes (Storm Risk)

- HA registry renames `sensor.power_meter` → `sensor.new_power_meter`
- This triggers registry events that could cause cascading updates
- **Protection**: Pre-update EntityIndex, then ignore the events we caused

### 4. Self-Change Detection Flow

1. SensorSet decides to rename entity_id: sensor.A → sensor.B
2. Pre-update SensorSet's EntityIndex: remove sensor.A, add sensor.B
3. Update all references in SensorSet storage (formulas, variables, global settings)
4. Update HA entity registry: sensor.A → sensor.B (triggers event)
5. Event listener receives registry event for sensor.A
6. Event listener checks SensorSet indexes: sensor.A no longer tracked
7. Event ignored - no processing storm

### 5. EntityIndex Simplicity

- **No Synthetic vs External**: EntityIndex contains ANY entity_id reference found in the SensorSet
- **Event Filtering Purpose**: Primary purpose is determining which SensorSets care about registry events
- **Always Rebuild**: Never incremental updates - always rebuild entire index from final SensorSet state
- **Self-References Included**: ConfigManager may inject self-references (e.g., for attribute formulas) - these are tracked too

## SPAN Panel Integration Details

### Entity Management Flow

```python
# SPAN Panel uses ha-synthetic-sensors StorageManager
storage_manager = hass.data["ha_synthetic_sensors"]["storage_managers"][config_entry_id]
sensor_set = storage_manager.get_sensor_set(sensor_set_id)

# Bulk modifications use ha-synthetic-sensors infrastructure
await sensor_set.async_modify(modification)

# Entity tracking handled by ha-synthetic-sensors EntityIndex
# SPAN Panel doesn't directly interact with EntityIndex
```

### Migration System

```python
# SPAN Panel implements custom migration for legacy naming patterns
class EntityIdMigrationManager:
    async def migrate_synthetic_entities(self, old_flags, new_flags):
        # Only handles legacy migration (no device prefix -> device prefix)
        # Non-legacy migrations not supported - users choose pattern during setup
        
        if not old_flags.get(USE_DEVICE_PREFIX, False):
            return await self._migrate_legacy_to_prefix(old_flags, new_flags)
        else:
            # Skip migration - pattern already chosen during setup
            return True
```

### Sensor Manager Integration

```python
# SPAN Panel uses ha-synthetic-sensors sensor manager
sensor_manager = hass.data["ha_synthetic_sensors"]["sensor_managers"][config_entry_id]

# Export current configuration
current_sensors = await sensor_manager.export()

# Modify with new entity IDs
await sensor_manager.modify(migrated_sensors)
```

## Architecture Details (ha-synthetic-sensors)

### SensorSet Structure

```python
# This is implemented in ha-synthetic-sensors package
class SensorSet:
    def __init__(self, storage_manager, sensor_set_id):
        self.storage_manager = storage_manager
        self.sensor_set_id = sensor_set_id
        self._entity_index = EntityIndex(storage_manager.hass)  # Per-SensorSet!

    def is_entity_tracked(self, entity_id: str) -> bool:
        return self._entity_index.contains(entity_id)

    async def async_modify(self, modification):
        # For entity ID changes: pre-update index for storm protection
        if modification.entity_id_changes:
            self._rebuild_entity_index_for_modification(modification)

        # Apply all modifications
        # ... sensor changes, global settings changes ...

        # Always rebuild index from final state
        self._rebuild_entity_index()

    def _rebuild_entity_index_for_modification(self, modification):
        """Pre-update index to reflect final state for storm protection."""
        # Calculate what the final state will be after all modifications
        # Update index to reflect that final state BEFORE making changes
        # This ensures registry events we trigger get filtered out

    def _rebuild_entity_index(self):
        """Always rebuild entire index from current SensorSet state."""
        self._entity_index.clear()

        # Add from all current sensors
        for sensor in self.get_sensors():
            self._entity_index.add_sensor_entities(sensor)

        # Add from current global settings
        if self.get_global_variables():
            self._entity_index.add_global_entities(self.get_global_variables())
```

### Event Listener Flow

```python
# This is implemented in ha-synthetic-sensors package
class EntityRegistryListener:
    def handle_entity_change(self, old_entity_id, new_entity_id):
        affected_sensor_sets = []

        # Check which SensorSets track this entity
        for sensor_set in self.storage_manager.get_all_sensor_sets():
            if sensor_set.is_entity_tracked(old_entity_id):
                affected_sensor_sets.append(sensor_set)

        # Only process for SensorSets that care
        for sensor_set in affected_sensor_sets:
            sensor_set.handle_external_entity_change(old_entity_id, new_entity_id)
```

## Bulk Modification Flow

### Phase 1: Validation

- Check for conflicts between operations within the SensorSet
- Validate global settings don't conflict with local sensor variables
- Ensure referenced sensors exist for updates/removals

### Phase 2: Entity ID Changes (Registry Storm Protection)

- **Calculate final state** after all modifications
- **Pre-update SensorSet's EntityIndex** to reflect final state (remove old entity IDs, add new ones)
- Update sensor configurations with new entity IDs within the SensorSet
- Update global settings within the SensorSet with new entity IDs
- **Update Home Assistant entity registry** (triggers events that get ignored)

### Phase 3: Configuration Changes

- Remove sensors (if specified)
- Update existing sensors (if specified)
- Add new sensors (if specified)
- Apply global settings changes

### Phase 4: Index Rebuild

- **Rebuild SensorSet's EntityIndex from final state** (handles any remaining changes)
- Invalidate formula caches for affected entity IDs

## Global vs SensorSet Scope

### What's Per-SensorSet

- **EntityIndex**: Each SensorSet tracks only its own entity references
- **Sensors**: Sensors belong to exactly one SensorSet
- **Global Settings**: Scoped to the SensorSet, not truly global
- **Bulk Operations**: Operate within a single SensorSet

### What's Global

- **Event Listener**: Receives all HA registry events, routes to relevant SensorSets
- **StorageManager**: Coordinates multiple SensorSets
- **Integration Interface**: SpanPanel integration works with its own SensorSet(s)

## Performance Considerations

### Event Processing Efficiency

- O(1) lookup per SensorSet to check if entity is tracked
- Only affected SensorSets process events (not all SensorSets)
- Registry event storms prevented by self-change detection

### Memory Usage

- EntityIndex memory scales with entities per SensorSet (typically small)
- No global entity tracking overhead
- SensorSet isolation prevents cross-contamination

### Bulk vs Individual Changes

- Individual sensor changes rebuild only that SensorSet's index
- Bulk operations within a SensorSet are more efficient
- Cross-SensorSet operations are rare and handled separately

## Integration Points

### For SpanPanel Integration

- SpanPanel creates/manages its own SensorSet(s) through ha-synthetic-sensors
- Uses `SensorSet.async_modify()` for bulk entity ID changes
- Registry event storms automatically prevented by ha-synthetic-sensors
- Isolated from other integrations' entity changes

### For Event Handling

- EntityRegistryListener routes events to relevant SensorSets only
- Each SensorSet handles its own entity reference updates
- Formula cache invalidation coordinated per SensorSet

## Error Handling

### Validation Failures

- All validation occurs before any changes within the SensorSet
- Clear error messages indicate specific conflicts within the SensorSet
- SensorSet remains in consistent state after validation failures

### Registry Update Failures

- Entity registry updates may fail (e.g., entity doesn't exist in HA)
- SensorSet storage updates continue even if registry updates fail
- Cache invalidation always occurs to maintain SensorSet consistency

## Future Considerations

### Cross-SensorSet Operations

- Rare but possible (e.g., moving sensors between SensorSets)
- Would require coordination between multiple SensorSet indexes
- Can be handled as separate bulk operations per affected SensorSet

### Scaling

- Per-SensorSet approach scales linearly with number of integrations
- Each integration's SensorSet(s) operate independently
- No global bottlenecks in entity tracking or event processing

## Implementation Notes

### EntityIndex Design Decisions

- **Simplicity**: No distinction between synthetic and external entities - just track all entity_id references
- **Rebuild Strategy**: Always rebuild entire index rather than incremental updates for consistency
- **Event Filtering**: Primary purpose is determining which SensorSets need to process registry events
- **Self-References**: Includes any entity_id references, including self-references injected by ConfigManager

### Dead Code Elimination

- No `update_sensor_entities` method - always rebuild entire index
- No incremental entity tracking - full rebuild is simpler and more reliable
- No shared entity reference logic - per-SensorSet design eliminates this complexity

### SPAN Panel Specific Notes

- **Legacy Migration Only**: SPAN Panel only supports legacy migration (no device prefix → device prefix)
- **Setup-Time Pattern Choice**: Users choose naming pattern during initial setup, no runtime changes
- **ha-synthetic-sensors Dependency**: All entity management delegated to ha-synthetic-sensors package
- **No Custom EntityIndex**: SPAN Panel uses ha-synthetic-sensors EntityIndex through StorageManager
