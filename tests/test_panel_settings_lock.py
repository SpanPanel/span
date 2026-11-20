"""Every writer of the shared settings file reads, changes and saves it under one lock.

`span_panel_settings` holds every panel's favorites and the sidebar settings in
one file. A writer loads the whole file, changes its part and saves the whole
file again, so two writers interleaved at their awaits would each save what
they read and the first save would be lost. Favorites, the options flow and the
device-id migration's rewrite of favorites all write through
`frontend.async_update_panel_settings`, which holds the lock across the three
steps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextlib
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel import frontend
from custom_components.span_panel.const import DOMAIN, PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR
from custom_components.span_panel.frontend import (
    async_rekey_favorite_circuits,
    async_set_favorite,
)

from .helpers import seed_panel_settings, shown_values, stored_panel_settings

_HOLD_S = 0.2
"""How long the first reader waits for a second, before deciding none will come."""


@pytest.fixture(autouse=True)
def panel_registration() -> Iterator[AsyncMock]:
    """Stand in for re-registering the sidebar panel, which saving the options does."""
    with patch(
        "custom_components.span_panel.async_apply_panel_registration", new_callable=AsyncMock
    ) as apply:
        yield apply


@contextlib.contextmanager
def readers_meet_between_read_and_save() -> Iterator[None]:
    """Hold the first reader of the file, after it has read, until a second reader has read too.

    Without the lock the second reads what the first is about to overwrite;
    with it, the second cannot read until the first has saved, so the first is
    held only for `_HOLD_S` and then carries on. Patched where both the module
    and the package expose the reader, so a writer that reads through either
    is held.
    """
    original = frontend.async_load_panel_settings
    second_read = asyncio.Event()
    reads = 0

    async def meeting_load(hass: HomeAssistant) -> dict[str, Any]:
        nonlocal reads
        reads += 1
        mine = reads
        settings = await original(hass)
        if mine == 1:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(second_read.wait(), _HOLD_S)
        else:
            second_read.set()
        return settings

    with (
        patch.object(frontend, "async_load_panel_settings", meeting_load),
        patch("custom_components.span_panel.async_load_panel_settings", meeting_load),
    ):
        yield


async def test_an_options_save_and_a_favorite_add_interleaved_both_survive(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The favorite and the sidebar change are both in the file after both writers ran.

    Fails when the options flow reads and saves the file without the lock.
    """
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: True, PANEL_ADMIN_ONLY: False})
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={}, entry_id="entry-settings-lock")
    entry.add_to_hass(hass)
    opened = await hass.config_entries.options.async_init(entry.entry_id)
    schema = opened["data_schema"]
    assert schema is not None
    submitted = shown_values(schema) | {PANEL_ADMIN_ONLY: True}

    with readers_meet_between_read_and_save():
        _, saved = await asyncio.gather(
            async_set_favorite(hass, "panel-device", "circuits", "circuit-5", True),
            hass.config_entries.options.async_configure(opened["flow_id"], user_input=submitted),
        )

    assert saved["type"] is FlowResultType.CREATE_ENTRY
    stored = stored_panel_settings(hass_storage)
    assert stored[PANEL_ADMIN_ONLY] is True
    assert stored["favorites"] == {"panel-device": {"circuits": ["circuit-5"], "sub_devices": []}}


async def test_the_migrations_favorites_rewrite_and_a_favorite_add_both_survive(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The device-id migration writes favorites through the same lock as the services."""
    hass_storage["span_panel_settings"] = {
        "version": 1,
        "key": "span_panel_settings",
        "data": {"favorites": {"panel-device": {"circuits": ["circuit-5"], "sub_devices": []}}},
    }

    with readers_meet_between_read_and_save():
        moved, _ = await asyncio.gather(
            async_rekey_favorite_circuits(hass, "panel-device", {"circuit-5": "p-1-16-5"}),
            async_set_favorite(hass, "panel-device", "circuits", "p-1-16-6", True),
        )

    assert moved == 1
    assert stored_panel_settings(hass_storage)["favorites"] == {
        "panel-device": {"circuits": ["p-1-16-5", "p-1-16-6"], "sub_devices": []}
    }


async def test_a_favorite_already_under_its_new_id_keeps_its_place(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The new id records a favorite made after the update; the old entry is dropped, others stay."""
    hass_storage["span_panel_settings"] = {
        "version": 1,
        "key": "span_panel_settings",
        "data": {
            "favorites": {
                "panel-device": {"circuits": ["circuit-5", "x", "p-1-16-5"], "sub_devices": []},
                "other-panel": {"circuits": ["circuit-5"], "sub_devices": []},
            },
            PANEL_SHOW_SIDEBAR: False,
        },
    }

    assert await async_rekey_favorite_circuits(hass, "panel-device", {"circuit-5": "p-1-16-5"}) == 1

    assert stored_panel_settings(hass_storage) == {
        "favorites": {
            "panel-device": {"circuits": ["x", "p-1-16-5"], "sub_devices": []},
            "other-panel": {"circuits": ["circuit-5"], "sub_devices": []},
        },
        PANEL_SHOW_SIDEBAR: False,
    }
