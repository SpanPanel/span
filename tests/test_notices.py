"""Notices survive a restart without pretending to be defects.

The property under test is the one that used to cost a Repair: a notice raised
while nobody was looking is still there when they look. Every test here either
restarts (drop the in-memory view, restore from the store) or dismisses, because
those are the only two things that decide whether a notice comes back.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.notices import (
    _DATA,
    _STORE_VERSION,
    async_forget,
    async_raise,
    async_restore,
    read_translations,
)

_NOTICE = "panel_upgraded"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry in hass."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-001")
    mock.add_to_hass(hass)
    return mock


@pytest.fixture
def other(hass: HomeAssistant) -> MockConfigEntry:
    """Return a second panel. Notices are per entry, and this is what proves it."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-002")
    mock.add_to_hass(hass)
    return mock


def _standing(hass: HomeAssistant) -> dict[str, Any]:
    """Every persistent notification currently on screen, by id."""
    return dict(hass.data.get("persistent_notification", {}))


def _id(entry: MockConfigEntry, notice_id: str = _NOTICE) -> str:
    return f"{DOMAIN}_{notice_id}_{entry.entry_id}"


async def _flush(hass: HomeAssistant) -> None:
    """Let the delayed store writes fire. Home Assistant does this at shutdown."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


async def _restart(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Restart Home Assistant, as far as this module can tell.

    The store is on disk and survives; the live view and the notifications are in
    memory and do not. Dropping exactly those two is what a restart does.

    Cleared rather than dismissed, which is the distinction the whole module turns
    on: a restart destroys a notification without anybody having read it, and
    dismissing here would quietly test the acknowledged path instead and report
    that notices vanish when they should not.
    """
    await _flush(hass)
    hass.data.get(_DATA, {}).pop(entry.entry_id, None)
    hass.data.get("persistent_notification", {}).clear()
    await async_restore(hass, entry)


# -- Raising -----------------------------------------------------------------


async def test_a_raised_notice_is_shown(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    notification = _standing(hass)[_id(entry)]
    assert notification["title"] == "Upgraded"
    assert notification["message"] == "Body"


async def test_a_notice_raised_before_a_restart_is_shown_after_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The whole point. A panel that upgrades while the owner is away still tells them."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    await _restart(hass, entry)

    assert _standing(hass)[_id(entry)]["message"] == "Body"


async def test_a_notice_survives_more_than_one_restart(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Restoring must re-record, not consume. Undismissed is undismissed."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    await _restart(hass, entry)
    await _restart(hass, entry)

    assert _id(entry) in _standing(hass)


async def test_raising_the_same_notice_again_replaces_its_text(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Restoring a notice already on screen must not stack a second copy."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="First")
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Second")

    assert [key for key in _standing(hass) if key.startswith(DOMAIN)] == [_id(entry)]
    assert _standing(hass)[_id(entry)]["message"] == "Second"


# -- Dismissal ---------------------------------------------------------------


async def test_a_dismissed_notice_does_not_come_back(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Dismissal is the acknowledgement. Ignoring it would turn a notice into a nag."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    async_dismiss(hass, _id(entry))
    await hass.async_block_till_done()
    hass.data.get(_DATA, {}).pop(entry.entry_id, None)
    await async_restore(hass, entry)

    assert _id(entry) not in _standing(hass)


async def test_dismissing_someone_elses_notification_leaves_ours_standing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The dispatcher delivers every notification change in the system, not just ours."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    async_dismiss(hass, "some_other_integration_thing")
    await _restart(hass, entry)

    assert _id(entry) in _standing(hass)


async def test_dismissing_one_panels_notice_leaves_the_other_panels_alone(
    hass: HomeAssistant, entry: MockConfigEntry, other: MockConfigEntry
) -> None:
    """Two panels upgrading are two notices; one owner reading is not both."""
    await async_restore(hass, entry)
    await async_restore(hass, other)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="First panel")
    async_raise(hass, other, _NOTICE, title="Upgraded", message="Second panel")

    async_dismiss(hass, _id(entry))
    await _restart(hass, entry)
    await _restart(hass, other)

    assert _id(entry) not in _standing(hass)
    assert _standing(hass)[_id(other)]["message"] == "Second panel"


# -- The reload race ---------------------------------------------------------


async def test_a_notice_raised_moments_before_a_reload_is_not_lost(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A schema upgrade raises a notice and schedules a reload in the same breath.

    Writes are delayed to coalesce them, so the setup that follows can arrive
    before the file has been written. Re-reading the store there would drop the
    notice from the standing set while it sat on the user's screen -- it would
    look fine until the next restart, and then be gone.
    """
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    await async_restore(hass, entry)  # the reload, before the delayed write lands

    # Asserted on the live view rather than on what comes back after a restart,
    # because the store is what makes this hard to see: it hands a pending write
    # back to its own reader, so a reload that re-read it would look correct here
    # and lose the notice only on the restart after the process ended.
    assert _NOTICE in hass.data[_DATA][entry.entry_id].standing

    await _restart(hass, entry)
    assert _standing(hass)[_id(entry)]["message"] == "Body"


# -- Removal -----------------------------------------------------------------


async def test_removing_the_entry_takes_its_notices_off_the_screen(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Nothing else clears them, so the user would keep reading about a panel they removed."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    await async_forget(hass, entry)

    assert _id(entry) not in _standing(hass)


async def test_re_adding_a_removed_panel_does_not_restore_its_old_notices(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The record has to go with the entry, or a new panel inherits an old upgrade."""
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")
    await async_forget(hass, entry)

    await async_restore(hass, entry)

    assert _id(entry) not in _standing(hass)


async def test_removing_one_panel_leaves_the_others_notices_alone(
    hass: HomeAssistant, entry: MockConfigEntry, other: MockConfigEntry
) -> None:
    await async_restore(hass, entry)
    await async_restore(hass, other)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="First panel")
    async_raise(hass, other, _NOTICE, title="Upgraded", message="Second panel")

    await async_forget(hass, entry)

    assert _id(entry) not in _standing(hass)
    assert _standing(hass)[_id(other)]["message"] == "Second panel"


async def test_raising_against_an_untracked_entry_still_reaches_the_user(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A notice in flight when the entry is removed describes something that happened.

    Showing it is right; recreating the record `async_forget` just deleted is not.
    """
    await async_restore(hass, entry)
    await async_forget(hass, entry)

    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    assert _standing(hass)[_id(entry)]["message"] == "Body"
    assert entry.entry_id not in hass.data.get(_DATA, {})


# -- A store file that is not what we wrote -------------------------------------
#
# `StoredNotices` and `StandingNotice` are compile-time only, so the disk can hold
# anything. Every shape below used to raise straight out of `async_setup_entry` --
# and out of the part of it above the `try`, so the entry went to SETUP_ERROR with
# no retry and stayed dead until somebody found and deleted the file by hand.
# Bookkeeping for a notification is not worth an integration that will not load.


def _seed(hass_storage: dict[str, Any], entry: MockConfigEntry, data: object) -> None:
    key = f"{DOMAIN}.notices.{entry.entry_id}"
    hass_storage[key] = {"version": _STORE_VERSION, "key": key, "data": data}


@pytest.mark.parametrize(
    ("shape", "description"),
    [
        ({"standing": ["panel_upgraded"]}, "a list where the mapping should be"),
        ({"standing": {"panel_upgraded": "oops"}}, "a string where the notice should be"),
        ({"standing": {"panel_upgraded": {"title": "T"}}}, "a notice with no message"),
        ({"standing": {"panel_upgraded": {"title": 1, "message": 2}}}, "non-string text"),
        (["standing"], "a list at the top level"),
        ({}, "an object with no standing key"),
        ("nonsense", "a bare string"),
    ],
)
async def test_a_malformed_store_does_not_take_the_integration_down(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entry: MockConfigEntry,
    shape: object,
    description: str,
) -> None:
    """Setup must survive every one of these; the record is recoverable, the entry is not."""
    _seed(hass_storage, entry, shape)

    await async_restore(hass, entry)

    assert hass.data[_DATA][entry.entry_id].standing == {}


async def test_a_malformed_store_is_overwritten_by_the_next_notice(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    """Self-healing is what makes falling back to empty the right trade.

    The user never has to find the file, because the next raise replaces it.
    """
    _seed(hass_storage, entry, {"standing": "nonsense"})
    await async_restore(hass, entry)

    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")
    await _restart(hass, entry)

    assert _standing(hass)[_id(entry)]["message"] == "Body"


async def test_one_malformed_notice_does_not_discard_its_healthy_neighbours(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    """Dropping the whole file for one bad row would lose notices that are still valid."""
    _seed(
        hass_storage,
        entry,
        {
            "standing": {
                "panel_upgraded": {"title": "Upgraded", "message": "Body"},
                "broken": {"title": "no message"},
            }
        },
    )

    await async_restore(hass, entry)

    assert _standing(hass)[_id(entry)]["message"] == "Body"
    assert "broken" not in hass.data[_DATA][entry.entry_id].standing


# -- The dismissal watch is unregistered on unload ------------------------------


async def test_unloading_stops_the_dismissal_watch(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Every setup registers a dispatcher handler, and the dispatcher fans out to all of them.

    Without `entry.async_on_unload` a reload leaves the previous handler behind,
    bound to an entry that is gone, and the leak grows by one per reload.

    Asserted through what a leaked handler would *do* rather than by counting
    subscribers: after unload nothing of ours is listening, so a dismissal reaches
    no one and the standing set is untouched.
    """
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    await entry._async_process_on_unload(hass)
    async_dismiss(hass, _id(entry))
    await hass.async_block_till_done()

    assert _NOTICE in hass.data[_DATA][entry.entry_id].standing


# -- A queued write records what was shown, not what came after -----------------


async def test_a_queued_write_records_the_set_as_it_was_when_raised(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """`async_delay_save` calls its argument when the write fires, not when it is queued.

    Closing over the live dict would record whatever the set had become by then --
    a state nobody was ever shown.
    """
    await async_restore(hass, entry)
    async_raise(hass, entry, _NOTICE, title="Upgraded", message="Body")

    # A second notice queues its own write; the first write must still be able to
    # produce the snapshot it was given rather than reading through to this one.
    async_raise(hass, entry, "second", title="Second", message="Also")
    await _flush(hass)
    hass.data[_DATA].pop(entry.entry_id)
    await async_restore(hass, entry)

    assert _standing(hass)[_id(entry)]["message"] == "Body"
    assert _standing(hass)[_id(entry, "second")]["message"] == "Also"


# -- Translations off disk ------------------------------------------------------


def test_a_translation_file_of_the_wrong_shape_falls_through_to_english() -> None:
    """Same class as the store: our own files are fine, and the disk is not ours."""
    assert read_translations("xx", "panel_upgraded")["title"]
