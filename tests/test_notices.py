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
    async_forget,
    async_raise,
    async_restore,
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
