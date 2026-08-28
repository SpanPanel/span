"""Surface schema findings as Home Assistant Repairs.

Two conditions reach the user, both defects: a field the adapter cannot resolve
(a sensor is dead), and a unit that disagrees with the schema (a reading or its
statistics are wrong). Both are reported regardless of install age — a defect is
not a change.

A third condition, a produced field nothing reads, is a sanctioned addition and
stays in the debug log.

One further notice lives here and is not a defect at all: entities that this
setup registered for the first time and disabled by default. Registering a new
diagnostic disabled is what keeps an upgrade from growing everybody's entity
list uninvited, but it also makes the addition invisible — the user only finds
it by opening the device's disabled-entity list. That notice is an event, not a
condition, and is raised on different terms from the two defects; see
`async_notice_new_disabled_entities`.

Every Repair here claims something the user owns is broken, so a finding whose
field path no enabled entity reads is not raised at all. Disabled-by-default
descriptions are registered but never added to hass, so they never self-register
with the coordinator and a finding against one has nothing to name; telling the
user "0 entity/entities are affected" only teaches them to ignore the category.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, EVENT_SCHEMA_ISSUE
from .schema_validation import SchemaFindings

_LOGGER = logging.getLogger(__name__)

_MAX_EXAMPLES = 3

# Issue-id prefixes. `_DEFECT_PREFIXES` is what the reconcile pass owns; the
# new-entity notice is deliberately not among them. See `_scoped_issue_ids`.
_DEFECT_PREFIXES = ("unresolved_", "unit_mismatch_")
_NEW_ENTITIES_PREFIX = "new_entities_"
"""Id prefix of the retired new-entity Repair.

Nothing raises one any more -- an addition is not a repair, and it is announced
as a notification by `additions` instead. The prefix survives so the ones already
standing on upgraded installs get cleared: they were raised `is_persistent`, so
without this they would outlive the mechanism that made them.
"""


_RETIRED_UPGRADE_ID = "panel_upgraded_to_ebus_v1"
"""Id stem of the retired firmware-upgrade Repair.

Nothing raises one any more. An upgrade that took nothing away is not a defect,
and the Repairs list stamped it with a severity and offered to ignore it, which
told the user their panel was broken; `notices` carries it as a notification that
survives a restart, which is the only property being a Repair was buying.

Unlike the other ids here this one is a whole id rather than a prefix -- it was
raised as `{stem}_{entry_id}` with nothing after -- so it is cleared by name.
"""


def _unresolved_id(entry_id: str, field_path: str) -> str:
    return f"unresolved_{entry_id}_{field_path}"


def _unit_id(entry_id: str, field_path: str) -> str:
    return f"unit_mismatch_{entry_id}_{field_path}"


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

    A finding with no affected entity is suppressed rather than raised, and a
    suppressed path leaves `wanted`, so an issue raised while entities existed is
    deleted once the last one goes — the same path that clears a genuinely
    resolved finding.

    Re-raises idempotently and deletes only on genuine resolution. Deleting is
    the one thing that clears a dismissal — `dismissed_version` is never
    compared against the running HA version, and neither the update branch nor
    the store reload touches it — so a delete-then-recreate loop would wipe
    every dismissal on every pass and turn an accepted notice into a permanent
    nag.
    """
    registry = ir.async_get(hass)
    wanted: set[str] = set()
    raised_unresolved: list[str] = []
    raised_mismatches: list[str] = []

    for field_path in sorted(findings.unresolved):
        affected = _affected(entity_ids_by_path, field_path)
        if not affected:
            _log_suppressed("unresolved-field", field_path)
            continue
        issue_id = _unresolved_id(entry.entry_id, field_path)
        wanted.add(issue_id)
        raised_unresolved.append(field_path)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            # Derived from live state, so it is re-asserted at every startup. A
            # non-persistent issue reloads as a tombstone carrying only the
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
        issue_id = _unit_id(entry.entry_id, mismatch.field_path)
        wanted.add(issue_id)
        raised_mismatches.append(mismatch.field_path)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
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

    # Defect prefixes only: everything in this scope is re-derived on every pass,
    # and the new-entity notice is not re-derivable at all.
    for issue_id in _scoped_issue_ids(registry, entry.entry_id, _DEFECT_PREFIXES) - wanted:
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    if wanted:
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
def _scoped_issue_ids(
    registry: ir.IssueRegistry, entry_id: str, prefixes: tuple[str, ...]
) -> set[str]:
    """Return this entry's issue ids under the given id prefixes.

    Scoping by entry is not cosmetic: with a shared namespace, a healthy panel's
    reconcile pass would delete a degraded panel's issues on every cycle, and
    removing one panel would clear every panel's issues.

    Which prefixes is not cosmetic either, and is why this takes them rather than
    answering for the whole domain. The reconcile pass deletes every id it did
    not re-derive, so it may only ever see `_DEFECT_PREFIXES`; the new-entity
    notice is derived exactly once and would not survive being reconciled against
    a pass that cannot re-derive it. Removal, which deletes unconditionally, is
    the one caller that passes every prefix.
    """
    scoped = tuple(f"{prefix}{entry_id}_" for prefix in prefixes)
    return {
        issue_id
        for (domain, issue_id) in registry.issues
        if domain == DOMAIN and issue_id.startswith(scoped)
    }


@callback
def async_clear_retired_new_entity_notices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete new-entity Repairs raised before additions became notifications.

    They were raised `is_persistent=True` precisely so a restart could not sweep
    them away, which now means an upgraded install keeps one standing in its
    Repairs list forever with nothing left to re-derive it. Cleared at setup
    rather than at removal, because the user is looking at it now.
    """
    registry = ir.async_get(hass)
    for issue_id in _scoped_issue_ids(registry, entry.entry_id, (_NEW_ENTITIES_PREFIX,)):
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
    if ir.async_get(hass).async_get_issue(DOMAIN, f"{_RETIRED_UPGRADE_ID}_{entry.entry_id}"):
        _LOGGER.debug("Clearing retired upgrade notice for %s", entry.entry_id)
        ir.async_delete_issue(hass, DOMAIN, f"{_RETIRED_UPGRADE_ID}_{entry.entry_id}")


@callback
def async_clear_schema_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove one entry's issues. Core does not do this on entry removal.

    Covers the new-entity notices too, and they are the ones that need it most:
    they are persistent, so unlike the defect notices they would not even be
    demoted to tombstones by a restart — a removed panel would leave them
    standing forever.
    """
    registry = ir.async_get(hass)
    every_prefix = (*_DEFECT_PREFIXES, _NEW_ENTITIES_PREFIX)
    for issue_id in _scoped_issue_ids(registry, entry.entry_id, every_prefix):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    # Setup normally clears this one, but an entry removed before it ever set up
    # successfully never got there.
    async_clear_retired_upgrade_notice(hass, entry)
