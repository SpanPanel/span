"""One reload per change to a config entry, however many parts of the integration ask.

**What a request means.** "A setup of this entry must begin after this moment."
Any setup satisfies every request made before it began, whoever started it: this
module, Home Assistant, a setup retry, the user's Reload, or the credential
rotation. A source asks once its write is complete.

Two kinds of request, because a failed setup treats them differently:

- **A reload** (`async_request_reload`) stands for a change to the entry, or for
  something a person did: the options listener, reauth, reconfigure, the CA re-pin
  Repair, an adopted-property save. It survives a setup that fails, so a
  reconfigure completed while a retry attempt is in flight reaches the very next
  setup rather than waiting for Home Assistant's retry timer.
- **A rebuild** (`async_request_rebuild`) is the running coordinator's: new
  entities, a circuit renamed in the SPAN app, a new schema generation, the solar
  migration. It belongs to the setup that built that coordinator, and a failed
  setup withdraws it.

**When the reload runs.** On a later pass of the event loop than the request,
never inside the caller: the caller is often the client's own push callback or an
update, which the reload tears down. While Home Assistant is starting, once it
has started, as Home Assistant's own setup retries and discovery flows do. Never
while it is stopping. The reload is Home Assistant's task, not the entry's,
because unloading an entry waits for the entry's own tasks and would wait for the
reload doing the unloading.

**Waiting for a reload in flight.** Before deciding, the run waits for whatever
holds the entry's setup lock: a setup, an unload, a reload, a removal. A request
that such a reload's setup applies then costs nothing more. That is not the wait
that once deadlocked two panels, which waited for every tracked task: nothing
that holds this lock waits for the run.

**What setup reads.** The listener asks for a reload only when the entry's data,
options, title or unique ID differ from what the running setup was built from.
A preference or a discovery key written to the entry asks for nothing: Home
Assistant reloads for the polling preference itself, and nothing here reads the
others. A writer that applies its own change, as setup does when it pins a CA or
renames an entry titled with its serial, writes through
`async_update_entry_applied` and asks for nothing either.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry, OperationNotAllowed, UnknownEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _EntryContent:
    """What setup reads of a config entry, copied so a later write cannot reach it."""

    data: dict[str, object]
    options: dict[str, object]
    title: str
    unique_id: str | None

    @classmethod
    def of(cls, entry: ConfigEntry) -> _EntryContent:
        """Copy the content of the entry as it stands."""
        return cls(
            data=copy.deepcopy(dict(entry.data)),
            options=copy.deepcopy(dict(entry.options)),
            title=entry.title,
            unique_id=entry.unique_id,
        )


@dataclass(slots=True)
class _EntryReloads:
    """One entry's requests, kept in `hass.data` so they outlive the runtime data a reload replaces."""

    unapplied: bool = False
    """A change or a person's request that no setup begun since has applied."""

    rebuild: bool = False
    """The running setup's own coordinator asked for a rebuild."""

    scheduled: bool = False
    """A run is waiting for Home Assistant to start, or is under way."""

    setups_begun: int = 0
    """Counts setups, so a run can tell whether its reload set the entry up."""

    applied: _EntryContent | None = None
    """What the running setup was built from, with the writes it applied itself."""


_RELOADS: HassKey[dict[str, _EntryReloads]] = HassKey(f"{DOMAIN}_entry_reloads")


def _record(hass: HomeAssistant, entry_id: str) -> _EntryReloads:
    """Return the entry's record, created empty if it has none."""
    return hass.data.setdefault(_RELOADS, {}).setdefault(entry_id, _EntryReloads())


@callback
def async_setup_began(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Record that a setup of the entry has begun: it applies every request made so far.

    The first statement of `async_setup_entry`, so every setup counts, whoever
    started it.
    """
    record = _record(hass, entry.entry_id)
    record.unapplied = False
    record.rebuild = False
    record.setups_begun += 1
    record.applied = _EntryContent.of(entry)


@callback
def async_setup_failed(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw the rebuild the failing setup's own coordinator asked for.

    Only that. A change, or a person's request, made while this setup was under
    way has not been applied by it, and the next setup has to begin straight
    after it, not at Home Assistant's retry timer.
    """
    if (record := hass.data.get(_RELOADS, {}).get(entry_id)) is not None:
        record.rebuild = False


@callback
def async_request_reload(hass: HomeAssistant, entry_id: str) -> None:
    """Ask for a setup of the entry to begin after this moment, for a change or a person's request."""
    if hass.config_entries.async_get_entry(entry_id) is None:
        return
    record = _record(hass, entry_id)
    record.unapplied = True
    _async_schedule(hass, entry_id, record)


@callback
def async_request_rebuild(hass: HomeAssistant, entry_id: str) -> None:
    """Ask for a setup of the entry to begin after this moment, for the running coordinator."""
    if hass.config_entries.async_get_entry(entry_id) is None:
        return
    record = _record(hass, entry_id)
    record.rebuild = True
    _async_schedule(hass, entry_id, record)


@callback
def async_entry_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask for a reload when the entry differs from what the running setup was built from.

    The update listener's whole work. Home Assistant calls it for any write to
    the entry, preferences and discovery keys included, and starts it eagerly
    inside the write, so it must not suspend.
    """
    applied = _record(hass, entry.entry_id).applied
    if applied is None or _EntryContent.of(entry) != applied:
        async_request_reload(hass, entry.entry_id)


@callback
def async_update_entry_applied(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    data: Mapping[str, object] | UndefinedType = UNDEFINED,
    title: str | UndefinedType = UNDEFINED,
) -> bool:
    """Write the entry for a writer that applies the change itself, asking for no reload.

    For setup's own writes and the running coordinator's bookkeeping. The fields
    written are recorded as applied before the write, so the listener, which Home
    Assistant starts inside it, finds them equal. A field changed from outside
    and not applied yet still differs, and still asks.
    """
    record = _record(hass, entry.entry_id)
    if (applied := record.applied) is not None:
        record.applied = _EntryContent(
            data=applied.data if isinstance(data, UndefinedType) else copy.deepcopy(dict(data)),
            options=applied.options,
            title=applied.title if isinstance(title, UndefinedType) else title,
            unique_id=applied.unique_id,
        )
    return hass.config_entries.async_update_entry(entry, data=data, title=title)


@callback
def async_forget_reloads(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the entry's record, so a run still pending for it ends without a word.

    The first statement of `async_remove_entry`: Home Assistant catches what that
    function raises, and a store removal failing after it would leave the record.
    """
    if (record := hass.data.get(_RELOADS, {}).pop(entry_id, None)) is not None:
        record.unapplied = False
        record.rebuild = False


@callback
def _async_schedule(hass: HomeAssistant, entry_id: str, record: _EntryReloads) -> None:
    """Start a run for the entry, once Home Assistant has started, unless one is pending."""
    if record.scheduled or hass.is_stopping:
        return
    record.scheduled = True

    @callback
    def _start(hass: HomeAssistant) -> None:
        hass.async_create_task(
            _async_run(hass, entry_id, record),
            f"{DOMAIN} reload {entry_id}",
            eager_start=False,
        )

    # Never cancelled. Started, it runs at once; while starting, the run finds
    # nothing to do if a setup came first. Should Home Assistant stop before it
    # has started, the event never fires and the process ends.
    async_at_started(hass, _start)


async def _async_run(hass: HomeAssistant, entry_id: str, record: _EntryReloads) -> None:
    """Reload the entry until no request is left that a setup has not applied.

    Goes round again only when its reload set the entry up and a request still
    arrived after that setup began, which that setup may not have read. Ends
    when nothing set the entry up, as after a failed unload or for a disabled
    entry: the request stays recorded, and the next one starts a new run.
    """
    try:
        while True:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                return
            await _async_wait_for_reload_in_flight(entry)
            if hass.config_entries.async_get_entry(entry_id) is None:
                return
            if not (record.unapplied or record.rebuild) or hass.is_stopping:
                return
            began = record.setups_begun
            try:
                await hass.config_entries.async_reload(entry_id)
            except (UnknownEntry, OperationNotAllowed) as err:
                _LOGGER.warning("Could not reload %s: %s", entry.title, err)
                return
            except Exception:  # noqa: BLE001 -- a task's exception would otherwise go unread
                _LOGGER.exception("Reloading %s failed", entry.title)
                return
            if record.setups_begun == began:
                return
    finally:
        record.scheduled = False


async def _async_wait_for_reload_in_flight(entry: ConfigEntry) -> None:
    """Return once nothing holds the entry's setup lock, at once if nothing does.

    Taken and released unconditionally. Asking `locked()` first would read False
    just after a release while a woken waiter has yet to run, and the run would
    then queue behind that waiter for a reload of its own. `asyncio.Lock` lets an
    acquirer through at once only when the lock is free and nobody waits.
    """
    async with entry.setup_lock:
        pass
