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

from collections.abc import Collection, Mapping
import hashlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN, EVENT_SCHEMA_ISSUE
from .schema_validation import SchemaFindings

_LOGGER = logging.getLogger(__name__)

_MAX_EXAMPLES = 3

# Issue-id prefixes. `_DEFECT_PREFIXES` is what the reconcile pass owns; the
# new-entity notice is deliberately not among them. See `_scoped_issue_ids`.
_DEFECT_PREFIXES = ("unresolved_", "unit_mismatch_")
_NEW_ENTITIES_PREFIX = "new_entities_"


def _unresolved_id(entry_id: str, field_path: str) -> str:
    return f"unresolved_{entry_id}_{field_path}"


def _unit_id(entry_id: str, field_path: str) -> str:
    return f"unit_mismatch_{entry_id}_{field_path}"


def _new_entities_id(entry_id: str, unique_ids: Collection[str]) -> str:
    """One id per (entry, exact set of new entities).

    Keyed on the set and not on the entry alone for the reason the two defect
    notices are keyed per field path: `async_get_or_create`'s update branch
    preserves `dismissed_version` while replacing the placeholders, so an entry-
    wide id would let a user who dismissed "Part Number appeared" never be told
    about the next addition — it would silently rewrite the notice they already
    put away.

    Truncated to 12 hex characters. The digest only has to separate one set from
    another within a single config entry, and the id ends up in a storage file a
    human occasionally reads.
    """
    joined = "\n".join(sorted(unique_ids))
    digest = hashlib.sha256(joined.encode()).hexdigest()[:12]
    return f"{_NEW_ENTITIES_PREFIX}{entry_id}_{digest}"


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
def async_registered_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> frozenset[str]:
    """Return the unique_ids already registered for this entry.

    Taken by `async_setup_entry` immediately before the platforms are forwarded,
    which is the last moment "already registered" still means "registered by an
    earlier run". Unique_ids rather than entity_ids: an entity_id is the user's
    to rename, a unique_id is the identity the registry itself keys on.
    """
    return frozenset(
        registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    )


def _label(registry_entry: er.RegistryEntry) -> str:
    """Return what to call an entity the user has never seen.

    A disabled entity has no state, so there is no friendly name to read off the
    state machine — only what the registry recorded when the platform added it.
    The entity_id is the last resort rather than the first choice because it is
    the name the user will *not* see in the device's disabled-entity list.
    """
    return registry_entry.name or registry_entry.original_name or registry_entry.entity_id


@callback
def async_notice_new_disabled_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    known_unique_ids: Collection[str],
) -> None:
    """Tell the user about entities this setup added and disabled.

    A release that adds a diagnostic with `entity_registry_enabled_default=False`
    grows nobody's entity list, which is the point — and is also why the addition
    reaches the user through nothing at all. This is the notice for that: the
    entities exist, they are switched off, and here is what they are called.

    Silent on a first install. With nothing registered beforehand every entity is
    new, so the notice would name the entire integration and teach the user to
    ignore it. An empty `known_unique_ids` is the probe for that:
    `er.async_entries_for_config_entry` answers with nothing for an entry that
    has never registered anything.

    An EVENT, not a condition, and that shapes three decisions:

    * `is_persistent=True`, unlike the two defect notices. Those are re-derived
      from live state at every startup, so they can afford to reload as
      tombstones. This one cannot be re-derived at all: on the next startup the
      entity is in `known_unique_ids` and the diff is empty by construction. A
      non-persistent issue would therefore vanish unread at the first restart
      after the upgrade, which for a user who was away is the same silent add
      the notice exists to prevent.
    * It is never reconciled away. `async_sync_schema_issues` deletes the defect
      ids it does not re-derive, and applying that here would delete this notice
      on the very next startup. Its reconcile scope is `_DEFECT_PREFIXES`, which
      deliberately does not include this one.
    * Raising is skipped outright when the id already exists. What that buys is
      narrow and worth stating exactly: a repeat of the same set cannot rewrite
      the text of a notice the user has already read. Without it the repeat would
      take `async_get_or_create`'s update branch, which replaces the placeholders
      — so a set that came back with a renamed entity would silently restate
      itself. It buys nothing against duplication, which the shared id already
      rules out.

    Severity is the mildest Home Assistant offers. `IssueSeverity` has no
    informational member — it is CRITICAL, ERROR, WARNING — so WARNING is the
    floor, the same floor `panel_upgraded_to_ebus_v1` settled on for the same
    reason: nothing is broken and no action is required.
    """
    if not known_unique_ids:
        _LOGGER.debug(
            "Suppressed new-entity notice for %s: nothing was registered before this "
            "setup, so this is a first install and every entity is new",
            entry.entry_id,
        )
        return

    new_disabled = [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if registry_entry.unique_id not in known_unique_ids
        and registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    ]
    if not new_disabled:
        return

    issue_id = _new_entities_id(
        entry.entry_id, [registry_entry.unique_id for registry_entry in new_disabled]
    )
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None:
        _LOGGER.debug("New-entity notice %s already raised; leaving it alone", issue_id)
        return

    labels = sorted(_label(registry_entry) for registry_entry in new_disabled)
    _LOGGER.debug("Raising new-entity notice %s for %s", issue_id, labels)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="new_entities_disabled",
        translation_placeholders=_placeholders(labels),
    )


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
