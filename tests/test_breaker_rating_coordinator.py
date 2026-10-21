"""Tests for the coordinator's wiring of breaker rating overrides."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import Event, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from span_panel_api import FieldMetadata, SpanMqttClient, SpanPanelSnapshot

from custom_components.span_panel import async_setup_entry
from custom_components.span_panel.breaker_rating_manager import BreakerRatingManager
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
    EVENT_BREAKER_RATING_CHANGED,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.notices import is_standing

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

SERVICE_A = 100
_DECLARED = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}
_NOTICE = "breaker_rating_changed"


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
    """Leave an override of 40 A, set when the panel said 20 A, on disk for `entry`."""
    hass_storage[f"span_panel_breaker_ratings.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"span_panel_breaker_ratings.{entry.entry_id}",
        "data": {
            "overrides": {
                circuit_id: {
                    "rating_a": 40,
                    "panel_a_at_set": 20.0,
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
    client = MagicMock()
    client.field_metadata = field_metadata
    return client


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> SpanPanelCoordinator:
    return SpanPanelCoordinator(hass, cast(SpanMqttClient, client), entry)


async def _begun(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, hass: HomeAssistant
) -> BreakerRatingManager:
    manager = coordinator.breaker_rating_manager
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    await hass.async_block_till_done()
    return manager


def _events(hass: HomeAssistant) -> list[Event]:
    events: list[Event] = async_capture_events(hass, EVENT_BREAKER_RATING_CHANGED)
    return events


# -- The attribute ------------------------------------------------------------


async def test_the_coordinator_owns_a_manager_from_construction(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _client(_DECLARED))
    assert isinstance(coordinator.breaker_rating_manager, BreakerRatingManager)


# -- Before listeners see a snapshot -----------------------------------------


async def test_a_pushed_snapshot_is_reconciled_before_listeners_are_told(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The panel overtakes a correction; the entity update must already see the panel's rating."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    coordinator = _coordinator(hass, entry, _client(_DECLARED))
    manager = await _begun(coordinator, _snapshot({"circuit-2": 20.0}), hass)
    assert manager.resolve("circuit-2", _snapshot({"circuit-2": 20.0})).user_set

    overtaken = _snapshot({"circuit-2": 30.0})
    seen: list[float] = []

    def _listeners(_: SpanPanelSnapshot) -> None:
        seen.append(manager._overrides["circuit-2"].panel_a_at_set)

    with (
        patch.object(coordinator, "async_set_updated_data", side_effect=_listeners),
        patch.object(coordinator, "_run_post_update_tasks", AsyncMock()),
    ):
        await coordinator._on_snapshot_push(overtaken)

    assert seen == [30.0]


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
    assert manager._overrides["circuit-2"].panel_a_at_set == 30.0
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
    await coordinator.breaker_rating_manager.async_load()
    events = _events(hass)

    with patch.object(coordinator, "_run_post_update_tasks", AsyncMock()):
        await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []
    assert not is_standing(hass, entry, _NOTICE)
    stored = hass_storage[f"span_panel_breaker_ratings.{entry.entry_id}"]
    assert isinstance(stored, dict)
    assert "circuit-2" in stored["data"]["overrides"]


async def test_a_reconnect_replay_with_no_metadata_deletes_and_announces_nothing(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """`field_metadata` is None while the client rebuilds; the rating arrives on the second push."""
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

    assert events == []
    assert not is_standing(hass, entry, _NOTICE)
    client.field_metadata = _DECLARED
    manager.reconcile(rated_late, _DECLARED)
    rating = manager.resolve("circuit-2", rated_late)
    assert (rating.user_set, rating.effective_a) == (True, 40)
    assert events == []


# -- Setup ---------------------------------------------------------------------


async def _set_up(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    snapshot: SpanPanelSnapshot,
    *,
    before_begin: object | None = None,
) -> tuple[MagicMock, SpanPanelCoordinator]:
    """Run `async_setup_entry` with a real coordinator whose first refresh yields `snapshot`.

    `before_begin` stands in for a device-id migration: it runs in place of the
    hardware verdict, the call immediately before `begin`.
    """
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = _client(_DECLARED)
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.get_snapshot = AsyncMock(return_value=snapshot)
    client.start_streaming = AsyncMock()
    client.stop_streaming = AsyncMock()
    coordinators: list[SpanPanelCoordinator] = []

    def _build(
        hass_: HomeAssistant, client_: MagicMock, entry_: MockConfigEntry
    ) -> SpanPanelCoordinator:
        coordinator = SpanPanelCoordinator(hass_, cast(SpanMqttClient, client_), entry_)
        coordinators.append(coordinator)
        return coordinator

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", side_effect=_build),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch(
            "custom_components.span_panel._async_enforce_hardware_verdict", side_effect=before_begin
        ),
        patch.object(SpanPanelCoordinator, "async_setup_streaming", AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True
    await hass.async_block_till_done()
    return client, coordinators[0]


async def test_an_override_stored_before_setup_is_in_force_after_it(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "circuit-2")
    snapshot = _snapshot({"circuit-2": 20.0})

    _, coordinator = await _set_up(hass, entry, snapshot)

    rating = coordinator.breaker_rating_manager.resolve("circuit-2", snapshot)
    assert (rating.user_set, rating.effective_a) == (True, 40)


async def test_an_override_for_a_missing_circuit_survives_until_begin_and_is_then_judged(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """Without a migration the first reconcile removes it; before then nothing is deleted or announced."""
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "old-circuit")
    snapshot = _snapshot({"circuit-2": 20.0})
    events = _events(hass)
    before: list[tuple[int, int]] = []

    def _look(*_: object) -> None:
        manager = coordinators_manager[0]
        before.append((len(manager._overrides), len(events)))

    coordinators_manager: list[BreakerRatingManager] = []
    orig_load = BreakerRatingManager.async_load

    async def _capture(self: BreakerRatingManager) -> None:
        coordinators_manager.append(self)
        await orig_load(self)

    with patch.object(BreakerRatingManager, "async_load", _capture):
        _, coordinator = await _set_up(hass, entry, snapshot, before_begin=_look)

    assert before == [(1, 0)]
    assert coordinator.breaker_rating_manager._overrides == {}
    assert [event.data["circuit_ids"] for event in events] == [["old-circuit"]]


async def test_a_stand_in_rekey_before_begin_keeps_the_override_on_the_new_id(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    _seed(hass_storage, entry, "old-circuit")
    snapshot = _snapshot({"new-circuit": 20.0})
    events = _events(hass)
    managers: list[BreakerRatingManager] = []
    orig_load = BreakerRatingManager.async_load

    async def _capture(self: BreakerRatingManager) -> None:
        managers.append(self)
        await orig_load(self)

    def _migrate(*_: object) -> None:
        overrides = managers[0]._overrides
        overrides["new-circuit"] = overrides.pop("old-circuit")

    with patch.object(BreakerRatingManager, "async_load", _capture):
        _, coordinator = await _set_up(hass, entry, snapshot, before_begin=_migrate)

    rating = coordinator.breaker_rating_manager.resolve("new-circuit", snapshot)
    assert (rating.user_set, rating.effective_a) == (True, 40)
    assert events == []
    assert not is_standing(hass, entry, _NOTICE)
