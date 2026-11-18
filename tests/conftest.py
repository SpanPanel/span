"""Configure test framework."""

from collections.abc import Callable, Iterator
import inspect
import logging
from pathlib import Path
import sys
import traceback
import types
from typing import Concatenate, Final
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from span_panel_api import V2HomieSchema, V2StatusInfo

from custom_components.span_panel.const import DOMAIN

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


UNLINKED_ISSUES_ALLOWED: Final = "unlinked_issues_allowed"
"""The marker of a test that raises this integration's issues unlinked on purpose.

Such a test stands in for an issue raised before issues were linked to their
entry, which is what the removal sweep, the schema reconcile and the clears of
the retired kinds exist to handle.
"""

_TESTS: Final = Path(__file__).resolve().parent
_PACKAGE: Final = _TESTS.parent / "custom_components" / "span_panel"


def _raised_at(stack: traceback.StackSummary) -> str:
    """Return where an issue was raised, innermost first.

    The two innermost frames in the integration, so a create that goes through
    `async_create_entry_issue` names its caller too, or failing those the
    innermost frame in a test.
    """
    here = Path(__file__).resolve()
    for root, depth in ((_PACKAGE, 2), (_TESTS, 1)):
        frames = [
            f"{Path(frame.filename).relative_to(_TESTS.parent)}:{frame.lineno}"
            for frame in reversed(stack)
            if Path(frame.filename).is_relative_to(root) and Path(frame.filename) != here
        ]
        if frames:
            return " from ".join(frames[:depth])
    return "an unknown caller"


def recording_unlinked_issues[**P](
    create: Callable[Concatenate[ir.IssueRegistry, P], ir.IssueEntry], unlinked: list[str]
) -> Callable[Concatenate[ir.IssueRegistry, P], ir.IssueEntry]:
    """Wrap the issue registry's create so it records each of this integration's issues raised unlinked.

    Each record is the issue's id and where it was raised. The wrapper passes
    every argument through unchanged, and reads the three it needs by binding
    them to `create`'s signature, so it holds however Core spells the call.
    """
    signature = inspect.signature(create)

    def linked_or_recorded(
        registry: ir.IssueRegistry, /, *args: P.args, **kwargs: P.kwargs
    ) -> ir.IssueEntry:
        arguments = signature.bind(registry, *args, **kwargs).arguments
        if arguments["domain"] == DOMAIN and arguments.get("config_entry_id") is None:
            unlinked.append(
                f"{arguments['issue_id']} (raised at {_raised_at(traceback.extract_stack())})"
            )
        return create(registry, *args, **kwargs)

    return linked_or_recorded


@pytest.fixture(autouse=True)
def every_issue_linked(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail a test in which one of this integration's issues is raised with no config entry.

    Every issue is linked to its entry, so Home Assistant deletes it with the
    entry, and an issue raised again without the link comes unlinked. The AST
    rule in `test_entry_issues` keeps the integration's source on
    `async_create_entry_issue`; this watches every path the suite actually runs,
    under whatever spelling reaches the issue registry, since Core's create
    functions and every other route end in `IssueRegistry.async_get_or_create`.

    Recorded rather than raised, so code that catches exceptions cannot hide
    it, and reported when the test finishes. A test that raises unlinked issues
    on purpose carries `@pytest.mark.unlinked_issues_allowed`.
    """
    if request.node.get_closest_marker(UNLINKED_ISSUES_ALLOWED) is not None:
        yield
        return
    unlinked: list[str] = []
    with patch.object(
        ir.IssueRegistry,
        "async_get_or_create",
        recording_unlinked_issues(ir.IssueRegistry.async_get_or_create, unlinked),
    ):
        yield
    if unlinked:
        pytest.fail(
            f"{DOMAIN} issues raised with no config entry: {'; '.join(unlinked)}. Raise them "
            "through `async_create_entry_issue`, or mark a test that raises unlinked issues on "
            f"purpose with `@pytest.mark.{UNLINKED_ISSUES_ALLOWED}`.",
            pytrace=False,
        )


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
    everything while it is active. Started before the `hass` fixture, it would
    reach that fixture's own registry load and break it. Hence the dependency
    on `hass`: the patch starts once Home Assistant is set up and ends before it
    is torn down.
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
