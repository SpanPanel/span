# SPAN Panel Simulation Testing Documentation

## Overview

The SPAN Panel integration includes a simulation testing mode that allows tests to run against real simulation data instead of mocked responses. This provides
more accurate testing by using the actual SpanPanelApi library with a built-in simulator.

## Solution: CI-Friendly Simulation Tests

### Problem Solved

The main challenge was that simulation tests require `SPAN_USE_REAL_SIMULATION=1` to be set **before** the test module imports, but this made it difficult to
integrate into CI where you want regular `pytest` to work without environment variables.

### Solution: Module-Level Skip

Simulation tests now automatically skip when the environment variable isn't set:

```python
import os
import pytest

# Skip this test if SPAN_USE_REAL_SIMULATION is not set externally
if not os.environ.get('SPAN_USE_REAL_SIMULATION', '').lower() in ('1', 'true', 'yes'):
    pytest.skip("Simulation tests require SPAN_USE_REAL_SIMULATION=1", allow_module_level=True)

os.environ['SPAN_USE_REAL_SIMULATION'] = '1'
```

## How It Works

### Regular Test Runs (Default)

```bash
# Regular pytest - simulation tests are automatically skipped
pytest tests/
pytest tests/test_solar_configuration_with_simulator.py  # Shows "SKIPPED"
```

### Simulation Test Runs

```bash
# Run simulation tests with environment variable
SPAN_USE_REAL_SIMULATION=1 pytest tests/test_solar_configuration_with_simulator.py -v

# Clean output (reduced YAML noise)
SPAN_USE_REAL_SIMULATION=1 python -m pytest tests/test_solar_configuration_with_simulator.py::test_solar_configuration_with_simulator_friendly_names -v
```

### Mock vs Simulation Architecture

#### Regular Tests (Default)

- `tests/conftest.py` checks for `SPAN_USE_REAL_SIMULATION` environment variable
- If not set, installs mock modules for `span_panel_api` and `span_panel_api.exceptions`
- All API calls are mocked with predefined responses
- Fast execution but limited real-world accuracy

#### Simulation Tests (With Environment Variable)

- Environment variable set before module imports
- `tests/conftest.py` sees the variable and skips mock installation
- Real `span_panel_api` library is imported and used
- Integration connects to built-in simulator with realistic data

## Reduced Logging Output

The simulation test includes automatic logging configuration to reduce YAML and other verbose output:

```python
# Configure logging to reduce noise BEFORE other imports
import logging
logging.getLogger("homeassistant.core").setLevel(logging.WARNING)
logging.getLogger("homeassistant.loader").setLevel(logging.WARNING)
logging.getLogger("homeassistant.setup").setLevel(logging.WARNING)
logging.getLogger("homeassistant.components").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yaml").setLevel(logging.WARNING)
logging.getLogger("homeassistant.helpers").setLevel(logging.WARNING)
logging.getLogger("homeassistant.config_entries").setLevel(logging.WARNING)

# Keep our own logs visible for debugging
logging.getLogger("custom_components.span_panel").setLevel(logging.INFO)
logging.getLogger("ha_synthetic_sensors").setLevel(logging.INFO)
```

## CI Integration

### GitHub Actions Example

```yaml
# Regular tests - simulation tests are automatically skipped
- name: Run tests with coverage
  run: poetry run pytest tests/ --cov=custom_components/span_panel --cov-report=xml

# Optional: Run simulation tests separately
- name: Run simulation tests
  env:
    SPAN_USE_REAL_SIMULATION: 1
  run: poetry run pytest tests/test_solar_configuration_with_simulator.py -v
```

### Advantages for CI/CD

1. **No Configuration Required**: Regular `pytest` just works
2. **Automatic Skipping**: Simulation tests skip gracefully without environment setup
3. **Optional Simulation**: Can run simulation tests in separate CI job if desired
4. **Clean Output**: Reduced logging noise for better CI readability
5. **Flexible**: Can run all tests together or separately

## Solar Configuration Testing

### Test Flow for Solar Sensors

The solar configuration test follows this specific sequence:

1. **Initial Setup**: Integration loads with native sensors only
2. **Options Change**: Trigger solar configuration to create solar sensors
3. **Reload**: Integration reloads to activate the new solar sensors
4. **Verification**: Solar sensors are verified in the entity registry

### Why This Sequence is Necessary

Solar sensors are created via the options flow (when users change settings in the UI), not during initial integration setup. The test simulates this by:

1. Setting up the integration normally
2. Manually calling `handle_solar_options_change()`
3. Reloading the integration to activate the new sensors

## Available Simulation Data

The built-in simulator provides:

- **Circuits**: Realistic circuit data including tabs 30 and 32 used for solar testing
- **Power Data**: Simulated power consumption and generation values
- **Device Info**: Realistic device metadata and status information
- **Unmapped Tabs**: Circuits not assigned to specific loads (essential for solar)

## Running Simulation Tests

### Local Development

```bash
# Check test status without running
pytest tests/test_solar_configuration_with_simulator.py -v
# Output: SKIPPED [1] Simulation tests require SPAN_USE_REAL_SIMULATION=1

# Run simulation tests
SPAN_USE_REAL_SIMULATION=1 pytest tests/test_solar_configuration_with_simulator.py -v

# Run specific test with clean output
SPAN_USE_REAL_SIMULATION=1 python -m pytest tests/test_solar_configuration_with_simulator.py::test_solar_configuration_with_simulator_friendly_names -v
```

### CI/CD Pipeline

```bash
# Regular tests (simulation automatically skipped)
pytest tests/ --cov=custom_components/span_panel

# Simulation tests (separate job)
SPAN_USE_REAL_SIMULATION=1 pytest tests/test_solar_configuration_with_simulator.py -v
```

## Alternative: Simulation Directory Approach

We also created an alternative approach with a dedicated `tests/simulation/` directory that has its own `conftest.py`. This approach works but has some fixture
complexity. The module-level skip approach is simpler and more reliable.

## Advantages of Current Solution

1. **CI-Friendly**: Works without external environment variables
2. **Automatic**: Simulation tests skip gracefully when environment not set
3. **Clean Output**: Logging configuration reduces YAML noise
4. **Flexible**: Can run regular and simulation tests separately or together
5. **Simple**: Single test file approach is easier to maintain

## Future Improvements

The simulation testing mechanism provides a foundation for:

- Testing edge cases with realistic data
- Validating complex solar configurations
- Testing error recovery scenarios
- Performance testing with large circuit counts

This documentation should be updated as the simulation capabilities expand.
