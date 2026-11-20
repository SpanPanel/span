"""The monitoring store is read through one reader, which holds the disk to what is saved there.

`CurrentMonitor` saves a panel's overrides, its mains overrides, its global
settings and whether monitoring is on in one store, and three paths read it
back: the monitor's load, the setup's check of whether monitoring was on, and
the device-id migration's re-key. Each goes through `_read_stored`, which
keeps every part of the shape it is saved in and drops, with a WARNING, a part
that is not.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.current_monitor import CurrentMonitor, async_rekey_circuit_ids
from custom_components.span_panel.options import (
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from custom_components.span_panel.package_rating_manager import PackageRatingManager

_KEY: Final = "span_panel_current_monitor"
_MONITOR_LOGGER: Final = "custom_components.span_panel.current_monitor"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-monitoring-store")
    entry.add_to_hass(hass)
    return entry


def _seed(hass_storage: dict[str, object], entry: MockConfigEntry, data: object) -> None:
    hass_storage[f"{_KEY}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{_KEY}.{entry.entry_id}",
        "data": data,
    }


def _stored(hass_storage: dict[str, object], entry: MockConfigEntry) -> object:
    stored = hass_storage[f"{_KEY}.{entry.entry_id}"]
    assert isinstance(stored, dict)
    return stored["data"]


def _monitor(hass: HomeAssistant, entry: MockConfigEntry) -> CurrentMonitor:
    return CurrentMonitor(hass, entry, PackageRatingManager(hass, entry))


_MALFORMED: Final = {
    "enabled": "yes",
    "circuit_overrides": {
        "circuit-1": {SPIKE_THRESHOLD_PCT: 120, "monitoring_enabled": False, "note": [1, 2]},
        "circuit-2": "off",
    },
    "mains_overrides": ["upstream_l1"],
    "global": {WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"},
}
"""A store of valid JSON and the wrong shape in four places, around parts of the right one."""


async def test_what_the_monitor_saves_it_reads_back(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """A circuit override, a mains override and a global setting survive a save and a load."""
    entry = _entry(hass)
    monitor = _monitor(hass, entry)
    monitor.set_circuit_override("circuit-1", {SPIKE_THRESHOLD_PCT: 120})
    monitor.set_mains_override("upstream_l1", {SPIKE_THRESHOLD_PCT: 90})
    monitor.set_global_settings({WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"})
    await hass.async_block_till_done()

    reloaded = _monitor(hass, entry)
    await reloaded.async_load_overrides()

    assert _stored(hass_storage, entry) == {
        "enabled": True,
        "circuit_overrides": {"circuit-1": {SPIKE_THRESHOLD_PCT: 120}},
        "mains_overrides": {"upstream_l1": {SPIKE_THRESHOLD_PCT: 90}},
        "global": {WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"},
    }
    assert reloaded.get_global_settings()[WINDOW_DURATION_M] == 30
    assert await CurrentMonitor.async_is_enabled(hass, entry)
    await reloaded.async_save_overrides()
    assert _stored(hass_storage, entry) == {
        "enabled": True,
        "circuit_overrides": {"circuit-1": {SPIKE_THRESHOLD_PCT: 120}},
        "mains_overrides": {"upstream_l1": {SPIKE_THRESHOLD_PCT: 90}},
        "global": {WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"},
    }


async def test_a_part_of_the_wrong_shape_is_dropped_and_the_rest_kept(
    hass: HomeAssistant, hass_storage: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    """Each part is read on its own: what has the saved shape loads, and each part that has not is named.

    A value of the wrong type inside an override goes alone, so the rest of the
    override applies. Fails when the reader trusts the disk, or refuses the
    whole store for one bad part.
    """
    entry = _entry(hass)
    _seed(hass_storage, entry, _MALFORMED)
    monitor = _monitor(hass, entry)

    await monitor.async_load_overrides()
    assert not await CurrentMonitor.async_is_enabled(hass, entry)
    # What the monitor holds now, as it saves it.
    await monitor.async_save_overrides()

    assert _stored(hass_storage, entry) == {
        "enabled": True,
        "circuit_overrides": {"circuit-1": {SPIKE_THRESHOLD_PCT: 120, "monitoring_enabled": False}},
        "mains_overrides": {},
        "global": {WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"},
    }
    dropped = [
        record.getMessage()
        for record in caplog.records
        if record.name == _MONITOR_LOGGER and record.levelno == logging.WARNING
    ]
    for part in ("enabled", "circuit_overrides.circuit-2", "mains_overrides"):
        assert any(f"Ignoring {part} " in line for line in dropped), (part, dropped)


@pytest.mark.parametrize("stored", [["circuit-1"], "monitoring", 3])
async def test_a_store_that_is_not_an_object_reads_as_none(
    hass: HomeAssistant, hass_storage: dict[str, object], stored: object
) -> None:
    """Nothing to keep: the monitor starts on its defaults, and monitoring reads as off."""
    entry = _entry(hass)
    _seed(hass_storage, entry, stored)
    monitor = _monitor(hass, entry)

    await monitor.async_load_overrides()

    assert not await CurrentMonitor.async_is_enabled(hass, entry)
    assert await async_rekey_circuit_ids(hass, entry, {"circuit-1": "p-1-16-1"}) == 0
    assert _stored(hass_storage, entry) == stored
    await monitor.async_save_overrides()
    assert _stored(hass_storage, entry) == {
        "enabled": True,
        "circuit_overrides": {},
        "mains_overrides": {},
    }


async def test_switching_monitoring_off_keeps_the_settings(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """`async_save_disabled` writes `enabled` alone over what the reader keeps, and nothing else changes."""
    entry = _entry(hass)
    _seed(hass_storage, entry, _MALFORMED)

    await _monitor(hass, entry).async_save_disabled()

    assert _stored(hass_storage, entry) == {
        "enabled": False,
        "circuit_overrides": {"circuit-1": {SPIKE_THRESHOLD_PCT: 120, "monitoring_enabled": False}},
        "global": {WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"},
    }


async def test_the_re_key_reads_through_the_same_reader(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The device-id migration moves the overrides it reads, and writes back what the monitor would load.

    Fails when the re-key reads the disk its own way, so a part of the wrong
    shape could crash it or be written back.
    """
    entry = _entry(hass)
    _seed(hass_storage, entry, _MALFORMED)

    moved = await async_rekey_circuit_ids(hass, entry, {"circuit-1": "p-1-16-1"})

    assert moved == 1
    assert _stored(hass_storage, entry) == {
        "circuit_overrides": {"p-1-16-1": {SPIKE_THRESHOLD_PCT: 120, "monitoring_enabled": False}},
        "global": {WINDOW_DURATION_M: 30, NOTIFY_TARGETS: "notify.phone"},
    }
