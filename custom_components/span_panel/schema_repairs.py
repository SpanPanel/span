"""Surface schema findings as Home Assistant Repairs.

Two conditions reach the user, both defects: a field the adapter cannot resolve
(a sensor is dead), and a unit that disagrees with the schema (a reading or its
statistics are wrong). Both are reported regardless of install age — a defect is
not a change.

A third condition, a produced field nothing reads, is a sanctioned addition and
stays in the debug log.

Setup also clears two retired Repairs here, the new-entity notice and the
firmware-upgrade notice, which only untagged builds raised, never a release,
and nothing raises now; `additions` and `notices` announce those events as
notifications instead.

Every Repair here claims something the user owns is broken, so a finding whose
field path no enabled entity reads is not raised at all. Disabled-by-default
descriptions are registered but never added to hass, so they never self-register
with the coordinator and a finding against one has nothing to name; telling the
user "0 entity/entities are affected" only teaches them to ignore the category.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, EVENT_SCHEMA_ISSUE
from .entry_issues import EntryIssue, EntryIssueFamily, async_create_entry_issue
from .schema_validation import SchemaFindings

_LOGGER = logging.getLogger(__name__)

_MAX_EXAMPLES = 3

_DEFECTS: Final = (EntryIssueFamily.UNRESOLVED_FIELD, EntryIssueFamily.UNIT_MISMATCH)
"""The kinds the reconcile pass derives, and so the only ones it may delete.

It deletes every issue in its set that it did not raise again, so an issue it
cannot derive must never be in that set.
"""


def _defect_issue_pattern(entry_id: str) -> str:
    """Return the pattern matching the id of every defect issue of one entry.

    Scoped to the entry because panels share the domain: a healthy panel's
    pass would otherwise delete a degraded panel's issues on every cycle. Home
    Assistant matches the pattern from the start of each id, and the entry's
    part of each prefix is closed with an underscore. That keeps one entry's
    pattern off every other entry's issues only because no entry id Home
    Assistant creates holds an underscore (see `EntryIssueFamily`).
    """
    alternatives = "|".join(re.escape(defect.entry_prefix(entry_id)) for defect in _DEFECTS)
    return f"(?:{alternatives})"


def _affected(entity_ids_by_path: Mapping[str, list[str]], field_path: str) -> list[str]:
    """Entities in hass that read this field, sorted.

    Sorted so an unchanged panel produces an unchanged payload: the update branch
    only rewrites the entry when something actually differs.
    """
    return sorted(entity_ids_by_path.get(field_path, []))


def _log_suppressed(condition: str, field_path: str) -> None:
    _LOGGER.debug(
        "Suppressed %s Repair for %s: no enabled entity reads that field path, so "
        "nothing the user owns is affected",
        condition,
        field_path,
    )


def _placeholders(affected: list[str]) -> dict[str, str]:
    """Count plus a bounded sample, never the full list.

    One missing `circuit.instant_power_w` affects every circuit on the panel.
    """
    return {
        "count": str(len(affected)),
        "examples": ", ".join(affected[:_MAX_EXAMPLES]),
    }


@callback
def async_sync_schema_issues(
    hass: HomeAssistant,
    entry: ConfigEntry,
    findings: SchemaFindings,
    entity_ids_by_path: Mapping[str, list[str]],
) -> None:
    """Reconcile Repairs against the current findings.

    One issue per (class, field path). Aggregating would be actively harmful:
    `async_get_or_create`'s update branch preserves `dismissed_version`, so a
    user who dismissed an aggregate would never be told when another field
    joined it.

    One refresh of this entry's defect issues (`ir.async_refresh_issues`).
    Each finding raised again touches its issue, which keeps its dismissal, and
    when the pass finishes the refresh deletes every issue in its set that the
    pass did not touch: a finding that resolved, and one suppressed because no
    entity reads its field any more, so an issue raised while entities existed
    goes once the last one does. An exception inside the pass cancels the
    refresh instead, and nothing the pass left untouched is deleted.

    Nothing else is deleted. Deleting is the one thing that clears a dismissal
    -- `dismissed_version` is never compared against the running HA version,
    and neither the update branch nor the store reload touches it -- so a pass
    that deleted an issue to raise it again would wipe every dismissal and turn
    an accepted notice into a permanent nag.

    The set is chosen by id rather than by the issues' link to the entry, so it
    also holds those an older release raised before issues were linked: each
    one still found is linked as it is raised again, and the rest are deleted.
    """
    raised_unresolved: list[str] = []
    raised_mismatches: list[str] = []

    with ir.async_refresh_issues(
        hass, DOMAIN, issue_id_pattern=_defect_issue_pattern(entry.entry_id)
    ):
        for field_path in sorted(findings.unresolved):
            affected = _affected(entity_ids_by_path, field_path)
            if not affected:
                _log_suppressed("unresolved-field", field_path)
                continue
            raised_unresolved.append(field_path)
            async_create_entry_issue(
                hass,
                entry,
                EntryIssueFamily.UNRESOLVED_FIELD.for_key(field_path),
                is_fixable=False,
                # Derived from live state, so it is re-asserted at every startup.
                # A non-persistent issue reloads as a tombstone carrying only the
                # dismissal, which is what lets re-assertion happen without
                # resurrecting one the user already accepted.
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="schema_field_unresolved",
                translation_placeholders={"field_path": field_path, **_placeholders(affected)},
            )

        for mismatch in findings.unit_mismatches:
            affected = _affected(entity_ids_by_path, mismatch.field_path)
            if not affected:
                _log_suppressed("unit-mismatch", mismatch.field_path)
                continue
            raised_mismatches.append(mismatch.field_path)
            async_create_entry_issue(
                hass,
                entry,
                EntryIssueFamily.UNIT_MISMATCH.for_key(mismatch.field_path),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="schema_unit_mismatch",
                translation_placeholders={
                    "field_path": mismatch.field_path,
                    "ha_unit": mismatch.ha_unit,
                    "schema_unit": mismatch.schema_unit,
                    **_placeholders(affected),
                },
            )

    if raised_unresolved or raised_mismatches:
        # The event mirrors what the user was actually told: a suppressed finding
        # is deliberately not user-facing, and an automation reacting to one
        # would be reacting to a defect that took nothing down.
        hass.bus.async_fire(
            EVENT_SCHEMA_ISSUE,
            {
                "entry_id": entry.entry_id,
                "unresolved": raised_unresolved,
                "unit_mismatches": raised_mismatches,
            },
        )


@callback
def async_clear_retired_new_entity_notices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete new-entity Repairs raised before additions became notifications.

    They were raised `is_persistent=True` precisely so a restart could not sweep
    them away, which now means an upgraded install keeps one standing in its
    Repairs list forever with nothing left to re-derive it. Cleared at setup
    rather than left for the entry's removal, because the user is looking at it
    now. Only this entry's: each id carries its entry's.
    """
    prefix = EntryIssueFamily.RETIRED_NEW_ENTITIES.entry_prefix(entry.entry_id)
    for issue_id in [
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(prefix)
    ]:
        _LOGGER.debug("Clearing retired new-entity notice %s", issue_id)
        ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_clear_retired_upgrade_notice(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the firmware-upgrade Repair raised before it became a notification.

    Cleared at setup rather than left to expire: it was raised non-persistent, so
    a restart would sweep it away on its own, but the user is looking at it now
    and has no reason to restart. The dismissal tombstone goes with it, which
    costs nothing -- there is no longer an issue for it to suppress.
    """
    issue_id = EntryIssue.RETIRED_EBUS_UPGRADE.issue_id(entry.entry_id)
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id):
        _LOGGER.debug("Clearing retired upgrade notice for %s", entry.entry_id)
        ir.async_delete_issue(hass, DOMAIN, issue_id)
