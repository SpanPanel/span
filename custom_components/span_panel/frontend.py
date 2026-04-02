"""Frontend registration helpers for the Span Panel integration.

Handles sidebar panel registration, Lovelace card JS resource management,
static path serving, and panel settings storage.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR

PANEL_URL = "/span_panel_frontend"
PANEL_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")
CARD_FILENAME = "span-panel-card.js"

_PANEL_SETTINGS_STORAGE_VERSION = 1
_PANEL_SETTINGS_STORAGE_KEY = "span_panel_settings"


def _frontend_file_hash(filename: str) -> str:
    """Compute a short hash of a frontend dist file for cache busting.

    This is a synchronous helper—call via ``await hass.async_add_executor_job``
    to avoid blocking the event loop.
    """
    path = os.path.join(PANEL_FRONTEND_DIR, filename)
    try:
        with open(path, "rb") as fh:  # noqa: ASYNC230
            return hashlib.md5(fh.read(), usedforsecurity=False).hexdigest()[:8]
    except FileNotFoundError:
        return "0"


async def async_load_panel_settings(hass: HomeAssistant) -> dict[str, Any]:
    """Load domain-level panel settings from storage."""
    store: Store[dict[str, Any]] = Store(
        hass, _PANEL_SETTINGS_STORAGE_VERSION, _PANEL_SETTINGS_STORAGE_KEY
    )
    data = await store.async_load()
    return data or {}


async def async_save_panel_settings(hass: HomeAssistant, settings: dict[str, Any]) -> None:
    """Save domain-level panel settings to storage."""
    store: Store[dict[str, Any]] = Store(
        hass, _PANEL_SETTINGS_STORAGE_VERSION, _PANEL_SETTINGS_STORAGE_KEY
    )
    await store.async_save(settings)


async def _async_ensure_lovelace_resource(hass: HomeAssistant, url: str) -> None:
    """Ensure the card JS is registered as a Lovelace resource.

    Reads the lovelace_resources storage directly and adds an entry for our
    card URL if one isn't already present.  This avoids the MIME-type issues
    that occur when serving from /local/ in dev mode.
    """
    store: Store[dict[str, list[dict[str, str]]]] = Store(hass, 1, "lovelace_resources")
    data = await store.async_load()
    if data is None:
        data = {"items": []}

    items: list[dict[str, str]] = data.get("items", [])

    # Strip query strings when comparing so a cache-bust bump doesn't duplicate
    base_url = url.split("?")[0]
    for item in items:
        if item.get("url", "").split("?")[0] == base_url:
            # Update the URL in case the cache tag changed
            if item["url"] != url:
                item["url"] = url
                await store.async_save(data)
            return

    items.append(
        {
            "id": hashlib.md5(base_url.encode(), usedforsecurity=False).hexdigest(),
            "url": url,
            "type": "module",
        }
    )
    data["items"] = items
    await store.async_save(data)


async def async_apply_panel_registration(hass: HomeAssistant) -> None:
    """Register or remove the sidebar panel based on stored settings.

    Uses deferred imports from the parent package so that test patches
    applied to ``custom_components.span_panel.*`` take effect.
    """
    # Deferred imports: tests patch these names on the package namespace
    # (custom_components.span_panel.X). Resolving through ``from .`` at
    # call time ensures patched values are picked up.
    from . import (  # pylint: disable=import-outside-toplevel
        _async_ensure_lovelace_resource as _ensure_lovelace,
        async_load_panel_settings as _load_settings,
        async_register_panel as _register_panel,
        async_remove_panel as _remove_panel,
    )

    settings = await _load_settings(hass)
    show = settings.get(PANEL_SHOW_SIDEBAR, True)
    admin_only = settings.get(PANEL_ADMIN_ONLY, False)

    # Always register static paths so the card JS is reachable even when
    # the sidebar panel is hidden.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_URL, PANEL_FRONTEND_DIR, cache_headers=True)]
    )

    # Auto-register the Lovelace card as a resource so users don't need a
    # manual entry (also avoids MIME-type issues with /local/ in dev mode).
    card_cache_tag = await hass.async_add_executor_job(_frontend_file_hash, CARD_FILENAME)
    card_url = f"{PANEL_URL}/{CARD_FILENAME}?v={card_cache_tag}"
    await _ensure_lovelace(hass, card_url)

    if show:
        # Remove first to allow re-registration with updated require_admin
        _remove_panel(hass, "span-panel", warn_if_unknown=False)
        cache_tag = await hass.async_add_executor_job(_frontend_file_hash, "span-panel.js")
        await _register_panel(
            hass,
            webcomponent_name="span-panel",
            frontend_url_path="span-panel",
            sidebar_title="Span Panel",
            sidebar_icon="mdi:lightning-bolt",
            module_url=f"{PANEL_URL}/span-panel.js?v={cache_tag}",
            require_admin=admin_only,
            config={},
        )
    else:
        _remove_panel(hass, "span-panel", warn_if_unknown=False)
