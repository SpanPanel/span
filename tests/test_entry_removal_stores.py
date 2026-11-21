"""Removing a config entry leaves none of its stores behind.

Every store this integration keeps for one entry is named for the entry's id,
so no other entry ever reads it: left behind, it is a file nothing owns, and
for the stores keyed by wire ids, one whose contents a later entry for the
same panel could mistake for its own. `async_remove_entry` drops each through
its owner. The shared `span_panel_settings` is the one domain-wide file, and
it loses only the removed panel's favorites (`test_stale_favorites`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel.const import DOMAIN, PANEL_SHOW_SIDEBAR

from .captures_replay import capture
from .helpers import seed_panel_settings, stored_panel_settings
from .replayed_panel import Panel, replayed_panels
from .test_device_id_migration import flush_delayed_saves

TREE: Final = "r202645-e"

PER_ENTRY_STORES: Final[Mapping[str, Mapping[str, object]]] = {
    "span_panel_package_ratings": {"packages": {}, "unacknowledged": {}},
    "span_panel_current_monitor": {"enabled": True, "circuit_overrides": {}, "mains_overrides": {}},
    "span_panel_graph_horizon": {
        "global_horizon": "5m",
        "circuit_overrides": {},
        "subdevice_overrides": {},
    },
    f"{DOMAIN}.announced": {"announced_unique_ids": []},
    f"{DOMAIN}.curation": {"records": {}},
    f"{DOMAIN}.notices": {"standing": {}, "announced": {}},
    f"{DOMAIN}.pv_binding": {"circuit_id": None},
}
"""Every store the integration keeps per entry, by the prefix its key carries, with data its loader reads."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


def seed_every_store(hass_storage: dict[str, object], entry_id: str) -> None:
    """Write each per-entry store as its owner would, so each has a file to leave behind."""
    for prefix, data in PER_ENTRY_STORES.items():
        key = f"{prefix}.{entry_id}"
        hass_storage[key] = {"version": 1, "minor_version": 2, "key": key, "data": dict(data)}


def files_of(hass_storage: Mapping[str, object], entry_id: str) -> list[str]:
    """Every stored file of this integration named for the entry."""
    return sorted(
        key for key in hass_storage if key.startswith(DOMAIN) and key.endswith(f".{entry_id}")
    )


async def test_removing_a_set_up_entry_leaves_none_of_its_stores(
    hass: HomeAssistant, hass_storage: dict[str, object], panels: Callable[..., Panel]
) -> None:
    """After a session that wrote to them, every file named for the entry goes with it.

    Fails when the removal leaves any store behind, whatever its owner.
    """
    panel = panels(capture(TREE).tree())
    entry_id = panel.entry.entry_id
    seed_every_store(hass_storage, entry_id)
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: False})
    await panel.set_up(capture(TREE).tree())
    circuit_id = next(iter(panel.snapshot.circuits))
    await panel.call("set_circuit_threshold", circuit_id=circuit_id, spike_threshold_pct=150)
    await panel.call("set_circuit_graph_horizon", circuit_id=circuit_id, horizon="1w")
    flush_delayed_saves(hass)
    await hass.async_block_till_done()
    assert files_of(hass_storage, entry_id) == sorted(
        f"{prefix}.{entry_id}" for prefix in PER_ENTRY_STORES
    )

    await hass.config_entries.async_remove(entry_id)
    flush_delayed_saves(hass)
    await hass.async_block_till_done()

    assert files_of(hass_storage, entry_id) == []
    assert stored_panel_settings(hass_storage) == {PANEL_SHOW_SIDEBAR: False}


async def test_removing_an_entry_this_session_never_set_up_leaves_none_of_its_stores(
    hass: HomeAssistant, hass_storage: dict[str, object], panels: Callable[..., Panel]
) -> None:
    """An entry removed while disabled, or after a setup that failed early, has its files too.

    Nothing of it is loaded: no live notices, no monitor, no manager. Fails when
    an owner drops its store only from what this session loaded.
    """
    panel = panels(capture(TREE).tree())
    entry_id = panel.entry.entry_id
    seed_every_store(hass_storage, entry_id)
    assert panel.entry.state is ConfigEntryState.NOT_LOADED

    await hass.config_entries.async_remove(entry_id)
    await hass.async_block_till_done()

    assert files_of(hass_storage, entry_id) == []
