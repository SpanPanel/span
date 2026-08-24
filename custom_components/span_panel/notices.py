"""Notifications that outlive a restart.

A persistent notification lives in memory. Raise one, restart Home Assistant,
and it is gone whether or not anybody read it. That is why the things worth
saying exactly once have historically been filed as Repairs instead: a Repair is
stored, so it stands until the user dismisses it.

That trade is a bad one. The Repairs list means "something wants your attention
because it went wrong" -- it is stamped with a severity, it offers to fix or
ignore, and everything else filed there is a defect. Putting good news in it
tells the user their panel is broken. A firmware upgrade that took nothing away
arrived looking like a warning, which is the opposite of what it was for.

This module keeps the notification and buys back the one property the Repair had
that it lacked. A notice raised here is recorded per config entry and re-raised
at every setup until it is dismissed, and the dismissal is *observed* rather than
assumed: Home Assistant reports removals to a registered callback, so "the user
has seen it" is a fact rather than a hope. A notice therefore survives a restart,
a reload, and an owner who was on holiday when their panel upgraded.

Which is durable and which is not is a real distinction, not a default. A notice
belongs here when it reports something that happened once and cannot be
re-derived -- a firmware upgrade, an entity that appeared. Anything re-derived
from live state on every refresh does not belong here; it belongs in
`schema_repairs`, which reconciles instead, and where being a defect is the
point.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypedDict

from homeassistant.components.persistent_notification import (
    Notification,
    UpdateType,
    async_create,
    async_dismiss,
    async_register_callback,
)
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION: Final = 1

_SAVE_DELAY: Final = 1.0
"""Seconds to coalesce writes.

Raising and dismissing both write, and a reload raises every standing notice in
a burst. Delayed saves collapse that into one file write, and Home Assistant
flushes anything outstanding at shutdown.
"""


class StandingNotice(TypedDict):
    """A notice the user has been shown and has not dismissed.

    The rendered text is stored, not the arguments that produced it. A notice can
    outlive the state that raised it -- the schema upgrade that raised one is over
    by the time it is re-raised -- so re-deriving the wording is not possible, and
    a notice that changed its story between restarts would be worse than one that
    did not survive at all.
    """

    title: str
    message: str


class StoredNotices(TypedDict):
    """One config entry's undismissed notices, by notice id."""

    standing: dict[str, StandingNotice]


@dataclass(slots=True)
class _Notices:
    """One entry's live view of its notices.

    The store is held rather than rebuilt per call because `async_delay_save`
    schedules on the instance: two `Store` objects over the same key would each
    hold a pending write and race to be last.
    """

    store: Store[StoredNotices]
    standing: dict[str, StandingNotice]


_DATA: HassKey[dict[str, _Notices]] = HassKey(f"{DOMAIN}_standing_notices")


def _notification_id(entry: ConfigEntry, notice_id: str) -> str:
    """Namespace a notice id by domain and entry.

    Per entry, not per domain: two panels upgrading are two notices, and a shared
    id would let the second overwrite the first's text and the first's dismissal
    silence the second.
    """
    return f"{DOMAIN}_{notice_id}_{entry.entry_id}"


async def async_restore(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-raise this entry's undismissed notices and watch for dismissals.

    Call before anything that can raise a notice. Raising needs the live view
    this builds, and building it afterwards would overwrite what was on disk with
    only what this run happened to raise.

    The watch is registered through `entry.async_on_unload`, so a reload replaces
    it rather than accumulating one per setup -- the dispatcher fans every
    notification change out to every registered callback, so a leaked
    registration would be a leaked handler running against a dead entry.

    A reload keeps the live view it already had rather than re-reading the file,
    because memory is the fresher of the two and the gap between them is exactly
    where the interesting case lives: a schema upgrade raises a notice and
    schedules a reload in the same breath, so the setup that follows can easily
    arrive before the delayed write has landed. Re-reading there would drop the
    notice from the standing set while it sat on the user's screen, and it would
    not come back after the next restart.
    """
    known = hass.data.setdefault(_DATA, {})
    notices = known.get(entry.entry_id)
    if notices is None:
        store: Store[StoredNotices] = Store(
            hass, _STORE_VERSION, f"{DOMAIN}.notices.{entry.entry_id}"
        )
        notices = _Notices(store=store, standing=_load(await store.async_load(), entry))
        known[entry.entry_id] = notices

    for notice_id, notice in notices.standing.items():
        _LOGGER.debug("Re-raising undismissed notice %s for %s", notice_id, entry.entry_id)
        async_create(
            hass,
            notice["message"],
            title=notice["title"],
            notification_id=_notification_id(entry, notice_id),
        )

    entry.async_on_unload(async_register_callback(hass, partial(_on_change, hass, entry)))


def _load(stored: object, entry: ConfigEntry) -> dict[str, StandingNotice]:
    """Read the standing set off disk, tolerating a file that is not what we wrote.

    `StoredNotices` and `StandingNotice` are compile-time only. On-disk data
    violates them freely -- a hand edit, a partially restored backup, a file
    written by a later version and read after a rollback -- and every such shape
    used to raise straight out of setup. Home Assistant already handles
    *undecodable* JSON by renaming the file and raising a core repair; the gap is
    valid JSON of the wrong shape, which it hands back intact.

    Failing there is the wrong trade by a wide margin. This module exists to tell
    the user about something that already happened; a panel that will not load
    because its *notification bookkeeping* is malformed has turned a cosmetic
    record into a dead integration, and one that stays dead, because setup is not
    retried on a bad shape. Falling back to empty loses only the memory of which
    notices were standing, and the next raise overwrites the file with a valid
    one -- so it self-heals rather than needing the user to find and delete it.
    """
    if stored is None:
        return {}
    standing = stored.get("standing") if isinstance(stored, dict) else None
    if not isinstance(standing, dict):
        _LOGGER.warning(
            "Ignoring the notice record for %s: expected an object with a 'standing' "
            "mapping, found %s. Any notice already dismissed stays dismissed; one still "
            "standing may be shown again.",
            entry.entry_id,
            type(standing if isinstance(stored, dict) else stored).__name__,
        )
        return {}
    kept: dict[str, StandingNotice] = {}
    for notice_id, notice in standing.items():
        if (
            isinstance(notice, dict)
            and isinstance(notice.get("title"), str)
            and isinstance(notice.get("message"), str)
        ):
            kept[str(notice_id)] = StandingNotice(title=notice["title"], message=notice["message"])
        else:
            _LOGGER.warning(
                "Dropping malformed notice %s for %s: a notice needs a title and a "
                "message, both strings",
                notice_id,
                entry.entry_id,
            )
    return kept


@callback
def async_raise(
    hass: HomeAssistant, entry: ConfigEntry, notice_id: str, *, title: str, message: str
) -> None:
    """Show a notice, and keep showing it until the user dismisses it.

    A callback rather than a coroutine because the callers are: a schema-change
    notice is raised from the MQTT client's own callback fan-out, which is not a
    place that can await.

    Re-raising the same notice id replaces the text in place, which is what makes
    the restore above a no-op when it lands on a notice already on screen.
    """
    async_create(hass, message, title=title, notification_id=_notification_id(entry, notice_id))
    notices = hass.data.get(_DATA, {}).get(entry.entry_id)
    if notices is None:
        # The entry was removed while this notice was in flight. Showing it is
        # still right -- it describes something that happened -- but there is
        # nothing left to record it against, and recreating the record here would
        # resurrect the file `async_forget` just deleted.
        _LOGGER.debug(
            "Raised %s for untracked entry %s; it will not survive a restart",
            notice_id,
            entry.entry_id,
        )
        return
    notices.standing[notice_id] = StandingNotice(title=title, message=message)
    _persist(notices)


@callback
def _on_change(
    hass: HomeAssistant,
    entry: ConfigEntry,
    change: UpdateType,
    notifications: dict[str, Notification],
) -> None:
    """Forget a notice once it is dismissed.

    Dismissal is the whole acknowledgement mechanism. There is no "read" signal
    for a notification, and inventing one -- a restart count, an age -- would
    either nag somebody who read it on day one or drop it before somebody on
    holiday got back. Dismissing is the act of a user who has seen it.

    Filtered to this entry's own ids because the dispatcher delivers every
    notification change in the system, most of which belong to other
    integrations.
    """
    if change is not UpdateType.REMOVED:
        return
    notices = hass.data.get(_DATA, {}).get(entry.entry_id)
    if notices is None:
        return
    dismissed = [
        notice_id
        for notice_id in notices.standing
        if _notification_id(entry, notice_id) in notifications
    ]
    if not dismissed:
        return
    for notice_id in dismissed:
        _LOGGER.debug("Notice %s dismissed for %s", notice_id, entry.entry_id)
        del notices.standing[notice_id]
    _persist(notices)


@callback
def _persist(notices: _Notices) -> None:
    """Queue a write of the current standing set.

    The snapshot is taken now rather than read through in the callback, because
    `async_delay_save` calls its argument when the write fires rather than when it
    is queued. Defensive rather than load-bearing, and worth being precise about:
    a `Store` keeps only one pending write, so the newest queued function is the
    one that runs, and every site that mutates the standing set calls this
    immediately afterwards. There is therefore no reachable state today in which
    the live dict has moved on and no newer write has superseded this one -- a
    mutation test confirms the difference is unobservable through the store.

    It stays a snapshot anyway. The cost is one shallow dict copy, and the
    property it buys -- what is queued is what was true when it was queued -- does
    not then depend on every future mutation site remembering to re-persist.
    """
    written = StoredNotices(standing=dict(notices.standing))
    notices.store.async_delay_save(lambda: written, _SAVE_DELAY)


async def async_forget(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop this entry's notices when the entry is removed.

    Both halves matter. The record has to go, or re-adding the same panel would
    restore notices about an upgrade the new entry never saw. The notifications
    themselves have to go too: nothing else clears them, so a removed panel would
    leave the user reading about a device that is no longer in their system.
    """
    notices = hass.data.get(_DATA, {}).pop(entry.entry_id, None)
    if notices is None:
        return
    for notice_id in notices.standing:
        async_dismiss(hass, _notification_id(entry, notice_id))
    await notices.store.async_remove()


def read_translations(language: str, section: str) -> dict[str, str]:
    """One notification's strings for one language, or an empty mapping.

    Read from this component's `notifications/` directory rather than through
    `homeassistant.helpers.translation`, because that helper filters to the
    categories Home Assistant defines and a persistent notification is not one of
    them -- a custom category loads as nothing at all. These are this
    integration's own package files, so reading them is not reaching into
    somebody else's layout.

    **A directory of its own, not `translations/`.** hassfest validates
    `strings.json` and `translations/en.json` against Home Assistant's schema and
    rejects any key it does not define, so a `notifications` section there fails
    the check outright -- which is what a custom category being unsupported looks
    like from the outside. Keeping these strings beside those files rather than
    inside them is what makes both true at once: hassfest sees only what it
    defines, and the notices keep per-language files.

    Falls back along the language chain -- `pt-BR`, then `pt`, then `en` -- so a
    regional variant with no file of its own still gets its language rather than
    English.

    Blocking file I/O. Callers run it in an executor.
    """
    directory = Path(__file__).parent / "notifications"
    for candidate in (f"{language}.json", f"{language.split('-')[0]}.json", "en.json"):
        path = directory / candidate
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _LOGGER.debug("Could not read notification strings from %s", path, exc_info=True)
            continue
        if not isinstance(loaded, dict):
            continue
        strings = loaded.get(section, {})
        if isinstance(strings, dict) and strings:
            return {str(key): str(value) for key, value in strings.items()}
    return {}
