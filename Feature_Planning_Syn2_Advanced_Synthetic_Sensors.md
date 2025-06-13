# Syn2: Advanced Synthetic Sensors - HA Synthetic Sensors Package

## Overview

Syn2 (Synthetic Sensors v2) is a standalone Python package that enables users to create custom mathematical sensors using
any Home Assistant entity as input. This system provides flexible energy analysis and calculation capabilities through
YAML configuration.

### Key Concept

Syn2 allows you to create synthetic sensors using mathematical equations with any number of
parameters. The syntax uses sensor keys as unique identifiers and flattened single-formula sensors.
Multi-formula sensors use calculated attributes to provide rich data without cluttering the UI.

### Core Capabilities

- **Universal Entity Support**: Use any Home Assistant sensor entity in formulas
- **Mathematical Formulas**: Safe evaluation of mathematical expressions with real-time updates
- **Hierarchical Relationships**: Create sensors that reference other synthetic sensors
- **YAML Configuration**: Flattened syntax for common use cases
- **Calculated Attributes**: Rich sensor data through computed attributes
- **Cross-References**: Automatic entity ID resolution

### Dynamic Entity Aggregation Functions and Attribute Access

Syn2 supports aggregation and attribute access patterns for Home Assistant entities. This enables users to sum, average,
count, or otherwise aggregate values from groups of entities selected by device class, regex, tag/label, area, or
attribute conditions, as well as directly access entity attributes using dot notation.

**Current Features:**

- **Variable Inheritance**: Attribute formulas inherit parent sensor variables
- **Dot Notation**: Entity attribute access via `entity.attribute_name`
- **Dynamic Collections**: Aggregate entities by regex, device_class, tags, area, or attribute conditions
- **Mathematical Functions**: Full suite including count(), std(), var(), trigonometry, and more
- **Runtime Resolution**: Dynamic entity discovery and value aggregation

**Implemented Features:**

- **Complete OR Logic Support**: All collection patterns support pipe (`|`) syntax for OR conditions
- **Enhanced Variable Support**: Variables in collection function patterns for dynamic queries
- **State Pattern Support**: New `state:` pattern for filtering entities by state values

### Key Use Cases

**Energy Management:**

- Convert negative grid power to positive "solar sold" values
- Calculate real-time energy costs based on time-of-use rates
- Aggregate multiple circuits into logical groupings (HVAC, lighting, etc.)
- Monitor efficiency metrics and performance indicators

**Device Monitoring:**

- Count devices with low battery levels across your home
- Average temperature readings from specific rooms or areas
- Track total power consumption by device class

**Home Automation:**

- Create comfort indices combining temperature, humidity, and air quality
- Calculate derived values like "feels like" temperature
- Build hierarchical sensor relationships for complex analysis

## Why Synthetic Sensors vs Templates

Home Assistant templates are powerful but have limitations for complex mathematical calculations:

**Template Limitations:**

- No persistent state between calculations
- Limited mathematical functions
- No variable reuse or inheritance
- Difficult to build hierarchical calculations
- Performance concerns with complex expressions
- No built-in aggregation across entity collections

**Synthetic Sensors Advantages:**

- Persistent calculated sensors that integrate with HA statistics
- Rich mathematical function library
- Variable inheritance between main sensors and attributes
- Hierarchical sensor relationships
- Collection-based aggregation (sum all circuit power, count low batteries)
- Proper device classes and state classes for HA integration

**When Templates Are Better:**

- Simple one-off calculations that don't need persistence
- Dynamic entity selection based on complex logic
- Text manipulation and string formatting
- Conditional logic with multiple branches
- Integration with Jinja2 filters and Home Assistant context
- Real-time dashboard displays that don't need sensor history

**Using Templates WITH Synthetic Sensors:**
Template sensors can serve as inputs to synthetic sensors for complex workflows:

```yaml
# Template sensor for dynamic logic
template:
  - sensor:
      - name: "Current Rate Tier"
        state: >
          {% if now().hour >= 16 and now().hour < 21 %}
            peak
          {% elif now().hour >= 21 or now().hour < 7 %}
            off_peak
          {% else %}
            standard
          {% endif %}

      - name: "Peak Load Factor"
        state: >
          {% set peak_devices = ['sensor.hvac_power', 'sensor.water_heater_power', 'sensor.ev_charger_power'] %}
          {% set total = peak_devices | map('states') | map('float', 0) | sum %}
          {{ (total / 7200) | round(2) }}  # 7200W theoretical max

# Synthetic sensor using template results
sensors:
  dynamic_energy_cost:
    name: "Dynamic Energy Cost"
    formula: "power * rate_multiplier * load_factor"
    variables:
      power: "sensor.total_power"
      rate_multiplier: "sensor.current_rate_tier" # Uses template sensor
      load_factor: "sensor.peak_load_factor" # Uses template sensor
    unit_of_measurement: "¢/h"
    device_class: "monetary"

  efficiency_index:
    name: "Home Efficiency Index"
    formula: "base_efficiency * seasonal_adjustment"
    variables:
      base_efficiency: "sensor.calculated_base_efficiency"
      seasonal_adjustment: "sensor.seasonal_factor" # Could be from template
    attributes:
      monthly_trend:
        formula: "efficiency_index * 30 * 24"
        unit_of_measurement: "efficiency hours"
```

**Current Limitations:**

- No loops or conditional logic beyond ternary operators (like sum, avg, etc.)
- Collection functions are the only iteration mechanism

## Configuration Concepts

**Entity Creation**: Each sensor key becomes `sensor.syn2_{sensor_key}` in Home Assistant

**Two Configuration Patterns**:

- **Single Formula**: Direct `formula` field for simple calculations
- **Multi-Formula**: Main formula with calculated `attributes` for rich data analysis

### YAML Configuration Format

```yaml
# ha-synthetic-sensors configuration
version: "1.0"
global_settings:
  domain_prefix: "syn2" # Creates sensor.syn2_* entities

sensors:
  # Single Formula Sensors (Flattened Syntax)
  solar_sold_positive: # REQUIRED: Unique identifier (key)
    name: "Solar Sold (Positive Value)" # OPTIONAL: Display name only
    formula: "abs(solar_power)" # Direct formula (no nested array)
    variables:
      solar_power: "sensor.span_panel_solar_inverter_instant_power"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"

  # Multi-Formula Sensors (Calculated Attributes)
  net_energy_analysis: # REQUIRED: Unique identifier (key)
    name: "Net Energy Analysis" # OPTIONAL: Display name only
    formula: "net_power * buy_rate / 1000 if net_power > 0 else abs(net_power) * sell_rate / 1000"
    attributes:
      daily_projected:
        formula: "state * 24" # References main state
        unit_of_measurement: "¢/day"
      monthly_projected:
        formula: "net_energy_analysis * 24 * 30" # Reference main state by key
        unit_of_measurement: "¢/month"
      efficiency_rating:
        formula: "abs(net_power) / max_capacity * 100"
        unit_of_measurement: "%"
    variables:
      net_power: "sensor.span_panel_current_power"
      buy_rate: "input_number.electricity_buy_rate_cents_kwh"
      sell_rate: "input_number.electricity_sell_rate_cents_kwh"
      max_capacity: "input_number.max_panel_capacity"
    unit_of_measurement: "¢/h"
    device_class: "monetary"
```

## Example Use Cases

### Solar Analytics

```yaml
sensors:
  # Solar sold as positive value
  solar_sold_watts: # Sensor key
    name: "Solar Energy Sold" # Display name
    formula: "abs(min(grid_power, 0))" # Direct formula
    variables:
      grid_power: "sensor.span_panel_current_power"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"

  # Solar self-consumption rate
  solar_self_consumption_rate: # Sensor key
    name: "Solar Self-Consumption Rate" # Display name
    formula: "if(solar_production > 0, (solar_production - solar_export) / solar_production * 100, 0)"
    variables:
      solar_production: "sensor.span_panel_solar_inverter_instant_power"
      solar_export: "sensor.syn2_solar_sold_watts" # Reference other synthetic sensor
    unit_of_measurement: "%"
    state_class: "measurement"
```

### Hierarchical Calculations

```yaml
sensors:
  # Child sensors - base calculations
  hvac_total_power: # Sensor key
    name: "HVAC Total Power" # Display name
    formula: "heating_power + cooling_power"
    variables:
      heating_power: "sensor.span_panel_circuit_5_power"
      cooling_power: "sensor.span_panel_circuit_6_power"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"

  lighting_total_power: # Sensor key
    name: "Lighting Total Power" # Display name
    formula: "living_room + kitchen + bedroom"
    variables:
      living_room: "sensor.span_panel_circuit_10_power"
      kitchen: "sensor.span_panel_circuit_11_power"
      bedroom: "sensor.span_panel_circuit_12_power"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"

  # Parent sensor - references other synthetic sensors by entity ID
  total_home_consumption: # Sensor key
    name: "Total Home Consumption" # Display name
    formula: "hvac + lighting + appliances"
    variables:
      hvac: "sensor.syn2_hvac_total_power" # Entity ID reference
      lighting: "sensor.syn2_lighting_total_power" # Entity ID reference
      appliances: "sensor.major_appliances_power"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"
```

### Cost Analysis

```yaml
sensors:
  # Real-time energy cost rate
  current_energy_cost_rate: # Sensor key
    name: "Current Energy Cost Rate" # Display name
    formula: "net_power * buy_rate / 1000 if net_power > 0 else abs(net_power) * sell_rate / 1000"
    variables:
      net_power: "sensor.span_panel_current_power"
      buy_rate: "input_number.electricity_buy_rate_cents_kwh"
      sell_rate: "input_number.electricity_sell_rate_cents_kwh"
    unit_of_measurement: "¢/h"
    device_class: "monetary"
    state_class: "measurement"
```

## Integration with Home Assistant

Synthetic sensors integrate naturally with Home Assistant's ecosystem:

- **Statistics**: Properly configured `device_class` and `state_class` enable long-term statistics
- **Energy Dashboard**: Power sensors can feed into Home Assistant's energy dashboard
- **Integration Platform**: Use synthetic sensors as sources for HA's integration platform to create cumulative sensors
- **Automations**: Reference synthetic sensors in automations just like any other sensor
- **Cards & Dashboards**: Full compatibility with all Home Assistant frontend features

### Syntax Patterns

#### Device Class Aggregation

```yaml
sensors:
  open_doors_and_windows: # This sensor key IS the unique_id
    name: "Open Doors and Windows" # Friendly name for HA UI
    formula: sum(device_class:door|window)
    unit_of_measurement: "count"
    device_class: "door"
    state_class: "measurement"
```

_Aggregates all entities with device_class `door` or `window`._

#### Regex Aggregation

```yaml
sensors:
  total_circuit_power: # This sensor key IS the unique_id
    name: "Total Circuit Power" # Friendly name for HA UI
    formula: sum(regex:sensor\.span_panel_circuit_.*_instant_power)
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"
```

_Sums all sensors whose entity_id matches the regex pattern._

#### Area and Device Class Aggregation

```yaml
sensors:
  garage_windows_open: # This sensor key IS the unique_id
    name: "Garage Windows Open" # Friendly name for HA UI
    formula: sum(area:garage device_class:window)
    unit_of_measurement: "count"
    device_class: "window"
    state_class: "measurement"
```

_Sums all window sensors in the garage area._

#### Tag/Label Aggregation

```yaml
sensors:
  tagged_sensors_sum: # This sensor key IS the unique_id
    name: "Sum of Tagged Sensors" # Friendly name for HA UI
    formula: sum(tags:tag2,tag5)
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"
```

_Sums all sensors that have either the `tag2` or `tag5` label._

#### Attribute-Based Aggregation

```yaml
sensors:
  low_battery_sensors: # This sensor key IS the unique_id
    name: "Low Battery Sensors" # Friendly name for HA UI
    formula: sum(attribute:battery_level<20)
    unit_of_measurement: "count"
    device_class: "battery"
    state_class: "measurement"
```

_Sums all sensors with a `battery_level` attribute less than 20._

### YAML Quoting Guidance for Query Patterns

When using query patterns (such as `tags:`, `device_class:`, `regex:`, etc.) in formulas, you may use either quoted or
unquoted forms:

- **Unquoted**: Works for simple patterns with no spaces or special YAML characters.
- **Quoted**: Required if your tag, device class, or pattern contains spaces or special characters (such as `:`, `#`, `,`, etc.).

**Examples:**

```yaml
# No spaces or special characters: quotes optional
formula: sum(tags:tag2,tag5)
formula: sum(device_class:door|window)

# Spaces or special characters: quotes required
formula: sum("tags:my tag with spaces,tag2")
formula: sum('tags:tag2,#tag3')
```

**Tip:**
If in doubt, use quotes. Both single and double quotes are supported.

### Dot Notation and Attribute Shortcuts

For simple and direct access to entity attributes, Syn2 supports dot notation in formulas:

- **Full attribute path:**
  Reference any attribute using `entity_id.attributes.attribute_name`

  ```yaml
  formula: sensor1.attributes.battery_level
  ```

  This resolves to the value of the `battery_level` attribute of `sensor1`.

- **Attribute shortcut:**
  If the attribute is not a state property, `entity_id.attribute_name` will resolve to `entity_id.attributes.attribute_name`
  if present.

  ```yaml
  formula: sensor1.battery_level
  ```

  This is a shortcut for `sensor1.attributes.battery_level`.

- **Aggregation with attribute access:**

  ```yaml
  formula: avg(sensor1.battery_level, sensor2.battery_level, sensor3.battery_level)
  ```

  Averages the `battery_level` attribute across the listed sensors.

### Notes

- All aggregation functions (`sum`, `avg`, `count`, etc.) support these query patterns.
- Dot notation for attribute access is supported everywhere a variable or entity can be referenced.
- These features are designed to be user-friendly, and compatible with YAML best practices.
- **Sensor Key = Unique ID**: The YAML sensor key (e.g., `open_doors_and_windows`) IS the unique_id. No separate
  `unique_id` field is needed.
- **Name = Friendly Name**: The `name` field provides the human-readable display name in Home Assistant UI.
- **Recommended Fields**: While only `formula` is required, adding `device_class`, `state_class`, and `unit_of_measurement`
  ensures proper Home Assistant integration.

### Variable Inheritance in Attribute Formulas

Attribute formulas automatically inherit all variables from their parent sensor, enabling flexible calculations that reference
both the main sensor state and external entities.

**Inheritance Rules:**

1. **Parent Variables**: All variables defined in the parent sensor are available to attribute formulas
2. **Main Sensor Reference**: The parent sensor's state is available using the sensor key as a variable name
3. **Variable Access Only**: Attributes inherit variables but cannot define their own variables sections
4. **Variable Resolution**: All variables must be defined in the parent sensor's variables section

**Examples:**

```yaml
sensors:
  energy_analysis:
    name: "Energy Analysis"
    formula: "grid_power + solar_power"
    variables:
      grid_power: "sensor.grid_meter"
      solar_power: "sensor.solar_inverter"
      efficiency_factor: "input_number.base_efficiency"
      electricity_rate: "input_number.current_rate"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"
    attributes:
      # Attribute inherits all parent variables
      daily_projection:
        formula: "energy_analysis * 24" # References main sensor by key
        unit_of_measurement: "Wh"

      # Attribute uses inherited variables
      efficiency_percent:
        formula: "solar_power / (grid_power + solar_power) * 100"
        unit_of_measurement: "%"

      # Attribute with additional variables
      cost_analysis:
        formula: "grid_power * electricity_rate / 1000"
        variables:
          electricity_rate: "input_number.current_rate" # New variable
        unit_of_measurement: "¢/h"

      # Attribute overriding parent variable
      custom_efficiency:
        formula: "solar_power * efficiency_factor"
        variables:
          efficiency_factor: "input_number.custom_efficiency" # Overrides parent
        unit_of_measurement: "W"
```

**Variable Resolution Order:**

1. Parent sensor variables (defined in variables section)
2. Main sensor state reference (sensor key → entity_id)
3. Direct entity_id references in formula

**Advanced Features:**

- **Dynamic Queries**: Attribute formulas support all dynamic query types (`regex:`, `tags:`, etc.)
- **Dot Notation**: Access entity attributes using `entity.attribute_name` syntax
- **Cross-References**: Reference other synthetic sensors by entity_id
- **Runtime Resolution**: Dynamic queries are resolved at evaluation time based on current HA state

**Architecture Benefits**: The solution provides a extensible foundation ready for implementing the full dynamic entity
aggregation system while maintaining backward compatibility and comprehensive test coverage.

## Currently Implemented Features

### Variable Inheritance System

Attribute formulas now automatically inherit all variables from their parent sensor, enabling powerful calculation hierarchies:

```yaml
sensors:
  energy_analysis:
    name: "Complete Energy Analysis"
    formula: "grid_power + solar_power - battery_discharge"
    variables:
      grid_power: "sensor.grid_meter"
      solar_power: "sensor.solar_inverter"
      battery_discharge: "sensor.battery_system"
      efficiency_factor: "input_number.system_efficiency"
      electricity_rate: "input_number.current_rate"
    attributes:
      # All attributes inherit: grid_power, solar_power, battery_discharge, efficiency_factor

      # Reference main sensor state
      daily_projection:
        formula: "energy_analysis * 24"
        unit_of_measurement: "kWh"

      # Use inherited variables directly
      grid_dependency:
        formula: "grid_power / (grid_power + solar_power) * 100"
        unit_of_measurement: "%"

      # Add new variables specific to this attribute
      cost_analysis:
        formula: "grid_power * electricity_rate / 1000"
        unit_of_measurement: "¢/h"

      # Override parent variables
      adjusted_efficiency:
        formula: "solar_power * efficiency_factor"
        unit_of_measurement: "W"
    unit_of_measurement: "W"
    device_class: "power"
    state_class: "measurement"
```

### Enhanced Mathematical Functions

Mathematical function library:

```yaml
sensors:
  advanced_calculations:
    name: "Advanced Calculations"
    formula: "clamp(map(efficiency, 0, 100, 0, 255), 0, 255)"
    variables:
      efficiency: "sensor.system_efficiency"
      active_power: "sensor.active_power"
      reactive_power: "sensor.reactive_power"
      temp1: "sensor.living_room_temp"
      temp2: "sensor.kitchen_temp"
      temp3: "sensor.bedroom_temp"
      temp4: "sensor.office_temp"
    attributes:
      normalized_efficiency:
        formula: "percent(efficiency, 100)"

      power_analysis:
        formula: "sqrt(pow(active_power, 2) + pow(reactive_power, 2))"

      temperature_comfort:
        formula: "avg(temp1, temp2, temp3, temp4)"
```

### Dot Notation Attribute Access

Direct access to entity attributes using intuitive dot syntax:

```yaml
sensors:
  battery_health_summary:
    name: "Battery Health Summary"
    formula: "avg(phone.battery_level, tablet.battery_level, laptop.battery_level)"
    variables:
      phone: "sensor.phone_battery"
      tablet: "sensor.tablet_battery"
      laptop: "sensor.laptop_battery"
    attributes:
      min_battery:
        formula: "min(phone.battery_level, tablet.battery_level, laptop.battery_level)"

      critical_devices:
        formula: "count_if(phone.battery_level < 20) + count_if(tablet.battery_level < 20)"
        # Note: count_if is planned for future implementation
```

### Dynamic Query Resolution System

Runtime resolution of collection functions with full Home Assistant integration:

```yaml
# These patterns are fully functional with runtime entity resolution
sensors:
  all_circuit_power:
    name: "All Circuit Power"
    formula: sum("regex:sensor\.circuit_.*_power")

  open_access_points:
    name: "Open Access Points"
    formula: count("device_class:door|window|lock")

  critical_battery_devices:
    name: "Critical Battery Devices"
    formula: count("attribute:battery_level<20|online=false")

  garage_temperature_average:
    name: "Garage Temperature Average"
    formula: avg("area:garage|basement device_class:temperature")

  tagged_sensors_summary:
    name: "Tagged Sensors Summary"
    formula: sum("tags:energy|monitoring|critical")

  high_value_or_active_states:
    name: "High Value or Active States"
    formula: count("state:>100|=on|=active")

  comprehensive_monitoring:
    name: "Comprehensive Monitoring"
    formula: 'sum("device_class:power|energy") + count("area:living_room|kitchen") + avg("tags:monitor|alert")'
```

**Supported Pattern Types:**

- `regex:pattern` - Match entity IDs using regular expressions
- `device_class:class1|class2` - Match entities by device class with OR logic
- `tags:tag1|tag2|tag3` - Match entities by labels/tags with OR logic (requires entity registry)
- `area:area_name1|area_name2` - Match entities in specific areas with OR logic
- `attribute:attr_name<value|attr_name>value` - Match entities by attribute conditions with OR logic (=, !=, <, >, <=, >=)
- `state:>value|=value|<value` - Match entities by state conditions with OR logic

**Supported Aggregation Functions:**

- `sum()`, `avg()`, `count()`, `min()`, `max()`, `std()`, `var()`

## Dynamic Collection Variable Support

### Current Implementation Status

The system **fully supports static and dynamic collection patterns** with complete OR logic and variable substitution:

```yaml
# Static Patterns with OR Logic
sensors:
  static_device_monitoring:
    name: "Static Device Monitoring"
    formula: sum("device_class:temperature|humidity|pressure")

  multi_area_monitoring:
    name: "Multi-Area Monitoring"
    formula: count("area:living_room|kitchen|dining_room")

  comprehensive_attributes:
    name: "Comprehensive Attributes"
    formula: count("attribute:battery_level<20|signal_strength<30|online=false")

  state_based_monitoring:
    name: "State-Based Monitoring"
    formula: count("state:>100|=on|=active")

# Dynamic Patterns with OR Logic - IMPLEMENTED
sensors:
  dynamic_device_monitoring:
    name: "Dynamic Device Monitoring"
    formula: sum("device_class:primary_type|secondary_type")
    variables:
      primary_type: "input_select.primary_device_type"  # resolves to "temperature"
      secondary_type: "input_select.secondary_device_type"  # resolves to "humidity"
    # Results in: sum("device_class:temperature|humidity")
```

### Template Alternative (Optional)

While variable substitution is implemented, complex dynamic logic can still use templates when needed:

```yaml
# For complex conditional pattern building
template:
  - sensor:
      - name: "Complex Dynamic Pattern"
        state: >
          {% if states('input_boolean.advanced_mode') == 'on' %}
            device_class:{{ states('input_select.device_type') }}
          {% else %}
            regex:sensor\.basic_.*
          {% endif %}

# Synthetic sensor uses template result for complex scenarios
sensors:
  conditional_monitoring:
    formula: sum("pattern")
    variables:
      pattern: "sensor.complex_dynamic_pattern"
```

### Processing Architecture (Current)

The system processes formulas in this order:

1. **Static Pattern Recognition**: Collection functions with quoted static patterns are identified
2. **Collection Function Resolution**: Patterns are resolved to entity lists and aggregated values
3. **Mathematical Evaluation**: Final expression with resolved values is evaluated by simpleeval

**Key Insight**: Collection functions are **pre-processed** and replaced with numeric values before mathematical evaluation,
enabling seamless integration with complex formulas.

### Comparison: Synthetic Sensors vs Templates

| Capability                        | Current Synthetic Sensors | Templates |
| --------------------------------- | ------------------------- | --------- |
| **Static Collection Aggregation** | YES                       | NO        |
| **Dynamic Collection Patterns**   | PARTIAL                   | YES       |
| **Mathematical Functions**        | YES                       | NO        |
| **Boolean Logic in Formulas**     | YES                       | YES       |
| **Variable Inheritance**          | YES                       | NO        |
| **Persistent HA Integration**     | YES                       | YES       |
| **Multi-Step Analysis**           | NO                        | YES       |
| **Time-Based Logic**              | NO                        | YES       |
| **Text Manipulation**             | NO                        | YES       |
| **User Familiarity**              | YES                       | YES       |
| **Implementation Complexity**     | Low                       | N/A       |

### Current Capabilities Summary

**Synthetic Sensors Excel At:**

- Mathematical calculations with collection aggregation
- Dynamic entity discovery through variable substitution
- Variable inheritance between main sensors and attributes
- Hierarchical sensor relationships
- Persistent sensor entities with proper HA integration

**Templates Are Better For:**

- Complex multi-step calculations with intermediate variables
- Time-based conditional logic
- Text formatting and string manipulation
- Dynamic entity selection based on complex logic

**Implementation Status:**

- **Static Collection Patterns**: **IMPLEMENTED** with regex, device_class, area, tags, attribute, and state patterns
- **OR Logic Support**: **IMPLEMENTED** across all pattern types using pipe (`|`) syntax
- **Dynamic Collection Variables**: **IMPLEMENTED** - Variable substitution within quoted strings
- **Mathematical Functions**: **COMPREHENSIVE** library available
- **Variable Inheritance**: **FULL SUPPORT** in attribute formulas
- **Template Integration**: **AVAILABLE** as alternative approach for complex dynamic logic

### Future Enhancement Priorities

**Medium Priority Additions:**

- Additional mathematical functions (`median`, `percentile`, `mode`)
- Time-based functions (`hours_since`, `days_since`)
- Enhanced attribute condition operators (`contains`, `startswith`, `endswith`)

**Low Priority (Template Territory):**

- Complex multi-step analysis with intermediate variables
- Advanced text manipulation and formatting
- Real-time conditional logic based on time/state
