"""Serving the frontend, loading the card on every dashboard, and the sidebar panel.

The frontend is set up for real, and with it http and Lovelace, so the static
path lands in the real router, the card's module URL in the frontend's real set
of extra modules, and the resource earlier versions wrote is read and deleted
through Lovelace's real resource collection over the harness's storage.

Only the sidebar panel's registration is stood in for. The harness replaces
`homeassistant.components.frontend`, which panel_custom registers through, for
the whole session, so a real registration would land wherever import order
happened to leave it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import copy
import hashlib
import logging
from pathlib import Path
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.web_urldispatcher import StaticResource
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.components.http.static import CACHE_HEADER, CachingStaticResource
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import (
    RESOURCE_STORAGE_KEY,
    ResourceStorageCollection,
    ResourceYAMLCollection,
)
from homeassistant.components.lovelace.websocket import websocket_lovelace_resources_impl
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.collection import CHANGE_REMOVED
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.span_panel.const import DOMAIN, PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR
from custom_components.span_panel.frontend import CARD_FILENAME, PANEL_FRONTEND_DIR, PANEL_URL

from .helpers import shown_values

_FRONTEND_LOGGER = "custom_components.span_panel.frontend"

_LEGACY_TAGGED = {
    "id": "legacy-tagged",
    "url": f"{PANEL_URL}/{CARD_FILENAME}?v=0a1b2c3d",
    "type": "module",
}
"""The resource earlier versions wrote, carrying the cache tag of the bundle they shipped."""

_LEGACY_BARE = {"id": "legacy-bare", "url": f"{PANEL_URL}/{CARD_FILENAME}", "type": "module"}
"""The same resource without a query."""

_UNRELATED = {"id": "unrelated", "url": "/local/another-card.js", "type": "module"}
"""A resource for some other card."""

_HACS_CARD = {"id": "hacs-card", "url": "/hacsfiles/span-card/span-panel-card.js", "type": "module"}
"""The same card installed from HACS: the user's resource, not this integration's."""


class _SidebarPanel(NamedTuple):
    """The stand-ins for registering and removing the sidebar panel."""

    register: AsyncMock
    remove: MagicMock


@pytest.fixture
def sidebar_panel() -> Iterator[_SidebarPanel]:
    """Stand in for the sidebar panel's registration on the package namespace."""
    with (
        patch(
            "custom_components.span_panel.async_register_panel", new_callable=AsyncMock
        ) as register,
        patch("custom_components.span_panel.async_remove_panel") as remove,
    ):
        yield _SidebarPanel(register, remove)


async def _set_up_frontend(
    hass: HomeAssistant, lovelace: dict[str, Any] | None = None
) -> list[tuple[str, str]]:
    """Set the frontend up for real, and record each change made to its extra modules.

    Lovelace is set up first when given a configuration of its own; otherwise the
    frontend sets it up with the defaults, keeping resources in storage.

    The frontend's own set of extra modules is swapped for one that records each
    change it is told of. The frontend's own sends each change to whoever has
    subscribed to extra-module changes over the websocket, so a recorded change
    is one the server would have sent.
    """
    if lovelace is not None:
        assert await async_setup_component(hass, "lovelace", {"lovelace": lovelace})
    assert await async_setup_component(hass, "frontend", {})
    changes: list[tuple[str, str]] = []
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(
        lambda change, url: changes.append((change, url)), []
    )
    return changes


def _seed_resources(
    hass_storage: dict[str, Any], *items: Mapping[str, str], version: int = 1
) -> dict[str, Any]:
    """Store Lovelace resources as an earlier startup left them, and return what was stored.

    A `version` above the one Lovelace reads stands for resources a newer Home
    Assistant stored, which this one refuses to load.
    """
    stored: dict[str, Any] = {
        "version": version,
        "minor_version": 1,
        "key": RESOURCE_STORAGE_KEY,
        "data": {"items": [dict(item) for item in items]},
    }
    hass_storage[RESOURCE_STORAGE_KEY] = stored
    return copy.deepcopy(stored)


def _seed_panel_settings(hass_storage: dict[str, Any], settings: dict[str, bool]) -> None:
    """Store the domain-level panel settings as the options flow saves them."""
    hass_storage["span_panel_settings"] = {
        "version": 1,
        "minor_version": 1,
        "key": "span_panel_settings",
        "data": settings,
    }


async def _flush_resource_storage(hass: HomeAssistant) -> None:
    """Write what Lovelace's resource collection has waiting for its delayed save."""
    resources = hass.data[LOVELACE_DATA].resources
    assert isinstance(resources, ResourceStorageCollection)
    await flush_store(resources.store)


async def _card_url(hass: HomeAssistant) -> str:
    """Return the card's module URL, tagged with a hash of the bundle as shipped."""
    bundle = await hass.async_add_executor_job(Path(PANEL_FRONTEND_DIR, CARD_FILENAME).read_bytes)
    return f"{PANEL_URL}/{CARD_FILENAME}?v={hashlib.md5(bundle, usedforsecurity=False).hexdigest()[:8]}"


def _static_resources(hass: HomeAssistant) -> list[StaticResource]:
    """Return the static resources registered for the frontend's URL."""
    return [
        resource
        for resource in hass.http.app.router.resources()
        if isinstance(resource, StaticResource) and resource.canonical == PANEL_URL
    ]


def _resource_urls(hass: HomeAssistant) -> list[str]:
    """Return the URLs of the resources Lovelace holds, in its order."""
    return [str(item["url"]) for item in hass.data[LOVELACE_DATA].resources.async_items()]


def _deletions_logged(caplog: pytest.LogCaptureFixture) -> int:
    """Return how many deletions of the old resource were logged."""
    return sum(
        1
        for record in caplog.records
        if record.name == _FRONTEND_LOGGER
        and record.levelno == logging.INFO
        and record.getMessage().startswith("Deleted the Lovelace resource")
    )


# ---------------------------------------------------------------------------
# Serving the frontend and loading the card
# ---------------------------------------------------------------------------


async def test_setup_loads_the_card_on_every_dashboard(
    hass: HomeAssistant, sidebar_panel: _SidebarPanel
) -> None:
    """Setup loads the card as an extra module, its URL tagged with the bundle's hash, and serves it once."""
    changes = await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    card_url = await _card_url(hass)
    assert card_url in hass.data[DATA_EXTRA_MODULE_URL].urls
    assert changes == [("added", card_url)]

    resources = _static_resources(hass)
    assert len(resources) == 1
    assert isinstance(resources[0], CachingStaticResource)


async def test_static_path_serves_the_bundle_with_cache_headers(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, sidebar_panel: _SidebarPanel
) -> None:
    """The card's URL answers with the bundle as shipped, cached for as long as the hash tag allows."""
    await _set_up_frontend(hass)
    assert await async_setup_component(hass, DOMAIN, {})

    client = await hass_client()
    response = await client.get(await _card_url(hass))

    assert response.status == 200
    assert response.headers["Cache-Control"] == CACHE_HEADER
    bundle = await hass.async_add_executor_job(Path(PANEL_FRONTEND_DIR, CARD_FILENAME).read_bytes)
    assert await response.read() == bundle


async def test_static_path_is_registered_before_the_panel(
    hass: HomeAssistant, sidebar_panel: _SidebarPanel
) -> None:
    """The sidebar panel's module is served from the static path, so the path comes first."""
    await _set_up_frontend(hass)
    registered_when_panel_was: list[int] = []
    sidebar_panel.register.side_effect = lambda *args, **kwargs: registered_when_panel_was.append(
        len(_static_resources(hass))
    )

    assert await async_setup_component(hass, DOMAIN, {})

    assert registered_when_panel_was == [1]


# ---------------------------------------------------------------------------
# The sidebar panel
# ---------------------------------------------------------------------------


async def test_panel_registered_on_setup(hass: HomeAssistant, sidebar_panel: _SidebarPanel) -> None:
    """With no settings stored, the panel is shown to everyone."""
    await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    sidebar_panel.register.assert_called_once()
    kwargs = sidebar_panel.register.call_args.kwargs
    assert kwargs["webcomponent_name"] == "span-panel"
    assert kwargs["frontend_url_path"] == "span-panel"
    assert kwargs["sidebar_title"] == "Span Panel"
    assert kwargs["sidebar_icon"] == "mdi:lightning-bolt"
    assert kwargs["module_url"].startswith(f"{PANEL_URL}/span-panel.js?v=")
    assert kwargs["require_admin"] is False


async def test_hidden_panel_still_loads_the_card(
    hass: HomeAssistant, hass_storage: dict[str, Any], sidebar_panel: _SidebarPanel
) -> None:
    """Hiding the sidebar panel removes it and leaves the card loaded on every dashboard."""
    _seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: False})
    changes = await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    sidebar_panel.register.assert_not_called()
    sidebar_panel.remove.assert_called_once_with(hass, "span-panel", warn_if_unknown=False)
    assert changes == [("added", await _card_url(hass))]


async def test_panel_admin_only_when_configured(
    hass: HomeAssistant, hass_storage: dict[str, Any], sidebar_panel: _SidebarPanel
) -> None:
    """The panel requires an administrator when the settings say so."""
    _seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: True, PANEL_ADMIN_ONLY: True})
    await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    sidebar_panel.register.assert_called_once()
    assert sidebar_panel.register.call_args.kwargs["require_admin"] is True


async def test_reapplying_panel_settings_touches_only_the_panel(
    hass: HomeAssistant, sidebar_panel: _SidebarPanel
) -> None:
    """Saving the options re-registers the panel without serving the card or adding its module again."""
    changes = await _set_up_frontend(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={}, entry_id="entry-panel-settings")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    assert schema is not None
    submitted = shown_values(schema)
    submitted[PANEL_ADMIN_ONLY] = True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=submitted
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert sidebar_panel.register.call_count == 2
    assert sidebar_panel.register.call_args.kwargs["require_admin"] is True
    assert len(_static_resources(hass)) == 1
    assert changes == [("added", await _card_url(hass))]


# ---------------------------------------------------------------------------
# The Lovelace resource earlier versions wrote
# ---------------------------------------------------------------------------


async def test_legacy_card_resource_is_removed(
    hass: HomeAssistant, hass_storage: dict[str, Any], sidebar_panel: _SidebarPanel
) -> None:
    """The card's old resource goes, with or without its tag; every other resource stays."""
    _seed_resources(hass_storage, _LEGACY_TAGGED, _UNRELATED, _LEGACY_BARE, _HACS_CARD)
    await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    assert _resource_urls(hass) == [_UNRELATED["url"], _HACS_CARD["url"]]

    await _flush_resource_storage(hass)
    assert hass_storage[RESOURCE_STORAGE_KEY]["data"]["items"] == [_UNRELATED, _HACS_CARD]


async def test_resources_without_the_legacy_card_are_left_alone(
    hass: HomeAssistant, hass_storage: dict[str, Any], sidebar_panel: _SidebarPanel
) -> None:
    """Once the old resource is gone, a startup deletes nothing."""
    stored = _seed_resources(hass_storage, _UNRELATED, _HACS_CARD)
    await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    assert _resource_urls(hass) == [_UNRELATED["url"], _HACS_CARD["url"]]
    await _flush_resource_storage(hass)
    assert hass_storage[RESOURCE_STORAGE_KEY] == stored


async def test_yaml_resource_mode_is_left_alone(
    hass: HomeAssistant, hass_storage: dict[str, Any], sidebar_panel: _SidebarPanel
) -> None:
    """With resources in YAML, Lovelace's storage and the YAML resources are both untouched, and the card loads."""
    stored = _seed_resources(hass_storage, _LEGACY_TAGGED, _UNRELATED)
    yaml_resources = [
        {"url": f"{PANEL_URL}/{CARD_FILENAME}", "type": "module"},
        {"url": _HACS_CARD["url"], "type": "module"},
    ]
    changes = await _set_up_frontend(
        hass, {"resource_mode": "yaml", "resources": copy.deepcopy(yaml_resources)}
    )

    assert await async_setup_component(hass, DOMAIN, {})

    assert isinstance(hass.data[LOVELACE_DATA].resources, ResourceYAMLCollection)
    assert hass.data[LOVELACE_DATA].resources.async_items() == yaml_resources
    await hass.async_block_till_done()
    assert hass_storage[RESOURCE_STORAGE_KEY] == stored
    assert changes == [("added", await _card_url(hass))]


async def test_legacy_card_resource_is_removed_from_resources_already_loaded(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    sidebar_panel: _SidebarPanel,
) -> None:
    """Resources a browser fetched before setup lose the old entry once, and are not loaded again.

    Lovelace's own handler for a browser's `lovelace/resources` request loads
    them, run directly: a websocket client would start the test server, whose
    router then refuses the static path.
    """
    _seed_resources(hass_storage, _LEGACY_TAGGED, _UNRELATED)
    await _set_up_frontend(hass)
    connection = MagicMock()
    await websocket_lovelace_resources_impl(
        hass, connection, {"id": 1, "type": "lovelace/resources"}
    )
    sent = connection.send_result.call_args.args
    assert [item["url"] for item in sent[1]] == [_LEGACY_TAGGED["url"], _UNRELATED["url"]]

    resources = hass.data[LOVELACE_DATA].resources
    assert isinstance(resources, ResourceStorageCollection)
    assert resources.loaded
    collection_changes: list[tuple[str, str]] = []

    async def record(change_type: str, item_id: str, item: dict[str, Any]) -> None:
        collection_changes.append((change_type, item_id))

    resources.async_add_listener(record)

    assert await async_setup_component(hass, DOMAIN, {})

    # Loading the resources a second time would announce every one of them as added.
    assert collection_changes == [(CHANGE_REMOVED, _LEGACY_TAGGED["id"])]
    assert _resource_urls(hass) == [_UNRELATED["url"]]
    await _flush_resource_storage(hass)
    assert hass_storage[RESOURCE_STORAGE_KEY]["data"]["items"] == [_UNRELATED]


async def test_legacy_resource_deleted_meanwhile_is_skipped(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    sidebar_panel: _SidebarPanel,
) -> None:
    """An old entry deleted elsewhere just before setup reaches it is skipped, and the next one still goes."""
    caplog.set_level(logging.INFO, logger=_FRONTEND_LOGGER)
    _seed_resources(hass_storage, _LEGACY_TAGGED, _UNRELATED, _LEGACY_BARE)
    await _set_up_frontend(hass)
    resources = hass.data[LOVELACE_DATA].resources
    assert isinstance(resources, ResourceStorageCollection)
    delete = resources.async_delete_item
    deleted_elsewhere: list[str] = []

    async def deleted_elsewhere_first(item_id: str) -> None:
        """Delete the first entry, as the resource editor might, just before setup's own delete."""
        if not deleted_elsewhere:
            deleted_elsewhere.append(item_id)
            await delete(item_id)
        await delete(item_id)

    with patch.object(resources, "async_delete_item", deleted_elsewhere_first):
        assert await async_setup_component(hass, DOMAIN, {})

    assert deleted_elsewhere == [_LEGACY_TAGGED["id"]]
    assert _resource_urls(hass) == [_UNRELATED["url"]]
    assert _deletions_logged(caplog) == 1


async def test_resources_that_cannot_be_loaded_leave_the_card_loaded(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    sidebar_panel: _SidebarPanel,
) -> None:
    """Resources stored by a newer Home Assistant are left as they are, with a warning, and the card still loads."""
    stored = _seed_resources(hass_storage, _LEGACY_TAGGED, _UNRELATED, version=2)
    changes = await _set_up_frontend(hass)

    assert await async_setup_component(hass, DOMAIN, {})

    assert changes == [("added", await _card_url(hass))]
    sidebar_panel.register.assert_called_once()
    warnings = [
        record
        for record in caplog.records
        if record.name == _FRONTEND_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "Could not load the Lovelace resources" in warnings[0].getMessage()
    assert "has version 2" in warnings[0].getMessage()
    await hass.async_block_till_done()
    assert hass_storage[RESOURCE_STORAGE_KEY] == stored
