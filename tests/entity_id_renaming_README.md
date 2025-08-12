# Bulk Entity ID Naming Pattern Tests Summary

## Overview

This test suite validates bulk entity ID naming pattern changes with user customization preservation. It demonstrates how the integration handles system-wide
transformations while preserving all user modifications.

## Test Structure

### 1. YAML Fixture Validation (`TestYamlFixtureValidation`)

- **`test_all_yaml_fixtures_are_valid`**: Validates all YAML fixtures have proper structure and required fields
- **`test_fixture_naming_patterns_are_consistent`**: Ensures naming patterns follow expected conventions across fixtures
- **`test_fixture_user_customizations_are_preserved`**: Verifies user customizations are identical across before/after fixtures

### 2. Bulk Naming Pattern Tests (`TestBulkEntityIdNamingPatterns`)

- **`test_crud_then_bulk_naming_pattern_change_preserves_customizations`**: Main workflow test demonstrating CRUD operations followed by bulk naming changes
- **`test_naming_pattern_combinations`**: Tests all four naming pattern combinations (device prefix + circuit numbers)

### 3. Edge Case Tests (`TestBulkModificationEdgeCases`)

- **`test_empty_sensor_set_handling`**: Validates graceful handling of empty sensor sets
- **`test_unknown_sensor_types_preserved`**: Ensures unknown sensor types are preserved during bulk modifications

## YAML Fixtures

### `/tests/fixtures/circuit_numbers.yaml`

### `/tests/fixtures/device_prefix.yaml`

### `/tests/fixtures/device_prefix.yaml`

Demonstrates solar sensors with friendly naming pattern:

- `entity_id: "sensor.span_panel_solar_inverter_power"`
- `entity_id: "sensor.span_panel_solar_inverter_energy_produced"`
- Contains user customizations (custom attributes, modified names)

### `/tests/fixtures/solar_sensors_circuit_numbers.yaml`

Shows the same sensors with circuit number naming pattern:

- `entity_id: "sensor.span_panel_circuit_30_32_power"`
- `entity_id: "sensor.span_panel_circuit_30_32_energy_produced"`
- **All user customizations preserved** - only entity_ids change

## Key Testing Principles

1. **Real YAML Fixtures**: Tests use actual YAML files instead of embedded strings
2. **Fixture Validation**: Dedicated tests ensure all fixtures are valid and consistent
3. **User Customization Preservation**: Validates that bulk operations preserve all user modifications
4. **Pattern Consistency**: Tests verify naming patterns follow expected conventions
5. **Edge Case Coverage**: Handles empty sets and unknown sensor types

## Test Benefits

- **Comprehensive Validation**: All fixtures are validated together
- **Real-world Scenarios**: Tests actual YAML structures users would create
- **Pattern Verification**: Ensures naming patterns are consistent and predictable
- **Customization Safety**: Proves user modifications survive bulk changes
- **Maintainability**: Easy to add new fixtures and validate them automatically

This test suite provides confidence that bulk entity ID naming pattern changes work correctly while preserving all user customizations.
