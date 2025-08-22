# YAML Generation Scripts

This directory contains scripts for generating YAML configurations for the SPAN Panel integration.

## Quick Start

From the project root, you can run:

```bash
# Generate complete YAML configuration (75 sensors)
./generate_yaml.sh

# Demo template generation (1 sensor)
./demo_templates.sh
```

## Scripts Overview

### `generate_complete_config.py`

Generates a complete YAML configuration using the integration's actual code path. This script:

- Uses the `SpanPanelSimulationFactory` to create realistic mock data
- Generates panel sensors (6 sensors)
- Generates circuit sensors (69 sensors for 27 circuits)
- Combines them into a complete YAML configuration
- Saves output to `/tmp/span_simulator_complete_config.yaml`

**Output:**

- Complete YAML: `/tmp/span_simulator_complete_config.yaml`
- Summary: `/tmp/span_simulator_config_summary.txt`

### `generate_yaml_from_templates.py`

Demonstrates how to use the YAML template system directly. This script:

- Shows how to use individual YAML templates
- Demonstrates placeholder substitution
- Generates a single circuit energy sensor
- Saves output to `/tmp/span_template_demo.yaml`

**Output:**

- Template demo: `/tmp/span_template_demo.yaml`

### `generate_from_test.py`

**Note: This script is currently broken** - it references a test file that doesn't exist.

## Shell Scripts

### `generate_yaml.sh`

Simple wrapper script that runs `generate_complete_config.py` from the project root.

### `demo_templates.sh`

Simple wrapper script that runs `generate_yaml_from_templates.py` from the project root.

## YAML Templates

The YAML templates are located in `custom_components/span_panel/yaml_templates/`:

- `sensor_set_header.yaml.txt` - Global settings and header
- `circuit_energy_consumed.yaml.txt` - Circuit energy consumption sensor
- `circuit_energy_produced.yaml.txt` - Circuit energy production sensor
- `circuit_power.yaml.txt` - Circuit power sensor
- `panel_energy_consumed.yaml.txt` - Panel energy consumption sensor
- `panel_energy_produced.yaml.txt` - Panel energy production sensor
- `panel_sensor.yaml.txt` - Generic panel sensor
- `solar_consumed_energy.yaml.txt` - Solar energy consumption sensor
- `solar_produced_energy.yaml.txt` - Solar energy production sensor
- `solar_current_power.yaml.txt` - Solar current power sensor

## Usage Examples

### Generate Complete Configuration

```bash
cd /path/to/span
./generate_yaml.sh
```

### Demo Template System

```bash
cd /path/to/span
./demo_templates.sh
```

### Run Python Scripts Directly

```bash
cd /path/to/span
python scripts/generate_complete_config.py
python scripts/generate_yaml_from_templates.py
```

## Generated YAML Structure

The generated YAML follows this structure:

```yaml
version: "1.0"

global_settings:
  device_identifier: "span-sim-001"
  variables:
    energy_grace_period_minutes: 15
  metadata:
    attribution: "Data from SPAN Panel"

sensors:
  # Panel sensors
  "span_span-sim-001_current_power":
    name: "Current Power"
    entity_id: "sensor.span-sim-001_current_power"
    # ... sensor configuration

  # Circuit sensors
  "span_span-sim-001_kitchen_lights_power":
    name: "Kitchen Lights Power"
    entity_id: "sensor.span-sim-001_kitchen_lights_power"
    # ... sensor configuration
```

## Troubleshooting

### Import Errors

Make sure you're running from the project root and have the virtual environment activated.

### Permission Errors

Make sure the shell scripts are executable:

```bash
chmod +x generate_yaml.sh demo_templates.sh
```

### Template Not Found

The scripts expect the YAML templates to be in `custom_components/span_panel/yaml_templates/`. Make sure this directory exists and contains the template files.
