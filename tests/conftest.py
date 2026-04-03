"""Configure test framework."""

import logging
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The real span_panel_api library is used directly (no sys.modules mocking).
# Individual tests mock SpanMqttClient as needed.

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_CC_DIR = str(Path(_PROJECT_ROOT) / "custom_components")


def _ensure_span_panel_importable() -> None:
    """Ensure custom_components.span_panel can be imported.

    The HA test plugin initialises the ``custom_components`` namespace package
    from a temporary config directory.  When tests that don't use the ``hass``
    fixture run in isolation, our project's ``custom_components`` directory is
    never added to the namespace path.  This helper repairs that at call time.
    """
    import importlib

    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

    # If custom_components is already cached in sys.modules (pointing to the
    # HA temp config dir), extend its __path__ to include our directory.
    if "custom_components" in sys.modules:
        cc_mod = sys.modules["custom_components"]
        if hasattr(cc_mod, "__path__") and _CC_DIR not in list(cc_mod.__path__):
            cc_mod.__path__.append(_CC_DIR)  # type: ignore[union-attr]
    else:
        importlib.import_module("custom_components")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations defined in the test dir."""
    yield


@pytest.fixture(autouse=True)
def ensure_custom_components_imported():
    """Ensure custom_components module is imported before tests run."""
    _ensure_span_panel_importable()
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
def async_add_entities():
    """Mock async_add_entities callback for testing."""
    return AsyncMock()
