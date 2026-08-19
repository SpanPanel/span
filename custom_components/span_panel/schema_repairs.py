"""Surface schema findings as Home Assistant Repairs.

Two conditions reach the user, both defects: a field the adapter cannot resolve
(a sensor is dead), and a unit that disagrees with the schema (a reading or its
statistics are wrong). Both are reported regardless of install age — a defect is
not a change.

A third condition, a produced field nothing reads, is a sanctioned addition and
stays in the debug log.
"""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, EVENT_SCHEMA_ISSUE
from .schema_validation import SchemaFindings

_MAX_EXAMPLES = 3


def _unresolved_id(entry_id: str, field_path: str) -> str:
    return f"unresolved_{entry_id}_{field_path}"


def _unit_id(entry_id: str, field_path: str) -> str:
    return f"unit_mismatch_{entry_id}_{field_path}"


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

    Re-raises idempotently and deletes only on genuine resolution. Deleting is
    the one thing that clears a dismissal — `dismissed_version` is never
    compared against the running HA version, and neither the update branch nor
    the store reload touches it — so a delete-then-recreate loop would wipe
    every dismissal on every pass and turn an accepted notice into a permanent
    nag.
    """
    registry = ir.async_get(hass)
    wanted: set[str] = set()

    for field_path in sorted(findings.unresolved):
        issue_id = _unresolved_id(entry.entry_id, field_path)
        wanted.add(issue_id)
        # Sorted so an unchanged panel produces an unchanged payload: the update
        # branch only rewrites the entry when something actually differs.
        affected = sorted(entity_ids_by_path.get(field_path, []))
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
            translation_placeholders={
                "field_path": field_path,
                # A count plus a few examples, never the full list: one missing
                # `circuit.instant_power_w` affects every circuit on the panel.
                "count": str(len(affected)),
                "examples": ", ".join(affected[:_MAX_EXAMPLES]) or "none",
            },
        )

    for mismatch in findings.unit_mismatches:
        issue_id = _unit_id(entry.entry_id, mismatch.field_path)
        wanted.add(issue_id)
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
            },
        )

    for issue_id in _ours(registry, entry.entry_id) - wanted:
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    if wanted:
        hass.bus.async_fire(
            EVENT_SCHEMA_ISSUE,
            {
                "entry_id": entry.entry_id,
                "unresolved": sorted(findings.unresolved),
                "unit_mismatches": [m.field_path for m in findings.unit_mismatches],
            },
        )


def _ours(registry: ir.IssueRegistry, entry_id: str) -> set[str]:
    """Our issue ids for ONE config entry.

    Scoping by entry is not cosmetic: with a shared namespace, a healthy panel's
    reconcile pass would delete a degraded panel's issues on every cycle, and
    removing one panel would clear every panel's issues.
    """
    prefixes = (f"unresolved_{entry_id}_", f"unit_mismatch_{entry_id}_")
    return {
        issue_id
        for (domain, issue_id) in registry.issues
        if domain == DOMAIN and issue_id.startswith(prefixes)
    }


@callback
def async_clear_schema_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove one entry's issues. Core does not do this on entry removal."""
    registry = ir.async_get(hass)
    for issue_id in _ours(registry, entry.entry_id):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
