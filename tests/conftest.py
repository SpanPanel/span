"""Configure test framework."""

import logging
import os
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory

# The real span_panel_api library is used directly (no sys.modules mocking).
# Individual tests mock SpanMqttClient / DynamicSimulationEngine as needed.


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
    yield


@pytest.fixture(autouse=True)
def configure_ha_synthetic_logging():
    """Configure logging for tests."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root_logger.addHandler(handler)

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
