"""Configure test framework."""

from collections.abc import Iterator
import logging
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from span_panel_api import V2HomieSchema, V2StatusInfo

# The real span_panel_api library is used directly (no sys.modules mocking).
# Individual tests mock SpanMqttClient as needed.
#
# `custom_components.span_panel` imports as this checkout's package with no help:
# `pythonpath = .` in pytest.ini puts the checkout first on `sys.path`, and the
# harness's configuration directory (`tests/testing_config`, absent here) holds
# no `custom_components` of its own to shadow it. The `hass` fixture enables
# custom integrations by itself.

UPDATE_CAPTURE_FIXTURES = "--update-capture-fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the one switch that rewrites fixtures instead of comparing against them."""
    parser.addoption(
        UPDATE_CAPTURE_FIXTURES,
        action="store_true",
        default=False,
        help=(
            "Rewrite tests/fixtures/expected_entities and tests/fixtures/topology from the "
            "captures' replay rather than comparing against them. Select one stem with "
            '-k "<stem>]".'
        ),
    )


@pytest.fixture
def update_capture_fixtures(request: pytest.FixtureRequest) -> bool:
    """Whether this run rewrites the capture-derived fixtures it would otherwise compare."""
    return bool(request.config.getoption(UPDATE_CAPTURE_FIXTURES))


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


@pytest.fixture
def sensor_registry_lookup(hass: HomeAssistant) -> Iterator[MagicMock]:
    """Answer the entity-registry lookup of sensors built by hand, as for an entity not yet registered.

    For modules that construct sensors directly over a coordinator mock whose
    `hass` is itself a mock, which the real registry cannot read; they request
    it with `pytestmark`. The patch reaches further than `sensor_base`: the `er`
    it names is Home Assistant's own module, so `er.async_get` is replaced for
    everything, including the `hass` fixture's own registry load. Hence the
    dependency on `hass`: the patch starts once Home Assistant is set up and
    ends before it is torn down.
    """
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch(
        "custom_components.span_panel.sensor_base.er.async_get",
        return_value=registry,
    ):
        yield registry


VALIDATED_PANEL_STATUS = V2StatusInfo(
    serial_number="sp3-status-001",
    firmware_version="spanos3/r202639/03",
    hardware_version="2.0",
)
"""What the stand-in for setup's status read answers unless a test says otherwise."""


@pytest.fixture(autouse=True)
def panel_status() -> Iterator[AsyncMock]:
    """Answer the unauthenticated status read without touching the network.

    Autouse because every test that runs `async_setup_entry` or a credential
    rotation reaches the read, and the harness refuses the socket it would open.
    The default is a validated hardware version, so setup and rotation proceed
    exactly as they did before the read existed; a test about the hardware
    version sets `return_value` or `side_effect` on the mock this yields.
    """
    with patch(
        "custom_components.span_panel.hardware_guard.get_v2_status",
        new=AsyncMock(return_value=VALIDATED_PANEL_STATUS),
    ) as status:
        yield status


PARENT_CHILD_PANEL_SCHEMA = V2HomieSchema(
    firmware_version="spanos3/r202639/03",
    types_schema_hash="0000000000000000",
    types={},
    data_model_version="1.0",
)
"""What the stand-in for setup's schema read answers unless a test says otherwise."""

FLAT_PANEL_SCHEMA = V2HomieSchema(
    firmware_version="spanos2/r202603/05",
    types_schema_hash="0000000000000000",
    types={},
    data_model_version=None,
)
"""What a panel on spanos2 firmware answers: a schema that publishes no `data_model_version`."""


@pytest.fixture(autouse=True)
def panel_schema() -> Iterator[AsyncMock]:
    """Answer the unauthenticated schema read without touching the network.

    Autouse for the reason `panel_status` is: every test that runs
    `async_setup_entry` reaches the read, and the harness refuses the socket it
    would open. The default is a parent/child schema, so setup proceeds exactly
    as it did before the read existed; a test about flat firmware sets
    `return_value` or `side_effect` on the mock this yields.
    """
    with patch(
        "custom_components.span_panel.firmware_schema.get_homie_schema",
        new=AsyncMock(return_value=PARENT_CHILD_PANEL_SCHEMA),
    ) as schema:
        yield schema
