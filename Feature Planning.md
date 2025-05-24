# SPAN Panel Integration: Core Principles & Implementation Strategy

## Core Principles

### 1. Entity Stability Principle

- **Entity IDs are immutable** - never change after creation to prevent breaking automations/dashboards
- **Friendly names are dynamic** - can update to reflect current SPAN panel state
- **Clear separation of concerns** - entity IDs for system stability, friendly names for user experience

### 2. Zero Breaking Changes Principle

- **Existing installations unchanged** - upgrades never alter current behavior
- **Backward compatibility forever** - legacy patterns preserved indefinitely
- **New installations get better defaults** - without affecting existing setups

### 3. Configuration-Driven Behavior Principle

Two global flags control entity creation across the integration:

```python
USE_CIRCUIT_NUMBERS = "use_circuit_numbers"    # Stable circuit-based IDs vs legacy name-based IDs
USE_DEVICE_PREFIX = "use_device_prefix"        # Device prefix in entity IDs (legacy compatibility)
```

**Auto-Sync Always Enabled**: Circuit name synchronization is built-in and always active - there is no configuration flag to disable it. All circuit entities automatically detect SPAN panel name changes and trigger integration reload to update friendly names while preserving entity IDs.

### 4. Entity Naming Consistency Principle

- **All entity types follow identical patterns** - switch, select, sensor use same logic
- **Friendly names always clean format** - never include device prefix regardless of settings
- **Entity ID generation consistent** - same rules across all entity types

### 5. User Control Principle

- **Migration features only** - users opt-in to entity ID migration (Phase 2)
- **User customizations respected** - Home Assistant entity registry overrides preserved
- **Clear communication** - notifications explain new features and changes

### 6. Auto-Sync Effectiveness Principle

- **Name change detection** - all circuit entities detect SPAN panel name changes
- **Integration reload for updates** - preserves entity IDs while updating friendly names
- **Conservative operation** - one reload per update cycle maximum

## Phase 1: Stable Entity IDs (Implemented)

### New Installations (v1.0.9+)

- **Flags**: `USE_CIRCUIT_NUMBERS = True`, `USE_DEVICE_PREFIX = True`
- **Entity IDs**: `sensor.span_panel_circuit_1_power` (stable, never change)
- **Friendly Names**: "Air Conditioner Power" (clean, auto-sync always enabled)
- **Benefit**: Immune to SPAN panel circuit renaming

### Existing Installations

- **Flags**: `USE_CIRCUIT_NUMBERS = False`, `USE_DEVICE_PREFIX = varies`
- **Entity IDs**: `sensor.span_panel_air_conditioner_power` (name-based, legacy pattern)
- **Friendly Names**: "Air Conditioner Power" (clean format, auto-sync always enabled)
- **Behavior**: Continues working exactly as before upgrade

### Auto-Sync Feature

- **Purpose**: Update friendly names when SPAN circuit names change via integration reload
- **Scope**: Friendly names only - never touches entity IDs
- **Availability**: Always enabled - built into all circuit entities (switch, select, sensor)
- **Operation**: Integration reloads when name changes detected, preserving entity IDs while updating display names

## Phase 2: Migration Feature (To Implement)

### Purpose

Provide existing installations an **optional** migration path to stable circuit-based entity IDs while preserving all historical data and user customizations.

### New Configuration Flag

```python
MIGRATE_TO_CIRCUIT_IDS = "migrate_to_circuit_ids"  # Triggers migration process
```

### Migration Requirements

1. **Completely optional** - no automatic migration, user-initiated only
2. **Data preservation** - all statistics history and friendly name customizations maintained
3. **One-way operation** - clear communication about irreversibility (backup-only recovery)
4. **Leverages Home Assistant's entity registry** - uses proven migration infrastructure

### Migration Behavior

- **Availability**: Only appears for legacy installations (`USE_CIRCUIT_NUMBERS = False`)
- **Preview**: Shows current vs. proposed entity ID changes before execution
- **Execution**: Updates entity registry, preserves statistics and customizations
- **Result**: Changes `USE_CIRCUIT_NUMBERS` to `True`, enabling Phase 1 stable ID behavior

### Post-Migration State

After successful migration, installation behaves identically to new installations:

- Stable circuit-based entity IDs
- Full auto-sync capability available
- All Phase 1 principles and behaviors apply

### Migration Implementation Details

**Technical Approach:**

- Uses Home Assistant's entity registry migration system
- Preserves all historical statistics data automatically
- Maintains current friendly names as user overrides when appropriate
- One-way operation with clear backup recovery path

**User Experience:**

1. Migration option appears in integration configuration
2. Preview shows entity ID changes before execution
3. User confirms understanding of irreversible nature
4. Migration executes with full data preservation
5. All statistics history preserved, friendly names maintained

**Error Handling:**

- Dry-run validation before actual migration
- Clear error messages for any conflicts
- Recommendation to create backup before migration

## Phase 3: Circuit Grouping & Synthetic Entities

### Purpose

Provide advanced circuit management capabilities allowing users to create logical groupings and custom computed entities that extend beyond basic circuit monitoring.

### Core Features

#### Circuit Grouping (Synthetic Circuits)

- **Multi-circuit entities**: Combine multiple physical circuits into logical synthetic entities
- **Voltage aggregation**: Proper electrical calculations (e.g., 120V + 120V = 240V for split-phase loads)
- **Power summation**: Aggregate power consumption/production across circuit groups
- **Child circuit hiding**: Individual circuits become hidden when part of synthetic groups
- **Metadata tracking**: Full attribution of which circuits compose synthetic entities

#### Custom Entity Definitions

- **User-defined calculations**: Create custom sensors with formulas (e.g., amperage = power/voltage)
- **Full sensor configuration**: All Home Assistant sensor attributes (units, device class, state class)
- **Statistics integration**: Custom entities fully integrate with Home Assistant statistics
- **Flexible targeting**: Apply to individual circuits or synthetic circuit groups

### Example Use Cases

#### US Residential Scenarios (Split-Phase 120V/240V)

- **240V Appliances**: Group circuits 30+32 for electric dryer monitoring
  - Combined voltage: 120V + 120V = 240V (split-phase)
  - Combined power: Individual power watts summed
  - Single entity replacing two separate circuit entities
- **Sub-panel Monitoring**: Group circuits 10-15 for garage sub-panel total
  - Aggregate power consumption across all garage circuits
  - Custom amperage calculation: total_power / 240V
  - Single dashboard tile for entire garage electrical load
- **HVAC Systems**: Group multiple circuits for complete system monitoring
  - Main unit + outdoor condenser + auxiliary heat
  - Total system power consumption and efficiency metrics
- **Electric Vehicle Charging**: Group circuits for Level 2 EV charger
  - 240V split-phase monitoring (circuits on opposite legs)
  - Power tracking for charging sessions
  - Cost calculations based on time-of-use rates

#### European Scenarios (230V Single-Phase / 400V Three-Phase)

- **230V Single-Phase Appliances**: Individual high-power device monitoring
  - Electric oven, washing machine, dishwasher on dedicated 230V circuits
  - Direct power monitoring without voltage combination
  - Amperage calculations: power / 230V
- **400V Three-Phase Systems**: Industrial/commercial installations
  - Group three circuits (L1, L2, L3) for three-phase motor monitoring
  - Balanced load calculations across all three phases
  - Total system power: sum of all three phase powers
  - Voltage monitoring: 400V line-to-line, 230V line-to-neutral
- **Heat Pump Systems**: Three-phase HVAC monitoring
  - Compressor + fans + controls across multiple phases
  - Seasonal efficiency tracking (COP calculations)
  - Load balancing verification across phases
- **EV Charging (European)**: Three-phase 400V charging stations
  - 11kW or 22kW three-phase charging monitoring
  - Power distribution across L1/L2/L3 phases
  - Charging efficiency and session cost tracking

#### Universal Scenarios (Any Voltage System)

- **Solar Enhancement**: Beyond basic leg configuration
  - Custom efficiency calculations (output/input ratios)
  - Performance tracking with weather data integration
  - Time-of-day optimization metrics
- **Workshop/Garage Sub-panels**: Aggregate monitoring
  - Multiple circuits grouped by physical location
  - Total consumption tracking for cost allocation
  - Peak demand monitoring for capacity planning

### Detailed Technical Requirements

#### Circuit Group Data Structure

```python
CircuitGroup = {
    "group_id": "unique_group_identifier",
    "name": "User Friendly Group Name",
    "circuit_ids": ["circuit_30", "circuit_32"],
    "group_type": "voltage_additive",  # or "parallel", "custom"
    "voltage_calculation": "sum",      # sum, max, average, custom_formula
    "power_calculation": "sum",        # sum, max, average, custom_formula
    "custom_formulas": {
        "voltage": "circuit_30.voltage + circuit_32.voltage",
        "amperage": "total_power / combined_voltage"
    },
    "hide_child_circuits": true,
    "metadata": {
        "created_date": "2024-01-01",
        "electrical_type": "split_phase_240v",
        "notes": "Electric dryer monitoring"
    }
}
```

#### Custom Entity Definition Structure

```python
# User-defined custom entity that extends Home Assistant sensor entity descriptions
CustomSensorEntityDescription = {
    "key": "dryer_amperage",
    "name": "Dryer Amperage",
    "native_unit_of_measurement": "A",
    "device_class": "current",
    "state_class": "measurement",
    "suggested_display_precision": 1,
    "icon": "mdi:current-ac",
    "entity_category": None,

    # Phase 3 extensions for synthetic entities
    "target_type": "circuit_group",    # or "circuit", "individual_circuit"
    "target_id": "dryer_group_240v",   # References CircuitGroup.group_id
    "calculation_formula": "power / voltage",
    "formula_variables": ["power", "voltage"],  # Available from target
    "fallback_value": 0.0,            # When calculation fails

    # Tab-level linkage (simpler, follows solar pattern)
    "source_circuits": ["circuit_30", "circuit_32"]  # Circuit/tab level dependency
}

# Circuit group structure with entity relationship tracking
CircuitGroup = {
    "group_id": "dryer_group_240v",
    "name": "Electric Dryer 240V",
    "circuit_ids": ["circuit_30", "circuit_32"],
    "group_type": "voltage_additive",
    "hide_child_circuits": True,

    # Entity relationship tracking
    "child_entities": [                # Entities that get hidden/grouped
        "sensor.span_panel_circuit_30_power",
        "sensor.span_panel_circuit_30_energy_consumed",
        "sensor.span_panel_circuit_32_power",
        "sensor.span_panel_circuit_32_energy_consumed",
        "switch.span_panel_circuit_30",
        "switch.span_panel_circuit_32",
        "select.span_panel_circuit_30_priority",
        "select.span_panel_circuit_32_priority"
    ],
    "synthetic_entities": [            # New entities created for this group
        "sensor.dryer_group_240v_power",       # Auto-generated group entity
        "sensor.dryer_group_240v_voltage",     # Auto-generated group entity
        "sensor.dryer_amperage"                # Custom user-defined entity
    ]
}
```

#### Electrical Calculation Engine

- **Voltage Combinations**:
  - Split-phase (leg-to-leg): 120V + 120V = 240V for split-phase loads
  - Single-leg: Multiple 120V circuits remain 120V (no voltage combination)
  - Custom formulas: User-defined calculations for complex scenarios
- **Power Aggregation**:
  - Summation: Most common for grouped circuits
  - Maximum: Peak load tracking across group
  - Average: Baseline consumption patterns
- **Advanced Metrics**:
  - Amperage: power / voltage with proper electrical math
  - Power factor: For AC loads (future enhancement)
  - Efficiency ratios: Input vs output for solar/battery systems

#### Entity Creation & Management Workflow

**Synthetic Circuit Entity Generation**:

- Automatically creates standard sensor entities for circuit groups (power, voltage, energy)
- Follows existing `SpanPanelCircuitsSensorEntityDescription` pattern
- Entity IDs use group naming: `sensor.span_panel_dryer_group_power`
- Friendly names combine group name + sensor type: "Electric Dryer 240V Power"

**Custom Entity Integration**:

- User-defined entities extend `SpanPanelCircuitsSensorEntityDescription`
- Custom `value_fn` uses formula evaluation engine
- Full Home Assistant entity lifecycle (creation, updates, removal)
- Statistics integration maintains historical data

**Parent-Child Entity Relationships**:

```python
# When circuits 30+32 are grouped into "dryer_group_240v":
hidden_entities = [
    "sensor.span_panel_circuit_30_power",      # Hidden from UI
    "sensor.span_panel_circuit_32_power",      # Hidden from UI
    "switch.span_panel_circuit_30",            # Hidden from UI
    "switch.span_panel_circuit_32"             # Hidden from UI
]

created_entities = [
    "sensor.span_panel_dryer_group_power",     # Auto: sum of child power
    "sensor.span_panel_dryer_group_voltage",   # Auto: calculated voltage
    "sensor.dryer_amperage"                    # Custom: power/voltage formula
]
```

**Entity State Synchronization**:

- Real-time calculation updates when child circuit data changes
- Formula error handling with fallback values
- Entity availability based on child circuit connectivity

### Integration with Existing Features

- **Builds on solar infrastructure**: Extends existing inverter leg configuration concept
- **Respects naming principles**: All synthetic entities follow established naming patterns
- **Auto-sync compatible**: Circuit name changes propagate to synthetic entities
- **Migration aware**: Existing configurations preserved during Phase 3 rollout

### User Workflow & Interface Design

#### Circuit Group Creation Workflow

1. **Circuit Selection Interface**

   - Visual panel layout showing all available circuits with their current names
   - Multi-select capability with validation (prevent conflicts)
   - Real-time preview of electrical calculations
   - Template selection for common scenarios (240V appliance, sub-panel, etc.)

2. **Group Configuration**

   - Electrical type selection
   - Naming and entity ID generation preview
   - Voltage/power calculation method selection

3. **Validation & Preview**

   - Electrical safety validation (prevent dangerous configurations)
   - Entity conflict detection and resolution
   - Preview of resulting entities and their attributes
   - Warning about hidden child circuits

4. **Activation & Testing**
   - One-click activation with integration reload
   - Real-time validation of calculations against actual data
   - Rollback capability if issues detected

#### Custom Entity Creation Workflow

1. **Target Selection**

   - Choose individual circuit or circuit group
   - Available calculation inputs displayed

2. **Entity Configuration**

   - Full Home Assistant entity configuration
   - Unit validation and electrical safety checks
   - Statistics tracking preferences
   - Dashboard integration suggestions

3. **Formula Definition**
   - Built-in templates (amperage, efficiency, etc.)
   - Real-time calculation preview with current data
   - Error handling and fallback value definition

### Configuration Storage & Management

#### Data Persistence

- **Integration configuration**: Stored in Home Assistant config entry options
- **Circuit group definitions**: JSON-serialized in integration storage

#### Configuration Migration

- **Version compatibility**: Forward/backward compatibility for configuration formats
- **Automatic upgrades**: Seamless migration of existing solar leg configurations
- **Conflict resolution**: Smart handling of overlapping configurations
- **Rollback capability**: Deletion of a synthetic circuit enables the underlying child entities

#### Multi-Panel Support

- **Isolated configurations**: Each SPAN panel maintains separate circuit groups
- **Cross-panel grouping**: Future capability for multi-panel installations
- **Naming collision prevention**: Panel-specific entity ID generation

### Implementation Benefits

- **Electrical accuracy**: Proper multi-phase and split-phase calculations
- **Reduced complexity**: Logical entity grouping reduces dashboard clutter
- **Extensible framework**: Foundation for future electrical monitoring enhancements
- **User control**: Optional feature that doesn't impact existing installations

### Implementation Strategy

#### Phase 3a: Foundation (Core Infrastructure)

- **Circuit group data models**: Define storage structures and validation
- **Configuration management**: Integration options UI and storage
- **Basic grouping engine**: Simple circuit combination logic
- **Entity hiding mechanism**: Suppress child circuits when grouped

#### Phase 3b: Advanced Calculations (Electrical Intelligence)

- **Electrical calculation engine**: Voltage, power, and current math
- **Custom formula parser**: Safe expression evaluation with validation
- **Template system**: Pre-built configurations for common scenarios
- **Real-time validation**: Electrical safety and logic checking

#### Phase 3c: User Interface (Configuration Experience)

- **Visual circuit selection**: Interactive panel layout interface
- **Formula builder**: User-friendly equation creation tools
- **Preview system**: Real-time calculation and entity preview
- **Configuration management**: Import/export, templates, and backups

#### Phase 3d: Advanced Features (Power User Tools)

- **Cross-panel grouping**: Multi-panel installation support
- **Statistics integration**: Advanced historical data analysis
- **Automation triggers**: Events for electrical threshold monitoring
- **API extensions**: External system integration capabilities

### Technical Architecture

#### Core Components

- **CircuitGroupManager**: Central orchestration of all grouping operations
- **ElectricalCalculator**: Safe and accurate electrical mathematics
- **EntitySynthesizer**: Dynamic entity creation and management
- **ConfigurationValidator**: Electrical safety and logic validation
- **TemplateLibrary**: Pre-built common configurations

#### Integration Points

- **Existing sensor creation**: Seamless integration with current entity creation flow
- **Configuration flow**: Extended options interface for group management
- **Coordinator updates**: Real-time data flow to synthetic entities
- **Entity registry**: Proper Home Assistant entity lifecycle management

#### Safety & Validation

- **Electrical safety checks**: Prevent dangerous configuration combinations
- **Formula validation**: Secure expression parsing with electrical constants
- **Conflict detection**: Prevent overlapping circuit assignments
- **Rollback capability**: Safe configuration changes with undo functionality

## Installation Types Summary

### Pre-1.0.4 Installations

- `USE_DEVICE_PREFIX = False`, `USE_CIRCUIT_NUMBERS = False`
- Entity IDs: `sensor.air_conditioner_power`
- **No changes during upgrade**

### Post-1.0.4 Installations

- `USE_DEVICE_PREFIX = True`, `USE_CIRCUIT_NUMBERS = False`
- Entity IDs: `sensor.span_panel_air_conditioner_power`
- **No changes during upgrade**

### New Installations (v1.0.9+)

- `USE_DEVICE_PREFIX = True`, `USE_CIRCUIT_NUMBERS = True`
- Entity IDs: `sensor.span_panel_circuit_1_power` (stable)
- Friendly Names: "Air Conditioner Power" (dynamic with auto-sync)

## Benefits Summary

### Phase 1 (Implemented)

- **New users**: Stable entity IDs from day one
- **Existing users**: Zero disruption, exact same behavior
- **Auto-sync built-in**: Automatic friendly name synchronization for all installations

### Phase 2 (To Implement)

- **Migration path**: Existing users can upgrade to stable IDs
- **Data preservation**: Full history and customizations maintained (dashboard updates are out of scope until we provide our own cards)
- **Future-proof**: All installations benefit from stable entity IDs and built-in auto-sync

### Phase 3 (Future)

- **Advanced circuit management**: Group circuits into logical synthetic entities
- **Custom entity creation**: User-defined calculations and metrics
- **Electrical accuracy**: Proper voltage and power calculations for multi-circuit loads
- **Enhanced monitoring**: Comprehensive electrical system visibility

## Success Criteria

- Phase 1: ✅ New installations immune to circuit renaming, existing installations unchanged
- Phase 2: Existing installations can optionally migrate to stable IDs with full data preservation
- Phase 3: Advanced circuit grouping and custom entity capabilities for comprehensive electrical monitoring

This framework ensures that all users can eventually benefit from stable entity IDs while respecting their choice of when (or if) to migrate, with complete preservation of their historical data and customizations. Phase 3 extends this foundation with advanced circuit management capabilities that build naturally on the existing architecture.
