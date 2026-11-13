"""Tests for the Repair raised when a panel is on flat-schema firmware.

spanos2 firmware publishes the older flat eBus schema, which this integration no
longer parses. Setup refuses such a panel and retries; this Repair is what tells
the user why, and that the remedy is the panel's firmware rather than anything in
Home Assistant. The wording is approved verbatim, so the English is pinned here
exactly, and every locale is held to the same placeholders and the same rule of
naming the firmware line rather than a release.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from string import Formatter

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.flat_firmware_repairs import (
    FLAT_FIRMWARE_ISSUE_PREFIX,
    async_clear_flat_firmware,
    async_raise_flat_firmware,
    flat_firmware_issue_id,
)

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"

_LANGUAGE_FILES: dict[str, Path] = {
    "en": _COMPONENT / "strings.json",
    "es": _COMPONENT / "translations" / "es.json",
    "fr": _COMPONENT / "translations" / "fr.json",
    "ja": _COMPONENT / "translations" / "ja.json",
    "pt": _COMPONENT / "translations" / "pt.json",
}
"""English is read from `strings.json`, the source `translations/en.json` is generated from."""

_KEY = "panel_firmware_flat"

_TITLE = ("issues", _KEY, "title")
_DESCRIPTION = ("issues", _KEY, "description")
_MESSAGE = ("exceptions", _KEY, "message")
_FLOW_ERROR = ("config", "error", _KEY)
_FLOW_ABORT = ("config", "abort", _KEY)

_APPROVED_ENGLISH: dict[tuple[str, ...], str] = {
    _TITLE: "SPAN panel firmware needs an update",
    _DESCRIPTION: (
        "{panel} is on spanos2 firmware, which publishes the older flat eBus schema"
        " that this integration no longer supports. Update the panel's firmware;"
        " it reconnects on its own once updated."
    ),
    _MESSAGE: (
        "SPAN panel {panel} is on spanos2 firmware, which publishes the older flat"
        " eBus schema; update the panel's firmware and it reconnects on its own"
    ),
    _FLOW_ERROR: (
        "This panel is on spanos2 firmware, which publishes the older flat eBus"
        " schema. Update the panel's firmware, then add it again."
    ),
    _FLOW_ABORT: (
        "This panel is on spanos2 firmware, which publishes the older flat eBus"
        " schema. Update the panel's firmware, then add it again."
    ),
}

_PLACEHOLDERS: dict[tuple[str, ...], set[str]] = {
    _TITLE: set(),
    _DESCRIPTION: {"panel"},
    _MESSAGE: {"panel"},
    _FLOW_ERROR: set(),
    _FLOW_ABORT: set(),
}
"""What each string may name: the entry's title where one is supplied, and nothing else.

The config flow has no entry yet, so its error and abort name no panel at all.
"""

_RELEASE_NUMBER = re.compile(r"\br\d{6}\b")
"""A SPAN release number such as r202633, which the strings must never name as the boundary.

The firmware line is the boundary rather than a release: r202633 on spanos3 is
parent/child, as the captures under `tests/fixtures/expected_entities/` record.
"""


def _text(language: str, location: tuple[str, ...]) -> str:
    """One string from one language's file, by its path of keys."""
    node: object = json.loads(_LANGUAGE_FILES[language].read_text(encoding="utf-8"))
    for key in location:
        assert isinstance(node, dict), location
        node = node[key]
    assert isinstance(node, str), location
    return node


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field}


def _entry(hass: HomeAssistant, entry_id: str = "entry-flat") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        title=f"Span Panel {entry_id}",
        unique_id=f"sp2-{entry_id}",
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, flat_firmware_issue_id(entry.entry_id))


# ---------- the Repair ----------


def test_the_issue_id_is_the_prefix_and_the_entry_id() -> None:
    """One Repair per entry, so a second panel on current firmware is not caught up in it."""
    assert FLAT_FIRMWARE_ISSUE_PREFIX == "flat_firmware_"
    assert flat_firmware_issue_id("entry-flat") == "flat_firmware_entry-flat"


async def test_raising_names_the_panel_in_an_error_nobody_can_click_through(
    hass: HomeAssistant,
) -> None:
    """An error because setup has stopped; not fixable because the remedy is the panel's.

    Not persistent either: every setup reads the schema again, so the verdict is
    always re-derived and a restart has nothing to carry over.
    """
    entry = _entry(hass)

    async_raise_flat_firmware(hass, entry)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.domain == DOMAIN
    assert issue.issue_id == "flat_firmware_entry-flat"
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_key == _KEY
    assert issue.translation_placeholders == {"panel": "Span Panel entry-flat"}


async def test_the_description_names_only_the_placeholders_the_repair_supplies(
    hass: HomeAssistant,
) -> None:
    """A placeholder nothing supplies would reach the user as a literal `{name}`."""
    entry = _entry(hass)

    async_raise_flat_firmware(hass, entry)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert _placeholders(_text("en", _DESCRIPTION)) == set(issue.translation_placeholders)


async def test_clearing_removes_the_repair(hass: HomeAssistant) -> None:
    """A setup that finds parent/child firmware takes the notice down."""
    entry = _entry(hass)
    async_raise_flat_firmware(hass, entry)

    async_clear_flat_firmware(hass, entry)

    assert _issue(hass, entry) is None


async def test_clearing_with_nothing_standing_is_harmless(hass: HomeAssistant) -> None:
    """Setup clears on every pass that proceeds, so most clears find nothing to remove."""
    entry = _entry(hass)

    async_clear_flat_firmware(hass, entry)

    assert _issue(hass, entry) is None


async def test_each_panel_has_its_own_repair(hass: HomeAssistant) -> None:
    """Clearing one panel's notice leaves another panel's standing."""
    updated = _entry(hass, "entry-updated")
    still_flat = _entry(hass, "entry-still-flat")
    async_raise_flat_firmware(hass, updated)
    async_raise_flat_firmware(hass, still_flat)

    async_clear_flat_firmware(hass, updated)

    assert _issue(hass, updated) is None
    still_standing = _issue(hass, still_flat)
    assert still_standing is not None
    assert still_standing.translation_placeholders == {"panel": "Span Panel entry-still-flat"}


# ---------- the strings ----------


@pytest.mark.parametrize("location", list(_APPROVED_ENGLISH), ids=".".join)
def test_the_english_is_the_approved_wording(location: tuple[str, ...]) -> None:
    """Approved word for word, so any drift is a change to be approved again."""
    assert _text("en", location) == _APPROVED_ENGLISH[location]


@pytest.mark.parametrize("language", list(_LANGUAGE_FILES))
@pytest.mark.parametrize("location", list(_PLACEHOLDERS), ids=".".join)
def test_every_language_names_only_what_is_supplied(
    language: str, location: tuple[str, ...]
) -> None:
    """The sync hook checks that keys match, not that a translation keeps `{panel}` intact."""
    assert _placeholders(_text(language, location)) == _PLACEHOLDERS[location]


@pytest.mark.parametrize("language", list(_LANGUAGE_FILES))
@pytest.mark.parametrize(
    "location", [_DESCRIPTION, _MESSAGE, _FLOW_ERROR, _FLOW_ABORT], ids=".".join
)
def test_every_language_names_the_firmware_line_and_no_release(
    language: str, location: tuple[str, ...]
) -> None:
    """spanos2 and the flat eBus schema are the boundary; a release number is not."""
    text = _text(language, location)
    assert "spanos2" in text
    assert "eBus" in text
    assert _RELEASE_NUMBER.search(text) is None
