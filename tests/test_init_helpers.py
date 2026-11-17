"""Tests for Span Panel helper functions in __init__.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    async_remove_config_entry_device,
    async_unload_entry,
    ensure_device_registered,
    update_listener,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.entry_reloads import async_setup_began
from custom_components.span_panel.util import snapshot_to_device_info
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr

from .factories import SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device

from pytest_homeassistant.common import MockConfigEntry


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
    entry.add_to_hass(hass)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, snapshot.serial_number),
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
    entry.add_to_hass(hass)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, snapshot.serial_number),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    device = MagicMock()
    device.identifiers = {(DOMAIN, "sp3-main-001_evse")}

    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_update_listener_asks_for_a_reload_that_starts_after_it_returns(
    hass: HomeAssistant,
) -> None:
    """A change reloads the entry once Home Assistant is running, never inside the write."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.running)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        mock_reload.assert_not_awaited()
        await hass.async_block_till_done()

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


async def test_a_setup_that_begins_before_start_applies_the_deferred_reload(
    hass: HomeAssistant,
) -> None:
    """Any setup satisfies the requests made before it began, so the deferred one finds nothing."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.starting)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        async_setup_began(hass, entry)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    mock_reload.assert_not_awaited()


async def test_an_update_after_the_deferred_reload_ran_defers_again(hass: HomeAssistant) -> None:
    """Once a reload has run, a later start-up update gets its own."""
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


async def test_an_unexpected_reload_failure_is_logged_and_can_be_asked_for_again(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected reload error is logged with its traceback, and the next change still reloads."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-123", title="Span Panel 1")
    entry.add_to_hass(hass)
    hass.set_state(CoreState.running)
    caplog.set_level("ERROR")

    with patch.object(
        hass.config_entries,
        "async_reload",
        AsyncMock(side_effect=[RuntimeError("reload boom"), True]),
    ) as mock_reload:
        await update_listener(hass, entry)
        await hass.async_block_till_done()
        assert "Reloading Span Panel 1 failed" in caplog.text
        assert "reload boom" in caplog.text

        await update_listener(hass, entry)
        await hass.async_block_till_done()

    assert mock_reload.await_count == 2


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
    """A missing panel device is created with every field of the snapshot's device info."""
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
    info = snapshot_to_device_info(snapshot, "SPAN Panel", host="192.168.1.21")
    assert {
        "identifiers": created.identifiers,
        "manufacturer": created.manufacturer,
        "model": created.model,
        "name": created.name,
        "sw_version": created.sw_version,
        "hw_version": created.hw_version,
        "configuration_url": created.configuration_url,
    } == info.as_dict()
    assert info.name == "SPAN Panel"


def _loaded_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, MagicMock]:
    """Add an entry whose runtime data holds a stand-in coordinator, and return both."""
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry-789")
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create()
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, snapshot.serial_number),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    return entry, coordinator


async def test_async_unload_entry_shuts_down_runtime_data(
    hass: HomeAssistant,
) -> None:
    """Unload stops the current monitor and the coordinator once the platforms have unloaded."""
    entry, coordinator = _loaded_entry(hass)

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    ) as mock_unload:
        await async_unload_entry(hass, entry)

    mock_unload.assert_awaited_once()
    coordinator.current_monitor.async_stop.assert_called_once_with()
    coordinator.async_shutdown.assert_awaited_once()


async def test_an_unload_whose_platforms_fail_raises_and_leaves_the_coordinator_running(
    hass: HomeAssistant,
) -> None:
    """A platform that fails to unload raises Home Assistant's own error, and nothing is torn down.

    Whatever entities the platform did not remove are still in place, so the
    coordinator and the current monitor must stay up under them.
    """
    entry, coordinator = _loaded_entry(hass)

    with (
        patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)),
        pytest.raises(ConfigEntryError) as raised,
    ):
        await async_unload_entry(hass, entry)

    assert (raised.value.translation_domain, raised.value.translation_key) == (
        HOMEASSISTANT_DOMAIN,
        "unload_platforms_failed",
    )
    coordinator.current_monitor.async_stop.assert_not_called()
    coordinator.async_shutdown.assert_not_awaited()


async def test_home_assistant_reports_a_failed_platform_unload_in_its_own_words(
    hass: HomeAssistant,
) -> None:
    """Home Assistant marks the entry failed to unload, with the reason the raise carries.

    Through Home Assistant's own unload, so the translated reason the user sees
    is shown to come from the error raised, not from a bare False it would
    report generically. The runtime data stays, since only a successful unload
    deletes it.
    """
    entry, coordinator = _loaded_entry(hass)
    runtime_data = entry.runtime_data
    entry.mock_state(hass, ConfigEntryState.LOADED)

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    ):
        assert await hass.config_entries.async_unload(entry.entry_id) is False

    assert entry.state is ConfigEntryState.FAILED_UNLOAD
    assert (entry.error_reason_translation_domain, entry.error_reason_translation_key) == (
        HOMEASSISTANT_DOMAIN,
        "unload_platforms_failed",
    )
    assert entry.runtime_data is runtime_data
    coordinator.async_shutdown.assert_not_awaited()
