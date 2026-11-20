"""The settings file is read through one reader, which holds the disk and the options form to its shape.

`span_panel_settings` holds the sidebar panel's two settings and every panel's
favorites. `panel_settings.read_panel_settings` reads it, keeping each part of
the shape it is saved in, and every write goes back through
`frontend.async_update_panel_settings`, so what is written is what was read.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.span_panel.const import PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR
from custom_components.span_panel.frontend import (
    async_load_panel_settings,
    async_update_panel_settings,
)
from custom_components.span_panel.panel_settings import (
    PanelSettings,
    read_panel_settings,
    read_sidebar_settings,
)

from .helpers import seed_panel_settings, stored_panel_settings


def test_a_sidebar_setting_is_kept_only_as_a_boolean() -> None:
    """A stored string `"false"` would read as true; left out, the reader's default applies.

    The same reader takes the options form's input, which holds every other
    option besides: only the two sidebar settings are kept from it.
    """
    assert read_sidebar_settings({PANEL_SHOW_SIDEBAR: "false", PANEL_ADMIN_ONLY: 1}) == {}
    assert read_sidebar_settings(
        {PANEL_SHOW_SIDEBAR: False, PANEL_ADMIN_ONLY: True, "snapshot_update_interval": 5}
    ) == {PANEL_SHOW_SIDEBAR: False, PANEL_ADMIN_ONLY: True}
    assert read_sidebar_settings(["show_panel"]) == {}


def test_the_file_is_read_in_the_shape_it_is_saved_in() -> None:
    """Favorites in either stored shape read as the current one; a malformed panel entry is left out."""
    assert read_panel_settings(
        {
            PANEL_SHOW_SIDEBAR: True,
            "favorites": {
                "panel-a": ["circuit-1", "", 7],
                "panel-b": {"circuits": ["circuit-2"], "sub_devices": ["device-1"]},
                "panel-c": "circuit-3",
                "panel-d": {"circuits": "circuit-4"},
            },
            "something-else": 1,
        }
    ) == {
        PANEL_SHOW_SIDEBAR: True,
        "favorites": {
            "panel-a": {"circuits": ["circuit-1"], "sub_devices": []},
            "panel-b": {"circuits": ["circuit-2"], "sub_devices": ["device-1"]},
        },
    }
    assert read_panel_settings(None) == {}
    assert read_panel_settings({"favorites": None}) == {"favorites": {}}


async def test_a_write_keeps_what_it_did_not_change(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """An options save writes the sidebar settings over the file, and the favorites stay, in the current shape.

    Fails when a writer is handed less than the reader keeps, or the reader
    keeps a part the writer then writes back unread.
    """
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: True})
    stored_panel_settings(hass_storage)["favorites"] = {"panel-a": ["circuit-1"]}
    stored_panel_settings(hass_storage)["something-else"] = 1

    def _merge(settings: PanelSettings) -> bool:
        settings.update({PANEL_SHOW_SIDEBAR: False, PANEL_ADMIN_ONLY: True})
        return True

    await async_update_panel_settings(hass, _merge)

    assert stored_panel_settings(hass_storage) == {
        PANEL_SHOW_SIDEBAR: False,
        PANEL_ADMIN_ONLY: True,
        "favorites": {"panel-a": {"circuits": ["circuit-1"], "sub_devices": []}},
    }
    assert await async_load_panel_settings(hass) == stored_panel_settings(hass_storage)
