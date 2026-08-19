"""An entity that arrives disabled must not arrive silently.

`battery.part_number` shipped with `entity_registry_enabled_default=False` so
that upgrading would not grow anybody's entity list uninvited. It worked, and
the cost was that nothing whatsoever told the user the sensor now existed --
they found it by opening the device's disabled-entity list on a hunch.

These cover the notice that closes that gap, and the four ways it could be worse
than nothing: shouting on a first install, being swallowed by an earlier
dismissal, nagging on every restart, or vanishing unread at the first one.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.schema_repairs import (
    _new_entities_id,
    async_clear_schema_issues,
    async_notice_new_disabled_entities,
    async_registered_unique_ids,
    async_sync_schema_issues,
)
from custom_components.span_panel.schema_validation import SchemaFindings

_PART_NUMBER = "sp3-001_bess_part_number"


@pytest.fixture
def entry(hass) -> MockConfigEntry:
    """Return a config entry in hass. No conftest fixture exists for this."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-001")
    mock.add_to_hass(hass)
    return mock


def _register(
    hass,
    entry: MockConfigEntry,
    unique_id: str,
    *,
    disabled: bool = True,
    name: str | None = None,
) -> er.RegistryEntry:
    """Register one entity the way a platform would."""
    return er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        original_name=name,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION if disabled else None,
    )


def _notices(hass, entry: MockConfigEntry) -> list[ir.IssueEntry]:
    prefix = f"new_entities_{entry.entry_id}_"
    return [
        issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith(prefix)
    ]


# --- The gap this closes --------------------------------------------------


async def test_a_new_disabled_entity_raises_one_notice_naming_it(hass, entry) -> None:
    """The `battery.part_number` case, end to end."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False, name="Serial Number")
    known = async_registered_unique_ids(hass, entry)

    _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)

    notices = _notices(hass, entry)
    assert len(notices) == 1
    assert notices[0].translation_key == "new_entities_disabled"
    assert notices[0].translation_placeholders == {"count": "1", "examples": "Part Number"}


async def test_the_notice_is_informational_and_not_fixable(hass, entry) -> None:
    """Nothing is broken; data became available.

    `IssueSeverity` has no informational member -- CRITICAL, ERROR, WARNING --
    so WARNING is the mildest Home Assistant offers, and is what the equally
    action-free `panel_upgraded_to_ebus_v1` notice settled on.
    """
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")

    async_notice_new_disabled_entities(hass, entry, known)

    notice = _notices(hass, entry)[0]
    assert notice.severity is ir.IssueSeverity.WARNING
    assert notice.is_fixable is False


async def test_the_notice_falls_back_to_the_entity_id_when_unnamed(hass, entry) -> None:
    """A disabled entity has no state, so there is no friendly name to read."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    registered = _register(hass, entry, _PART_NUMBER, name=None)

    async_notice_new_disabled_entities(hass, entry, known)

    assert _notices(hass, entry)[0].translation_placeholders["examples"] == registered.entity_id


# --- Silent on a first install --------------------------------------------


async def test_a_first_install_raises_no_notice(hass, entry) -> None:
    """Everything is new, so naming it would name the whole integration."""
    known = async_registered_unique_ids(hass, entry)
    assert known == frozenset()

    _register(hass, entry, _PART_NUMBER, name="Part Number")
    _register(hass, entry, "sp3-001_bess_model", name="Model")
    async_notice_new_disabled_entities(hass, entry, known)

    assert _notices(hass, entry) == []


async def test_another_entrys_history_does_not_make_this_one_established(hass) -> None:
    """The probe is scoped per entry, so a second panel still installs quietly."""
    established = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-old")
    established.add_to_hass(hass)
    _register(hass, established, "sp3-old_bess_serial_number", disabled=False)

    fresh = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-new")
    fresh.add_to_hass(hass)
    known = async_registered_unique_ids(hass, fresh)
    _register(hass, fresh, "sp3-new_bess_part_number", name="Part Number")

    async_notice_new_disabled_entities(hass, fresh, known)

    assert _notices(hass, fresh) == []


# --- Only the invisible additions -----------------------------------------


async def test_an_entity_that_is_enabled_by_default_raises_no_notice(hass, entry) -> None:
    """It shows up in the user's entity list on its own; a notice is noise."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)

    _register(hass, entry, "sp3-001_bess_model", disabled=False, name="Model")
    async_notice_new_disabled_entities(hass, entry, known)

    assert _notices(hass, entry) == []


async def test_a_user_disabled_entity_is_not_a_new_addition(hass, entry) -> None:
    """Only `INTEGRATION` means "we shipped it switched off"."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)

    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "sp3-001_bess_model",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    async_notice_new_disabled_entities(hass, entry, known)

    assert _notices(hass, entry) == []


async def test_the_named_entities_are_bounded_and_counted(hass, entry) -> None:
    """The same rule the defect Repairs follow: a few names plus a count."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)

    for n in range(12):
        _register(hass, entry, f"sp3-001_new_{n:02d}", name=f"New Sensor {n:02d}")
    async_notice_new_disabled_entities(hass, entry, known)

    placeholders = _notices(hass, entry)[0].translation_placeholders
    assert placeholders["count"] == "12"
    assert placeholders["examples"].count(",") < 5
    assert "New Sensor 00" in placeholders["examples"]


# --- Dismissing one set must not swallow a later, different set -----------


async def test_a_dismissed_notice_does_not_swallow_a_later_addition(hass, entry) -> None:
    """The trap that forced per-field ids on the degradation Repairs.

    `async_get_or_create`'s update branch preserves `dismissed_version` while
    replacing the placeholders, so an entry-wide id would rewrite the notice the
    user put away rather than raising a new one.
    """
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)

    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)

    first = _notices(hass, entry)[0]
    ir.async_ignore_issue(hass, DOMAIN, first.issue_id, True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, first.issue_id).dismissed_version

    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, "sp3-001_bess_cell_temperature", name="Cell Temperature")
    async_notice_new_disabled_entities(hass, entry, known)

    by_id = {notice.issue_id: notice for notice in _notices(hass, entry)}
    assert len(by_id) == 2
    second = next(issue for issue_id, issue in by_id.items() if issue_id != first.issue_id)
    assert second.dismissed_version is None
    assert second.translation_placeholders["examples"] == "Cell Temperature"
    assert by_id[first.issue_id].dismissed_version is not None


async def test_the_id_is_keyed_on_the_exact_set(hass, entry) -> None:
    """Two different sets produce two different ids; the same set, one id."""
    assert _new_entities_id("e1", ["a"]) != _new_entities_id("e1", ["a", "b"])
    assert _new_entities_id("e1", ["a"]) != _new_entities_id("e2", ["a"])
    assert _new_entities_id("e1", ["b", "a"]) == _new_entities_id("e1", ["a", "b"])


# --- An event, not a condition --------------------------------------------


async def test_the_notice_is_persistent(hass, entry) -> None:
    """It cannot be re-derived, so it has to survive the restart itself.

    On the next startup the entity is in the known set and the diff is empty by
    construction. A non-persistent issue reloads as a tombstone, so this notice
    would disappear unread at the first restart after the upgrade -- the same
    silent add it exists to prevent, one step later.
    """
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")

    async_notice_new_disabled_entities(hass, entry, known)

    assert _notices(hass, entry)[0].is_persistent is True


async def test_a_restart_with_no_new_entities_neither_duplicates_nor_re_raises(hass, entry) -> None:
    """The event property, exercised the way the field will exercise it."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)

    raised = _notices(hass, entry)[0]
    created = raised.created

    for _ in range(3):
        # Each pass is a setup: probe the registry, forward nothing new, notice.
        async_notice_new_disabled_entities(hass, entry, async_registered_unique_ids(hass, entry))

    notices = _notices(hass, entry)
    assert len(notices) == 1
    assert notices[0].issue_id == raised.issue_id
    assert notices[0].created == created


async def test_a_repeat_of_the_same_set_cannot_rewrite_the_notice(hass, entry) -> None:
    """What the already-raised guard actually buys.

    It buys nothing against duplication -- the shared id rules that out. It buys
    exactly this: a set that comes back cannot silently restate a notice the user
    has already read. Without the guard the repeat takes `async_get_or_create`'s
    update branch, which replaces the placeholders in place.

    The set comes back when an entity is removed from the registry and later
    re-registered -- a BESS taken off the panel and put back -- because the
    unique_ids, and therefore the digest, are unchanged.
    """
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    added = _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)
    issue_id = _notices(hass, entry)[0].issue_id

    er.async_get(hass).async_remove(added.entity_id)
    _register(hass, entry, _PART_NUMBER, name="BESS Part Number")
    async_notice_new_disabled_entities(hass, entry, known)

    notices = _notices(hass, entry)
    assert len(notices) == 1
    assert notices[0].issue_id == issue_id
    assert notices[0].translation_placeholders["examples"] == "Part Number"


async def test_a_dismissed_notice_is_not_resurrected_by_a_restart(hass, entry) -> None:
    """Dismissing it must end it, not defer it to the next startup."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)

    issue_id = _notices(hass, entry)[0].issue_id
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    dismissed = ir.async_get(hass).async_get_issue(DOMAIN, issue_id).dismissed_version

    async_notice_new_disabled_entities(hass, entry, async_registered_unique_ids(hass, entry))

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).dismissed_version == dismissed


async def test_the_defect_reconcile_pass_does_not_delete_the_notice(hass, entry) -> None:
    """`_ours` is scoped to the two defect prefixes on purpose.

    The reconcile pass deletes every id it did not re-derive. This notice is
    derived exactly once, so a shared scope would delete it on the same startup
    that raised it.
    """
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)
    issue_id = _notices(hass, entry)[0].issue_id

    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (), frozenset()), {})

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


# --- Removal ---------------------------------------------------------------


async def test_the_notice_is_cleared_when_the_entry_is_removed(hass, entry) -> None:
    """Persistent, so a restart would not even demote it to a tombstone."""
    _register(hass, entry, "sp3-001_bess_serial_number", disabled=False)
    known = async_registered_unique_ids(hass, entry)
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    async_notice_new_disabled_entities(hass, entry, known)
    assert _notices(hass, entry)

    async_clear_schema_issues(hass, entry)

    assert _notices(hass, entry) == []


async def test_removing_one_entry_leaves_another_entrys_notice(hass) -> None:
    """Two panels share the domain; one leaving must not silence the other."""
    kept = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-kept")
    kept.add_to_hass(hass)
    removed = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-removed")
    removed.add_to_hass(hass)

    for panel in (kept, removed):
        _register(hass, panel, f"{panel.unique_id}_bess_serial_number", disabled=False)
        known = async_registered_unique_ids(hass, panel)
        _register(hass, panel, f"{panel.unique_id}_bess_part_number", name="Part Number")
        async_notice_new_disabled_entities(hass, panel, known)

    async_clear_schema_issues(hass, removed)

    assert _notices(hass, kept)
    assert _notices(hass, removed) == []
