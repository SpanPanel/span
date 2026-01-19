# Copilot Instructions for SPAN Panel Integration

You are working on a Home Assistant custom integration for SPAN Panel, a smart electrical panel that provides circuit-level monitoring and control.

## Project Context

This is a **Home Assistant custom integration** (not a standalone application) that:

- Connects to SPAN Panel hardware via REST API
- Provides sensors, switches, and controls for Home Assistant
- Manages circuit-level power monitoring and energy tracking
- Handles authentication, data coordination, and state management

## Tech Stack

- **Python**: 3.13.2+ (strictly required)
- **Framework**: Home Assistant 2025.12.4
- **Package Manager**: Poetry (not pip)
- **Type Checking**: MyPy, Pyright
- **Linting/Formatting**: Ruff (primary), Pylint (import checks only)
- **Code Quality**: Bandit (security), Radon (complexity)
- **Testing**: pytest with pytest-homeassistant-custom-component
- **Pre-commit**: Enforced hooks for all commits

## Project Structure

```text
custom_components/span_panel/    # Main integration code
├── __init__.py                  # Integration setup
├── config_flow.py              # Configuration UI
├── coordinator.py              # Data update coordinator
├── sensor.py                   # Sensor platform
├── switch.py                   # Switch platform
├── select.py                   # Select platform
├── binary_sensor.py            # Binary sensor platform
├── services/                   # Custom services
├── sensors/                    # Sensor definitions
└── ...
tests/                          # Test files
scripts/                        # Development scripts
```

## Coding Standards and Conventions

### Code Style

1. **Imports**: Use absolute imports. No relative imports like `from .module import X`. Always import from `custom_components.span_panel` or use top-level imports
2. **Imports Location**: ALL imports MUST be at the top of the file (enforced by Pylint). No `import-outside-toplevel` violations allowed
3. **Type Hints**: Required for all functions and methods (enforced by MyPy with strict settings)
4. **Docstrings**: Required for all public modules, classes, and functions (Google style, enforced by Ruff D-rules)
5. **Line Length**: 100 characters maximum (enforced by Ruff)
6. **String Quotes**: Double quotes `"` preferred (enforced by Ruff formatter)
7. **Complexity**: Maximum cyclomatic complexity of 25 (enforced by Radon)

### Home Assistant Specific

1. **Entity IDs**: Support both friendly names and circuit numbers patterns (see `entity_id_naming_patterns.py`)
2. **Async**: All I/O operations must be async (Home Assistant requirement)
3. **Coordinator Pattern**: Use `DataUpdateCoordinator` for polling data
4. **Config Flow**: Use config flow for UI-based configuration (no YAML config)
5. **Services**: Define in `services.yaml` with proper schema validation
6. **Translations**: Add UI strings to `strings.json` and `translations/en.json`

### Python Patterns

1. **Exception Handling**: Use specific exceptions from `exceptions.py`
2. **Constants**: Define in `const.py`, use UPPER_CASE naming
3. **Data Classes**: Use `@dataclass` with type hints
4. **None Checks**: Use `if value is None:` not `if not value:`
5. **Dictionary Access**: Use `.get()` with defaults instead of try/except for missing keys

## Build, Test, and Lint Commands

### Setup (First Time)

```bash
# Install dependencies
poetry install --with dev

# Install pre-commit hooks
poetry run pre-commit install
```

### Development Workflow

```bash
# Run all pre-commit checks manually
poetry run pre-commit run --all-files

# Run tests with coverage
poetry run pytest tests/ --cov=custom_components/span_panel --cov-report=term-missing -v

# Run specific test file
poetry run pytest tests/test_config_flow.py -v

# Type checking
poetry run mypy custom_components/span_panel/

# Linting only
poetry run ruff check custom_components/span_panel/

# Format code
poetry run ruff format custom_components/span_panel/

# Security check
poetry run bandit -c pyproject.toml -r custom_components/span_panel/

# Check complexity
poetry run radon cc --min=B custom_components/span_panel/
```

### Before Committing

Pre-commit hooks will automatically run when you commit. If hooks modify files, review changes, re-stage, and commit again.

## Boundaries and Precautions

### NEVER Do These Things

1. **DO NOT** use `pip install` - always use `poetry add` or `poetry add --group dev`
2. **DO NOT** add imports inside functions (violates `import-outside-toplevel`)
3. **DO NOT** modify files in `.github/workflows/` without understanding CI implications
4. **DO NOT** change the Home Assistant version in `pyproject.toml` without testing
5. **DO NOT** add type: ignore comments without specific error codes (enforced by MyPy)
6. **DO NOT** commit secrets, tokens, or API keys
7. **DO NOT** modify the SPAN Panel API contract (it's external hardware)
8. **DO NOT** break backward compatibility with existing entity IDs without migration
9. **DO NOT** add print statements in production code (use logging via `_LOGGER`)
10. **DO NOT** modify translation files for other languages (only `en.json`)

### Be Careful With

1. **Entity Migrations**: Changes to entity IDs require migration logic (see `migration.py`)
2. **Energy Statistics**: SPAN panels can reset causing data issues (see spike cleanup service)
3. **Authentication**: Door proximity auth is time-limited (15 minutes)
4. **Async Operations**: All Home Assistant platform methods must be async
5. **Test Coverage**: Maintain or improve coverage percentage

### Files You Should NOT Modify

- `.github/workflows/*.yml` - CI/CD configuration (unless explicitly asked)
- `poetry.lock` - Managed by Poetry (use `poetry lock` to update)
- `custom_components/span_panel/manifest.json` - Version and metadata (coordinate with release process)
- `custom_components/span_panel/translations/*.json` - Non-English translations

## File Naming and Organization

### New Python Files

- Test files: `test_*.py` in `tests/` directory
- Service files: `*_service.py` or module in `services/` directory
- Sensor definitions: In `sensors/` directory
- Utilities: `*_utils.py` or in existing utility modules

### Naming Conventions

- Classes: `PascalCase` (e.g., `SpanPanelCoordinator`)
- Functions/Methods: `snake_case` (e.g., `async_setup_entry`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `DOMAIN`, `SCAN_INTERVAL`)
- Private members: Prefix with `_` (e.g., `_async_update_data`)
- Type variables: `PascalCase` with `T` suffix (e.g., `DataT`)

## Testing Guidelines

1. **Test Location**: All tests in `tests/` directory
2. **Fixtures**: Use pytest fixtures from `conftest.py`
3. **Factories**: Use test factories from `factories.py` for test data
4. **Mocking**: Use pytest-homeassistant-custom-component mocks
5. **Coverage**: Aim for >80% coverage for new code
6. **Test Types**: Unit tests, integration tests with Home Assistant test harness

### Test File Structure

```python
"""Test module for X functionality."""
import pytest
from homeassistant.core import HomeAssistant
# ... other imports

async def test_something(hass: HomeAssistant) -> None:
    """Test that something works correctly."""
    # Arrange
    # Act
    # Assert
```

## Common Patterns in This Codebase

### Logging

```python
import logging
_LOGGER = logging.getLogger(__name__)

_LOGGER.debug("Debug message")
_LOGGER.info("Info message")
_LOGGER.warning("Warning message")
_LOGGER.error("Error message")
```

### Data Coordinator

```python
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

class SpanPanelCoordinator(DataUpdateCoordinator[dict]):
    """Class to manage fetching SPAN Panel data."""

    async def _async_update_data(self) -> dict:
        """Fetch data from API."""
```

### Config Flow

```python
from homeassistant import config_entries

class SpanPanelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""
```

## When Making Changes

### For Bug Fixes

1. Write a failing test that reproduces the bug
2. Fix the bug with minimal changes
3. Verify the test passes
4. Run the full test suite
5. Run pre-commit hooks

### For New Features

1. Check if the feature fits Home Assistant patterns
2. Update `const.py` with any new constants
3. Add type hints and docstrings
4. Write comprehensive tests
5. Update `strings.json` for any new UI elements
6. Update README.md if it's a user-facing feature

### For Refactoring

1. Ensure tests exist and pass before refactoring
2. Make changes incrementally
3. Run tests after each change
4. Maintain or improve type coverage
5. Keep the same external API/behavior

## Useful References

- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [Home Assistant Integration Quality Scale](https://www.home-assistant.io/docs/quality_scale/)
- [SPAN Panel API](https://github.com/SpanPanel/span) - This repository
- Repository README.md - User-facing documentation
- `developer_attribute_readme.md` - Developer notes

## Questions to Ask Before Starting

1. Does this change affect entity IDs? (Need migration logic?)
2. Does this change affect the API integration? (Breaking change?)
3. Is this a user-facing change? (Update README and strings?)
4. Does this require new tests?
5. Does this change affect Home Assistant compatibility?

Remember: This is a custom integration that users install in their Home Assistant instance.
Breaking changes can affect real home automation systems, so test thoroughly and maintain
backward compatibility when possible.
