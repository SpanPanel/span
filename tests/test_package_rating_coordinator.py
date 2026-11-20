"""Tests for the coordinator's wiring of package ratings copied from the SPAN app."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import Event, HomeAssistant
from pytest_homeassistant.common import MockConfigEntry, async_capture_events
from span_panel_api import FieldMetadata, SpanMqttClient, SpanPanelSnapshot

from custom_components.span_panel import async_setup_entry
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
    EVENT_PACKAGE_RATINGS_CHANGED,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.notices import is_standing

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .helpers import ensure_device_registered_stand_in

SERVICE_A = 100
_DECLARED = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}
_NOTICE = "package_ratings_changed"


def _snapshot(ratings: Mapping[str, float | None]) -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        serial_number="sp3-rating-001",
        circuits={
            cid: SpanCircuitSnapshotFactory.create(
                circuit_id=cid,
                name=f"Name of {cid}",
                breaker_rating_a=rating,
                breaker_rating_declared=True,
            )
            for cid, rating in ratings.items()
        },
        main_breaker_rating_a=SERVICE_A,
    )


def _seed(hass_storage: dict[str, object], entry: MockConfigEntry, circuit_id: str) -> None:
    """Leave a 20-20 package, 40 A together, copied for space 1 where the panel says 20 A, on disk for `entry`."""
    hass_storage[f"span_panel_package_ratings.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"span_panel_package_ratings.{entry.entry_id}",
        "data": {
            "packages": {
                circuit_id: {
                    "package_ratings_a": [20, 20],
                    "space": 1,
                    "label": f"Name of {circuit_id}",
                }
            },
            "unacknowledged": {},
        },
    }


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.168.1.50",
            CONF_EBUS_BROKER_HOST: "span-panel.local",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_EBUS_BROKER_PASSWORD: "mqtt-pass",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_HTTP_PORT: 80,
        },
        entry_id="entry-rating",
        title="sp3-rating-001",
        unique_id="sp3-rating-001",
    )


def _client(field_metadata: Mapping[str, FieldMetadata] | None) -> MagicMock:
    client = MagicMock(spec=SpanMqttClient)
    client.field_metadata = field_metadata
    return client


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> SpanPanelCoordinator:
    return SpanPanelCoordinator(hass, client, entry)


async def _begun(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, hass: HomeAssistant
) -> PackageRatingManager:
    manager = coordinator.package_rating_manager
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    await hass.async_block_till_done()
    return manager


def _events(hass: HomeAssistant) -> list[Event]:
    events: list[Event] = async_capture_events(hass, EVENT_PACKAGE_RATINGS_CHANGED)
    return events


# -- The attribute ------------------------------------------------------------


async def test_the_coordinator_owns_a_manager_from_construction(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _client(_DECLARED))
    assert isinstance(coordinator.package_rating_manager, PackageRatingManager)


# -- Before a snapshot is published ------------------------------------------


async def test_a_pushed_snapshot_is_reconciled_before_listeners_are_told(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The panel's rating changes under a copy; by the time the snapshot is published, the copy is gone."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    coordinator = _coordinator(hass, entry, _client(_DECLARED))
    manager = await _begun(coordinator, _snapshot({"circuit-2": 20.0}), hass)
    assert manager.resolve("circuit-2", _snapshot({"circuit-2": 20.0})).user_set

    overtaken = _snapshot({"circuit-2": 30.0})
    seen: list[set[str]] = []

    def _listeners(_: SpanPanelSnapshot) -> None:
        seen.append(set(manager._packages))

    with (
        patch.object(coordinator, "async_set_updated_data", side_effect=_listeners),
        patch.object(coordinator, "_run_post_update_tasks", AsyncMock()),
    ):
        await coordinator._on_snapshot_push(overtaken)

    assert seen == [set()]


async def test_a_polled_snapshot_is_reconciled_before_it_is_returned(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    overtaken = _snapshot({"circuit-2": 30.0})
    client = _client(_DECLARED)
    client.get_snapshot = AsyncMock(return_value=overtaken)
    coordinator = _coordinator(hass, entry, client)
    manager = await _begun(coordinator, _snapshot({"circuit-2": 20.0}), hass)
    events = _events(hass)

    with patch.object(coordinator, "_run_post_update_tasks", AsyncMock()):
        result = await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert result is overtaken
    assert "circuit-2" not in manager._packages
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]


async def test_a_snapshot_before_begin_judges_nothing(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The first refresh runs before `begin`; it must delete and announce nothing."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    client = _client(_DECLARED)
    client.get_snapshot = AsyncMock(return_value=_snapshot({"circuit-9": 20.0}))
    coordinator = _coordinator(hass, entry, client)
    await coordinator.package_rating_manager.async_load()
    events = _events(hass)

    with patch.object(coordinator, "_run_post_update_tasks", AsyncMock()):
        await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []
    assert not is_standing(hass, entry, _NOTICE)
    stored = hass_storage[f"span_panel_package_ratings.{entry.entry_id}"]
    assert isinstance(stored, dict)
    assert "circuit-2" in stored["data"]["packages"]


async def test_a_reconnect_replay_with_no_metadata_deletes_and_announces_nothing(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """`field_metadata` is None while the client rebuilds; the rating arrives on the second push.

    The copy waits while the rating is away and applies again when it is back:
    readers are told each time, and nothing is removed or put in a notice.
    """
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    client = _client(_DECLARED)
    coordinator = _coordinator(hass, entry, client)
    manager = await _begun(coordinator, _snapshot({"circuit-2": 20.0}), hass)
    events = _events(hass)

    client.field_metadata = None
    described_late = _snapshot({"circuit-2": None})
    rated_late = _snapshot({"circuit-2": 20.0})
    with (
        patch.object(coordinator, "async_set_updated_data"),
        patch.object(coordinator, "_run_post_update_tasks", AsyncMock()),
    ):
        await coordinator._on_snapshot_push(described_late)
        await coordinator._on_snapshot_push(rated_late)
    await hass.async_block_till_done()

    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"], ["circuit-2"]]
    assert not is_standing(hass, entry, _NOTICE)
    assert "circuit-2" in manager._packages
    client.field_metadata = _DECLARED
    manager.reconcile(rated_late, lambda: client.field_metadata)
    await hass.async_block_till_done()
    rating = manager.resolve("circuit-2", rated_late)
    assert (rating.user_set, rating.judged_a) == (True, 40)
    assert len(events) == 2


async def test_snapshots_do_not_read_field_metadata_once_the_declaration_is_known(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """Pushed and polled snapshots alike: the declaration made at `begin` holds for the session."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    snapshot = _snapshot({"circuit-2": 20.0})
    client = _client(None)
    reads = PropertyMock(return_value=_DECLARED)
    type(client).field_metadata = reads
    client.get_snapshot = AsyncMock(return_value=snapshot)
    coordinator = _coordinator(hass, entry, client)
    await _begun(coordinator, snapshot, hass)

    with (
        patch.object(coordinator, "async_set_updated_data"),
        patch.object(coordinator, "_run_post_update_tasks", AsyncMock()),
    ):
        for _ in range(3):
            await coordinator._on_snapshot_push(snapshot)
            await coordinator._async_update_data()

    assert reads.call_count == 0


async def test_snapshots_read_field_metadata_while_the_declaration_is_unknown(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """Metadata absent at `begin`: each snapshot asks again until it is known, then stops."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    snapshot = _snapshot({"circuit-2": 20.0})
    client = _client(None)
    reads = PropertyMock(side_effect=[None, _DECLARED])
    type(client).field_metadata = reads
    coordinator = _coordinator(hass, entry, client)
    manager = coordinator.package_rating_manager
    await manager.async_load()
    manager.begin(snapshot, None)

    with (
        patch.object(coordinator, "async_set_updated_data"),
        patch.object(coordinator, "_run_post_update_tasks", AsyncMock()),
    ):
        for _ in range(4):
            await coordinator._on_snapshot_push(snapshot)

    assert reads.call_count == 2
    rating = manager.resolve("circuit-2", snapshot)
    assert (rating.user_set, rating.judged_a) == (True, 40)


# -- Setup ---------------------------------------------------------------------


async def _set_up(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    snapshot: SpanPanelSnapshot,
    *,
    before_begin: Callable[[], None] | None = None,
) -> tuple[MagicMock, SpanPanelCoordinator]:
    """Run `async_setup_entry` with a real coordinator whose first refresh yields `snapshot`.

    `before_begin` runs as `begin` is called: after the device-id migration
    and the load, so it sees the manager as `begin` does.
    """
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = _client(_DECLARED)
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.get_snapshot = AsyncMock(return_value=snapshot)
    client.start_streaming = AsyncMock()
    client.stop_streaming = AsyncMock()
    coordinators: list[SpanPanelCoordinator] = []
    begin = PackageRatingManager.begin

    def _build(
        hass_: HomeAssistant, client_: MagicMock, entry_: MockConfigEntry
    ) -> SpanPanelCoordinator:
        coordinator = SpanPanelCoordinator(hass_, client_, entry_)
        coordinators.append(coordinator)
        return coordinator

    def _begin(
        manager: PackageRatingManager,
        begun: SpanPanelSnapshot,
        field_metadata: Mapping[str, FieldMetadata] | None,
    ) -> None:
        if before_begin is not None:
            before_begin()
        begin(manager, begun, field_metadata)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", side_effect=_build),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(side_effect=ensure_device_registered_stand_in),
        ),
        patch.object(PackageRatingManager, "begin", _begin),
        patch.object(SpanPanelCoordinator, "async_setup_streaming", AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        await async_setup_entry(hass, entry)
    await hass.async_block_till_done()
    return client, coordinators[0]


async def test_a_package_stored_before_setup_is_in_force_after_it(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    snapshot = _snapshot({"circuit-2": 20.0})

    _, coordinator = await _set_up(hass, entry, snapshot)

    rating = coordinator.package_rating_manager.resolve("circuit-2", snapshot)
    assert (rating.user_set, rating.judged_a) == (True, 40)


async def test_a_package_for_a_missing_circuit_survives_until_begin_and_is_then_judged(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """Without a migration the first reconcile removes it; before then nothing is deleted or announced."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "old-circuit")
    snapshot = _snapshot({"circuit-2": 20.0})
    events = _events(hass)
    before: list[tuple[int, int]] = []

    def _look() -> None:
        manager = coordinators_manager[0]
        before.append((len(manager._packages), len(events)))

    coordinators_manager: list[PackageRatingManager] = []
    orig_load = PackageRatingManager.async_load

    async def _capture(self: PackageRatingManager) -> None:
        coordinators_manager.append(self)
        await orig_load(self)

    with patch.object(PackageRatingManager, "async_load", _capture):
        _, coordinator = await _set_up(hass, entry, snapshot, before_begin=_look)

    assert before == [(1, 0)]
    assert coordinator.package_rating_manager._packages == {}
    assert [event.data["circuit_ids"] for event in events] == [["old-circuit"]]


async def test_a_package_under_a_circuits_old_device_id_is_judged_under_its_new_one(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The migration re-keys the store before the manager loads it, so `begin` removes nothing.

    Fails if the load runs before the migration: the manager would hold the
    package under `circuit-5`, which the snapshot lacks, and remove it.
    """
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-5")
    new_id = "sp3-rating-001-00000000000000aa-1-16-5"
    snapshot = _snapshot({new_id: 20.0})
    events = _events(hass)

    _, coordinator = await _set_up(hass, entry, snapshot)

    rating = coordinator.package_rating_manager.resolve(new_id, snapshot)
    assert (rating.user_set, rating.judged_a) == (True, 40)
    assert events == []
    assert not is_standing(hass, entry, _NOTICE)
    stored = hass_storage[f"span_panel_package_ratings.{entry.entry_id}"]
    assert isinstance(stored, dict)
    assert list(stored["data"]["packages"]) == [new_id]
