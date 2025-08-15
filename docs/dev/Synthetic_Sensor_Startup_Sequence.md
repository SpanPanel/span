# Synthetic Sensor Startup Sequence

This document explains the critical startup sequence for synthetic sensors in the SPAN Panel integration, highlighting the key differences between fresh
installations, migrated installations, and existing installations.

## Overview

The SPAN Panel integration uses the `ha-synthetic-sensors` library to create formula-based sensors that extend circuit and panel data. The startup sequence has
a crucial "fork in the road" that determines whether sensor configurations are generated from scratch, regenerated during migration, or loaded from existing
storage.

**Important**: With the Version 2 migration strategy, the concept of "existing installations" has changed. After migration, all installations become "existing"
from the storage perspective, with YAML configurations pre-generated during the migration process.

## Data Path Construction and Usage

### Data Path Purpose

The `data_path` is a **critical bridge** between the SPAN panel integration and the ha-synthetic-sensors package. It serves as a **lookup key** that tells the
synthetic sensor system where to find the actual data values in the SPAN panel's data structure.

### Data Path Construction Flow

#### 1. Sensor Definition (Hardcoded)

```python
# In synthetic_named_circuits.py - NAMED_CIRCUIT_SENSOR_DEFINITIONS
{
    "key": "instantPowerW",
    "name": "Power",
    "template": "circuit_power",
    "data_path": "instant_power",  # ← HARDCODED attribute name
},
{
    "key": "producedEnergyWh",
    "name": "Produced Energy",
    "template": "circuit_energy_produced",
    "data_path": "produced_energy",  # ← HARDCODED attribute name
},
{
    "key": "consumedEnergyWh",
    "name": "Consumed Energy",
    "template": "circuit_energy_consumed",
    "data_path": "consumed_energy",  # ← HARDCODED attribute name
}
```

#### 2. Data Path Construction

```python
# In synthetic_named_circuits.py - line 283
backing_entity = BackingEntity(
    entity_id=backing_entity_id,
    value=data_value,
    data_path=f"circuits.{circuit_id}.{sensor_def['data_path']}",
    #                    ↑              ↑
    #              UUID from API    Hardcoded attribute
)
```

#### 3. Example Data Paths Generated

```python
# For circuit UUID "795e8eddb4f448af9625130332a41df8":
data_path = "circuits.795e8eddb4f448af9625130332a41df8.instant_power"
data_path = "circuits.795e8eddb4f448af9625130332a41df8.produced_energy"
data_path = "circuits.795e8eddb4f448af9625130332a41df8.consumed_energy"
```

### Data Path Usage in SPAN Integration

#### 1. Data Path Parsing

```python
# In synthetic_sensors.py - _populate_backing_entity_metadata()
data_path = "circuits.795e8eddb4f448af9625130332a41df8.instant_power"
parts = data_path.split(".", 2)  # ["circuits", "795e8eddb4f448af9625130332a41df8", "instant_power"]
circuit_id = parts[1]  # "795e8eddb4f448af9625130332a41df8"
api_key = parts[2]     # "instant_power"

self.backing_entity_metadata[backing_entity_id] = {
    "api_key": api_key,
    "circuit_id": circuit_id,
    "data_path": data_path,
    "friendly_name": None,  # Will be populated during first update
}
```

#### 2. Data Extraction

```python
# In synthetic_sensors.py - _extract_value_from_panel()
def _extract_value_from_panel(self, span_panel: Any, meta: dict[str, Any]) -> Any:
    api_key = meta["api_key"]        # "instant_power"
    circuit_id = meta["circuit_id"]  # "795e8eddb4f448af9625130332a41df8"

    circuit = span_panel.circuits.get(circuit_id)  # Get circuit by UUID
    value = getattr(circuit, api_key, None)        # Get attribute by name
    # e.g., getattr(circuit, "instant_power", None)
```

### Data Path Connection to SpanPanelCircuit

The data path attributes map directly to the `SpanPanelCircuit` class attributes:

```python
@dataclass
class SpanPanelCircuit:
    circuit_id: str
    name: str
    instant_power: float          # ← Matches "instant_power" data_path
    produced_energy: float        # ← Matches "produced_energy" data_path
    consumed_energy: float        # ← Matches "consumed_energy" data_path
    # ... other attributes
```

### Data Path Usage in ha-synthetic-sensors

#### 1. Data Provider Registration

```python
# In synthetic_sensors.py - setup_synthetic_sensors()
sensor_manager.register_data_provider_entities(
    backing_entity_ids,  # Set of backing entity IDs
    change_notifier      # Callback for data changes
)
```

#### 2. Data Provider Resolution

```python
# In synthetic_sensors.py - data_provider_callback()
def data_provider_callback(entity_id: str) -> DataProviderResult:
    # Get value from virtual backing entity using live coordinator data
    value = synthetic_coord.get_backing_value(entity_id)
    exists = entity_id in synthetic_coord.backing_entity_metadata

    return {
        "value": value,
        "exists": exists,
        "attributes": {}
    }
```

### Complete Data Flow Example

**For a "Fountain Power" synthetic sensor:**

1. **Data Path:** `"circuits.795e8eddb4f448af9625130332a41df8.instant_power"`
2. **Backing Entity ID:** `"sensor.span_panel_12345_fountain_power"`
3. **Synthetic Sensor Formula:** `"{{ backing_entity_id }}"`
4. **Data Provider Call:** `data_provider_callback("sensor.span_panel_12345_fountain_power")`
5. **Value Extraction:** `getattr(circuit, "instant_power", None)` → `1250.5`
6. **Result:** Synthetic sensor displays `1250.5 W`

### Key Functions of Data Path

#### 1. Data Lookup Bridge

- **SPAN Integration** uses `data_path` to extract values from the SPAN panel data structure
- **ha-synthetic-sensors** uses the backing entity ID to request data via the data provider callback
- The `data_path` connects these two systems

#### 2. Real-time Data Access

- When synthetic sensors need to evaluate formulas, they call the data provider
- The data provider uses the `data_path` to get live values from the SPAN panel
- This enables real-time sensor updates without polling

#### 3. Change Detection

- The `data_path` enables the system to detect when circuit names change
- By storing the circuit's friendly name in backing entity metadata, the system can compare old vs new names
- This triggers the YAML update process for synthetic sensors

#### 4. Virtual Entity Management

- Backing entities are **virtual** - they don't exist as real Home Assistant entities
- The `data_path` allows the system to provide data for these virtual entities
- This creates a seamless interface between SPAN data and synthetic sensor formulas

## Component Relationships and Responsibilities

### SyntheticSensorCoordinator

**Purpose**: Central coordinator for synthetic sensor lifecycle management and data flow.

**Responsibilities**:

- Owns and manages the StorageManager instance
- Generates sensor configurations and backing entities
- Manages YAML configuration creation and storage
- Listens to SPAN coordinator updates and triggers synthetic sensor updates
- Provides data provider callback for live SPAN panel data access
- Handles change detection and selective synthetic sensor updates

**Assets Owned**:

- `storage_manager`: StorageManager instance for persistent configuration storage
- `backing_entity_metadata`: Dictionary mapping backing entity IDs to metadata (api_key, circuit_id, data_path)
- `sensor_to_backing_mapping`: Dictionary mapping sensor keys to backing entity IDs
- `all_backing_entities`: Complete list of backing entity configurations for re-registration
- `change_notifier`: Callback for notifying synthetic sensors of data changes
- `sensor_set_id`: Unique identifier for the sensor set (e.g., "span123_sensors")
- `device_identifier`: Device identifier used for unique ID generation
- `_last_values`: Snapshot of last emitted values for change detection
- `_last_sensor_manager`: Reference to SensorManager for metrics enrichment

**Key Methods**:

- `setup_configuration()`: Generates configurations and manages storage
- `_handle_coordinator_update()`: Processes SPAN coordinator updates
- `get_backing_value()`: Extracts live values from SPAN panel data
- `_populate_backing_entity_metadata()`: Creates metadata for data provider

### StorageManager

**Purpose**: Manages persistent storage of synthetic sensor configurations.

**Responsibilities**:

- Loads existing sensor sets from persistent storage
- Creates new sensor sets for fresh installations
- Imports YAML configurations into storage
- Provides configuration data to the SensorManager
- Manages sensor set lifecycle (create, update, delete)

**Assets Owned**:

- Persistent storage location for sensor configurations
- Sensor set registry with device identifiers
- YAML configuration cache

### SensorManager (from ha-synthetic-sensors)

**Purpose**: Creates and manages synthetic sensor entities.

**Responsibilities**:

- Creates synthetic sensor entities based on configurations
- Registers backing entities for change notifications
- Handles synthetic sensor updates when backing entities change
- Manages synthetic sensor lifecycle

**Assets Owned**:

- Synthetic sensor entity instances
- Backing entity registration mappings
- Change notification handlers

## Detailed YAML Generation Process

### Phase 1: Sensor Platform Setup Entry

The sensor platform's `async_setup_entry()` function initiates the synthetic sensor setup process:

1. **Create StorageManager**: Instantiates StorageManager with integration domain
2. **Load Existing Configurations**: Calls `async_load()` to load existing sensor sets
3. **Check for Existing Sets**: Determines if sensor sets already exist from migration
4. **Create SyntheticSensorCoordinator**: Instantiates coordinator for this config entry
5. **Store in Global Registry**: Adds coordinator to `_synthetic_coordinators` dictionary
6. **Call setup_synthetic_configuration()**: Delegates configuration setup to coordinator

### Phase 2: Configuration Generation by SyntheticSensorCoordinator

The coordinator's `setup_configuration()` method orchestrates the complete YAML generation process:

1. **Initialize StorageManager**: Creates StorageManager instance
2. **Load Existing Data**: Calls `async_load()` to load existing sensor sets
3. **Delegate to Live Configuration**: Calls `_setup_live_configuration()`

### Phase 3: Multi-Module YAML Generation

The `_setup_live_configuration()` method coordinates YAML generation across multiple modules:

#### 3a. Panel Sensor Generation (`synthetic_panel_circuits.py`)

**Process**:

- Iterates through `PANEL_SENSOR_DEFINITIONS` (current power, feedthrough power, energy sensors)
- For each sensor definition:
  - Generates entity ID using helper functions
  - Creates backing entity configuration with data_path
  - Builds sensor-to-backing mapping
  - Generates YAML configuration using templates
  - Extracts current data values from SPAN panel

**Output**: Tuple of (sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping)

#### 3b. Named Circuit Generation (`synthetic_named_circuits.py`)

**Process**:

- Iterates through all named circuits (non-unmapped circuits)
- For each circuit and sensor type (power, energy):
  - Generates entity ID using helper functions
  - Creates backing entity configuration with data_path
  - Builds sensor-to-backing mapping
  - Generates YAML configuration using templates
  - Extracts current data values from circuit data

**Output**: Tuple of (sensor_configs, backing_entities, global_settings, sensor_to_backing_mapping)

#### 3c. Configuration Combination

**Process**:

- Combines panel and circuit sensor configurations
- Merges backing entity lists
- Combines sensor-to-backing mappings
- Uses global settings from panel sensors (they should be identical)
- Populates backing entity metadata for data provider

### Phase 4: YAML Construction and Storage

#### 4a. YAML Assembly (`_construct_complete_yaml_config()`)

**Process**:

- Creates YAML dictionary structure
- Adds global settings at the top level
- Adds sensors section with cleaned configurations
- Removes device_identifier from individual sensors (moves to global settings)
- Uses custom YAML representer for proper string formatting
- Dumps to YAML string with specific formatting options

#### 4b. Storage Decision Logic

**Fresh Installation**:

- Creates new sensor set with `async_create_sensor_set()`
- Generates and imports YAML with `async_from_yaml()`
- Sets `replace_existing=False` for default configuration

**Migration Installation**:

- Regenerates YAML from existing entities
- Imports YAML with `replace_existing=True`
- Updates existing sensor set configuration

**Existing Installation**:

- Skips YAML generation and import
- Uses existing sensor set loaded from storage
- Only ensures backing entity metadata is populated

### Phase 5: Sensor Set ID Management

#### 5a. Sensor Set ID Construction

**Process**:

- Uses `construct_sensor_set_id()` helper function
- Pattern: `{device_identifier}_sensors`
- Examples: `span123_sensors`, `span-simulator_sensors`

#### 5b. Device Identifier Logic

**Live Panels**: Uses true serial number from SPAN panel **Simulators**: Uses slugified device name to avoid cross-entry collisions

#### 5c. Persistent Storage

**Storage Location**: Sensor set IDs are stored in ha-synthetic-sensors persistent storage **Retrieval**: On subsequent boots, `async_load()` retrieves existing
sensor sets **Lookup**: `sensor_set_exists()` checks if sensor set exists for device identifier

### Phase 6: Convenience Method Processing

The `async_setup_synthetic_sensors()` function uses the ha-synthetic-sensors simplified interface:

1. **Create Data Provider**: Creates callback for live SPAN panel data access
2. **Get Synthetic Coordinator**: Retrieves coordinator from global registry
3. **Extract Mapping**: Uses sensor-to-backing mapping from coordinator
4. **Create Change Notifier**: Defines callback for synthetic sensor updates
5. **Call Convenience Method**: Uses `async_setup_synthetic_sensors_with_entities()`
6. **Connect Notifier**: Links change notifier to coordinator for updates
7. **Re-bind Entities**: Re-registers backing entities with change notifier
8. **Trigger Initial Update**: Sends initial update to synthetic sensors

## High-Level Flow

The startup sequence follows this general flow:

1. **Integration Start** → **Migration Check**
2. **Migration Check** → If v1 to v2: **Generate YAML from Entities** → **Store YAML in Storage**
3. **Coordinator Data Fetch** (common to all paths)
4. **Native Sensors Created** (common to all paths)
5. **Storage Manager Init** → **Storage Manager async_load**
6. **Configuration Generation** (always happens for backing entity metadata)
7. **The Critical Check**: Does sensor set exist in storage?
   - **No**: Fresh Install Path → **Generate YAML + Import**
   - **Yes + Migration Mode**: Migration Path → **Regenerate YAML + Import**
   - **Yes + No Migration Mode**: Existing Install Path → **Use Pre-stored Config**
8. **Convenience Method** (common to all paths)
9. **Sensors Created** (common to all paths)

## Detailed Startup Sequence

### Phase 0: Migration (Version 1 to Version 2 Only)

During the migration phase, the system analyzes existing entities for the config entry and generates YAML configurations from the current entity registry. These
configurations are stored in the ha-synthetic-sensors storage before normal setup begins.

**Result**: For v1 installations, YAML configurations are pre-generated and stored before normal setup begins.

### Phase 1: Core Initialization (Common to All Paths)

#### 1. Integration Setup

The coordinator is created and performs an initial data refresh to ensure live SPAN panel data is available.

**Result**: Coordinator has live SPAN panel data

#### 2. Native Sensor Creation

Native HA sensors are created first, including panel data status sensors, unmapped circuit sensors, hardware status sensors, and battery sensors if enabled.

**Result**: Native HA sensors created first

#### 3. Storage Manager Initialization

The storage manager is initialized and loads all existing sensor sets from persistent storage.

**Critical**: The async_load() call loads ALL existing sensor sets from persistent storage. After this call:

- **Fresh Install**: Storage manager is empty (new installations only)
- **Migrated Install**: Storage manager contains sensor configurations generated during migration
- **Existing v2 Install**: Storage manager contains all previously saved sensor configurations

### Phase 2: Configuration Generation (Always Done)

#### 4. Sensor Configuration Generation (Always Required)

**Critical Insight**: Sensor configurations and mappings are **always regenerated** during every bootup, regardless of installation type. This includes:

- Panel sensor configurations and backing entities
- Named circuit sensor configurations and backing entities
- Global settings for the sensor set
- Sensor-to-backing entity mappings
- Backing entity metadata for the data provider

**Why This Always Happens**: Configuration generation is required in ALL cases because:

- **Data Provider Needs Metadata**: The data provider callback needs backing entity metadata to extract values from coordinator data
- **Convenience Method Needs Mapping**: The convenience method requires sensor-to-backing mapping to register backing entities
- **Live Data Access**: Backing entities provide virtual access to live SPAN panel data
- **Dynamic Circuit Changes**: Circuit configurations can change between boots, requiring fresh generation

**Backing Entity Process**: The system creates virtual backing entities that map to live SPAN panel data:

- **Panel-level data**: Maps to panel status information (power, energy, etc.)
- **Circuit-level data**: Maps to individual circuit information (power, energy, etc.)
- **Metadata Population**: Each backing entity gets metadata describing how to extract its value from coordinator data
- **Value Extraction**: The data provider uses this metadata to get live values from the SPAN panel

#### 5. The Critical Check

The system checks if a sensor set already exists in storage for the current device identifier. This determines which path to take:

- **Fresh Install Path**: No sensor set exists
- **Migration Path**: Sensor set exists and migration mode is active
- **Existing Install Path**: Sensor set exists and no migration mode

**Important**: Even for existing installations, the backing entity mapping is **recreated fresh** during every bootup. Only the YAML sensor configurations are
loaded from storage.

### Fresh Installation Path

#### 6a. Fresh Install: Create and Import

For fresh installations, the system creates a new sensor set and imports the default YAML configuration. This includes:

- Creating a new sensor set with the device identifier
- Generating the initial YAML configuration from the sensor configs
- Importing the YAML into the storage manager

**Result**: Storage manager now contains newly created sensor set with default configurations

### Migration Installation Path

#### 6b. Migration Install: Regenerate and Import

For migrated installations, the system regenerates the YAML configuration from existing entities and imports it, replacing the existing configuration. This
ensures that:

- Entity IDs are preserved from the original installation
- Unique IDs are normalized to the helper format
- All existing sensor configurations are maintained

**Result**: Storage manager contains updated sensor set with regenerated configurations from existing entities

### Existing Installation Path

#### 6c. Existing Install: Use Stored Configuration

For existing v2 installations, the system uses the stored YAML configuration without generating new YAML. The sensor set is already loaded from disk during the
async_load() call.

**Critical**: Even though YAML generation is skipped, the backing entity mapping is still **recreated fresh** during every bootup to ensure it matches the
current SPAN panel state.

**Result**: Storage manager already contains the sensor set loaded from persistent storage

### Phase 3: Convenience Method Processing (Common Path Resumes)

#### 7. Backing Entity Registration

The convenience method processes the storage manager and sensor-to-backing mapping to create the synthetic sensors. This method is agnostic to the installation
type and simply:

- Registers backing entities from the provided mapping
- Loads whatever sensor configurations are in the storage manager
- Creates sensors based on those configurations

#### 8. What the Convenience Method Does

The convenience method extracts backing entity IDs from the mapping, registers them with the sensor manager, and loads the configuration from the storage
manager.

**Key Insight**: The convenience method is **agnostic** to fresh vs existing installation. It just:

1. Registers backing entities from the provided mapping
2. Loads whatever sensor configurations are in the storage manager
3. Creates sensors based on those configurations

### Phase 4: Migration Flag Handling

#### 9. Migration Mode Detection and Cleanup

The system checks for migration mode early in the process and passes this information to all functions that need it. After successful setup, migration flags are
cleared to prevent future migration processing.

**Migration Flag Logic**: The system checks both transient flags in hass.data and persisted flags in config entry options to determine if migration mode is
active.

### Phase 5: Solar Setup Integration

#### 10. Initial Solar Sensor Setup

If solar is enabled during initial setup, solar sensors are created using the same migration-aware process as other synthetic sensors. This ensures that solar
sensors are properly integrated with the existing sensor set.

**Solar Setup Logic**: If solar is enabled during initial setup, solar sensors are created using the same migration-aware process as other synthetic sensors.

## Backing Entity Lifecycle

### During Bootup

1. **Generation**: Backing entity configurations and mappings are always regenerated from current SPAN panel data
2. **Metadata Population**: Each backing entity gets metadata describing how to extract its value from coordinator data
3. **Registration**: Backing entities are registered with the sensor manager for change notifications
4. **Value Snapshot**: Initial values are captured from current coordinator data

### During Runtime

1. **Coordinator Updates**: The synthetic coordinator listens for SPAN coordinator updates
2. **Change Detection**: Only backing entities with changed values trigger synthetic sensor updates
3. **Value Extraction**: The data provider uses metadata to extract live values from coordinator data
4. **Synthetic Updates**: Changed backing entities trigger synthetic sensor recalculations

### After Bootup

1. **Persistent Storage**: YAML configurations are stored in persistent storage
2. **Memory Cleanup**: Backing entity mappings are recreated fresh on next bootup
3. **Change Tracking**: The system tracks value changes to optimize synthetic sensor updates

## Performance Implications

### Fresh Installation

- **Higher startup cost**: YAML generation and import
- **One-time operation**: Only happens on first setup
- **Complete configuration**: Creates all default sensors

### Migrated Installation (v1→v2)

- **Migration cost**: One-time YAML generation during migration phase
- **Regeneration cost**: YAML is regenerated during first normal boot
- **Fast subsequent startups**: Uses pre-generated YAML from migration
- **Preserves identifiers**: All entity IDs and unique IDs preserved from v1
- **Transparent operation**: Appears identical to fresh v2 installation after migration

### Existing v2 Installation

- **Fast startup**: Skips expensive YAML generation
- **Uses cached config**: Loads from persistent storage
- **Preserves customizations**: User modifications are retained
- **Fresh backing entities**: Backing entity mappings are recreated to match current SPAN panel state

## Key Components Always Generated

Even on existing installations, these components are always generated:

1. **Sensor-to-Backing Mapping**: Maps sensor keys to backing entity IDs (recreated every bootup)
2. **Backing Entity Metadata**: Enables data provider to extract values from coordinator (recreated every bootup)
3. **Data Provider Callback**: Provides live data from SPAN coordinator (recreated every bootup)
4. **Configuration Objects**: Sensor configurations and global settings (recreated every bootup)

## Storage Manager State Summary

| Installation Type   | Storage Manager Contents After Setup                                          |
| ------------------- | ----------------------------------------------------------------------------- |
| Fresh Install       | Newly created sensor set + imported YAML configurations                       |
| Migrated Install    | Sensor set created during migration + regenerated YAML from existing entities |
| Existing v2 Install | Existing sensor set loaded from disk                                          |

All result in the same outcome: storage manager contains sensor configurations that the convenience method can load and process.

## Data Flow

### Fresh Installation

```text
Coordinator Data → Config Generation → YAML Creation → Storage Import → Convenience Method → Sensors
```

### Migrated Installation (v1→v2)

```text
Migration Phase: Entity Registry → YAML Generation → Storage Import
                                                           ↓
Runtime Phase:   Coordinator Data → Config Generation → Storage (Pre-loaded) → YAML Regeneration → Storage Update → Convenience Method → Sensors
```

### Existing v2 Installation

```text
Coordinator Data → Config Generation → Storage (Pre-loaded) → Convenience Method → Sensors
                                   ↗
                        Skip YAML Creation/Import
```

## Troubleshooting

### Common Issues

1. **Sensor set not found**: Check if async_load() completed successfully
2. **Missing backing entities**: Verify sensor-to-backing mapping generation
3. **No sensor data**: Ensure coordinator has valid data before synthetic setup
4. **Configuration not loading**: Check storage manager sensor set existence
5. **Migration flags not cleared**: Verify migration flag cleanup execution
6. **Backing entity mapping issues**: Check that backing entity metadata is populated correctly

### Debug Points

- **Fresh Install Detection**: Look for "Fresh installation detected" log message
- **Migration Mode Detection**: Look for "Migration mode detected" log message
- **Existing Install Detection**: Look for "Existing sensor set found" log message
- **Backing Entity Count**: Check sensor-to-backing mapping size in logs
- **Storage Contents**: Use storage manager list_sensor_sets() to verify loaded data
- **Migration Flag Status**: Check migration mode detection return value
- **Backing Entity Metadata**: Verify that backing entity metadata is populated for data provider

## Conclusion

The startup sequence's "fork in the road" optimizes performance by:

- Generating complete configurations only when needed (fresh installs)
- Regenerating configurations during migration to preserve entity IDs
- Reusing stored configurations for fast restarts (existing installs)
- Always providing live data through the coordinator-backed data provider
- Recreating backing entity mappings on every bootup to ensure current SPAN panel state

All paths converge at the convenience method, which creates functional synthetic sensors regardless of the configuration source. The migration flag system
ensures smooth transitions between installation types while preserving user configurations and entity identifiers. The backing entity system ensures that
synthetic sensors always have access to current SPAN panel data, even as circuit configurations change over time.

The data path system serves as the critical bridge between SPAN panel data and synthetic sensor formulas, enabling real-time data access through virtual backing
entities while maintaining the flexibility to handle dynamic circuit configurations and name changes.
