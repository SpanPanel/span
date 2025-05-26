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

### Pre-1.0.4 Installations

- `USE_DEVICE_PREFIX = False`, `USE_CIRCUIT_NUMBERS = False`
- Entity IDs: `sensor.air_conditioner_power`
- **No changes during upgrade**

### Post-1.0.4 Installations

- `USE_DEVICE_PREFIX = True`, `USE_CIRCUIT_NUMBERS = False`
- Entity IDs: `sensor.span_panel_air_conditioner_power`
- **No changes during upgrade**

### New Installations (v1.0.9+) - Summary

- `USE_DEVICE_PREFIX = True`, `USE_CIRCUIT_NUMBERS = True`
- Entity IDs: `sensor.span_panel_circuit_1_power` (stable)
- Friendly Names: "Air Conditioner Power" (dynamic with auto-sync)

## Phase 2: Migration Feature (Completed)

After successful migration, installation behaves identically to new installations:

- Provide endity ID migration in config options
- Stable circuit-based entity IDs as default
- Full auto-sync capability available
- All Phase 1 principles and behaviors apply

## Phase 3: Circuit Grouping & Synthetic Entities

### Purpose

Provide advanced circuit management capabilities allowing users to extend circuit entities with additional attributes like circuit breaker amperage and create logical groupings and custom computed entities that extend beyond basic circuit monitoring.

### Core Features

#### Circuit Grouping (Synthetic Circuits)

- **Multi-circuit aggregation**: Combine multiple logical circuits for sub-panel monitoring or related load tracking
- **Split-phase already handled**: 240V appliances appear as single circuits with multiple `tabs` - no manual grouping needed
- **Power summation**: Aggregate power consumption/production across logically related circuit groups
- **Child circuit hiding**: Individual circuits become hidden when part of synthetic groups
- **Metadata tracking**: Full attribution of which circuits compose synthetic entities

#### Custom Entity Definitions

- **Full sensor configuration**: All Home Assistant sensor attributes (units, device class, state class)
- **Custom Attributes on Circuit Entities**: Addition of attributes like circuit amperage, upper threshold for amperage as compared against the observed amperage (new feature), arbitrary attributes the user determines
- **Flexible targeting**: Apply to individual circuits or synthetic circuit groups with features like notification of excessive power consumption for a group

### Example Use Cases

#### US Residential Scenarios

- **Sub-panel Monitoring**: Group circuits 10-15 for garage sub-panel total
  - Aggregate power consumption across all garage circuits
  - Custom amperage calculation: total_power / 240V
  - Single dashboard tile for entire garage electrical load
- **HVAC Systems**: Group multiple circuits for complete system monitoring
  - Main unit + outdoor condenser + auxiliary heat
  - Total system power consumption and efficiency metrics
- **Electric Vehicle Charging**:
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
# Simplified circuit group structure
CircuitGroup = {
    "group_id": "workshop_subpanel_group",
    "name": "Workshop Sub-Panel Total",
    "circuit_ids": ["circuit_15", "circuit_16", "circuit_17", "circuit_18"],
    "group_type": "power_additive",    # Sum power across independent 120V circuits
    "power_calculation": "sum",        # sum, max, average, custom_formula
    "voltage_calculation": "same",     # All circuits are 120V - no voltage combining
    "custom_formulas": {
        "amperage": "total_power / 120",  # Simple amperage calculation for 120V circuits
        "peak_demand": "max(circuit_power_values)"
    },
    "hide_child_circuits": true,
    "metadata": {
        "created_date": "2025-01-01",
        "electrical_type": "120v_subpanel",
        "notes": "Workshop sub-panel aggregate monitoring"
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
# This example shows grouping for sub-panel or multi-appliance scenarios
CircuitGroup = {
    "group_id": "garage_subpanel_group",
    "name": "Garage Sub-Panel Total",
    "circuit_ids": ["circuit_10", "circuit_11", "circuit_12", "circuit_13"],
    "group_type": "power_additive",     # Sum power across multiple independent circuits
    "hide_child_circuits": True,

    # Entity relationship tracking
    "child_entities": [                # Entities that get hidden/grouped
        "sensor.span_panel_circuit_10_power",
        "sensor.span_panel_circuit_10_energy_consumed",
        "sensor.span_panel_circuit_11_power",
        "sensor.span_panel_circuit_11_energy_consumed",
        "sensor.span_panel_circuit_12_power",
        "sensor.span_panel_circuit_12_energy_consumed",
        "sensor.span_panel_circuit_13_power",
        "sensor.span_panel_circuit_13_energy_consumed"
    ],
    "synthetic_entities": [            # New entities created for this group
        "sensor.garage_subpanel_total_power",      # Auto-generated group entity
        "sensor.garage_subpanel_total_energy",     # Auto-generated group entity
        "sensor.garage_amperage"                   # Custom user-defined entity
    ]
}
```

#### Electrical Calculation Engine

- **Power Aggregation**:
  - Summation: Most common for grouped circuits (sub-panels, multi-appliance monitoring)
  - Maximum: Peak load tracking across group
  - Average: Baseline consumption patterns
- **Advanced Metrics**:
  - Amperage: power / voltage with proper electrical math (single circuits or groups)
  - Efficiency ratios: Input vs output for solar/battery systems

#### Entity Creation & Management Workflow

**Synthetic Circuit Entity Generation**:

- Automatically creates standard sensor entities for circuit groups (power, voltage, energy)
- Follows existing `SpanPanelCircuitsSensorEntityDescription` pattern
- Entity IDs use group naming: `sensor.span_panel_dryer_group_power`
- Friendly names combine group name + sensor type: "Electric Dryer 240V Power"

**Custom Entity Integration**:

- User-defined entities extend `SpanPanelCircuitsSensorEntityDescription`
- Full Home Assistant entity lifecycle (creation, updates, removal)
- Statistics integration maintains historical data

**Parent-Child Entity Relationships**:

```python
# When circuits 30+32 are grouped
hidden_entities = [
    "sensor.span_panel_circuit_30_power",      # Hidden from UI
    "sensor.span_panel_circuit_32_power",      # Hidden from UI
    "switch.span_panel_circuit_30",            # Combined into a single switch
    "switch.span_panel_circuit_32"             # Combined into a single switch
]

created_entities = [
    "sensor.span_panel_dryer_group_power",     # Auto: sum of child power
    "sensor.dryer_amperage"                    # Custom: power/voltage formula
]
```

**Entity State Synchronization**:

- Real-time calculation updates when child circuit data changes
- Formula error handling with fallback values
- Entity availability based on child circuit connectivity

### Integration with Existing Features

- **Respects naming principles**: All synthetic entities follow established naming patterns
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
   - Power calculation method selection

3. **Validation & Preview**

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

#### Multi-Panel Support

- **Isolated configurations**: Each SPAN panel maintains separate circuit groups
- **Cross-panel grouping**: Future capability for multi-panel installations
- **Naming collision prevention**: Panel-specific entity ID generation

### Implementation Benefits

- **Reduced complexity**: Logical entity grouping reduces dashboard clutter
- **Extensible framework**: Foundation for future electrical monitoring enhancements
- **User control**: Optional feature that doesn't impact existing installations

### Implementation Strategy

#### Phase 3a: Foundation (Core Infrastructure)

- **Extra Attributes on entities**: User adds extra attributes (for amerperage threshold monitoring, use a calculation from existing data, i.e., amperage = power/volts)
- **Circuit group data models**: Define storage structures and validation for sub-panel/multi-appliance grouping
- **Configuration management**: Integration options UI and storage
- **Basic grouping engine**: Simple circuit aggregation logic (240V appliances already handled by API)
- **Entity hiding mechanism**: Optionally allow the user to suppress child circuits when grouped into a synthetic value

#### Phase 3c: User Interface (Configuration Experience)

- **Visual circuit selection**: Interactive panel layout interface
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
- **EntitySynthesizer**: Dynamic entity creation and management
- **ConfigurationValidator**: Electrical safety and logic validation
- **TemplateLibrary**: Pre-built common configurations

#### Validation

- **Conflict detection**: Prevent overlapping circuit assignments
- **Rollback capability**: undo functionality
