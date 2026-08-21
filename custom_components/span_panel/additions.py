"""Telling the user what this integration just added.

Split out of `schema_repairs` deliberately. An addition is not a repair: nothing
is broken, nothing needs fixing, and filing it in the Repairs list puts it in a
category whose whole meaning is "something wants your attention because it went
wrong". It is a notification, and it says what was added rather than only how
many.

Two things this fixes about the previous behaviour.

**Enabled additions were announced to nobody.** The old notice covered only
entities added `disabled_by=INTEGRATION`, on the reasoning that an enabled entity
is already visible in the entity list and its history. That reasoning does not
survive contact with how anyone actually uses Home Assistant: nobody watches
their entity count, so an addition that breaks nothing is indistinguishable from
no addition at all.

**The diff was derived from setup timing, so it was unrepeatable.** It compared
the registry before the platforms against the registry after, which answers
correctly exactly once: on the next startup the entity is already registered
beforehand and the diff is empty by construction. Anything not announced in that
one window was never announced. What is announced is now recorded, so the
question becomes "has this been announced" rather than "was this registered in
the last few seconds", and nothing is lost to a restart landing in the wrong
place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.persistent_notification import async_create
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .util import ADOPTED_IDENTIFIER_TOKEN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION: Final = 1
_ANNOUNCED: Final = "announced_unique_ids"

_FALLBACK: Final[dict[str, str]] = {
    "title": "SPAN Panel added new entities",
    "intro_one": "This update added 1 new entity to your SPAN Panel.",
    "intro_many": "This update added {count} new entities to your SPAN Panel.",
    "enabled_heading": "Added and ready to use",
    "disabled_heading": "Added but switched off",
    "how_to_enable": (
        "Switched-off entities record nothing until you turn them on. Open the SPAN Panel "
        "device page, show its disabled entities, and enable the ones you want."
    ),
    "nothing_broken": "Nothing is broken and no action is required.",
}
"""English text, used when a translation file cannot be read.

A fallback rather than the source of truth: `strings.json` and `translations/`
carry the same keys in five languages, and `_text` prefers those. Kept in code so
a notification is never *lost* to an unreadable file -- the addition still gets
announced, in English.
"""


def _store(hass: HomeAssistant, entry: ConfigEntry) -> Store[dict[str, Any]]:
    """Return the record of what has already been announced for this entry.

    Per entry rather than per domain: two panels add entities independently, and
    a shared record would let one panel's announcement suppress the other's.
    """
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.announced.{entry.entry_id}")


def _read_translations(language: str) -> dict[str, str]:
    """Our own notification strings for one language, or an empty mapping.

    Read from this component's `translations/` directory rather than through
    `homeassistant.helpers.translation`, because that helper filters to the
    categories Home Assistant defines and a persistent notification is not one of
    them -- a custom category loads as nothing at all. These are this
    integration's own package files, so reading them is not reaching into
    somebody else's layout.

    Blocking file I/O. Callers run it in an executor.
    """
    directory = Path(__file__).parent / "translations"
    for candidate in (f"{language}.json", f"{language.split('-')[0]}.json", "en.json"):
        path = directory / candidate
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _LOGGER.debug("Could not read notification strings from %s", path, exc_info=True)
            continue
        section = loaded.get("notifications", {}).get("new_entities", {})
        if section:
            return {str(key): str(value) for key, value in section.items()}
    return {}


async def async_announce_new_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Tell the user which entities this setup added, and which need enabling.

    Silent on a first install. With nothing recorded and nothing registered, every
    entity is new, so the notification would name the entire integration and teach
    the user that this category is noise -- which would cost them the real
    additions later.

    Silent, once, on the first run after this mechanism ships. An install that
    predates the record has entities that were never announced but are not new
    either, so the first pass adopts them as already-known rather than announcing
    a release's worth of history.
    """
    store = _store(hass, entry)
    stored = await store.async_load()
    registered = _registered(hass, entry)

    if stored is None:
        await store.async_save({_ANNOUNCED: sorted(registered)})
        _LOGGER.debug(
            "Seeded the announcement record for %s with %d entities; nothing announced",
            entry.entry_id,
            len(registered),
        )
        return

    announced = frozenset(stored.get(_ANNOUNCED, ()))
    added = [
        registry_entry
        for registry_entry in _entries(hass, entry)
        if registry_entry.unique_id not in announced
    ]
    if not added:
        return

    text = await hass.async_add_executor_job(_read_translations, hass.config.language)
    async_create(
        hass,
        message=_message(hass, added, text),
        title=_text(text, "title"),
        notification_id=f"{DOMAIN}_new_entities_{entry.entry_id}",
    )
    await store.async_save({_ANNOUNCED: sorted(announced | registered)})
    _LOGGER.debug("Announced %d new entities for %s", len(added), entry.entry_id)


def _entries(hass: HomeAssistant, entry: ConfigEntry) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def _registered(hass: HomeAssistant, entry: ConfigEntry) -> frozenset[str]:
    return frozenset(registry_entry.unique_id for registry_entry in _entries(hass, entry))


def _text(text: dict[str, str], key: str, **placeholders: object) -> str:
    """One string, translated where a file supplied it and English where not."""
    template = text.get(key) or _FALLBACK[key]
    return template.format(**placeholders) if placeholders else template


def _message(hass: HomeAssistant, added: list[er.RegistryEntry], text: dict[str, str]) -> str:
    """Build the notification body: what was added, split by whether it is switched on.

    The split is the actionable part. An enabled entity is already recording and
    needs nothing; a disabled one records nothing at all until the user turns it
    on, and saying so is the difference between a notice they can act on and one
    they can only acknowledge.
    """
    devices = dr.async_get(hass)
    enabled: list[str] = []
    disabled: list[str] = []
    adopted: dict[str, int] = {}

    for registry_entry in added:
        device_name = _adopted_device_name(devices, registry_entry)
        if device_name is not None:
            adopted[device_name] = adopted.get(device_name, 0) + 1
            continue
        target = (
            disabled
            if registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            else enabled
        )
        target.append(_label(devices, registry_entry))

    # Two keys rather than one with a plural placeholder: a plural rule the caller
    # picks a *word* for is an English rule, and the five languages here do not
    # share it.
    intro = (
        _text(text, "intro_one") if len(added) == 1 else _text(text, "intro_many", count=len(added))
    )
    lines = [intro, ""]
    if enabled:
        lines += [f"**{_text(text, 'enabled_heading')}**", ""]
        lines += [f"- {label}" for label in sorted(enabled)]
        lines.append("")
    if adopted:
        lines += [f"- {name} ({count} entities)" for name, count in sorted(adopted.items())]
        lines.append("")
    if disabled:
        lines += [f"**{_text(text, 'disabled_heading')}**", ""]
        lines += [f"- {label}" for label in sorted(disabled)]
        lines += ["", _text(text, "how_to_enable"), ""]
    lines.append(_text(text, "nothing_broken"))
    return "\n".join(lines)


def _adopted_device_name(
    devices: dr.DeviceRegistry, registry_entry: er.RegistryEntry
) -> str | None:
    """Return the adopted device this entity belongs to, or None when it belongs to none.

    An adopted device's entities are collapsed to one line with a count. A vendor
    device declaring a dozen properties would otherwise spend the whole
    notification on itself and teach the user to skip it -- which would cost them
    the curated additions in the same message.
    """
    if registry_entry.device_id is None:
        return None
    device = devices.async_get(registry_entry.device_id)
    if device is None:
        return None
    token = f"_{ADOPTED_IDENTIFIER_TOKEN}_"
    if not any(token in identifier for _domain, identifier in device.identifiers):
        return None
    return device.name_by_user or device.name or registry_entry.entity_id


def _label(devices: dr.DeviceRegistry, registry_entry: er.RegistryEntry) -> str:
    """Return what to call an entity the user has never seen.

    A disabled entity has no state, so there is no friendly name on the state
    machine -- only what the registry recorded when the platform added it. The
    entity_id is the last resort rather than the first choice, because it is the
    name the user will *not* see in the device's entity list.

    **Sub-device entities carry their device's name.** Every platform here sets
    `_attr_has_entity_name`, so the registry stores only the entity half and Home
    Assistant prepends the device in its own UI. A flat list does not, and the
    result was two chargers contributing "EVSE Charge Current Limit" twice with
    nothing to tell them apart, and the battery's meter reading as a bare "Meter
    Power". Two identical rows is what teaches somebody to skip the category.

    The panel's own entities are left bare. Prefixing them would put the panel
    name on every line of a notification that already says which panel it is
    about, which is noise rather than disambiguation -- so the prefix is added
    only where the device is a sub-device, which is exactly where the collision
    happens.
    """
    name = registry_entry.name or registry_entry.original_name
    if name is None:
        return registry_entry.entity_id
    device_name = _sub_device_name(devices, registry_entry)
    return f"{device_name} {name}" if device_name else name


def _sub_device_name(devices: dr.DeviceRegistry, registry_entry: er.RegistryEntry) -> str | None:
    """Return the sub-device this entity sits on, or None for the panel itself.

    A sub-device is one that hangs off another with `via_device_id` -- the
    battery, the MID, each charger, the solar inverter. The panel is the one
    device with no parent, and its entities need no prefix.
    """
    if registry_entry.device_id is None:
        return None
    device = devices.async_get(registry_entry.device_id)
    if device is None or device.via_device_id is None:
        return None
    return device.name_by_user or device.name


async def async_forget_announcements(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the announcement record when the entry is removed.

    Without this the record outlives the entry it describes, and re-adding the
    same panel would suppress the announcement of every entity it recreates.
    """
    await _store(hass, entry).async_remove()
