"""What the user is told when their panel's firmware changes data model.

This used to be two things at once: a hardcoded English notification about the
reload, and a translated Repair about the consequences. Neither was covered, so
the pairing survived until a screenshot showed it -- an upgrade that took nothing
away, filed under Warning, offering to be ignored.

The tests below pin the three properties that fixes it: one message, it is a
notification, and it outlives the restart that was the Repair's only advantage.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from span_panel_api import SpanMqttClient

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.notices import _DATA, async_restore
from custom_components.span_panel.schema_repairs import async_clear_retired_upgrade_notice

_RETIRED_ISSUE = "panel_upgraded_to_ebus_v1"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry in hass."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-001", title="SPAN Panel")
    mock.add_to_hass(hass)
    return mock


@pytest.fixture
async def coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> SpanPanelCoordinator:
    """Return a coordinator whose notices are tracked, as setup would leave it."""
    await async_restore(hass, entry)
    return SpanPanelCoordinator(hass, cast(SpanMqttClient, MagicMock()), entry)


def _notice(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any] | None:
    standing = hass.data.get("persistent_notification", {})
    return standing.get(f"{DOMAIN}_panel_upgraded_{entry.entry_id}")


async def test_the_upgrade_is_a_notification(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    await coordinator._explain_the_upgrade(None, "1.0")

    notice = _notice(hass, entry)
    assert notice is not None
    assert "flat" in notice["message"] and "1.0" in notice["message"]


async def test_the_upgrade_is_not_a_repair(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    """Nothing is broken, so nothing belongs in the list of things that are."""
    await coordinator._explain_the_upgrade(None, "1.0")

    assert not [issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN]


async def test_the_upgrade_is_told_in_one_message_not_two(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    """The reload and its consequences are one event and read as one.

    Two rows for one event is what teaches somebody to skim past both.
    """
    await coordinator._explain_the_upgrade(None, "1.0")

    ours = [key for key in hass.data.get("persistent_notification", {}) if DOMAIN in key]
    assert len(ours) == 1


async def test_the_message_says_what_changed_and_what_did_not(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    """A firmware upgrade invites the assumption that something was lost."""
    await coordinator._explain_the_upgrade(None, "1.0")

    message = _notice(hass, entry)["message"]  # type: ignore[index]
    assert "DSM Grid State" in message
    assert "Grid Islandable" in message
    assert "Microgrid Interconnect Device" in message
    assert "has gone away" in message


async def test_the_notice_survives_a_restart(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    """Durability was the Repair's one real advantage, and it has to be kept.

    A panel upgrades on its own schedule. Somebody away for the weekend must
    still find out that a device appeared and why a sensor changed provenance.
    """
    await coordinator._explain_the_upgrade(None, "1.0")

    # Writes are delayed so a burst of them collapses into one; Home Assistant
    # flushes at shutdown, and a restart that skipped it would test nothing.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()
    hass.data[_DATA].pop(entry.entry_id)
    hass.data["persistent_notification"].clear()
    await async_restore(hass, entry)

    assert _notice(hass, entry) is not None


async def test_a_downgrade_says_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    """Panel firmware does not roll back; the upgrade rehearsal swaps simulators.

    Announcing a retirement there would be noise about a transition no user has.
    """
    await coordinator._explain_the_upgrade("1.0", None)

    assert _notice(hass, entry) is None


async def test_the_retired_repair_is_cleared_from_an_upgraded_install(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """It is on screen now, and its owner has no reason to restart to be rid of it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{_RETIRED_ISSUE}_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_RETIRED_ISSUE,
    )

    async_clear_retired_upgrade_notice(hass, entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{_RETIRED_ISSUE}_{entry.entry_id}") is None


async def test_clearing_the_retired_repair_leaves_another_panels_alone(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Two panels share the domain, and one upgrading is not both."""
    other = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-002")
    other.add_to_hass(hass)
    for target in (entry, other):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{_RETIRED_ISSUE}_{target.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_RETIRED_ISSUE,
        )

    async_clear_retired_upgrade_notice(hass, entry)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"{_RETIRED_ISSUE}_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"{_RETIRED_ISSUE}_{other.entry_id}")
