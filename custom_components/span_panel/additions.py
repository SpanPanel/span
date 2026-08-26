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

import logging
from typing import TYPE_CHECKING, Final, TypedDict

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .extension import is_extension_unique_id
from .notices import async_raise, read_translations
from .util import ADOPTED_IDENTIFIER_TOKEN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION: Final = 1
_ANNOUNCED: Final = "announced_unique_ids"

COLLAPSE_ABOVE: Final = 5
"""How many vendor readings one curated device may add before they are collapsed.

Above this, the device gets one line with a count; at or below it, each entity is
named. The flood this guards against is a firmware update adding fifteen
properties at once, which would spend the whole notification on one device and
cost the reader the curated additions in the same message. Two or three is not
that, and naming them is what tells the reader anything at all -- the card is one
they already have, so its name alone says nothing new.

Adopted *devices* are collapsed regardless of count, because their line names a
device that did not exist before, which is itself the news.
"""

_SECTION: Final = "new_entities"
"""Names both the translation section and the notice id. They describe the same
thing, and keeping them one symbol means a rename cannot leave a standing notice
orphaned from the strings that render it."""

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


class StoredAnnouncements(TypedDict):
    """The unique_ids this entry has already announced.

    Typed rather than `dict[str, Any]` so the one shape this file writes is
    stated once. It is a compile-time claim only -- see `_load` for what the disk
    is actually allowed to hold.
    """

    announced_unique_ids: list[str]


def _store(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredAnnouncements]:
    """Return the record of what has already been announced for this entry.

    Per entry rather than per domain: two panels add entities independently, and
    a shared record would let one panel's announcement suppress the other's.
    """
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.announced.{entry.entry_id}")


def _load(stored: object, entry: ConfigEntry) -> frozenset[str] | None:
    """Read the announced set off disk, or `None` when the file is not what we wrote.

    `notices._load` does this and has three tests; this file had neither, and the
    consequence here is the worse of the two. This coroutine is awaited from
    inside `async_setup_entry`, so a wrong-shaped file raised out of it, the
    coordinator was shut down and the entry went to SETUP_ERROR -- which is not
    retried. A panel would stay down until somebody found and deleted a file
    about *notifications*.

    On-disk data violates the TypedDict freely: a hand edit, a partially restored
    backup, a file written by a later version and read after a rollback. Home
    Assistant already renames undecodable JSON and raises a core repair; the gap
    is valid JSON of the wrong shape, which it hands back intact.

    `None` means "no usable record", which the caller treats exactly as a first
    install: re-seed from what is registered now and announce nothing. That
    silently swallows one pass of genuine additions, which is the same trade the
    first install already makes and is worth strictly less than the entry.

    An `announced_unique_ids` holding something that is not a list of strings is
    refused whole rather than filtered down to the strings in it. A partial read
    would announce every id the bad row displaced as though it were new, which is
    the flood the record exists to prevent -- and unlike a notice, an id here
    carries no meaning of its own to salvage.
    """
    if stored is None:
        return None
    announced = stored.get(_ANNOUNCED) if isinstance(stored, dict) else None
    if not isinstance(announced, list) or not all(
        isinstance(unique_id, str) for unique_id in announced
    ):
        _LOGGER.warning(
            "Ignoring the announcement record for %s: expected an object with an "
            "'%s' list of ids, found %s. Entities added by this setup are recorded "
            "as known rather than announced; the next addition is announced normally.",
            entry.entry_id,
            _ANNOUNCED,
            _describe(stored),
        )
        return None
    return frozenset(announced)


def _describe(stored: object) -> str:
    """Say what was on disk where the announced list should have been.

    A separate function because the honest answer has three cases and only one of
    them is a type name. Reporting `type(stored.get(...))` for all three says
    "found NoneType" for a record that simply has no such key, which reads as a
    null somebody wrote and sends whoever is looking at the wrong part of the
    file.
    """
    if not isinstance(stored, dict):
        return type(stored).__name__
    if _ANNOUNCED not in stored:
        return "missing"
    announced = stored[_ANNOUNCED]
    if isinstance(announced, list):
        offending = next((item for item in announced if not isinstance(item, str)), None)
        return f"a list holding {type(offending).__name__}"
    return type(announced).__name__


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

    Silent, once, after a record that could not be read -- the same seeding path,
    reached from `_load` returning `None`. Nothing about a notification is worth
    failing setup for.
    """
    store = _store(hass, entry)
    announced = _load(await store.async_load(), entry)
    registered = _registered(hass, entry)

    if announced is None:
        await store.async_save(StoredAnnouncements(announced_unique_ids=sorted(registered)))
        _LOGGER.debug(
            "Seeded the announcement record for %s with %d entities; nothing announced",
            entry.entry_id,
            len(registered),
        )
        return

    added = [
        registry_entry
        for registry_entry in _entries(hass, entry)
        if registry_entry.unique_id not in announced
    ]
    if not added:
        return

    text = await hass.async_add_executor_job(read_translations, hass.config.language, _SECTION)
    async_raise(
        hass,
        entry,
        _SECTION,
        title=_text(text, "title"),
        message=_message(hass, added, text),
    )
    await store.async_save(StoredAnnouncements(announced_unique_ids=sorted(announced | registered)))
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

    **Adopted devices belong on the switched-off side of that split**, because
    every adopted entity registers disabled -- `AdoptedEntity` sets
    `_attr_entity_registry_enabled_default = False` for all of them. Listing them
    anywhere else said the opposite of the truth twice over: under the
    ready-to-use heading they read as already recording, and being counted apart
    from `disabled` meant a release whose only additions were adopted rendered no
    heading and, worse, suppressed `how_to_enable` -- the one actionable string in
    the message. A vendor device appearing is the case adoption exists for, and it
    was the case that told the user least.
    """
    devices = dr.async_get(hass)
    enabled: list[str] = []
    disabled: list[str] = []
    collapsed: dict[str, int] = {}
    extensions: dict[str, list[str]] = {}

    for registry_entry in added:
        device_name = _adopted_device_name(devices, registry_entry)
        if device_name is not None:
            collapsed[device_name] = collapsed.get(device_name, 0) + 1
            continue
        # Vendor extensions need their own detector: they live on *curated*
        # devices, so the identifier test above cannot see them. Their unique_id
        # is what says what they are. Held as labels rather than counted,
        # because whether they collapse depends on how many there turn out to be.
        extension_device = _extension_device_name(devices, registry_entry)
        if extension_device is not None:
            extensions.setdefault(extension_device, []).append(_label(devices, registry_entry))
            continue
        target = (
            disabled
            if registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            else enabled
        )
        target.append(_label(devices, registry_entry))

    # Collapse a vendor's additions only once there are enough of them to crowd
    # the message out. Below the threshold the names are the useful part: an
    # adopted *device*'s line at least names a new device the user can go find,
    # while a curated card is one they already have, so "Span Panel (2 entities)"
    # tells them strictly less than naming the two would. Extension entities are
    # always disabled -- that is their arrival state -- so the uncollapsed ones
    # join that list rather than being sorted again.
    for device_name, labels in extensions.items():
        if len(labels) > COLLAPSE_ABOVE:
            collapsed[device_name] = collapsed.get(device_name, 0) + len(labels)
        else:
            disabled.extend(labels)

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
    if disabled or collapsed:
        lines += [f"**{_text(text, 'disabled_heading')}**", ""]
        lines += [f"- {label}" for label in sorted(disabled)]
        lines += [f"- {name} ({count} entities)" for name, count in sorted(collapsed.items())]
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


def _extension_device_name(
    devices: dr.DeviceRegistry, registry_entry: er.RegistryEntry
) -> str | None:
    """Return the curated device a vendor extension belongs to, or None for anything else.

    Detected by unique_id rather than by device identifier, because that is the
    only thing that distinguishes these: an extension entity sits on a *curated*
    card beside curated entities, so the card says nothing about it.

    Collapsed for the same reason adopted devices are, and the arithmetic is
    worse here: a firmware update adding fifteen vendor properties to the battery
    would spend the entire notification on them and teach the user to skip it,
    costing them the curated additions in the same message.
    """
    if registry_entry.device_id is None or not registry_entry.unique_id:
        return None
    if not is_extension_unique_id(registry_entry.unique_id):
        return None
    device = devices.async_get(registry_entry.device_id)
    if device is None:
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
