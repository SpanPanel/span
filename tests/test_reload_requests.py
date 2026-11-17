"""The rebuild a coordinator asks for: started after the update, once, and waiting for nothing else.

A reload is how the integration rebuilds devices and entities when the panel
changes underneath them, and every new circuit entity asks for one on its first
update. The reload used to await `hass.async_block_till_done()` first, which
waits for every tracked task but its own: two panels asking at once each waited
for the other, and neither reloaded. The coordinator now asks `entry_reloads`,
whose own tests cover what the scheduler does with every other source.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterator, Mapping
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState, OperationNotAllowed, UnknownEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanMqttClient, SpanPanelSnapshot

from custom_components.span_panel import update_listener
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.entry_reloads import async_setup_began, async_setup_failed
from custom_components.span_panel.id_builder import build_circuit_unique_id
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.runtime import loaded_runtime_data

from .captures_replay import ReplayClient, capture, replay
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .test_expected_entities import FRESH_INSTALL_OPTIONS

SETTLE_TIMEOUT_S = 5.0
"""How long a test waits for Home Assistant to settle before it calls the reload stuck."""

SNAPSHOT: SpanPanelSnapshot = SpanPanelSnapshotFactory.create()


def _coordinator(hass: HomeAssistant, entry_id: str = "entry-a") -> SpanPanelCoordinator:
    """Return a coordinator for an entry Home Assistant knows, over a client that can be shut down."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id, title=f"SPAN {entry_id}")
    entry.add_to_hass(hass)
    client = MagicMock()
    client.stop_streaming = AsyncMock()
    client.close = AsyncMock()
    return SpanPanelCoordinator(hass, cast(SpanMqttClient, client), entry)


async def _ask_for_a_reload(coordinator: SpanPanelCoordinator) -> None:
    """Ask as an entity or a capability check does, then run the update that carries the request out."""
    coordinator.request_reload()
    await coordinator._run_post_update_tasks(SNAPSHOT)


async def _settle(hass: HomeAssistant) -> None:
    """Wait for Home Assistant to settle, failing rather than hanging when a reload is stuck."""
    async with asyncio.timeout(SETTLE_TIMEOUT_S):
        await hass.async_block_till_done()


def _reload_that_replaces(coordinator: SpanPanelCoordinator) -> AsyncMock:
    """Return an `async_reload` that does what a real one does: shut the coordinator down, set the entry up."""

    async def _reload(_entry_id: str) -> bool:
        await coordinator.async_shutdown()
        async_setup_began(coordinator.hass, coordinator.config_entry)
        return True

    return AsyncMock(side_effect=_reload)


# ---------------------------------------------------------------------------
# When the reload starts
# ---------------------------------------------------------------------------


async def test_a_reload_waits_for_no_other_work(hass: HomeAssistant) -> None:
    """A reload starts while unrelated work is still running: a script, another integration's task.

    The reload used to wait for every tracked task to finish, so a script
    sitting in a long `delay` held a panel's reload back for as long as it ran.
    """
    coordinator = _coordinator(hass)
    release = asyncio.Event()
    unrelated = hass.async_create_task(release.wait(), "unrelated long-lived work")

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await _ask_for_a_reload(coordinator)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert not unrelated.done()
        reload.assert_awaited_once_with("entry-a")

        release.set()
        await _settle(hass)


async def test_a_reload_starts_only_after_the_update_that_asked_for_it_returns(
    hass: HomeAssistant,
) -> None:
    """The update carrying the request finishes before the reload tears its coordinator down.

    A request is carried out from inside the client's push callback or the poll,
    and the reload shuts that client down. Started in place, as an eager task
    would be, the whole unload ran before the call returned, inside the callback
    of the client being closed.
    """
    coordinator = _coordinator(hass)

    with patch.object(
        hass.config_entries, "async_reload", _reload_that_replaces(coordinator)
    ) as reload:
        await _ask_for_a_reload(coordinator)

        reload.assert_not_awaited()
        coordinator.client.close.assert_not_awaited()

        await _settle(hass)

    reload.assert_awaited_once_with("entry-a")
    coordinator.client.close.assert_awaited()


# ---------------------------------------------------------------------------
# How many reloads
# ---------------------------------------------------------------------------


async def test_a_coordinator_reloads_its_entry_once_however_often_it_asks(
    hass: HomeAssistant,
) -> None:
    """The reload replaces the coordinator, so a second request would only rebuild the new entry.

    A request can repeat before the reload starts (two updates in a row) and
    after it (a redelivered field asking again), and neither adds a reload.
    """
    coordinator = _coordinator(hass)

    with patch.object(
        hass.config_entries, "async_reload", _reload_that_replaces(coordinator)
    ) as reload:
        await _ask_for_a_reload(coordinator)
        await _ask_for_a_reload(coordinator)
        await _settle(hass)
        await _ask_for_a_reload(coordinator)
        await _settle(hass)

    reload.assert_awaited_once_with("entry-a")


async def test_a_coordinator_already_shut_down_asks_for_nothing(hass: HomeAssistant) -> None:
    """Whatever shut the coordinator down, an options change or a reload from the UI, rebuilt the entry."""
    coordinator = _coordinator(hass)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await coordinator.async_shutdown()
        await _ask_for_a_reload(coordinator)
        await _settle(hass)

    reload.assert_not_awaited()


@pytest.mark.parametrize(
    "before_the_run_starts",
    ["another-reload-set-the-entry-up", "its-setup-failed"],
)
async def test_a_rebuild_applied_or_withdrawn_before_its_run_starts_reloads_nothing(
    hass: HomeAssistant, before_the_run_starts: str
) -> None:
    """A rebuild asks for a setup after it, and is moot once one began or the setup that asked failed.

    Another reload, the user's from the UI say, sets the entry up and so builds
    what the rebuild asked for. A setup that fails after its coordinator asked
    leaves nothing of that coordinator to rebuild: the next setup starts afresh.
    """
    coordinator = _coordinator(hass)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await _ask_for_a_reload(coordinator)
        if before_the_run_starts == "another-reload-set-the-entry-up":
            await coordinator.async_shutdown()
            async_setup_began(hass, coordinator.config_entry)
        else:
            async_setup_failed(hass, coordinator.config_entry.entry_id)
        await _settle(hass)

    reload.assert_not_awaited()


@pytest.mark.parametrize(
    ("outcome", "logged"),
    [
        (UnknownEntry("entry-a"), True),
        (OperationNotAllowed("cannot be unloaded"), True),
        (False, False),
    ],
    ids=["entry-gone", "not-unloadable", "unload-failed"],
)
async def test_a_reload_that_did_not_go_through_can_be_asked_for_again(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    outcome: Exception | bool,
    logged: bool,
) -> None:
    """A refused reload is logged, and the coordinator, still in service, may ask again.

    `async_reload` raises only when it cannot try. A failed unload it records on
    the entry and reports as `False`, and the coordinator stays in service then
    too, so the next request reloads again rather than being taken for the one
    already made.
    """
    coordinator = _coordinator(hass)
    caplog.set_level(logging.WARNING, logger="custom_components.span_panel.entry_reloads")
    reload = (
        AsyncMock(side_effect=outcome)
        if isinstance(outcome, Exception)
        else AsyncMock(return_value=outcome)
    )

    with patch.object(hass.config_entries, "async_reload", reload):
        await _ask_for_a_reload(coordinator)
        await _settle(hass)
        await _ask_for_a_reload(coordinator)
        await _settle(hass)

    assert reload.await_count == 2
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "custom_components.span_panel.entry_reloads"
        and record.levelno == logging.WARNING
    ]
    if logged:
        assert warnings == [f"Could not reload SPAN entry-a: {outcome}"] * 2
    else:
        assert warnings == []


async def test_no_reload_starts_while_home_assistant_is_stopping(hass: HomeAssistant) -> None:
    """A reload started during shutdown would only delay it by a reconnect."""
    coordinator = _coordinator(hass)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        hass.set_state(CoreState.stopping)
        try:
            await _ask_for_a_reload(coordinator)
        finally:
            hass.set_state(CoreState.running)
        await _settle(hass)

    reload.assert_not_awaited()


# ---------------------------------------------------------------------------
# The solar migration's reload
# ---------------------------------------------------------------------------

SOLAR_SERIAL = "sp3-242424-001"
PV_CIRCUIT_ID = "pv-1"


def _pending_solar_migration(hass: HomeAssistant) -> tuple[SpanPanelCoordinator, str]:
    """Return a coordinator whose entry still has a v1 solar entity to move onto its PV circuit.

    Its setup has begun, as a coordinator in service's has, and the entry has
    its update listener, as a set-up entry's does.
    """
    coordinator = _coordinator(hass)
    entry = coordinator.config_entry
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "solar_migration_pending": True}
    )
    async_setup_began(hass, entry)
    entry.add_update_listener(update_listener)
    solar = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"span_{SOLAR_SERIAL}_solar_current_power", config_entry=entry
    )
    return coordinator, solar.entity_id


def _snapshot_with_a_pv_circuit() -> SpanPanelSnapshot:
    pv = SpanCircuitSnapshotFactory.create(circuit_id=PV_CIRCUIT_ID, device_type="pv")
    return SpanPanelSnapshotFactory.create(serial_number=SOLAR_SERIAL, circuits={PV_CIRCUIT_ID: pv})


async def test_the_solar_migration_reloads_the_entry_once_its_update_is_done(
    hass: HomeAssistant,
) -> None:
    """The moved entities register under their new unique IDs on the reload that follows."""
    coordinator, solar_entity_id = _pending_solar_migration(hass)

    with patch.object(
        hass.config_entries, "async_reload", _reload_that_replaces(coordinator)
    ) as reload:
        await coordinator._run_post_update_tasks(_snapshot_with_a_pv_circuit())
        reload.assert_not_awaited()
        await _settle(hass)

    reload.assert_awaited_once_with("entry-a")
    moved = er.async_get(hass).async_get(solar_entity_id)
    assert moved is not None
    assert moved.unique_id == build_circuit_unique_id(SOLAR_SERIAL, PV_CIRCUIT_ID, "instantPowerW")
    assert "solar_migration_pending" not in coordinator.config_entry.data


async def test_clearing_the_solar_flag_asks_for_no_reload_when_nothing_moved(
    hass: HomeAssistant,
) -> None:
    """The flag is the coordinator's bookkeeping, and clearing it changes nothing setup built.

    Clearing it is an entry update, which the listener used to answer with a
    full reload, started inside the update that cleared it.
    """
    coordinator, _solar_entity_id = _pending_solar_migration(hass)
    no_pv = SpanPanelSnapshotFactory.create(serial_number=SOLAR_SERIAL, circuits={})

    with patch.object(
        hass.config_entries, "async_reload", _reload_that_replaces(coordinator)
    ) as reload:
        await coordinator._run_post_update_tasks(no_pv)
        await _settle(hass)

    reload.assert_not_awaited()
    assert "solar_migration_pending" not in coordinator.config_entry.data


# ---------------------------------------------------------------------------
# End to end: two panels, each asking on its first update
# ---------------------------------------------------------------------------


class _Panels:
    """Real entries over replayed captures, counting the clients each panel's setups build."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.adapters: dict[str, Any] = {}
        self.built: Counter[str] = Counter()

    def add(self, name: str) -> MockConfigEntry:
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
            title=f"Span Panel {number}",
            entry_id=f"entry-{name}",
            unique_id=serial,
            version=CURRENT_CONFIG_VERSION,
            minor_version=CURRENT_CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(self.hass)
        return entry

    def client(self, _host: str, serial: str, *_args: object, **_kwargs: object) -> ReplayClient:
        """`SpanMqttClient` as setup calls it: the panel's serial is the second argument."""
        self.built[serial] += 1
        return ReplayClient(self.adapters[serial])


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[_Panels]:
    """Every setup in the test, the reloads' included, builds a replay client."""
    made = _Panels(hass)
    with patch("custom_components.span_panel.SpanMqttClient", side_effect=made.client):
        yield made


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Let the switch platform's relock and debounce timers outlive a test."""
    return True


def _coordinator_of(entry: MockConfigEntry) -> SpanPanelCoordinator:
    runtime = loaded_runtime_data(entry)
    assert runtime is not None
    return runtime.coordinator


async def test_two_panels_whose_first_updates_ask_together_both_reload(
    hass: HomeAssistant, panels: _Panels
) -> None:
    """The reported case: two fresh panels, each first update asking for a reload.

    An entity not yet in the registry asks for a reload on its first update, so
    two panels that both gain entities, on a fresh install or an upgrade that
    adds some, both ask. Each reload used to wait for the other's.
    """
    first = panels.add("main32_r202639")
    second = panels.add("r202639-a")
    # Sets the domain up, and with it every entry already added.
    assert await hass.config_entries.async_setup(first.entry_id) is True
    await _settle(hass)
    before: Mapping[str, int] = dict(panels.built)
    one, two = _coordinator_of(first), _coordinator_of(second)

    # What each panel's first streamed snapshot does.
    await one._on_snapshot_push(one.data)
    await two._on_snapshot_push(two.data)
    await _settle(hass)

    assert set(before.values()) == {1}
    assert dict(panels.built) == dict.fromkeys(before, 2)
    assert (first.state, second.state) == (ConfigEntryState.LOADED, ConfigEntryState.LOADED)
    assert _coordinator_of(first) is not one
    assert _coordinator_of(second) is not two
