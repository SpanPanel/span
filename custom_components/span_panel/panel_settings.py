"""The domain's settings file, `span_panel_settings`: its shape, and how it is read.

One file, kept by `frontend` through `async_update_panel_settings`, holds the
sidebar panel's two settings and every panel's favorites. Here is what it
holds and how a value of unknown shape -- the file as loaded, or the options
form's input -- is read into that, with no `Any`. Apart from `frontend` so the
options flow can read the form without importing the frontend's components.
"""

from __future__ import annotations

import logging
from typing import Final, Literal, TypedDict

from .const import PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR
from .json_values import is_list, is_mapping

_LOGGER = logging.getLogger(__name__)

type FavoriteKind = Literal["circuits", "sub_devices"]
"""Which of a panel's favorites a target is: a circuit, or a sub-device."""

FAVORITE_KINDS: Final[tuple[FavoriteKind, ...]] = ("circuits", "sub_devices")


class PanelFavorites(TypedDict):
    """One panel's favorites: circuits by circuit id, sub-devices by device registry id."""

    circuits: list[str]
    sub_devices: list[str]


class PanelSettings(TypedDict, total=False):
    """The domain's settings file, `span_panel_settings`, as it is read and written.

    Not total: a file the options flow never saved holds no sidebar settings,
    and one no favorite was ever saved to holds no favorites. A compile-time
    claim only; `read_panel_settings` is what the disk is held to.
    """

    show_panel: bool
    """Whether the sidebar panel is shown (`PANEL_SHOW_SIDEBAR`)."""

    panel_admin_only: bool
    """Whether only administrators see it (`PANEL_ADMIN_ONLY`)."""

    favorites: dict[str, PanelFavorites]
    """Each panel's favorites, by the panel's device registry id."""


def read_sidebar_settings(value: object) -> PanelSettings:
    """Read the sidebar settings out of `value`, the settings file or the options form's input.

    Each is kept only where it is a boolean, which is all either holds; one of
    another type is left out, and its reader's default applies, rather than a
    stored string such as `"false"` reading as true.
    """
    settings = PanelSettings()
    if not is_mapping(value):
        return settings
    show = value.get(PANEL_SHOW_SIDEBAR)
    if isinstance(show, bool):
        settings[PANEL_SHOW_SIDEBAR] = show
    admin_only = value.get(PANEL_ADMIN_ONLY)
    if isinstance(admin_only, bool):
        settings[PANEL_ADMIN_ONLY] = admin_only
    return settings


def read_panel_settings(stored: object) -> PanelSettings:
    """Read the settings file off disk, keeping what has the shape it is saved in.

    The sidebar settings by `read_sidebar_settings`, and the favorites by
    `normalize_favorites`, which reads the legacy shape too. Every write goes
    through `frontend.async_update_panel_settings`, which writes back what this
    read, so a file in the legacy shape is written in the current one the first
    time anything is written to it.
    """
    settings = read_sidebar_settings(stored)
    if is_mapping(stored) and "favorites" in stored:
        settings["favorites"] = normalize_favorites(stored.get("favorites"))
    return settings


def normalize_favorites(raw: object) -> dict[str, PanelFavorites]:
    """Normalize raw stored favorites into the canonical nested shape.

    Tolerates the legacy ``{panel_id: [uuid, ...]}`` shape (read transparently
    as circuits-only). Logs a warning on dropped/malformed entries so storage
    corruption is visible during debugging.
    """
    result: dict[str, PanelFavorites] = {}
    if not is_mapping(raw):
        return result
    for panel_id, value in raw.items():
        if not isinstance(panel_id, str):
            _LOGGER.warning(
                "span_panel_settings: dropping favorites entry with non-string panel id %r",
                panel_id,
            )
            continue
        # Legacy shape: panel_id maps to a flat list of circuit uuids.
        if is_list(value):
            circuits = _favorite_ids(value)
            if circuits:
                result[panel_id] = {"circuits": circuits, "sub_devices": []}
            continue
        if not is_mapping(value):
            _LOGGER.warning(
                "span_panel_settings: dropping favorites entry %s with unsupported value type %s",
                panel_id,
                type(value).__name__,
            )
            continue
        circuits = _favorite_ids(value.get("circuits", []))
        sub_devices = _favorite_ids(value.get("sub_devices", []))
        if circuits or sub_devices:
            result[panel_id] = {"circuits": circuits, "sub_devices": sub_devices}
    return result


def _favorite_ids(raw: object) -> list[str]:
    """Return the non-empty string ids of a stored list of favorites; none for anything else."""
    return [item for item in raw if isinstance(item, str) and item] if is_list(raw) else []
