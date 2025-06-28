"""Configure test framework."""

import logging
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# This import is required for patching even though it's not directly referenced
import custom_components.span_panel  # noqa: F401 # pylint: disable=unused-import


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations defined in the test dir."""
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
    from custom_components.span_panel.span_sensor_manager import SpanSensorManager

    SpanSensorManager._static_registered_entities = None
    SpanSensorManager._static_entities_generated = False
    SpanSensorManager.static_entities_registered = False

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

    # Also force clearing the SyntheticConfigManager singleton cache
    try:
        from custom_components.span_panel.synthetic_config_manager import SyntheticConfigManager

        SyntheticConfigManager._instances = {}
        print("DEBUG: Cleared SyntheticConfigManager singleton cache")
    except Exception as e:
        print(f"DEBUG: Could not clear SyntheticConfigManager cache: {e}")

    # Reset ha-synthetic-sensors package state if it exists
    try:
        import ha_synthetic_sensors

        # Clear any internal state that might persist
        if hasattr(ha_synthetic_sensors, "_global_sensor_managers"):
            ha_synthetic_sensors._global_sensor_managers.clear()
        if hasattr(ha_synthetic_sensors, "_registered_integrations"):
            ha_synthetic_sensors._registered_integrations.clear()
    except (ImportError, AttributeError):
        pass

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

    # Set debug level for all ha-synthetic-sensors loggers
    for logger_name in [
        "ha_synthetic_sensors",
        "ha_synthetic_sensors.sensor_manager",
        "ha_synthetic_sensors.config_manager",
        "ha_synthetic_sensors.name_resolver",
        "ha_synthetic_sensors.evaluator",
        "ha_synthetic_sensors.integration",
        "ha_synthetic_sensors.collection_resolver",
        "ha_synthetic_sensors.dependency_parser",
        "ha_synthetic_sensors.entity_factory",
        "ha_synthetic_sensors.service_layer",
        "ha_synthetic_sensors.variable_resolver",
    ]:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False  # Don't propagate to avoid double logging

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


@pytest.fixture(autouse=True)
def force_ha_synthetic_sensors_logging():
    import sys

    logger_names = [
        "ha_synthetic_sensors",
        "ha_synthetic_sensors.sensor_manager",
        "ha_synthetic_sensors.config_manager",
        "ha_synthetic_sensors.name_resolver",
        "ha_synthetic_sensors.evaluator",
        "ha_synthetic_sensors.integration",
        "ha_synthetic_sensors.collection_resolver",
        "ha_synthetic_sensors.dependency_parser",
        "ha_synthetic_sensors.entity_factory",
        "ha_synthetic_sensors.service_layer",
        "ha_synthetic_sensors.variable_resolver",
    ]
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
        # Remove all handlers to avoid duplicate logs
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    yield
