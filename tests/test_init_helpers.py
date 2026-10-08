"""Tests for Span Panel helper functions in __init__.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    _defer_reload_until_started,
    async_remove_config_entry_device,
    async_unload_entry,
    ensure_device_registered,
    update_listener,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr

from .factories import SpanPanelSnapshotFactory, pv_binding_for

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_async_remove_config_entry_device_allows_removal_without_runtime_data(
    hass: HomeAssistant,
) -> None:
    """Entries without runtime data should not block device removal."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    device = MagicMock()

    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_async_remove_config_entry_device_rejects_main_panel_device(
    hass: HomeAssistant,
) -> None:
    """The main panel device cannot be manually removed."""
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-main-001")
    coordinator = MagicMock()
    coordinator.data = snapshot
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    device = MagicMock()
    device.identifiers = {(DOMAIN, "sp3-main-001")}

    assert await async_remove_config_entry_device(hass, entry, device) is False


async def test_async_remove_config_entry_device_allows_subdevice_removal(
    hass: HomeAssistant,
) -> None:
    """Sub-devices should remain removable when runtime data exists."""
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-main-001")
    coordinator = MagicMock()
    coordinator.data = snapshot
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    device = MagicMock()
    device.identifiers = {(DOMAIN, "sp3-main-001_evse")}

    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_update_listener_reloads_running_entry(hass: HomeAssistant) -> None:
    """Options updates should reload the entry once Home Assistant is running."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    hass.state = CoreState.running

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)

    mock_reload.assert_awaited_once_with("entry-123")


async def test_update_listener_defers_the_reload_until_home_assistant_has_started(
    hass: HomeAssistant,
) -> None:
    """An update while Home Assistant is starting reloads once it has started, not never."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.starting)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        await hass.async_block_till_done()
        mock_reload.assert_not_awaited()

        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    mock_reload.assert_awaited_once_with("entry-123")


async def test_updates_while_starting_coalesce_into_one_reload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.starting)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        await update_listener(hass, entry)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    mock_reload.assert_awaited_once_with("entry-123")


async def test_each_entry_updated_while_starting_reloads_once_started(hass: HomeAssistant) -> None:
    """Two panels changed during start-up each get their own reload; neither absorbs the other's."""
    first = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-first")
    second = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    hass.set_state(CoreState.starting)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, first)
        await update_listener(hass, second)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    assert sorted(call.args for call in mock_reload.await_args_list) == [("entry-first",), ("entry-second",)]


async def test_a_deferral_made_while_running_reloads_now_and_leaves_nothing_pending(
    hass: HomeAssistant,
) -> None:
    """`async_at_started` runs the job at once when started, before the helper has stored anything.

    Unreachable through `update_listener`, whose guard is the same predicate; pinned so
    the bookkeeping cannot leave a stale handle that blocks every later deferral.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.running)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        _defer_reload_until_started(hass, entry)
        await hass.async_block_till_done()
        assert mock_reload.await_count == 1

        hass.set_state(CoreState.starting)
        await update_listener(hass, entry)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    assert mock_reload.await_count == 2


async def test_unloading_before_start_cancels_the_deferred_reload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.starting)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        await entry._async_process_on_unload(hass)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    mock_reload.assert_not_awaited()


async def test_an_update_after_the_deferred_reload_ran_defers_again(hass: HomeAssistant) -> None:
    """The deferral is one-shot: once it has run, a later start-up update gets its own."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.starting)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

        hass.set_state(CoreState.starting)
        await update_listener(hass, entry)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    assert mock_reload.await_count == 2


async def test_update_listener_propagates_cancelled_error(hass: HomeAssistant) -> None:
    """Cancellation should not be swallowed by the update listener."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    hass.state = CoreState.running

    with (
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await update_listener(hass, entry)


async def test_update_listener_logs_unexpected_reload_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Unexpected reload errors should be logged for diagnosis."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    hass.state = CoreState.running
    caplog.set_level("ERROR")

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(side_effect=RuntimeError("reload boom")),
    ):
        await update_listener(hass, entry)

    assert "Failed to reload SPAN Panel integration: reload boom" in caplog.text


async def test_ensure_device_registered_renames_placeholder_device(
    hass: HomeAssistant,
) -> None:
    """An existing placeholder device name should be replaced with the panel name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.20"},
        entry_id="entry-123",
        unique_id="sp3-rename-001",
    )
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-rename-001")
    device_registry = dr.async_get(hass)
    existing = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-rename-001")},
        name="sp3-rename-001",
    )

    await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")

    updated = device_registry.async_get(existing.id)
    assert updated is not None
    assert updated.name == "SPAN Panel"


async def test_ensure_device_registered_creates_missing_device(
    hass: HomeAssistant,
) -> None:
    """A missing panel device should be created from the snapshot."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.21"},
        entry_id="entry-456",
        unique_id="sp3-create-001",
    )
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-create-001")
    device_registry = dr.async_get(hass)

    await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")

    created = device_registry.async_get_device_by_identifier(
        (DOMAIN, "sp3-create-001"), entry.entry_id
    )
    assert created is not None
    assert created.name == "SPAN Panel"


async def test_async_unload_entry_shuts_down_runtime_data(
    hass: HomeAssistant,
) -> None:
    """Unload should stop the coordinator before unloading platforms."""
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-789")
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SpanPanelSnapshotFactory.create()),
        setup_snapshot=SpanPanelSnapshotFactory.create(),
    )

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    ) as mock_unload:
        assert await async_unload_entry(hass, entry) is True

    coordinator.async_shutdown.assert_awaited_once()
    mock_unload.assert_awaited_once()
