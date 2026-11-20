"""A panel's favorites go with its config entry, and favorites left behind by an earlier removal are swept.

Favorites live in the domain's settings file, `span_panel_settings`, keyed by
each panel's device registry id, and the file outlives every entry. A removed
entry takes its panel's favorites with it (`async_remove_entry`), and the
domain's setup sweeps out favorites under a device id the device registry no
longer holds at all, which is what an entry removed by an earlier release left.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Final
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel.const import DOMAIN, PANEL_SHOW_SIDEBAR
from custom_components.span_panel.frontend import (
    async_drop_orphaned_favorites,
    async_get_favorites,
)

from .captures_replay import capture
from .helpers import (
    add_panel_entry,
    register_panel_device,
    seed_panel_settings,
    stored_panel_settings,
)
from .replayed_panel import Panel, replayed_panels

TREE: Final = "r202645-e"
RANGE_NEW_ID_SUFFIX: Final = "-1-16-5"
"""`Electric range`, a circuit of `r202645-e`, by the trait suffix its new id ends in."""

STALE: Final = "0123456789abcdef0123456789abcdef"
"""A device registry id no device holds: a panel removed by an earlier release."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


def favorites_on_disk(hass_storage: dict[str, object]) -> object:
    """Return the favorites part of the settings file as last written."""
    return stored_panel_settings(hass_storage).get("favorites")


def range_circuit(panel: Panel) -> str:
    """Return the id `r202645-e` gives `Electric range`."""
    (circuit_id,) = (cid for cid in panel.snapshot.circuits if cid.endswith(RANGE_NEW_ID_SUFFIX))
    return circuit_id


async def test_removing_an_entry_drops_its_panels_favorites(
    hass: HomeAssistant, hass_storage: dict[str, object], panels: Callable[..., Panel]
) -> None:
    """The removed panel's favorites go, another panel's stay, and re-adding the panel finds none.

    Re-adding restores the panel's device, its registry id included, from the
    tombstone the removal left, so favorites left under that id would come back
    with it. Fails when the removal leaves them, or looks the panel's device up
    through runtime data the unload has already dropped.
    """
    panel = panels(capture(TREE).tree())
    await panel.set_up(capture(TREE).tree())
    panel_device_id = panel.runtime.panel_device_id
    await hass.services.async_call(
        DOMAIN,
        "add_favorite",
        {"entity_id": panel.power(range_circuit(panel))},
        blocking=True,
        return_response=True,
    )
    other = add_panel_entry(hass, "entry-other-panel", "nj-0000-00001")
    other_panel = register_panel_device(hass, other, "nj-0000-00001")
    seed = stored_panel_settings(hass_storage)
    favorites = seed["favorites"]
    assert isinstance(favorites, dict) and panel_device_id in favorites
    favorites[other_panel] = {"circuits": ["circuit-9"], "sub_devices": []}

    await hass.config_entries.async_remove(panel.entry.entry_id)
    await hass.async_block_till_done()

    assert favorites_on_disk(hass_storage) == {
        other_panel: {"circuits": ["circuit-9"], "sub_devices": []}
    }

    again = panels(capture(TREE).tree(), entry_id="entry-device-ids-again")
    await again.set_up(capture(TREE).tree())
    assert again.runtime.panel_device_id == panel_device_id, "restored from its tombstone"
    assert panel_device_id not in await async_get_favorites(hass)


async def test_the_sweep_drops_only_favorites_whose_device_is_gone(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """Favorites under a device id no device holds go, in either stored shape; every other part stays.

    A panel's device whose entry is disabled, or not set up, is in the device
    registry and keeps its favorites, in the shape they were stored in as far
    as the reader is concerned: the legacy flat list still reads as circuits.
    Fails when the sweep asks whether the entry is loaded, or drops what the
    legacy shape holds.
    """
    disabled = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-disabled",
        unique_id="nj-0000-00002",
        disabled_by=ConfigEntryDisabler.USER,
    )
    disabled.add_to_hass(hass)
    disabled_panel = register_panel_device(hass, disabled, "nj-0000-00002")
    idle = add_panel_entry(hass, "entry-not-loaded", "nj-0000-00003")
    idle_panel = register_panel_device(hass, idle, "nj-0000-00003")
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: False})
    stored_panel_settings(hass_storage)["favorites"] = {
        STALE: {"circuits": ["circuit-1"], "sub_devices": ["device-1"]},
        f"{STALE}-legacy": ["circuit-2"],
        disabled_panel: {"circuits": ["circuit-3"], "sub_devices": []},
        idle_panel: ["circuit-4"],
    }

    await async_drop_orphaned_favorites(hass)

    assert stored_panel_settings(hass_storage)[PANEL_SHOW_SIDEBAR] is False
    assert await async_get_favorites(hass) == {
        disabled_panel: {"circuits": ["circuit-3"], "sub_devices": []},
        idle_panel: {"circuits": ["circuit-4"], "sub_devices": []},
    }


async def test_a_clean_file_is_read_and_not_written(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """With every key a registered device, the sweep writes nothing, and a legacy file stays as it was.

    Fails when the sweep writes whatever it finds.
    """
    entry = add_panel_entry(hass, "entry-clean", "nj-0000-00004")
    panel = register_panel_device(hass, entry, "nj-0000-00004")
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: True})
    stored_panel_settings(hass_storage)["favorites"] = {panel: ["circuit-5"]}

    with patch.object(Store, "async_save", autospec=True) as saved:
        await async_drop_orphaned_favorites(hass)

    saved.assert_not_called()
    assert favorites_on_disk(hass_storage) == {panel: ["circuit-5"]}
    assert await async_get_favorites(hass) == {
        panel: {"circuits": ["circuit-5"], "sub_devices": []}
    }


async def test_the_domains_setup_sweeps_before_anything_can_read_favorites(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The sweep runs first in the domain's setup: before its services, and so before any entry.

    Fails when the domain's setup does not sweep, or sweeps after registering
    the services that read favorites.
    """
    seed_panel_settings(hass_storage, {})
    stored_panel_settings(hass_storage)["favorites"] = {STALE: ["circuit-6"]}
    registered_at_sweep: list[bool] = []
    sweep = async_drop_orphaned_favorites

    async def recording_sweep(sweep_hass: HomeAssistant) -> None:
        registered_at_sweep.append(sweep_hass.services.has_service(DOMAIN, "get_favorites"))
        await sweep(sweep_hass)

    with (
        patch("custom_components.span_panel.async_drop_orphaned_favorites", recording_sweep),
        patch("custom_components.span_panel.async_register_frontend_assets", AsyncMock()),
        patch("custom_components.span_panel.async_apply_panel_registration", AsyncMock()),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    assert registered_at_sweep == [False]
    response = await hass.services.async_call(
        DOMAIN, "get_favorites", {}, blocking=True, return_response=True
    )
    assert response == {"favorites": {}}
    assert favorites_on_disk(hass_storage) == {}
