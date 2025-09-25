# Simulation-Based Testing Implementation Plan

## Overview

This document outlines the implementation plan for transitioning SPAN Panel integration tests from mock-based testing to simulation-based testing using the
span-panel-api simulation mode. The primary goal is to generate realistic YAML test fixtures using the integration's actual code paths and helper functions,
ensuring proper entity ID and unique ID generation.

## Background and Motivation

### Current Issues

- Hand-crafted YAML fixtures don't accurately represent real panel data
- Mock data generation creates unrealistic circuit configurations
- Entity naming migration tests fail due to fixture structure mismatches
- Test data doesn't reflect actual SPAN panel response formats

### Solution Approach

- Leverage span-panel-api simulation mode for realistic panel data
- Use integration's actual YAML generation code paths
- Ensure all entity IDs and unique IDs are created through integration helpers
- Generate fixtures programmatically rather than hand-crafting them

### Key Principle: Integration Code Fidelity

**CRITICAL**: All YAML fixtures must be generated using the integration's actual code paths, particularly:

- Entity ID generation through `entity_id_naming_patterns.py` helpers
- Unique ID creation through integration helper functions
- YAML structure generation through `synthetic_*.py` modules
- Never bypass or mock the integration's ID generation logic

## Phase 1: Simulation Infrastructure Setup

### Task 1.1: Create SPAN Panel Simulation Factory

**File**: `tests/factories/span_panel_simulation_factory.py`

**Purpose**: Create a factory that uses span-panel-api simulation mode to generate realistic panel data, ensuring it exactly matches what the integration
expects.

**Key Requirements**:

```python
from span_panel_api import SpanPanelClient

class SpanPanelSimulationFactory:
    """Factory for creating simulation-based SPAN panel data."""

    @classmethod
    async def create_simulation_client(cls, **kwargs) -> SpanPanelClient:
        """Create a simulation client with realistic data."""
        return SpanPanelClient(
            host="localhost",  # Ignored in simulation
            simulation_mode=True,
            **kwargs
        )

    @classmethod
    async def get_realistic_panel_data(cls, variations=None):
        """Get panel data using simulation mode."""
        async with cls.create_simulation_client() as client:
            # Get all data types the integration needs
            circuits = await client.get_circuits(variations=variations)
            panel_state = await client.get_panel_state()
            status = await client.get_status()
            storage = await client.get_storage_soe()

            return {
                'circuits': circuits,
                'panel_state': panel_state,
                'status': status,
                'storage': storage
            }
```

**Implementation Details**:

- Support all simulation variations from span-panel-api
- Provide preset scenarios (normal operation, high load, circuit failures)
- Cache simulation data within test runs for performance
- Include circuit IDs that exactly match simulation fixtures

### Task 1.2: Create Integration-Driven Data Provider

**File**: `tests/providers/integration_data_provider.py`

**Purpose**: Bridge between simulation data and integration code, ensuring all data flows through integration processing.

**Key Requirements**:

```python
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from tests.factories.span_panel_simulation_factory import SpanPanelSimulationFactory

class IntegrationDataProvider:
    """Provides data using integration's actual processing logic."""

    async def create_coordinator_with_simulation_data(
        self,
        hass,
        config_entry,
        simulation_variations=None
    ) -> SpanPanelCoordinator:
        """Create coordinator with simulation data processed through integration."""

        # Get simulation data
        sim_data = await SpanPanelSimulationFactory.get_realistic_panel_data(
            variations=simulation_variations
        )

        # Create coordinator using integration's actual initialization
        coordinator = SpanPanelCoordinator(hass, None, config_entry)

        # Set data as if it came from real API calls
        coordinator.data = self._convert_sim_data_to_coordinator_format(sim_data)

        return coordinator

    def _convert_sim_data_to_coordinator_format(self, sim_data):
        """Convert simulation data to coordinator's expected format."""
        # Use integration's actual data processing logic
        # This ensures data structure matches exactly what coordinator expects
        pass
```

### Task 1.3: Update Test Configuration Management

**File**: `tests/conftest.py` (updates)

**Purpose**: Integrate simulation infrastructure into test framework.

**Implementation**:

- Add simulation-based fixtures that replace mock factories
- Maintain backward compatibility with existing tests
- Provide easy access to different simulation scenarios

## Phase 2: YAML Fixture Generation Using Integration Code

### Task 2.1: Create Integration-Driven YAML Generator

**File**: `tests/utils/integration_yaml_generator.py`

**Purpose**: Generate YAML fixtures by running integration's actual synthetic sensor creation code with simulation data.

**Critical Requirements**:

```python
from custom_components.span_panel.synthetic_sensors import (
    async_setup_synthetic_sensors_with_entities
)
from custom_components.span_panel.synthetic_named_circuits import (
    generate_named_circuit_sensors
)
from custom_components.span_panel.synthetic_panel_circuits import (
    generate_panel_sensors
)
from custom_components.span_panel.synthetic_solar import (
    generate_solar_sensors
)

class IntegrationYAMLGenerator:
    """Generate YAML fixtures using integration's actual code paths."""

    async def generate_yaml_for_naming_pattern(
        self,
        hass,
        naming_flags: dict,
        simulation_variations=None
    ) -> str:
        """Generate YAML using integration's actual sensor creation logic."""

        # Create config entry with specific naming flags
        config_entry = self._create_config_entry_with_flags(naming_flags)

        # Get coordinator with simulation data
        coordinator = await self.data_provider.create_coordinator_with_simulation_data(
            hass, config_entry, simulation_variations
        )

        # Run integration's actual synthetic sensor creation
        # This is CRITICAL - we must use the integration's real code paths
        sensor_manager = await async_setup_synthetic_sensors_with_entities(
            hass, coordinator
        )

        # Export YAML using the synthetic sensors package
        yaml_content = await sensor_manager.export_yaml()

        return yaml_content
```

**Key Features**:

- Use integration's actual `generate_*_sensors()` functions
- Ensure all entity IDs come from `entity_id_naming_patterns.py` helpers
- Support all naming pattern combinations
- Generate solar and battery sensor configurations
- Preserve integration's exact YAML structure and formatting

### Task 2.2: Create Naming Pattern Fixture Generator

**File**: `tests/utils/naming_pattern_fixtures.py`

**Purpose**: Generate complete sets of fixtures for all entity naming patterns.

**Implementation**:

```python
class NamingPatternFixtureGenerator:
    """Generate fixtures for all entity naming patterns."""

    NAMING_PATTERNS = [
        {"use_device_prefix": False, "use_circuit_numbers": False},  # Legacy
        {"use_device_prefix": True, "use_circuit_numbers": False},   # Device + Friendly
        {"use_device_prefix": True, "use_circuit_numbers": True},    # Device + Circuit Numbers
        {"use_device_prefix": False, "use_circuit_numbers": True},   # Circuit Numbers Only
    ]

    async def generate_all_pattern_fixtures(self, hass):
        """Generate fixtures for all naming patterns."""
        fixtures = {}

        for pattern in self.NAMING_PATTERNS:
            pattern_name = self._get_pattern_name(pattern)

            # Generate using integration code
            yaml_content = await self.yaml_generator.generate_yaml_for_naming_pattern(
                hass, pattern
            )

            fixtures[pattern_name] = yaml_content

        return fixtures
```

### Task 2.3: Create Migration Fixture Generator

**File**: `tests/utils/migration_fixture_generator.py`

**Purpose**: Generate before/after migration fixtures using integration's actual migration logic.

**Critical Implementation**:

```python
from custom_components.span_panel.entity_id_naming_patterns import (
    migrate_entity_ids_legacy_to_device_prefix,
    migrate_entity_ids_friendly_to_circuit_numbers
)

class MigrationFixtureGenerator:
    """Generate migration test fixtures using integration's migration logic."""

    async def generate_migration_fixtures(
        self,
        hass,
        from_pattern: dict,
        to_pattern: dict,
        scenario_name: str
    ) -> tuple[str, str]:
        """Generate before/after migration fixtures."""

        # Generate "before" state using integration code
        before_yaml = await self.yaml_generator.generate_yaml_for_naming_pattern(
            hass, from_pattern
        )

        # Set up integration state with "before" configuration
        coordinator = await self._setup_coordinator_with_yaml(hass, before_yaml, from_pattern)

        # Run actual migration using integration's migration functions
        success = await coordinator.migrate_synthetic_entities(from_pattern, to_pattern)
        assert success, f"Migration should succeed for {scenario_name}"

        # Export "after" state
        sensor_manager = coordinator.sensor_manager
        after_yaml = await sensor_manager.export_yaml()

        return before_yaml, after_yaml
```

**Key Requirements**:

- Use integration's actual migration functions
- Ensure entity ID transformations use integration helpers
- Preserve data integrity through migration
- Generate realistic migration scenarios

## Phase 3: Enhanced Test Infrastructure

### Task 3.1: Create Simulation Test Base Classes

**File**: `tests/base/simulation_test_base.py`

**Purpose**: Provide base classes for simulation-powered tests.

```python
class SimulationTestBase:
    """Base class for tests using simulation data."""

    async def setup_simulation_coordinator(
        self,
        hass,
        naming_flags=None,
        simulation_variations=None
    ):
        """Set up coordinator with simulation data."""
        # Use integration's actual setup process
        pass

class MigrationTestBase(SimulationTestBase):
    """Base class for entity naming migration tests."""

    async def verify_migration_preserves_data(self, before_yaml, after_yaml):
        """Verify migration preserves sensor data while changing entity IDs."""
        # Parse both YAML configurations
        # Verify sensor count matches
        # Verify references are properly updated
        # Ensure no data loss
        pass
```

### Task 3.2: Update Common Test Helpers

**File**: `tests/common.py` (major updates)

**Purpose**: Replace mock-based helpers with simulation-based equivalents.

**Implementation**:

- Deprecate `create_mock_span_panel_with_data()` in favor of simulation
- Update all helper functions to use realistic simulation data
- Maintain compatibility during transition period

### Task 3.3: Create Integration Scenario Factory

**File**: `tests/factories/integration_scenario_factory.py`

**Purpose**: Create complete integration scenarios for testing.

**Features**:

- Different panel configurations (circuit counts, types)
- Various operational states (normal, high load, failures)
- Multiple naming pattern configurations
- Solar and battery integration scenarios

## Phase 4: Migration Test Enhancement

### Task 4.1: Rewrite Migration Tests

**File**: `tests/test_config_flow_entity_naming.py` (complete rewrite)

**Purpose**: Replace existing migration tests with simulation-based versions that use generated fixtures.

**Key Changes**:

```python
class TestEntityNamingMigrationWithSimulation:
    """Test entity naming migration using simulation-generated fixtures."""

    async def test_legacy_to_device_prefix_migration(self, hass):
        """Test migration using integration-generated fixtures."""

        # Generate fixtures using integration code
        before_yaml, after_yaml = await self.migration_generator.generate_migration_fixtures(
            hass,
            from_pattern={"use_device_prefix": False, "use_circuit_numbers": False},
            to_pattern={"use_device_prefix": True, "use_circuit_numbers": False},
            scenario_name="legacy_to_device_prefix"
        )

        # Run migration test using generated fixtures
        # All entity IDs will be created through integration helpers
        # Test will use realistic circuit data from simulation

        # Verify migration results
        self.verify_migration_results(before_yaml, after_yaml)
```

**Critical Requirements**:

- Use only integration-generated fixtures
- Test against realistic simulation data
- Verify all entity ID transformations
- Test all migration paths comprehensively

### Task 4.2: Create Migration Validation Framework

**File**: `tests/utils/migration_validator.py`

**Purpose**: Comprehensive validation of migration results.

**Features**:

- Entity ID transformation verification
- Data preservation checks
- Reference integrity validation
- Performance impact assessment

### Task 4.3: Create Migration Test Coverage Matrix

**File**: `tests/data/migration_test_matrix.py`

**Purpose**: Define comprehensive migration test scenarios.

**Coverage**:

- All naming pattern combinations (4x4 = 16 migration paths)
- Different panel configurations
- Edge cases and error conditions
- Custom user modifications

## Phase 5: Development Tools and Maintenance

### Task 5.1: Create Fixture Generation Scripts

**File**: `scripts/generate_test_fixtures.py`

**Purpose**: Command-line tool for regenerating all test fixtures.

**Features**:

```bash
# Generate all fixtures
poetry run python scripts/generate_test_fixtures.py --all

# Generate specific pattern fixtures
poetry run python scripts/generate_test_fixtures.py --pattern device_prefix_friendly

# Generate migration fixtures
poetry run python scripts/generate_test_fixtures.py --migration legacy_to_device_prefix

# Validate existing fixtures
poetry run python scripts/generate_test_fixtures.py --validate
```

### Task 5.2: Create Simulation Data Inspector

**File**: `scripts/inspect_simulation_data.py`

**Purpose**: Development tool for examining simulation data and comparing to fixtures.

**Features**:

- Display simulation panel structure
- Show all available circuit IDs and properties
- Compare simulation data to existing fixtures
- Export data for debugging

### Task 5.3: Create Integration Test Validator

**File**: `scripts/validate_integration_fixtures.py`

**Purpose**: Validate that all fixtures were generated using integration code.

**Features**:

- Verify entity ID patterns match integration helpers
- Check YAML structure consistency
- Validate migration fixture correctness
- Generate validation reports

## Implementation Guidelines

### Code Quality Requirements

1. **Integration Fidelity**: All fixtures must be generated using integration's actual code paths
2. **Helper Usage**: Entity IDs and unique IDs must come from integration helpers only
3. **Realistic Data**: Use simulation mode data that matches real SPAN panels
4. **Test Coverage**: Cover all naming patterns and migration scenarios
5. **Maintainability**: Generated fixtures should be reproducible and updateable

### Testing Best Practices

1. **Use Integration Code**: Never bypass integration logic for fixture generation
2. **Realistic Scenarios**: Base tests on actual SPAN panel configurations
3. **Comprehensive Coverage**: Test all naming pattern combinations
4. **Data Validation**: Verify migration preserves all sensor data
5. **Performance Awareness**: Ensure simulation doesn't slow down test suite

### Simulation Usage Patterns

Based on span-panel-api simulation documentation:

```python
# Use realistic circuit variations
circuit_variations = {
    "0dad2f16cd514812ae1807b0457d473e": {  # Lights Dining Room
        "power_variation": 0.05,  # Low variation for lights
        "relay_state": "CLOSED"
    },
    "8a2ffda9dbd24bada9a01b880e910612": {  # EV Charger
        "power_variation": 0.8,   # High variation for EV
        "relay_state": "CLOSED"
    }
}

# Test specific scenarios
high_load_scenario = {
    "global_power_variation": 0.3,
    "variations": ev_charging_variations
}

circuit_failure_scenario = {
    "variations": {
        "critical_circuit_id": {"relay_state": "OPEN"}
    }
}
```

## Success Criteria

### Phase 1 Success

- Simulation factory provides realistic SPAN panel data
- Integration data provider bridges simulation to coordinator
- Test infrastructure supports simulation mode

### Phase 2 Success

- YAML fixtures generated using integration's actual code
- All entity IDs created through integration helpers
- Migration fixtures generated using real migration logic

### Phase 3 Success

- Test base classes simplify simulation-based testing
- Common helpers use realistic data
- Integration scenarios cover all use cases

### Phase 4 Success

- Migration tests pass with generated fixtures
- All naming pattern migrations tested
- Migration validation comprehensive

### Phase 5 Success

- Fixture generation automated and reproducible
- Development tools support debugging and validation
- Test maintenance streamlined

## Timeline and Dependencies

### Immediate Priority (Fix Current Tests)

1. **Task 1.1** - Simulation Factory (1-2 days)
2. **Task 2.1** - Integration YAML Generator (2-3 days)
3. **Task 4.1** - Rewrite Migration Tests (1-2 days)

### Short Term (Complete Infrastructure)

1. **Task 1.2, 1.3** - Data Provider and Test Integration (1-2 days)
2. **Task 2.2, 2.3** - Pattern and Migration Generators (2-3 days)
3. **Task 3.1** - Test Base Classes (1 day)

### Medium Term (Complete Testing Framework)

1. **Task 3.2, 3.3** - Updated Helpers and Scenarios (2-3 days)
2. **Task 4.2, 4.3** - Migration Validation and Coverage (2-3 days)

### Long Term (Development Tools)

1. **Task 5.1, 5.2, 5.3** - Scripts and Tools (2-3 days)

**Total Estimated Time**: 2-3 weeks for complete implementation

## Conclusion

This implementation plan leverages the span-panel-api simulation mode to create a robust, realistic testing framework that ensures:

1. **Realistic Test Data**: All tests use data that matches actual SPAN panels
2. **Integration Fidelity**: All fixtures generated using integration's actual code paths
3. **Proper Entity ID Generation**: All entity IDs created through integration helpers
4. **Comprehensive Coverage**: All naming patterns and migrations tested
5. **Maintainable Tests**: Automated fixture generation and validation

The key insight is using the integration's own code to generate test fixtures, ensuring perfect alignment between test data and production behavior while
leveraging realistic simulation data from actual SPAN panel responses.

## ✅ Completed Tasks

### Phase 1: Foundation Infrastructure ✅

- **Task 1.1: SPAN Panel Simulation Factory** ✅
  - Location: `/tests/factories/span_panel_simulation_factory.py`
  - Features: Dynamic circuit ID retrieval, realistic SPAN panel data generation
  - Status: Complete with dynamic circuit categorization

- **Task 1.2: Integration Data Provider** ✅
  - Location: `/tests/providers/integration_data_provider.py`
  - Features: Bridge between simulation and integration coordinator
  - Status: Complete with full integration setup support

### Phase 2: YAML Generation Infrastructure ✅

- **Task 2.1: Integration-Driven YAML Generator** ✅
  - Location: `/tests/utils/integration_yaml_generator.py`
  - Features: Generate YAML using integration's actual sensor creation code
  - Status: Complete with comprehensive naming pattern support
  - Test Coverage: `/tests/test_integration_yaml_generator.py`

## 🔄 Next Steps

Continue with Task 2.2: Naming Pattern Fixture Generator to generate fixtures for all entity naming combinations and test variations.
