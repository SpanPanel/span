"""One reload per change to a config entry, however many parts of the integration ask.

Real setups over replayed captures, reloaded through Home Assistant's own
`async_reload`, with a client that suspends in `connect()`, `start_streaming()`
and `close()` as a real one does. `ReplayClient` alone never suspends: a whole
reload then runs inside one call, and the interleavings these tests are about
never happen.
"""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import timedelta
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_ZEROCONF,
    ConfigEntryDisabler,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.discovery_flow import DiscoveryKey
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.typing import WebSocketGenerator
from span_panel_api.exceptions import SpanPanelConnectionError, SpanPanelValidationError

from custom_components.span_panel import entry_reloads
from custom_components.span_panel.config_flow import SpanPanelConfigFlow
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.entry_reloads import async_request_rebuild, async_request_reload
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import SNAPSHOT_UPDATE_INTERVAL
from custom_components.span_panel.runtime import loaded_runtime_data

from .captures_replay import ReplayClient, capture, replay
from .test_expected_entities import FRESH_INSTALL_OPTIONS

SETTLE_TIMEOUT_S = 10.0
"""How long a test waits for Home Assistant to settle before it calls a reload stuck."""

CAPTURE = "main32_r202639"
INTEGRATION = Path(__file__).parent.parent / "custom_components" / "span_panel"
SCHEDULER_LOGGER = "custom_components.span_panel.entry_reloads"
DEPRECATION = "has an update listener"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Let the switch platform's relock and debounce timers outlive a test."""
    return True


class _Panels:
    """Real entries over replayed captures, with a client that suspends where a real one does."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.adapters: dict[str, Any] = {}
        self.built: Counter[str] = Counter()
        self.closed: Counter[str] = Counter()
        self.hosts: list[str] = []
        self.intervals: list[object] = []
        self.connect_error: Exception | None = None
        self.on_connect: Callable[[], None] | None = None
        self.on_start_streaming: Callable[[], None] | None = None
        self.on_close: Callable[[], None] | None = None

    def add(self, name: str = CAPTURE, *, titled_with_the_serial: bool = False) -> MockConfigEntry:
        adapter = replay(capture(name).tree())
        serial = adapter.build_snapshot().serial_number
        self.adapters[serial] = adapter
        number = len(self.adapters)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_API_VERSION: "v2",
                CONF_HOST: f"192.0.2.{10 + number}",
                "device_name": f"Span Panel {number}",
                CONF_EBUS_BROKER_HOST: f"{serial}.local",
                CONF_EBUS_BROKER_USERNAME: "replay-user",
                CONF_EBUS_BROKER_PASSWORD: "replay-password",
                CONF_EBUS_BROKER_PORT: 8883,
            },
            options=dict(FRESH_INSTALL_OPTIONS),
            title=serial if titled_with_the_serial else f"Span Panel {number}",
            entry_id=f"entry-{name}",
            unique_id=serial,
            version=CURRENT_CONFIG_VERSION,
            minor_version=CURRENT_CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(self.hass)
        return entry

    def client(self, host: str, serial: str, *_args: object, **kwargs: object) -> ReplayClient:
        """`SpanMqttClient` as setup calls it: the panel's serial is the second argument."""
        panels = self
        self.built[serial] += 1
        self.hosts.append(host)
        self.intervals.append(kwargs.get("snapshot_interval"))

        class _Suspending(ReplayClient):
            async def connect(self) -> None:
                await asyncio.sleep(0)
                error = panels.connect_error
                if panels.on_connect is not None:
                    panels.on_connect()
                if error is not None:
                    raise error

            async def start_streaming(self) -> None:
                if panels.on_start_streaming is not None:
                    panels.on_start_streaming()
                # A real client subscribes here, which suspends: without it the rest
                # of setup finishes in one pass and nothing else runs meanwhile.
                for _ in range(4):
                    await asyncio.sleep(0)

            async def close(self) -> None:
                panels.closed[serial] += 1
                if panels.on_close is not None:
                    panels.on_close()
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        return _Suspending(self.adapters[serial])


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[_Panels]:
    """Every setup in the test, the reloads' included, builds a suspending replay client."""
    made = _Panels(hass)
    with patch("custom_components.span_panel.SpanMqttClient", side_effect=made.client):
        yield made


async def _settle(hass: HomeAssistant, *, background: bool = False) -> None:
    """Wait for Home Assistant to settle, failing rather than hanging when a reload is stuck."""
    async with asyncio.timeout(SETTLE_TIMEOUT_S):
        await hass.async_block_till_done(wait_background_tasks=background)


def _coordinator(entry: MockConfigEntry) -> SpanPanelCoordinator:
    runtime = loaded_runtime_data(entry)
    assert runtime is not None
    return runtime.coordinator


async def _loaded_and_quiet(hass: HomeAssistant, panels: _Panels) -> MockConfigEntry:
    """A loaded entry past its new entities' reload, so later counts are the case's alone."""
    entry = panels.add()
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await _settle(hass)
    first = _coordinator(entry)
    await first._on_snapshot_push(first.data)
    await _settle(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert panels.built[_serial(entry)] == 2
    return entry


def _serial(entry: MockConfigEntry) -> str:
    assert entry.unique_id is not None
    return entry.unique_id


def _moved(entry: MockConfigEntry, host: str) -> dict[str, Any]:
    return {**entry.data, CONF_HOST: host}


def _with_option(entry: MockConfigEntry, key: str, value: object) -> dict[str, Any]:
    return {**entry.options, key: value}


async def _into_retry(hass: HomeAssistant, panels: _Panels, entry: MockConfigEntry) -> None:
    panels.connect_error = SpanPanelConnectionError("panel unreachable")
    await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def _fire_retry(hass: HomeAssistant) -> None:
    """Let Home Assistant's own retry timer fire, and wait for the attempt and what follows."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await _settle(hass, background=True)
    await _settle(hass, background=True)


def _once(action: Callable[[], None]) -> Callable[[], None]:
    """Run a hook on its first call only, so a reload it causes does not repeat it."""
    done = False

    def _hook() -> None:
        nonlocal done
        if not done:
            done = True
            action()

    return _hook


def _scheduler_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == SCHEDULER_LOGGER and record.levelno >= logging.WARNING
    ]


# ---------------------------------------------------------------------------
# One reload, however many ask
# ---------------------------------------------------------------------------


async def test_every_source_asking_in_one_pass_gets_one_reload(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The listener, the coordinator and a flow asking together share one reload."""
    entry = await _loaded_and_quiet(hass, panels)

    hass.config_entries.async_update_entry(entry, options=_with_option(entry, "probe", 1))
    async_request_rebuild(hass, entry.entry_id)
    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3


async def test_a_request_during_a_reloads_unload_is_applied_by_that_reload(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Its setup has yet to begin, and will read whatever the request was for."""
    entry = await _loaded_and_quiet(hass, panels)
    panels.on_close = _once(lambda: async_request_reload(hass, entry.entry_id))

    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3


async def test_a_request_after_a_reloads_setup_began_gets_one_more(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """That setup may already have read the entry the request is about."""
    entry = await _loaded_and_quiet(hass, panels)
    panels.on_start_streaming = _once(lambda: async_request_reload(hass, entry.entry_id))

    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 4


async def test_a_request_during_a_reload_started_elsewhere_adds_none(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The run waits for the user's Reload, whose setup then applies the request."""
    entry = await _loaded_and_quiet(hass, panels)
    panels.on_close = _once(lambda: async_request_reload(hass, entry.entry_id))

    await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3


async def test_a_request_and_one_other_reloader_during_the_runs_own_setup_give_two_setups(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The run's second pass waits for the other reloader, whose setup applies the request.

    Asking the lock whether it is held would read False just after the run's
    own reload released it, before the woken reloader has run, and cost one
    setup more.
    """
    entry = await _loaded_and_quiet(hass, panels)

    def _during_the_runs_setup() -> None:
        async_request_reload(hass, entry.entry_id)
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    panels.on_start_streaming = _once(_during_the_runs_setup)

    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 4


async def test_a_removal_queued_during_the_runs_own_reload_logs_nothing(
    hass: HomeAssistant, panels: _Panels, caplog: pytest.LogCaptureFixture
) -> None:
    """A run waiting for the removal finds the entry gone, and says nothing."""
    entry = await _loaded_and_quiet(hass, panels)

    def _during_the_runs_setup() -> None:
        async_request_reload(hass, entry.entry_id)
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))

    panels.on_start_streaming = _once(_during_the_runs_setup)

    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert _scheduler_warnings(caplog) == []
    assert entry.entry_id not in hass.data.get(entry_reloads._RELOADS, {})


# ---------------------------------------------------------------------------
# When the reload runs
# ---------------------------------------------------------------------------


async def test_a_reload_starts_after_the_write_that_asked_for_it_returns(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Home Assistant starts the listener inside the write; the reload waits for the write to return.

    The listener used to reload in place, so an options write returned with the
    entry already unloading and its client closed, inside whatever made it.
    """
    entry = await _loaded_and_quiet(hass, panels)
    closed = panels.closed[_serial(entry)]

    hass.config_entries.async_update_entry(entry, options=_with_option(entry, "probe", 1))

    assert entry.state is ConfigEntryState.LOADED
    assert panels.closed[_serial(entry)] == closed
    await _settle(hass)
    assert panels.built[_serial(entry)] == 3


async def test_a_request_while_starting_runs_once_home_assistant_has_started(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """One policy for every source, as Home Assistant's own retries and discovery flows wait."""
    entry = await _loaded_and_quiet(hass, panels)
    hass.set_state(CoreState.starting)
    try:
        async_request_rebuild(hass, entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=_with_option(entry, "probe", 1))
        await _settle(hass)
        assert panels.built[_serial(entry)] == 2
    finally:
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3


# ---------------------------------------------------------------------------
# A setup that fails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_a_rebuild_asked_for_by_a_setup_that_then_fails_gets_no_second_setup(
    hass: HomeAssistant, panels: _Panels, failure: type[BaseException]
) -> None:
    """The coordinator's rebuild belongs to the setup that built it, and goes with it.

    A cancellation the setup did not ask for counts as a setup error to Home
    Assistant, so it withdraws the rebuild too.
    """
    entry = await _loaded_and_quiet(hass, panels)

    def _ask_then_fail() -> None:
        async_request_rebuild(hass, entry.entry_id)
        raise failure("setup fails after its coordinator asked")

    panels.on_start_streaming = _once(_ask_then_fail)

    await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_a_reconfigure_during_a_retry_attempt_reaches_the_next_setup_at_once(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Not at Home Assistant's retry timer, up to ten minutes later.

    A change, or a person's request, made while a setup is under way is not
    applied by it, and survives the setup failing.
    """
    entry = await _loaded_and_quiet(hass, panels)
    await _into_retry(hass, panels, entry)
    built = panels.built[_serial(entry)]

    def _reconfigure() -> None:
        hass.config_entries.async_update_entry(entry, data=_moved(entry, "192.0.2.200"))
        async_request_reload(hass, entry.entry_id)
        panels.connect_error = None

    panels.on_connect = _once(_reconfigure)
    await _fire_retry(hass)

    assert panels.built[_serial(entry)] == built + 2
    assert entry.state is ConfigEntryState.LOADED
    assert panels.hosts[-1] == "192.0.2.200"


async def test_a_flow_during_a_setup_that_ends_in_error_reaches_the_next_setup_at_once(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Nothing retries a setup error, so the request is the only thing that sets the entry up again."""
    entry = await _loaded_and_quiet(hass, panels)

    def _repair() -> None:
        hass.config_entries.async_update_entry(entry, data=_moved(entry, "192.0.2.201"))
        async_request_reload(hass, entry.entry_id)
        panels.connect_error = None

    panels.connect_error = SpanPanelValidationError("unusable")
    panels.on_connect = _once(_repair)

    await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 4
    assert entry.state is ConfigEntryState.LOADED


async def test_no_update_listener_accumulates_over_failed_setups(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Registered at the top of every setup, and removed by Home Assistant after each that fails."""
    entry = await _loaded_and_quiet(hass, panels)
    assert len(entry.update_listeners) == 1

    await _into_retry(hass, panels, entry)
    assert entry.update_listeners == []
    await _fire_retry(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.update_listeners == []

    panels.connect_error = None
    await _fire_retry(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.update_listeners) == 1


async def test_a_disabled_entry_ends_the_run_without_a_setup(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """`async_reload` returns without setting a disabled entry up, and the run must not spin on it."""
    entry = await _loaded_and_quiet(hass, panels)
    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await _settle(hass)

    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 2
    assert entry_reloads._RELOADS in hass.data
    assert hass.data[entry_reloads._RELOADS][entry.entry_id].scheduled is False


async def test_the_run_cancelled_while_it_waits_ends_cleanly_and_a_later_request_runs(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Cancelled as Home Assistant cancels tasks at shutdown: nothing is left scheduled or locked."""
    entry = await _loaded_and_quiet(hass, panels)
    name = f"{DOMAIN} reload {entry.entry_id}"

    def _ask_and_cancel_the_run() -> None:
        async_request_reload(hass, entry.entry_id)

        async def _cancel() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            for task in asyncio.all_tasks():
                if task.get_name() == name:
                    task.cancel()

        hass.async_create_task(_cancel())

    panels.on_start_streaming = _once(_ask_and_cancel_the_run)

    await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)
    assert hass.data[entry_reloads._RELOADS][entry.entry_id].scheduled is False
    assert not entry.setup_lock.locked()
    built = panels.built[_serial(entry)]

    async_request_reload(hass, entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == built + 1


async def test_removing_an_entry_with_a_request_pending_ends_the_run_silently(
    hass: HomeAssistant, panels: _Panels, caplog: pytest.LogCaptureFixture
) -> None:
    """The record goes first in removal, so the run started once Home Assistant has started finds nothing."""
    entry = await _loaded_and_quiet(hass, panels)
    hass.set_state(CoreState.starting)
    try:
        async_request_reload(hass, entry.entry_id)
        await hass.config_entries.async_remove(entry.entry_id)
    finally:
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await _settle(hass)

    assert _scheduler_warnings(caplog) == []
    assert entry.entry_id not in hass.data.get(entry_reloads._RELOADS, {})


# ---------------------------------------------------------------------------
# What the listener asks for
# ---------------------------------------------------------------------------


async def test_writes_that_change_nothing_setup_reads_ask_for_nothing(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Home Assistant reads these preferences itself, and nothing here reads a discovery key."""
    entry = await _loaded_and_quiet(hass, panels)
    key = DiscoveryKey(domain="zeroconf", key=("_span._tcp.local.", "panel"), version=1)

    hass.config_entries.async_update_entry(
        entry, pref_disable_new_entities=not entry.pref_disable_new_entities
    )
    hass.config_entries.async_update_entry(
        entry, discovery_keys=MappingProxyType({"zeroconf": (key,)})
    )
    await _settle(hass)

    assert panels.built[_serial(entry)] == 2


async def test_the_polling_preference_reloads_once_through_home_assistant(
    hass: HomeAssistant, panels: _Panels, hass_ws_client: WebSocketGenerator
) -> None:
    """Home Assistant reloads a loaded entry whose polling preference changed; the listener adds none.

    It reloads only an entry still loaded after the write, which the listener's
    own reload, started in place, used to move out of `loaded`.
    """
    entry = await _loaded_and_quiet(hass, panels)
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "config_entries/update", "entry_id": entry.entry_id, "pref_disable_polling": True}
    )
    reply = await client.receive_json()
    await _settle(hass)

    assert reply["success"] is True
    assert panels.built[_serial(entry)] == 3


async def test_a_title_the_user_changes_reloads_the_entry(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The panel device's name is read from the entry, and the naming rules need it current."""
    entry = await _loaded_and_quiet(hass, panels)

    hass.config_entries.async_update_entry(entry, title="Garage Panel")
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3


async def test_an_option_changed_and_changed_back_in_one_pass_still_reloads(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """A request is never withdrawn by a later write, only applied by a setup."""
    entry = await _loaded_and_quiet(hass, panels)
    original = dict(entry.options)

    hass.config_entries.async_update_entry(entry, options=_with_option(entry, "probe", 1))
    hass.config_entries.async_update_entry(entry, options=original)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 3


async def test_home_assistant_starts_the_listener_inside_the_write(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The applied writers rely on it, so a Home Assistant that stopped doing it fails here."""
    entry = await _loaded_and_quiet(hass, panels)

    with patch.object(
        entry_reloads, "async_request_reload", wraps=entry_reloads.async_request_reload
    ) as asked:
        hass.config_entries.async_update_entry(entry, options=_with_option(entry, "probe", 1))
        asked.assert_called_once_with(hass, entry.entry_id)
        await _settle(hass)


# ---------------------------------------------------------------------------
# Setup's own writes, and writes during setup
# ---------------------------------------------------------------------------


async def test_a_change_written_during_setup_gets_one_more_reload(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The listener is registered before setup reads the entry, so the change is not lost."""
    entry = panels.add()
    panels.on_start_streaming = _once(
        lambda: hass.config_entries.async_update_entry(
            entry, options=_with_option(entry, SNAPSHOT_UPDATE_INTERVAL, 7.5)
        )
    )

    # Not asserted on: a first setup that goes through `async_setup_component`
    # returns False here, because the reload has begun by the time it returns.
    await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)

    assert panels.built[_serial(entry)] == 2
    assert panels.intervals[-1] == 7.5
    assert entry.state is ConfigEntryState.LOADED


async def test_setups_own_title_rename_asks_for_no_reload(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """An entry still titled with its serial is renamed by the setup that applies the new title."""
    entry = panels.add(titled_with_the_serial=True)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await _settle(hass)

    assert entry.title == "SPAN Panel"
    assert panels.built[_serial(entry)] == 1


async def test_a_change_from_outside_during_setup_survives_setups_own_rename(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """Setup's write records only what it changed as applied, so the earlier change still reloads."""
    entry = panels.add(titled_with_the_serial=True)
    panels.on_start_streaming = _once(
        lambda: hass.config_entries.async_update_entry(
            entry, options=_with_option(entry, SNAPSHOT_UPDATE_INTERVAL, 7.5)
        )
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)

    assert entry.title == "SPAN Panel"
    assert panels.built[_serial(entry)] == 2
    assert panels.intervals[-1] == 7.5


def test_only_the_flows_the_repair_the_rotation_and_the_migrations_write_the_entry_directly() -> None:
    """Everything else writes through the applied writers in `entry_reloads`.

    The listener is registered before setup runs, so a plain write in setup
    would cost a reload, and one that never converges, a timestamp or a
    counter, would reload without end.
    """
    allowed = {"config_flow.py", "entry_reloads.py", "migrations.py", "repairs.py", "services.py"}
    writers = set()
    for path in sorted(INTEGRATION.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "async_update_entry"
            ):
                writers.add(path.name)

    assert writers - allowed == set()


# ---------------------------------------------------------------------------
# The flows
# ---------------------------------------------------------------------------


def _flow(hass: HomeAssistant, source: str, **context: Any) -> SpanPanelConfigFlow:
    flow = SpanPanelConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = f"{source}-test"
    flow.context = {"source": source, **context}  # type: ignore[typeddict-item]
    return flow


@pytest.mark.parametrize("changed", [True, False], ids=["changed", "unchanged"])
async def test_reconfigure_reloads_a_loaded_entry_once_and_logs_no_deprecation(
    hass: HomeAssistant, panels: _Panels, caplog: pytest.LogCaptureFixture, changed: bool
) -> None:
    """Home Assistant's own helper reloaded on top of the listener, and logged that it should not.

    An unchanged entry reloads too, as Home Assistant's default does.
    """
    entry = await _loaded_and_quiet(hass, panels)
    flow = _flow(hass, SOURCE_RECONFIGURE, entry_id=entry.entry_id)
    host = "192.0.2.99" if changed else entry.data[CONF_HOST]

    result = flow._update_reload_and_abort(entry, data_updates={CONF_HOST: host})
    await _settle(hass)

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == host
    assert panels.built[_serial(entry)] == 3
    assert DEPRECATION not in caplog.text


async def test_home_assistant_reloads_only_an_entry_with_no_update_listener(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """A rediscovery lets Home Assistant reload an entry in setup retry, and leaves a loaded one to the listener."""
    entry = await _loaded_and_quiet(hass, panels)
    known = _flow(hass, SOURCE_ZEROCONF, unique_id=entry.unique_id)
    unknown = _flow(hass, SOURCE_ZEROCONF, unique_id="sp3-not-configured")

    assert known._home_assistant_reloads_the_entry() is False
    assert unknown._home_assistant_reloads_the_entry() is True

    await _into_retry(hass, panels, entry)
    assert known._home_assistant_reloads_the_entry() is True


# ---------------------------------------------------------------------------
# Home Assistant's lock
# ---------------------------------------------------------------------------


def test_the_entry_still_has_the_setup_lock_the_run_waits_on() -> None:
    """The run waits on `ConfigEntry.setup_lock`, public but used only by Home Assistant itself.

    A Home Assistant bump that renames or replaces it fails here, not in production.
    """
    assert isinstance(MockConfigEntry(domain=DOMAIN).setup_lock, asyncio.Lock)
