"""Who may call each of the integration's services.

Every service that changes current monitoring -- its settings, a point's
thresholds, the ratings it judges against, the notifications it sends -- follows
Home Assistant's admin-service rule: a logged-in user who is not an
administrator is refused with Core's `Unauthorized` before anything changes,
and a call with no user, from an automation, goes through. Reading monitoring,
favorites and graph horizons stay open to every user, and `rotate_credentials`
keeps a stricter check of its own.

The services are classified here by name, and the integration's registered
services must match the classification exactly, so a new service cannot be
added without deciding which rule it follows.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    Context,
    Event,
    HomeAssistant,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry, MockUser, async_capture_events
from span_panel_api import FieldMetadata

from custom_components.span_panel.alert_dispatcher import EVENT_BUS_TARGET
from custom_components.span_panel.const import (
    DEFAULT_GRAPH_HORIZON,
    DOMAIN,
    EVENT_CURRENT_ALERT,
    VALID_GRAPH_HORIZONS,
)
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.graph_horizon import GraphHorizonManager
from custom_components.span_panel.id_builder import build_circuit_unique_id
from custom_components.span_panel.options import NOTIFY_TARGETS, SPIKE_THRESHOLD_PCT
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.runtime import SpanPanelRuntimeData

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device

ServiceData = Mapping[str, object]

_SERIAL: Final = "sp3-synthetic-001"
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}
_FAVORITE_ENTITY_ID: Final = "sensor.synthetic_circuit_energy_consumed"
"""A sensor of the loaded panel's circuit, which a favorite can name."""
_CHANGED_HORIZON: Final = "1h"
"""A graph horizon other than the default, so setting it changes what is stored."""
_UNKNOWN_USER_ID: Final = "no-such-user"
"""A user id Home Assistant has no user for."""

MONITORING_WRITES: Final[Mapping[str, ServiceData]] = {
    "set_global_monitoring": {},
    "set_circuit_threshold": {"circuit_id": "circuit-2"},
    "clear_circuit_threshold": {"circuit_id": "circuit-2"},
    "set_mains_threshold": {"leg": "upstream_l1"},
    "clear_mains_threshold": {"leg": "upstream_l1"},
    "set_circuit_package_ratings": {"circuit_id": "circuit-2", "package_ratings_a": [20, 30]},
    "clear_circuit_package_ratings": {"circuit_id": "circuit-2"},
    # It sends to every target monitoring's settings name, and starts monitoring
    # on a panel where it is off.
    "test_notification": {},
}
"""The services that change current monitoring: administrators and automations only.

Each with data its schema accepts, so a refusal comes from the rule rather than
from the schema, which Home Assistant applies first.
"""

READS: Final[Mapping[str, ServiceData]] = {
    "get_monitoring_status": {},
    "get_graph_settings": {},
    "get_favorites": {},
    "export_circuit_manifest": {},
}
"""The services that only read: open to every user."""

OPEN_WRITES: Final[Mapping[str, ServiceData]] = {
    # Left open by decision: a favorite and a graph horizon shape how the
    # dashboard shows a panel, not what monitoring watches or whom it notifies.
    "add_favorite": {"entity_id": _FAVORITE_ENTITY_ID},
    "remove_favorite": {"entity_id": _FAVORITE_ENTITY_ID},
    "set_graph_time_horizon": {"horizon": _CHANGED_HORIZON},
    "set_circuit_graph_horizon": {"circuit_id": "circuit-2", "horizon": _CHANGED_HORIZON},
    "clear_circuit_graph_horizon": {"circuit_id": "circuit-2"},
    "set_subdevice_graph_horizon": {
        "subdevice_id": "synthetic-subdevice",
        "horizon": _CHANGED_HORIZON,
    },
    "clear_subdevice_graph_horizon": {"subdevice_id": "synthetic-subdevice"},
}
"""The services that write and are open to every user.

Each with data that changes something on the loaded panel below, so the writes
can be shown to act for a non-admin.
"""

OPEN_TO_EVERY_USER: Final[Mapping[str, ServiceData]] = {**READS, **OPEN_WRITES}
"""The services every logged-in user may call."""

ADMINISTRATOR_IN_PERSON: Final[Mapping[str, ServiceData]] = {
    "rotate_credentials": {},
}
"""The services only a logged-in administrator may call, which refuse automations too."""


@pytest.fixture
async def integration(hass: HomeAssistant) -> None:
    """Set the integration up as Home Assistant does, so its services are exactly those `async_setup` registers.

    Only the sidebar panel's registration is stood in for, as in
    `test_panel_registration`: the harness replaces the frontend it registers
    through for the whole session.
    """
    with (
        patch("custom_components.span_panel.async_register_panel", new_callable=AsyncMock),
        patch("custom_components.span_panel.async_remove_panel"),
    ):
        assert await async_setup_component(hass, DOMAIN, {})


async def _non_admin(hass: HomeAssistant, group_id: str) -> MockUser:
    """Add a logged-in user in `group_id`, one of the groups a user who is not an administrator is in."""
    group = await hass.auth.async_get_group(group_id)
    assert group is not None
    user = MockUser(groups=[group]).add_to_hass(hass)
    assert not user.is_admin
    return user


@pytest.fixture(params=[GROUP_ID_USER, GROUP_ID_READ_ONLY], ids=["ordinary user", "read-only user"])
async def non_admin_user(hass: HomeAssistant, request: pytest.FixtureRequest) -> MockUser:
    """Return a logged-in user who is not an administrator, from each group such a user can be in."""
    return await _non_admin(hass, request.param)


@dataclass(frozen=True)
class _RefusedCaller:
    """A caller a monitoring write refuses, and the refusal Core raises for it."""

    context: Context
    refusal: type[Unauthorized]


@pytest.fixture(
    params=[GROUP_ID_USER, GROUP_ID_READ_ONLY, None],
    ids=["ordinary user", "read-only user", "unknown user"],
)
async def refused_caller(hass: HomeAssistant, request: pytest.FixtureRequest) -> _RefusedCaller:
    """Return each caller a monitoring write refuses: a user in each group that is not an administrator, or a user id Home Assistant does not know.

    A known user who is not an administrator gets `Unauthorized`; an unknown
    one gets its subclass `UnknownUser`.
    """
    if request.param is None:
        return _RefusedCaller(Context(user_id=_UNKNOWN_USER_ID), UnknownUser)
    user = await _non_admin(hass, request.param)
    return _RefusedCaller(Context(user_id=user.id), Unauthorized)


async def _call(
    hass: HomeAssistant, service: str, data: ServiceData, context: Context | None
) -> ServiceResponse:
    """Call one of the integration's services and wait for it, asking for a response where it gives one."""
    return await hass.services.async_call(
        DOMAIN,
        service,
        dict(data),
        blocking=True,
        context=context,
        return_response=hass.services.supports_response(DOMAIN, service)
        is not SupportsResponse.NONE,
    )


async def _refusal(
    hass: HomeAssistant, service: str, data: ServiceData, context: Context | None
) -> HomeAssistantError | None:
    """Return what a call raised, or None if it was answered."""
    try:
        await _call(hass, service, data, context)
    except HomeAssistantError as err:
        return err
    return None


# ---------------------------------------------------------------------------
# The classification, against what the integration registers
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("integration")
async def test_every_registered_service_is_classified(hass: HomeAssistant) -> None:
    """Each service the integration registers is named in exactly one classification."""
    classes = (MONITORING_WRITES, READS, OPEN_WRITES, ADMINISTRATOR_IN_PERSON)
    classified = [name for names in classes for name in names]

    assert len(classified) == len(set(classified))
    assert set(hass.services.async_services_for_domain(DOMAIN)) == set(classified)


@pytest.mark.usefixtures("integration")
@pytest.mark.parametrize(
    ("service", "data"), MONITORING_WRITES.items(), ids=list(MONITORING_WRITES)
)
async def test_a_monitoring_write_refuses_a_caller_who_is_not_an_administrator(
    hass: HomeAssistant, refused_caller: _RefusedCaller, service: str, data: ServiceData
) -> None:
    """A non-admin's call to a monitoring write is refused with Core's `Unauthorized`, an unknown user's with `UnknownUser`."""
    refusal = await _refusal(hass, service, data, refused_caller.context)

    assert type(refusal) is refused_caller.refusal


@pytest.mark.usefixtures("integration")
@pytest.mark.parametrize(
    ("service", "data"), MONITORING_WRITES.items(), ids=list(MONITORING_WRITES)
)
async def test_a_monitoring_write_is_not_refused_without_a_user(
    hass: HomeAssistant, service: str, data: ServiceData
) -> None:
    """A call with no user, as an automation makes, is not refused for want of one.

    No panel is loaded, so most writes refuse for that; none refuses as unauthorized.
    """
    refusal = await _refusal(hass, service, data, None)

    assert not isinstance(refusal, Unauthorized)


@pytest.mark.usefixtures("integration")
@pytest.mark.parametrize(
    ("service", "data"), OPEN_TO_EVERY_USER.items(), ids=list(OPEN_TO_EVERY_USER)
)
async def test_an_open_service_does_not_refuse_a_non_admin(
    hass: HomeAssistant, non_admin_user: MockUser, service: str, data: ServiceData
) -> None:
    """A non-admin's call to an open service is never refused as unauthorized.

    No panel is loaded, so some refuse for that, as they would for anyone.
    """
    refusal = await _refusal(hass, service, data, Context(user_id=non_admin_user.id))

    assert not isinstance(refusal, Unauthorized)


@pytest.mark.usefixtures("integration")
@pytest.mark.parametrize(
    ("service", "data"), ADMINISTRATOR_IN_PERSON.items(), ids=list(ADMINISTRATOR_IN_PERSON)
)
async def test_an_in_person_service_refuses_a_non_admin_and_a_call_without_a_user(
    hass: HomeAssistant, non_admin_user: MockUser, service: str, data: ServiceData
) -> None:
    """A non-admin and a call with no user are both refused, each with its own reason."""
    as_non_admin = await _refusal(hass, service, data, Context(user_id=non_admin_user.id))
    without_a_user = await _refusal(hass, service, data, None)

    assert isinstance(as_non_admin, ServiceValidationError)
    assert as_non_admin.translation_key == f"{service}_requires_admin"
    assert isinstance(without_a_user, ServiceValidationError)
    assert without_a_user.translation_key == f"{service}_requires_user"


# ---------------------------------------------------------------------------
# Each monitoring write on a loaded panel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Panel:
    """A loaded panel with monitoring on, and the alerts fired on the event bus."""

    hass: HomeAssistant
    alerts: list[Event]

    async def status(self, context: Context | None = None) -> Mapping[str, object]:
        """Return `get_monitoring_status`'s answer."""
        response = await _call(self.hass, "get_monitoring_status", {}, context)
        assert response is not None
        return response


@pytest.fixture
async def panel(hass: HomeAssistant, integration: None) -> _Panel:
    """Return a loaded panel with monitoring on, one circuit that takes a copied rating, and alerts sent to the event bus.

    The circuit draws well under its rating, so nothing but a test notification
    fires an alert. Its graph horizons start at the default, and a sensor of
    the circuit, `_FAVORITE_ENTITY_ID`, is registered on the panel device for a
    favorite to name. The sensor is not the circuit's current or power sensor,
    which the monitoring status would key the circuit by instead of its id.
    """
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number=_SERIAL,
        circuits={
            "circuit-2": SpanCircuitSnapshotFactory.create(
                circuit_id="circuit-2",
                name="Synthetic Circuit",
                tabs=[2],
                current_a=5.0,
                breaker_rating_a=20.0,
                breaker_rating_declared=True,
            )
        },
        main_breaker_rating_a=100,
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={NOTIFY_TARGETS: EVENT_BUS_TARGET}, unique_id=_SERIAL
    )
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    monitor = CurrentMonitor(hass, entry, manager)
    monitor.process_snapshot(snapshot)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.package_rating_manager = manager
    coordinator.current_monitor = monitor
    coordinator.graph_horizon_manager = GraphHorizonManager(hass, entry)
    panel_device_id = register_panel_device(hass, entry, _SERIAL)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel_device_id,
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    sensor = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(_SERIAL, "circuit-2", "consumedEnergyWh"),
        config_entry=entry,
        device_id=panel_device_id,
        suggested_object_id="synthetic_circuit_energy_consumed",
    )
    assert sensor.entity_id == _FAVORITE_ENTITY_ID
    return _Panel(hass, async_capture_events(hass, EVENT_CURRENT_ALERT))


def _point(status: Mapping[str, object], kind: str, key: str) -> Mapping[str, object]:
    """Return one point's entry from a monitoring status: `kind` is `circuits` or `mains`."""
    points = status[kind]
    assert isinstance(points, dict)
    point = points[key]
    assert isinstance(point, dict)
    return point


async def _global_spike(panel: _Panel) -> object:
    settings = (await panel.status())["global_settings"]
    assert isinstance(settings, dict)
    return settings[SPIKE_THRESHOLD_PCT]


async def _monitoring_on(panel: _Panel) -> object:
    return (await panel.status())["enabled"]


async def _circuit_spike(panel: _Panel) -> object:
    return _point(await panel.status(), "circuits", "circuit-2")[SPIKE_THRESHOLD_PCT]


async def _circuit_overridden(panel: _Panel) -> object:
    return _point(await panel.status(), "circuits", "circuit-2")["has_override"]


async def _mains_spike(panel: _Panel) -> object:
    return _point(await panel.status(), "mains", "upstream_l1")[SPIKE_THRESHOLD_PCT]


async def _mains_overridden(panel: _Panel) -> object:
    return _point(await panel.status(), "mains", "upstream_l1")["has_override"]


async def _copied_ratings(panel: _Panel) -> object:
    return _point(await panel.status(), "circuits", "circuit-2")["package_ratings_a"]


async def _alerts_fired(panel: _Panel) -> object:
    await panel.hass.async_block_till_done()
    return len(panel.alerts)


@dataclass(frozen=True)
class _Write:
    """A monitoring write, what it changes, and any state it needs there first."""

    service: str
    data: ServiceData
    observe: Callable[[_Panel], Awaitable[object]]
    arrange: tuple[tuple[str, ServiceData], ...] = ()
    label: str = ""

    def __str__(self) -> str:
        """Name the case: the service, and what it does where it does more than one thing."""
        return f"{self.service} {self.label}".strip()


_WRITES: Final = (
    _Write("set_global_monitoring", {SPIKE_THRESHOLD_PCT: 77}, _global_spike, label="settings"),
    _Write("set_global_monitoring", {"enabled": False}, _monitoring_on, label="off"),
    _Write(
        "set_circuit_threshold",
        {"circuit_id": "circuit-2", SPIKE_THRESHOLD_PCT: 77},
        _circuit_spike,
    ),
    _Write(
        "clear_circuit_threshold",
        {"circuit_id": "circuit-2"},
        _circuit_overridden,
        arrange=(("set_circuit_threshold", {"circuit_id": "circuit-2", SPIKE_THRESHOLD_PCT: 77}),),
    ),
    _Write("set_mains_threshold", {"leg": "upstream_l1", SPIKE_THRESHOLD_PCT: 77}, _mains_spike),
    _Write(
        "clear_mains_threshold",
        {"leg": "upstream_l1"},
        _mains_overridden,
        arrange=(("set_mains_threshold", {"leg": "upstream_l1", SPIKE_THRESHOLD_PCT: 77}),),
    ),
    _Write(
        "set_circuit_package_ratings",
        {"circuit_id": "circuit-2", "package_ratings_a": [20, 30]},
        _copied_ratings,
    ),
    _Write(
        "clear_circuit_package_ratings",
        {"circuit_id": "circuit-2"},
        _copied_ratings,
        arrange=(
            (
                "set_circuit_package_ratings",
                {"circuit_id": "circuit-2", "package_ratings_a": [20, 30]},
            ),
        ),
    ),
    _Write("test_notification", {}, _alerts_fired),
)


def test_every_monitoring_write_is_exercised_on_a_panel() -> None:
    """The writes run on a loaded panel below are every monitoring write the classification names."""
    assert {write.service for write in _WRITES} == set(MONITORING_WRITES)


async def _arranged(panel: _Panel, write: _Write) -> object:
    """Put in place what `write` needs, as an automation would, and return what it is about to change."""
    for service, data in write.arrange:
        await _call(panel.hass, service, data, None)
    return await write.observe(panel)


@pytest.mark.parametrize("write", _WRITES, ids=str)
async def test_a_caller_who_is_not_an_administrator_changes_nothing(
    hass: HomeAssistant, panel: _Panel, refused_caller: _RefusedCaller, write: _Write
) -> None:
    """A non-admin's or unknown user's call is refused before it acts, so what it would change is as it was."""
    before = await _arranged(panel, write)

    refusal = await _refusal(hass, write.service, write.data, refused_caller.context)

    assert type(refusal) is refused_caller.refusal
    assert await write.observe(panel) == before


@pytest.mark.parametrize("write", _WRITES, ids=str)
async def test_an_admin_changes_it(
    hass: HomeAssistant, panel: _Panel, hass_admin_user: MockUser, write: _Write
) -> None:
    """An administrator's call acts."""
    before = await _arranged(panel, write)

    await _call(hass, write.service, write.data, Context(user_id=hass_admin_user.id))

    assert await write.observe(panel) != before


@pytest.mark.parametrize("write", _WRITES, ids=str)
async def test_a_call_without_a_user_changes_it(
    hass: HomeAssistant, panel: _Panel, write: _Write
) -> None:
    """A call with no user, as an automation makes, acts."""
    before = await _arranged(panel, write)

    await _call(hass, write.service, write.data, None)

    assert await write.observe(panel) != before


@pytest.mark.parametrize("service", list(READS))
async def test_a_read_answers_a_non_admin_as_it_answers_an_automation(
    hass: HomeAssistant, panel: _Panel, non_admin_user: MockUser, service: str
) -> None:
    """Reading stays open: a non-admin gets the answer a call with no user gets."""
    as_non_admin = await _call(hass, service, READS[service], Context(user_id=non_admin_user.id))

    assert as_non_admin is not None
    assert as_non_admin == await _call(hass, service, READS[service], None)


async def test_a_non_admin_reads_the_panels_monitoring(
    panel: _Panel, non_admin_user: MockUser
) -> None:
    """The status a non-admin reads is the loaded panel's, monitoring on and its circuit listed."""
    status = await panel.status(Context(user_id=non_admin_user.id))

    assert status["enabled"] is True
    assert _point(status, "circuits", "circuit-2")["name"] == "Synthetic Circuit"


# ---------------------------------------------------------------------------
# Each open write on a loaded panel
# ---------------------------------------------------------------------------


async def _favorites(panel: _Panel) -> object:
    return await _call(panel.hass, "get_favorites", {}, None)


async def _graph_settings(panel: _Panel) -> object:
    return await _call(panel.hass, "get_graph_settings", {}, None)


def _open_write(
    service: str, observe: Callable[[_Panel], Awaitable[object]], arranged_by: str | None = None
) -> _Write:
    """Return an open write with its classification's data, after `arranged_by` with its own where it clears."""
    arrange = ((arranged_by, OPEN_WRITES[arranged_by]),) if arranged_by is not None else ()
    return _Write(service, OPEN_WRITES[service], observe, arrange)


_OPEN_WRITES_ON_A_PANEL: Final = (
    _open_write("add_favorite", _favorites),
    _open_write("remove_favorite", _favorites, arranged_by="add_favorite"),
    _open_write("set_graph_time_horizon", _graph_settings),
    _open_write("set_circuit_graph_horizon", _graph_settings),
    _open_write(
        "clear_circuit_graph_horizon", _graph_settings, arranged_by="set_circuit_graph_horizon"
    ),
    _open_write("set_subdevice_graph_horizon", _graph_settings),
    _open_write(
        "clear_subdevice_graph_horizon", _graph_settings, arranged_by="set_subdevice_graph_horizon"
    ),
)


def test_every_open_write_is_exercised_on_a_panel() -> None:
    """The open writes run on a loaded panel below are every one the classification names."""
    assert {write.service for write in _OPEN_WRITES_ON_A_PANEL} == set(OPEN_WRITES)


def test_the_horizon_the_open_writes_set_is_not_the_default() -> None:
    """Setting the default would change nothing, and prove nothing."""
    assert _CHANGED_HORIZON in VALID_GRAPH_HORIZONS
    assert _CHANGED_HORIZON != DEFAULT_GRAPH_HORIZON


@pytest.mark.parametrize("write", _OPEN_WRITES_ON_A_PANEL, ids=str)
async def test_an_open_write_acts_for_a_non_admin(
    hass: HomeAssistant, panel: _Panel, non_admin_user: MockUser, write: _Write
) -> None:
    """A non-admin's favorite or graph-horizon change takes effect, as an automation's does."""
    before = await _arranged(panel, write)

    await _call(hass, write.service, write.data, Context(user_id=non_admin_user.id))

    assert await write.observe(panel) != before
