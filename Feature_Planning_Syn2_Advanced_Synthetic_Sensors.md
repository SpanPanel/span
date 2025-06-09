# Syn2: Advanced Synthetic Sensors - HA Synthetic Sensors Package

## Overview

Syn2 (Synthetic Sensors v2) is a standalone Python package that enables users to create custom mathematical sensors using any Home Assistant entity as input. This system provides flexible energy analysis and calculation capabilities through YAML configuration.

### Key Concept

Syn2 allows you to create synthetic sensors using mathematical equations with any number of participant entities. All sensors must have **unique IDs** as primary identifiers - the package uses these IDs internally for all operations. Friendly names are optional and used only for display purposes.

### Core Capabilities

- **Universal Entity Support**: Use any Home Assistant sensor entity in formulas
- **Mathematical Formulas**: Safe evaluation of mathematical expressions with real-time updates
- **Hierarchical Relationships**: Create sensors that reference other synthetic sensors
- **YAML Configuration**: ID-based configuration management
- **Unique ID Requirements**: All sensors must have stable, unique identifiers

### Use Cases

- **Solar Analytics**: Convert negative grid power to positive "solar sold" values
- **Cost Calculations**: Real-time energy cost based on time-of-use rates
- **Sub-Panel Monitoring**: Aggregate multiple circuits into logical groupings
- **Custom Efficiency Metrics**: Calculate ratios and performance indicators
- **Hierarchical Analysis**: Build complex calculation trees using sensor IDs

## Configuration Principles

### **Critical: Unique ID Requirements**

⚠️ **All synthetic sensors MUST have unique IDs** - the package uses these for all internal operations:

Use sensor attributes the way HA new architecture uses them as defined in developer_attribute_readme.md
Use modern Poetry (poetry env activate, poetry install --with dev, poetry run, poetry shell is deprecated, etc.)

- **Entity ID Generation**: `sensor.syn2_{unique_id}` or `sensor.syn2_{sensor_id}_{formula_id}`
- **Service Operations**: All services accept `sensor_id` or `entity_id`, never friendly names
- **Cross-References**: Sensors reference each other by entity ID or unique ID
- **Configuration Storage**: All internal storage keyed by unique ID

✅ **Names are optional** - used only for display when creating sensors:

- Set as sensor's `name` attribute for Home Assistant UI
- Never used for internal identification or operations
- Can be changed without affecting functionality

### YAML Configuration Format

```yaml
# ha-synthetic-sensors configuration
version: "1.0"
global_settings:
  domain_prefix: "syn2" # Creates sensor.syn2_* entities

sensors:
  - unique_id: "solar_sold_positive" # REQUIRED: Unique identifier
    name: "Solar Sold (Positive Value)" # OPTIONAL: Display name only
    formulas:
      - id: "solar_sold" # REQUIRED: Formula ID
        name: "Solar Sold" # OPTIONAL: Display name only
        formula: "abs(solar_power)"
        variables:
          solar_power: "sensor.span_panel_solar_inverter_instant_power"
        unit_of_measurement: "W"
        device_class: "power"
        state_class: "measurement"

  - unique_id: "net_energy_cost" # REQUIRED: Unique identifier
    name: "Net Energy Cost" # OPTIONAL: Display name only
    formulas:
      - id: "cost_rate" # REQUIRED: Formula ID
        name: "Current Cost Rate" # OPTIONAL: Display name only
        formula: "net_power * buy_rate / 1000 if net_power > 0 else abs(net_power) * sell_rate / 1000"
        variables:
          net_power: "sensor.span_panel_current_power"
          buy_rate: "input_number.electricity_buy_rate_cents_kwh"
          sell_rate: "input_number.electricity_sell_rate_cents_kwh"
        unit_of_measurement: "¢/h"
        device_class: "monetary"
```

### Entity ID Generation

```python
# Entity IDs generated from unique IDs, not names
sensor_entity_id = f"sensor.syn2_{sensor_config.unique_id}"                    # sensor.syn2_solar_sold_positive
formula_entity_id = f"sensor.syn2_{sensor_config.unique_id}_{formula_config.id}"  # sensor.syn2_solar_sold_positive_solar_sold

# Names set as display attributes (if provided)
sensor_attributes = {
    "name": sensor_config.name or sensor_config.unique_id,  # Falls back to unique_id
    "unique_id": f"syn2_{sensor_config.unique_id}_{formula_config.id}"
}
```

## Service Interface

All services use **unique IDs or entity IDs** for identification, never names:

### Service Usage Examples

```yaml
# Load configuration via service call
service: synthetic_sensors.load_configuration
data:
  config: |
    sensors:
      - unique_id: "solar_sold_positive"
        formulas:
                  - id: "solar_sold"
          formula: "abs(min(grid_power, 0))"
            variables:
              grid_power: "sensor.span_panel_current_power"
            unit_of_measurement: "W"
            device_class: "power"

# Get sensor info by entity_id
service: synthetic_sensors.get_sensor_info
data:
  entity_id: "sensor.syn2_solar_sold_positive_solar_sold"  # Uses HA entity_id

# Update sensor by entity_id
service: synthetic_sensors.update_sensor
data:
  entity_id: "sensor.syn2_solar_sold_positive_solar_sold"  # Uses HA entity_id
  formulas:
    - id: "solar_sold"  # id for formula updates
      formula: "abs(min(grid_power, 0)) * 1.1"  # Updated formula

# Validate configuration
service: synthetic_sensors.validate_config
data:
  config: |
    sensors:
      - unique_id: "test_sensor"  # ID-based validation
        formulas:
                  - id: "test_formula"
          formula: "var1 + var2"
            variables:
              var1: "sensor.test_1"
              var2: "sensor.test_2"
```

## Example Use Cases

### Solar Analytics (ID-Based Configuration)

```yaml
# All examples use unique IDs as primary identifiers
sensors:
  # Solar sold as positive value
  - unique_id: "solar_sold_watts" # REQUIRED: Unique ID
    name: "Solar Energy Sold" # OPTIONAL: Display name
    formulas:
      - id: "solar_sold" # REQUIRED: Formula ID
        name: "Solar Sold Power" # OPTIONAL: Display name
        formula: "abs(min(grid_power, 0))"
        variables:
          grid_power: "sensor.span_panel_current_power"
        unit_of_measurement: "W"
        device_class: "power"
        state_class: "measurement"

  # Solar self-consumption rate
  - unique_id: "solar_self_consumption_rate" # REQUIRED: Unique ID
    name: "Solar Self-Consumption Rate" # OPTIONAL: Display name
    formulas:
      - id: "consumption_rate" # REQUIRED: Formula ID
        name: "Self-Consumption %" # OPTIONAL: Display name
        formula: "if(solar_production > 0, (solar_production - solar_export) / solar_production * 100, 0)"
        variables:
          solar_production: "sensor.span_panel_solar_inverter_instant_power"
          solar_export: "sensor.syn2_solar_sold_watts_solar_sold" # References by entity ID
        unit_of_measurement: "%"
        state_class: "measurement"
```

### Hierarchical Calculations (Cross-References by ID)

```yaml
sensors:
  # Child sensors - base calculations
  - unique_id: "hvac_total_power" # REQUIRED: Unique ID
    name: "HVAC Total Power" # OPTIONAL: Display name
    formulas:
      - id: "hvac_total" # REQUIRED: Formula ID
        name: "Total HVAC Power" # OPTIONAL: Display name
        formula: "heating_power + cooling_power"
        variables:
          heating_power: "sensor.span_panel_circuit_5_power"
          cooling_power: "sensor.span_panel_circuit_6_power"
        unit_of_measurement: "W"
        device_class: "power"
        state_class: "measurement"

  - unique_id: "lighting_total_power" # REQUIRED: Unique ID
    name: "Lighting Total Power" # OPTIONAL: Display name
    formulas:
      - id: "lighting_total" # REQUIRED: Formula ID
        name: "Total Lighting Power" # OPTIONAL: Display name
        formula: "living_room + kitchen + bedroom"
        variables:
          living_room: "sensor.span_panel_circuit_10_power"
          kitchen: "sensor.span_panel_circuit_11_power"
          bedroom: "sensor.span_panel_circuit_12_power"
        unit_of_measurement: "W"
        device_class: "power"
        state_class: "measurement"

  # Parent sensor - references other synthetic sensors by entity ID
  - unique_id: "total_home_consumption" # REQUIRED: Unique ID
    name: "Total Home Consumption" # OPTIONAL: Display name
    formulas:
      - id: "home_total" # REQUIRED: Formula ID
        name: "Total Home Power" # OPTIONAL: Display name
        formula: "hvac + lighting + appliances"
        variables:
          hvac: "sensor.syn2_hvac_total_power_hvac_total" # Entity ID reference
          lighting: "sensor.syn2_lighting_total_power_lighting_total" # Entity ID reference
          appliances: "sensor.major_appliances_power"
        unit_of_measurement: "W"
        device_class: "power"
        state_class: "measurement"
```

### Cost Analysis (ID-Based References)

```yaml
sensors:
  # Real-time energy cost rate
  - unique_id: "current_energy_cost_rate" # REQUIRED: Unique ID
    name: "Current Energy Cost Rate" # OPTIONAL: Display name
    formulas:
      - id: "cost_rate" # REQUIRED: Formula ID
        name: "Energy Cost Rate" # OPTIONAL: Display name
        formula: "net_power * buy_rate / 1000 if net_power > 0 else abs(net_power) * sell_rate / 1000"
        variables:
          net_power: "sensor.span_panel_current_power"
          buy_rate: "input_number.electricity_buy_rate_cents_kwh"
          sell_rate: "input_number.electricity_sell_rate_cents_kwh"
        unit_of_measurement: "¢/h"
        device_class: "monetary"
        state_class: "measurement"
```

## Integration with Home Assistant Platforms

### Syn2 + Integration Platform Pattern

**Key Design Philosophy**: Syn2 creates instantaneous calculated sensors using unique IDs, which can then be used as sources for Home Assistant's integration platform.

**Example Workflow**:

```yaml
# Step 1: Syn2 creates real-time power calculations (ID-based)
sensors:
  - unique_id: "solar_sold_watts" # REQUIRED: Unique ID
    formulas:
      - id: "solar_sold" # REQUIRED: Formula ID
        formula: "abs(min(grid_power, 0))"
        variables:
          grid_power: "sensor.span_panel_current_power"
        unit_of_measurement: "W"
        device_class: "power"
        state_class: "measurement"

# Step 2: Integration platform references by entity ID
sensor:
  - platform: integration
    source: sensor.syn2_solar_sold_watts_solar_sold # References by entity ID
    name: Solar Sold kWh
    unique_id: solar_sold_kwh
    unit_prefix: k
    round: 2
```

## Test Fixtures Update

### Updated Test Configurations

```python
# Updated test fixtures using unique IDs
syn2_sample_config_id_based = {
    "version": "1.0",
    "global_settings": {
        "domain_prefix": "syn2"
    },
    "sensors": [
        {
            "unique_id": "comfort_index",                     # REQUIRED: Unique ID
            "name": "Comfort Index",                          # OPTIONAL: Display name
            "formulas": [
                {
                    "id": "comfort_formula",                   # REQUIRED: Formula ID
                    "name": "Comfort Level",                   # OPTIONAL: Display name
                    "formula": "temp + humidity",
                    "variables": {
                        "temp": "sensor.temperature",
                        "humidity": "sensor.humidity"
                    },
                    "unit_of_measurement": "index",
                    "state_class": "measurement"
                }
            ]
        },
        {
            "unique_id": "power_status",                      # REQUIRED: Unique ID
            "name": "Power Status",                           # OPTIONAL: Display name
            "formulas": [
                {
                    "id": "total_power",                       # REQUIRED: Formula ID
                    "name": "Total Power",                     # OPTIONAL: Display name
                    "formula": "hvac_power + lighting_power",
                    "variables": {
                        "hvac_power": "sensor.hvac",
                        "lighting_power": "sensor.lighting"
                    },
                    "unit_of_measurement": "W",
                    "device_class": "power"
                }
            ]
        }
    ]
}

# Service operation examples using IDs
service_test_examples = {
    "get_sensor_by_entity_id": {
        "service": "synthetic_sensors.get_sensor_info",
        "data": {"entity_id": "sensor.syn2_comfort_index_comfort_formula"}  # Uses HA entity_id
    },
    "update_sensor_by_entity_id": {
        "service": "synthetic_sensors.update_sensor",
        "data": {
            "entity_id": "sensor.syn2_power_status_total_power",  # Uses HA entity_id
            "formulas": [
                {
                    "id": "total_power",  # id for formula updates
                    "formula": "hvac_power * 1.1 + lighting_power"  # Updated formula
                }
            ]
        }
    }
}
```

## Mathematical Expression Engine

### Formula Validation Library

The implementation uses **simpleeval** for safe mathematical expression evaluation:

- **Security**: Safe evaluation without `eval()` risks
- **Performance**: Lightweight library optimized for mathematical expressions
- **Flexibility**: Supports custom functions and variables
- **Maintenance**: Actively maintained modern Python library

### Supported Operations

```python
# Basic arithmetic with ID-based variable references
{
    "unique_id": "solar_analytics",
    "formulas": [
        {
            "id": "solar_sold",
            "formula": "abs(solar_power)",                           # Absolute value
            "variables": {"solar_power": "sensor.solar_inverter_power"}
        },
        {
            "id": "circuit_total",
            "formula": "circuit_1_power + circuit_2_power",          # Addition
            "variables": {
                "circuit_1_power": "sensor.circuit_1",
                "circuit_2_power": "sensor.circuit_2"
            }
        },
        {
            "id": "hvac_efficiency",
            "formula": "max(circuit_1_power, circuit_2_power)",      # Functions
            "variables": {
                "circuit_1_power": "sensor.heating",
                "circuit_2_power": "sensor.cooling"
            }
        }
    ]
}
```

## Service Interface Details

### Service Schemas (ID-Based)

```python
# Service schemas for entity operations (HA standard)
UPDATE_SENSOR_SCHEMA = vol.Schema({
    vol.Required('entity_id'): cv.entity_id,  # Primary: HA entity_id (required)
    vol.Optional('formulas'): [FORMULA_SCHEMA],
    vol.Optional('name'): cv.string,  # Optional display name
})

GET_SENSOR_INFO_SCHEMA = vol.Schema({
    vol.Required('entity_id'): cv.entity_id,  # Primary: HA entity_id (required)
})

FORMULA_SCHEMA = vol.Schema({
    vol.Required('id'): cv.string,                           # REQUIRED: Formula ID
    vol.Optional('name'): cv.string,                         # OPTIONAL: Display name
    vol.Required('formula'): cv.string,
    vol.Required('variables'): dict,
    vol.Optional('unit_of_measurement'): cv.string,
    vol.Optional('device_class'): cv.string,
})
```

## Implementation Architecture

### Unique ID Management

```python
@dataclass(frozen=True)
class SensorConfig:
    """Sensor configuration with required unique ID."""
    unique_id: str                       # REQUIRED: Unique identifier
    name: Optional[str] = None           # OPTIONAL: Display name only
    formulas: list[FormulaConfig] = field(default_factory=list)
    enabled: bool = True

@dataclass(frozen=True)
class FormulaConfig:
    """Formula configuration with required ID."""
    id: str                              # REQUIRED: Formula identifier
    name: Optional[str] = None           # OPTIONAL: Display name only
    formula: str
    variables: dict[str, str]
    unit_of_measurement: Optional[str] = None
    device_class: Optional[str] = None

class SensorManager:
    """Manages sensors using entity IDs as primary keys once created in HA."""

    def get_sensor(self, entity_id: str) -> Optional[DynamicSensor]:
        """Get sensor by entity ID (only method needed after HA creation)."""
        return self._sensors_by_entity_id.get(entity_id)

    def create_sensor_entity_id(self, sensor_config: SensorConfig, formula_config: FormulaConfig) -> str:
        """Generate entity ID from unique IDs during creation phase."""
        return f"sensor.syn2_{sensor_config.unique_id}_{formula_config.id}"
```

### Key Design Principles

1. **Unique IDs Required**: All sensors and formulas must have stable, unique identifiers in YAML config
2. **Names Optional**: Used only for display purposes when creating sensor entities
3. **Entity ID Primary**: Once created in HA, sensors are primarily identified by entity_id
4. **Service Interface**: Services require `entity_id` for sensor operations
5. **Cross-References**: Sensors reference each other by entity ID in variables
6. **Entity Registry**: Sensors registered using generated entity IDs: `sensor.syn2_{unique_id}_{formula_id}`

This approach ensures stable, predictable behavior while maintaining Home Assistant best practices for entity identification and management.
