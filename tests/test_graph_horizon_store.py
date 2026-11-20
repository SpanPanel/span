"""The graph-horizon store is read through one reader, by the manager's load and by the device-id re-key.

`GraphHorizonManager` saves a panel's global horizon and its circuit and
sub-device overrides in one store. `_read_stored` keeps each valid horizon and
drops anything else, so the two readers agree on what the store holds and
the re-key writes back exactly what the manager would load.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel.const import DEFAULT_GRAPH_HORIZON, DOMAIN
from custom_components.span_panel.graph_horizon import (
    GraphHorizonManager,
    async_rekey_circuit_ids,
)

_KEY: Final = "span_panel_graph_horizon"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-graph-horizon-store")
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


_MALFORMED: Final = {
    "global_horizon": "2y",
    "circuit_overrides": {"circuit-1": "1M", "circuit-2": "forever", "circuit-3": 5},
    "subdevice_overrides": ["device-1"],
}
"""Valid JSON with an unknown global horizon, two bad circuit horizons and no sub-device map."""


async def test_what_the_manager_saves_it_reads_back(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The global horizon and both kinds of override survive a save and a load."""
    entry = _entry(hass)
    manager = GraphHorizonManager(hass, entry)
    manager.set_global_horizon("1d")
    manager.set_circuit_horizon("circuit-1", "1M")
    manager.set_subdevice_horizon("device-1", "1h")
    await hass.async_block_till_done()

    reloaded = GraphHorizonManager(hass, entry)
    await reloaded.async_load()

    assert _stored(hass_storage, entry) == {
        "global_horizon": "1d",
        "circuit_overrides": {"circuit-1": "1M"},
        "subdevice_overrides": {"device-1": "1h"},
    }
    assert reloaded.get_all_settings() == manager.get_all_settings()


async def test_only_valid_horizons_are_read(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """An unknown global horizon reads as the default; a bad override, or a map that is not one, is dropped.

    Fails when the reader trusts the disk.
    """
    entry = _entry(hass)
    _seed(hass_storage, entry, _MALFORMED)
    manager = GraphHorizonManager(hass, entry)

    await manager.async_load()

    assert manager.get_all_settings() == {
        "global_horizon": DEFAULT_GRAPH_HORIZON,
        "circuits": {"circuit-1": {"horizon": "1M", "has_override": True}},
        "sub_devices": {},
    }


@pytest.mark.parametrize("stored", [["circuit-1"], "1d", 3])
async def test_a_store_that_is_not_an_object_reads_as_none(
    hass: HomeAssistant, hass_storage: dict[str, object], stored: object
) -> None:
    """The manager keeps its defaults, and the re-key moves nothing and writes nothing."""
    entry = _entry(hass)
    _seed(hass_storage, entry, stored)
    manager = GraphHorizonManager(hass, entry)

    await manager.async_load()

    assert manager.get_all_settings() == {
        "global_horizon": DEFAULT_GRAPH_HORIZON,
        "circuits": {},
        "sub_devices": {},
    }
    assert await async_rekey_circuit_ids(hass, entry, {"circuit-1": "p-1-16-1"}) == 0
    assert _stored(hass_storage, entry) == stored


async def test_the_re_key_reads_through_the_same_reader(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The device-id migration moves the horizons it reads, and writes back what the manager would load.

    Fails when the re-key reads the disk its own way.
    """
    entry = _entry(hass)
    _seed(hass_storage, entry, _MALFORMED)

    moved = await async_rekey_circuit_ids(hass, entry, {"circuit-1": "p-1-16-1"})

    assert moved == 1
    assert _stored(hass_storage, entry) == {
        "global_horizon": DEFAULT_GRAPH_HORIZON,
        "circuit_overrides": {"p-1-16-1": "1M"},
        "subdevice_overrides": {},
    }
