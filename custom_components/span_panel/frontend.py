"""Frontend registration helpers for the Span Panel integration.

Handles static path serving, loading the card on every dashboard, sidebar
panel registration, and panel settings storage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
import hashlib
import logging
import os

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.collection import ItemNotFound
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR
from .panel_settings import (
    FAVORITE_KINDS,
    FavoriteKind,
    PanelFavorites,
    PanelSettings,
    read_panel_settings,
)

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "/span_panel_frontend"
PANEL_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")
CARD_FILENAME = "span-panel-card.js"

_LEGACY_CARD_RESOURCE_URL = f"{PANEL_URL}/{CARD_FILENAME}"
"""The card's URL, without its query, as earlier versions wrote it into Lovelace's resources."""

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


async def async_load_panel_settings(hass: HomeAssistant) -> PanelSettings:
    """Load domain-level panel settings from storage, read by `panel_settings.read_panel_settings`."""
    store: Store[PanelSettings] = Store(
        hass, _PANEL_SETTINGS_STORAGE_VERSION, _PANEL_SETTINGS_STORAGE_KEY
    )
    return read_panel_settings(await store.async_load())


async def _async_save_panel_settings(hass: HomeAssistant, settings: PanelSettings) -> None:
    """Save domain-level panel settings to storage; only `async_update_panel_settings` calls it."""
    store: Store[PanelSettings] = Store(
        hass, _PANEL_SETTINGS_STORAGE_VERSION, _PANEL_SETTINGS_STORAGE_KEY
    )
    await store.async_save(settings)


_SETTINGS_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_panel_settings_lock")
"""Serialises every read-modify-write of `span_panel_settings`.

The file holds the favorites of every panel and the sidebar settings in one
blob, and HA's Store serialises writes but not the load -> mutate -> save
around them, so two writers interleaved at their awaits would each save what
they read and one change would be lost. It guards the whole file, not one key:
favorites, the options flow's sidebar settings and the device-id migration's
rewrite of favorites all write through `async_update_panel_settings`, which
holds it. Kept on `hass` rather than as a module global, as the file it
guards belongs to one Home Assistant: an `asyncio.Lock` belongs to the event
loop that first waits on it.
"""


async def async_update_panel_settings(
    hass: HomeAssistant, update: Callable[[PanelSettings], bool]
) -> None:
    """Read the settings file, apply `update` to it in place, and write it back, as one step.

    The one way anything writes `span_panel_settings`, so no write can land
    between another writer's read and its save. `update` answers whether it
    changed anything; the file is written only when it did.
    """
    lock = hass.data.get(_SETTINGS_LOCK)
    if lock is None:
        lock = hass.data[_SETTINGS_LOCK] = asyncio.Lock()
    async with lock:
        settings = await async_load_panel_settings(hass)
        if update(settings):
            await _async_save_panel_settings(hass, settings)


def _empty_panel_favorites() -> PanelFavorites:
    return {"circuits": [], "sub_devices": []}


async def async_get_favorites(
    hass: HomeAssistant,
) -> dict[str, PanelFavorites]:
    """Return the stored favorites map.

    Shape: ``{panel_device_id: {"circuits": [uuid, ...], "sub_devices": [devid, ...]}}``.

    Empty/missing storage returns an empty dict. Old single-list per-panel
    shape ``{panel_id: [uuid, ...]}`` is read transparently as circuits-only
    so favorites that were stored before sub-device support are preserved.
    """
    settings = await async_load_panel_settings(hass)
    return settings.get("favorites", {})


async def async_set_favorite(
    hass: HomeAssistant,
    panel_device_id: str,
    kind: FavoriteKind,
    target_id: str,
    favorited: bool,
) -> dict[str, PanelFavorites]:
    """Add or remove a circuit or sub-device from the favorites map.

    ``kind`` is either ``"circuits"`` or ``"sub_devices"``. ``target_id`` is
    the circuit uuid or sub-device HA device id, respectively. Deduplicates
    on add; drops empty lists and empty panel entries on remove. Returns
    the updated full favorites map.

    Written through `async_update_panel_settings`, so concurrent callers
    can't race; also persists the normalized shape so legacy data is migrated
    in place on the first write touching it.
    """
    if kind not in FAVORITE_KINDS:
        raise ValueError(f"Unknown favorite kind: {kind!r}")

    favorites: dict[str, PanelFavorites] = {}

    def _toggle(settings: PanelSettings) -> bool:
        nonlocal favorites
        favorites = settings.get("favorites", {})

        panel_entry = favorites.get(panel_device_id) or _empty_panel_favorites()
        current = list(panel_entry[kind])
        if favorited:
            if target_id not in current:
                current.append(target_id)
        else:
            if target_id in current:
                current.remove(target_id)

        panel_entry[kind] = current
        if any(panel_entry[k] for k in FAVORITE_KINDS):
            favorites[panel_device_id] = panel_entry
        else:
            favorites.pop(panel_device_id, None)

        settings["favorites"] = favorites
        return True

    await async_update_panel_settings(hass, _toggle)
    return favorites


async def async_rekey_favorite_circuits(
    hass: HomeAssistant, panel_device_id: str, mapping: Mapping[str, str]
) -> int:
    """Move one panel's favorite circuits from their old circuit ids to their new ones.

    Only the favorites of `panel_device_id` are touched, in their order. A
    circuit already favorited under its new id keeps that place and the old
    entry is dropped: the new id records a favorite made since the panel
    changed its ids. Written through `async_update_panel_settings`, and only
    when an entry moved. Returns how many moved.
    """
    moved = 0

    def _rekey(settings: PanelSettings) -> bool:
        nonlocal moved
        favorites = settings.get("favorites", {})
        panel_entry = favorites.get(panel_device_id)
        if panel_entry is None:
            return False
        circuits = panel_entry["circuits"]
        if not any(circuit_id in mapping for circuit_id in circuits):
            return False
        present = set(circuits)
        rekeyed: list[str] = []
        for circuit_id in circuits:
            current = mapping.get(circuit_id)
            if current is not None:
                moved += 1
                if current in present:
                    continue
            kept = circuit_id if current is None else current
            if kept not in rekeyed:
                rekeyed.append(kept)
        panel_entry["circuits"] = rekeyed
        settings["favorites"] = favorites
        return True

    await async_update_panel_settings(hass, _rekey)
    return moved


async def async_forget_favorites_of(hass: HomeAssistant, device_ids: Collection[str]) -> None:
    """Drop the favorites kept under any of `device_ids`: those of a config entry being removed.

    The favorites map is keyed by each panel's device registry id, and the
    file outlives every entry. Left there, a removed entry's favorites come
    back with the panel: re-adding it restores the device's id from its
    tombstone. `async_remove_entry` passes every device the entry owns, since
    Home Assistant clears them only after that returns, so the panel's is among
    them; its runtime data, which names the panel's device, is gone by then.
    Written through `async_update_panel_settings`, and only when a key went.
    """
    if device_ids:
        await async_update_panel_settings(
            hass, lambda settings: _drop_favorites(settings, device_ids.__contains__)
        )


async def async_drop_orphaned_favorites(hass: HomeAssistant) -> None:
    """Drop the favorites kept under a device id the device registry holds no device for.

    What an entry removed before `async_forget_favorites_of` existed left
    behind, and what any removal it did not see leaves. Only an id with no
    device at all goes: a panel's device stays in the registry while its entry
    is disabled, failed or not yet set up, and so do its favorites. Run once,
    from the domain's `async_setup`, before the services and any entry that
    reads favorites; written only when a key went, so a clean file costs one
    read.
    """
    devices = dr.async_get(hass)
    await async_update_panel_settings(
        hass,
        lambda settings: _drop_favorites(
            settings, lambda device_id: devices.async_get(device_id) is None
        ),
    )


def _drop_favorites(settings: PanelSettings, gone: Callable[[str], bool]) -> bool:
    """Drop each panel's favorites whose device id `gone` answers for; whether any went.

    The rest are kept as `panel_settings.normalize_favorites` read them, so a file in
    the legacy shape that loses a key is written back in the current one, as
    any write leaves it. A file that loses none is not written.
    """
    favorites = settings.get("favorites", {})
    kept = {device_id: kinds for device_id, kinds in favorites.items() if not gone(device_id)}
    if len(kept) == len(favorites):
        return False
    settings["favorites"] = kept
    return True


def _is_legacy_resource(item: Mapping[str, object]) -> bool:
    """Whether a Lovelace resource is the entry earlier versions wrote for the card.

    The query is ignored because those versions wrote the card's cache tag into
    it, and rewrote it whenever the bundle changed.
    """
    url = item.get("url")
    return isinstance(url, str) and url.split("?", 1)[0] == _LEGACY_CARD_RESOURCE_URL


async def _async_remove_legacy_resource(hass: HomeAssistant) -> None:
    """Delete the Lovelace resource earlier versions wrote for the card.

    Those versions made the card available by writing it into Lovelace's
    resource storage; it is now loaded as an extra frontend module, so the old
    entry would only load it a second time. It is deleted through Lovelace's
    own resource collection, never by editing the storage file, so the
    collection and its storage stay in step.

    Runs at every startup and deletes nothing once the entry is gone. Only an
    entry for the card this integration serves is deleted: the same card added
    from elsewhere, such as HACS, is the user's. In YAML resource mode Lovelace
    never reads its resource storage, so there is nothing to delete.

    Best effort: resources that cannot be loaded, such as a storage file
    written by a newer Home Assistant, are logged and left as they are. The
    card is loaded either way, and failing here would fail the integration's
    setup for the sake of a duplicate.
    """
    resources = hass.data[LOVELACE_DATA].resources
    if not isinstance(resources, ResourceStorageCollection):
        return

    # Lovelace loads its resources on first use. Asking for its info loads them
    # if nothing has yet, where loading them directly would load them twice.
    # Storage raises HomeAssistantError itself for a file it cannot read, and a
    # subclass of it for one a newer Home Assistant wrote, so nothing narrower
    # covers both.
    try:
        await resources.async_get_info()
    except HomeAssistantError as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning(
            "Could not load the Lovelace resources to delete the one for %s that "
            "an earlier version added, so it is left in place: %s",
            _LEGACY_CARD_RESOURCE_URL,
            err,
        )
        return

    legacy_ids = [item_id for item_id, item in resources.data.items() if _is_legacy_resource(item)]
    for item_id in legacy_ids:
        try:
            await resources.async_delete_item(item_id)
        except ItemNotFound:
            # Deleted in the meantime, for instance through the resource editor.
            continue
        _LOGGER.info(
            "Deleted the Lovelace resource for %s that an earlier version added; "
            "the card is now loaded on every dashboard without one",
            _LEGACY_CARD_RESOURCE_URL,
        )


async def async_register_frontend_assets(hass: HomeAssistant) -> None:
    """Serve the bundled frontend and load the card on every dashboard.

    Domain-level and independent of the panel settings, so it runs once, from
    `async_setup`, before the sidebar panel is registered. The card is loaded
    as an extra frontend module, which every dashboard gets in either Lovelace
    resource mode; a resource in Lovelace's storage reaches no dashboard when
    the resources are kept in YAML.

    The module URL carries a hash of the bundle because the static path is
    served with month-long cache headers, so a changed bundle needs a new URL
    for browsers to fetch it.

    The card is added before the resource earlier versions wrote is deleted,
    so that deleting it, which is best effort, can never keep the card from
    loading.
    """
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_URL, PANEL_FRONTEND_DIR, cache_headers=True)]
    )

    card_cache_tag = await hass.async_add_executor_job(_frontend_file_hash, CARD_FILENAME)
    add_extra_js_url(hass, f"{PANEL_URL}/{CARD_FILENAME}?v={card_cache_tag}")

    await _async_remove_legacy_resource(hass)


async def async_apply_panel_registration(hass: HomeAssistant) -> None:
    """Register or remove the sidebar panel based on stored settings.

    Runs from `async_setup`, after `async_register_frontend_assets` has
    registered the static path the panel's module is served from, and again
    from the options flow whenever the panel settings are saved. It touches
    the sidebar panel only: re-applying the settings must neither register the
    static path again nor add the card's module URL again, which the frontend
    would record as another change and send to every subscriber of its
    extra-module changes.

    Uses deferred imports from the parent package so that test patches
    applied to ``custom_components.span_panel.*`` take effect.
    """
    # Deferred imports: tests patch these names on the package namespace
    # (custom_components.span_panel.X). Resolving through ``from .`` at
    # call time ensures patched values are picked up.
    from . import (  # pylint: disable=import-outside-toplevel
        async_load_panel_settings as _load_settings,
        async_register_panel as _register_panel,
        async_remove_panel as _remove_panel,
    )

    settings = await _load_settings(hass)
    show = settings.get(PANEL_SHOW_SIDEBAR, True)
    admin_only = settings.get(PANEL_ADMIN_ONLY, False)

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
