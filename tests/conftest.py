"""Configure test framework."""

import logging
import os
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# Synthetic sensors package removed - no longer needed
import pytest

from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory

# sys.path.insert(0, str(Path(__file__).parent.parent))  # Removed - using pytest pythonpath instead

# Import real model classes before the mock is installed so that snapshot-based
# tests can construct real SpanPanelSnapshot / SpanCircuitSnapshot instances.
from span_panel_api.models import PanelCapability as _PanelCapability  # noqa: E402
from span_panel_api.models import PanelGeneration as _PanelGeneration  # noqa: E402
from span_panel_api.models import SpanCircuitSnapshot as _SpanCircuitSnapshot  # noqa: E402
from span_panel_api.models import SpanPanelSnapshot as _SpanPanelSnapshot  # noqa: E402

# Mock span_panel_api before importing custom_components
# Create mock modules for span_panel_api
span_panel_api_mock = MagicMock()
span_panel_api_exceptions_mock = MagicMock()

# Register real model classes on the mock so imports like
#   from span_panel_api import SpanPanelSnapshot
# return the actual dataclass, not a MagicMock.
span_panel_api_mock.PanelCapability = _PanelCapability
span_panel_api_mock.PanelGeneration = _PanelGeneration
span_panel_api_mock.SpanCircuitSnapshot = _SpanCircuitSnapshot
span_panel_api_mock.SpanPanelSnapshot = _SpanPanelSnapshot

# Create proper mock exception classes that maintain distinct types
class MockSpanPanelError(Exception):
    """Base mock exception."""

    pass


class MockSpanPanelAuthError(MockSpanPanelError):
    """Mock auth error."""

    pass


class MockSpanPanelConnectionError(MockSpanPanelError):
    """Mock connection error."""

    pass


class MockSpanPanelTimeoutError(MockSpanPanelError):
    """Mock timeout error."""

    pass


class MockSpanPanelRetriableError(MockSpanPanelError):
    """Mock retriable error."""

    pass


class MockSpanPanelServerError(MockSpanPanelError):
    """Mock server error."""

    pass


class MockSpanPanelAPIError(MockSpanPanelError):
    """Mock API error."""

    pass


# Add exception classes that are used
span_panel_api_exceptions_mock.SpanPanelAuthError = MockSpanPanelAuthError
span_panel_api_exceptions_mock.SpanPanelConnectionError = MockSpanPanelConnectionError
span_panel_api_exceptions_mock.SpanPanelTimeoutError = MockSpanPanelTimeoutError
span_panel_api_exceptions_mock.SpanPanelRetriableError = MockSpanPanelRetriableError
span_panel_api_exceptions_mock.SpanPanelServerError = MockSpanPanelServerError
span_panel_api_exceptions_mock.SpanPanelAPIError = MockSpanPanelAPIError

# Only mock span_panel_api if not using real simulation mode
if os.environ.get('SPAN_USE_REAL_SIMULATION', '').lower() not in ('1', 'true', 'yes'):
    sys.modules["span_panel_api"] = span_panel_api_mock
    sys.modules["span_panel_api.exceptions"] = span_panel_api_exceptions_mock

# This import is required for patching even though it's not directly referenced
# import custom_components.span_panel  # noqa: F401 # pylint: disable=unused-import  # Moved to fixture


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations defined in the test dir."""
    yield


@pytest.fixture(autouse=True)
def ensure_custom_components_imported():
    """Ensure custom_components module is imported before tests run."""
    import custom_components.span_panel  # noqa: F401 # pylint: disable=unused-import
    yield


@pytest.fixture(autouse=True)
def patch_dispatcher_send_for_teardown():
    """Patch dispatcher send for teardown."""
    yield
    patch("homeassistant.helpers.dispatcher.dispatcher_send", lambda *a, **kw: None).start()  # type: ignore


@pytest.fixture(autouse=True)
def reset_static_state():
    """Reset static state before each test to prevent pollution."""
    # Reset before test runs
    # LEGACY TEST IMPORT - these modules have been removed and replaced by template-based system
    # SpanSensorManager has been removed - no static state to reset for template-based system

    # Also clean up YAML files that might persist between tests
    import os
    from pathlib import Path

    # Clean up YAML files in both possible locations
    yaml_locations = [
        # Current working directory
        Path.cwd() / "custom_components" / "span_panel",
        # pytest testing directory (absolute path)
        Path(os.getcwd())
        / ".venv/lib/python3.13/site-packages/pytest_homeassistant_custom_component/testing_config/custom_components/span_panel",
    ]

    yaml_filenames = ["span_sensors.yaml", "solar_synthetic_sensors.yaml"]

    for location in yaml_locations:
        for filename in yaml_filenames:
            yaml_file = location / filename
            if yaml_file.exists():
                print(f"DEBUG: Cleaning up YAML file: {yaml_file}")
                yaml_file.unlink()
                print(f"DEBUG: Successfully removed: {yaml_file}")

    # LEGACY: SyntheticConfigManager has been removed - no singleton cache to clear for template-based system

    # Synthetic sensors package removed - no longer needed
    # Reset ha-synthetic-sensors package state if it exists
    # try:
    #     import ha_synthetic_sensors
    #
    #     # Clear any internal state that might persist
    #     if hasattr(ha_synthetic_sensors, "_global_sensor_managers"):
    #         ha_synthetic_sensors._global_sensor_managers.clear()
    #     if hasattr(ha_synthetic_sensors, "_registered_integrations"):
    #         ha_synthetic_sensors._registered_integrations.clear()
    # except (ImportError, AttributeError):
    #     pass

    yield


@pytest.fixture(autouse=True)
def configure_ha_synthetic_logging():
    """Configure logging for ha-synthetic-sensors package."""
    import sys

    # Be aggressive: remove all existing handlers and set up our own
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add a new handler to stream to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root_logger.addHandler(handler)

    # Synthetic sensors package removed - no longer needed
    # Set debug level for all ha-synthetic-sensors loggers
    # for logger_name in [
    #     "ha_synthetic_sensors",
    #     "ha_synthetic_sensors.sensor_manager",
    #     "ha_synthetic_sensors.config_manager",
    #     "ha_synthetic_sensors.name_resolver",
    #     "ha_synthetic_sensors.evaluator",
    #     "ha_synthetic_sensors.integration",
    #     "ha_synthetic_sensors.collection_resolver",
    #     "ha_synthetic_sensors.dependency_parser",
    #     "ha_synthetic_sensors.entity_factory",
    #     "ha_synthetic_sensors.service_layer",
    #     "ha_synthetic_sensors.variable_resolver",
    # ]:
    #     logger = logging.getLogger(logger_name)
    #     logger.setLevel(logging.DEBUG)
    #     logger.propagate = False  # Don't propagate to avoid double logging

    yield


@pytest.fixture(autouse=True, scope="session")
def patch_frontend_and_panel_custom():
    """Patch frontend and panel_custom."""
    hass_frontend = types.ModuleType("hass_frontend")
    setattr(hass_frontend, "where", lambda: Path("/tmp"))  # type: ignore[attr-defined]
    sys.modules["hass_frontend"] = hass_frontend
    with (
        patch("homeassistant.components.frontend", MagicMock()),
        patch("homeassistant.components.panel_custom", MagicMock(), create=True),
    ):
        yield


# Synthetic sensors package removed - no longer needed
# @pytest.fixture(autouse=True)
# def force_ha_synthetic_sensors_logging():
#     import sys
#
#     logger_names = [
#         "ha_synthetic_sensors",
#         "ha_synthetic_sensors.sensor_manager",
#         "ha_synthetic_sensors.config_manager",
#         "ha_synthetic_sensors.name_resolver",
#         "ha_synthetic_sensors.evaluator",
#         "ha_synthetic_sensors.integration",
#         "ha_synthetic_sensors.collection_resolver",
#         "ha_synthetic_sensors.dependency_parser",
#         "ha_synthetic_sensors.entity_factory",
#         "ha_synthetic_sensors.service_layer",
#         "ha_synthetic_sensors.variable_resolver",
#     ]
#     for logger_name in logger_names:
#         logger = logging.getLogger(logger_name)
#         logger.setLevel(logging.DEBUG)
#         logger.propagate = True
#         # Remove all handlers to avoid duplicate logs
#         for handler in logger.handlers[:]:
#             logger.removeHandler(handler)
#         handler = logging.StreamHandler(sys.stdout)
#         handler.setLevel(logging.DEBUG)
#         handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
#         logger.addHandler(handler)
#     yield


# Synthetic sensors package removed - no longer needed
# @pytest.fixture
# def mock_ha_storage():
#     """Mock Home Assistant storage system for use with ha-synthetic-sensors package.
#
#     This creates a properly mocked storage system that the ha-synthetic-sensors
#     package can use for its StorageManager operations. The storage persists
#     across operations within a single test but is reset between tests.
#     """
#     storage_data = {}

#     class MockStore:
#         def __init__(self, hass, version: int, key: str, *, encoder=None, decoder=None):
#             self.hass = hass
#             self.version = version
#             self.key = key
#             self.encoder = encoder
#             self.decoder = decoder
#             self._data = storage_data.get(key, {})
#
#         async def async_load(self):
#             """Load data from mock storage."""
#             return self._data.copy() if self._data else None
#
#         async def async_save(self, data):
#             """Save data to mock storage."""
#             storage_data[self.key] = data.copy() if data else {}
#             self._data = data.copy() if data else {}
#
#         async def async_remove(self):
#             """Remove data from mock storage."""
#             if self.key in storage_data:
#                 del storage_data[self.key]
#             self._data = {}
#
#     with patch("homeassistant.helpers.storage.Store", MockStore):
#         yield storage_data


# Synthetic sensors package removed - no longer needed
# @pytest.fixture
# async def synthetic_storage_manager(hass):
#     """Create a synthetic sensors storage manager for testing."""
#     storage_manager = StorageManager(hass, "test_synthetic_sensors")
#     await storage_manager.async_load()
#     return storage_manager


# Synthetic sensors package removed - no longer needed
# @pytest.fixture
# async def mock_synthetic_sensor_manager(hass, synthetic_storage_manager):
#     """Create a synthetic sensor manager with mocked storage for testing."""
#     # Mock async_add_entities since we're not actually adding entities in tests
#     mock_add_entities = AsyncMock()
#
#     # Create a basic mock config entry for testing
#     from homeassistant.config_entries import ConfigEntry
#     from unittest.mock import Mock
#
#     mock_config_entry = Mock(spec=ConfigEntry)
#     mock_config_entry.entry_id = "test_span_panel"
#     mock_config_entry.domain = "span_panel"
#     mock_config_entry.title = "Test SPAN Panel"
#     mock_config_entry.data = {"host": "192.168.1.100"}
#     mock_config_entry.options = {}
#
#     # Create a sensor set first with some basic sensors
#     sensor_set_id = f"{mock_config_entry.entry_id}_sensors"
#     device_identifier = "test_device_123"
#
#     # Create sensor set if it doesn't exist
#     if not synthetic_storage_manager.sensor_set_exists(sensor_set_id):
#         await synthetic_storage_manager.async_create_sensor_set(
#             sensor_set_id=sensor_set_id,
#             device_identifier=device_identifier,
#             name="Test SPAN Panel Sensors"
#         )
#
#     # Get the sensor set and add some basic test sensors
#     sensor_set = synthetic_storage_manager.get_sensor_set(sensor_set_id)
#
#     # Create a minimal test sensor configuration
#     test_sensor_yaml = f"""
# version: "1.0"
# global_settings:
#   device_identifier: "{device_identifier}"
# sensors:
#   test_power_sensor:
#     name: "Test Power Sensor"
#     entity_id: "sensor.test_power"
#     formula: "state"
#     metadata:
#       unit_of_measurement: "W"
#       device_class: "power"
# """
#
#     # Import the test configuration
#     await sensor_set.async_import_yaml(test_sensor_yaml)

#     # Create data provider that returns test values
#     def data_provider_callback(entity_id: str):
#         """Test data provider that returns mock values."""
#         # Return mock values for common test entity IDs
#         if "power" in entity_id:
#             return {"value": 1500.0, "exists": True}
#         elif "energy" in entity_id:
#             return {"value": 10000.0, "exists": True}
#         elif "voltage" in entity_id:
#             return {"value": 240.0, "exists": True}
#         else:
#             return {"value": None, "exists": False}
#
#     # Set up synthetic sensors with the convenience function
#     sensor_manager = await async_setup_synthetic_sensors(
#         hass=hass,
#         config_entry=mock_config_entry,
#         async_add_entities=mock_add_entities,
#         storage_manager=synthetic_storage_manager,
#         sensor_set_id=sensor_set_id,  # Use the created sensor set
#         data_provider_callback=data_provider_callback,
#     )
#
#     return sensor_manager


@pytest.fixture
async def baseline_serial_number():
    """Fixture to provide the serial number from the baseline YAML (friendly_names.yaml)."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    baseline_path = os.path.join(fixtures_dir, "friendly_names.yaml")
    return await SpanPanelSimulationFactory.extract_serial_number_from_yaml(baseline_path)


@pytest.fixture
def async_add_entities():
    """Mock async_add_entities callback for testing."""
    return AsyncMock()
