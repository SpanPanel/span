"""The Repairs an entry raises: their ids, their link to the entry, and the sweep at removal.

Every issue this integration raises belongs to one config entry, whose id its
own id carries, and is linked to that entry. Home Assistant then owns the
issue's lifetime: it deletes the issue, dismissal and all, when the entry is
removed, deletes one whose entry it no longer knows at startup, and hides one
while its entry is disabled.

- **Every kind is named here,** in `EntryIssue` for a kind an entry raises one
  of and in `EntryIssueFamily` for a kind it raises one of per key, such as a
  field path or a switch. Each member builds its own ids, so the modules that
  raise an issue and the sweep below read one set of prefixes. A released id
  never changes, because a dismissal is stored under it.
- **Raised only through `async_create_entry_issue`,** which links the issue to
  its entry. Home Assistant writes the link it is given each time an issue is
  raised again, so a single call without it would unlink the issue.
- **Swept at removal for what an older release raised.** Issues from before
  they were linked stand unlinked until they are raised again, and Home
  Assistant never deletes an unlinked issue; `async_clear_unlinked_issues`
  deletes the removed entry's.
"""

from __future__ import annotations

from enum import StrEnum

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


class EntryIssue(StrEnum):
    """A kind of issue an entry raises at most one of, by the prefix of its id.

    Its id is the prefix followed by the entry's id.
    """

    UNVALIDATED_HARDWARE = "unvalidated_hardware_"
    FLAT_FIRMWARE = "flat_firmware_"
    CA_CHANGED = "panel_ca_changed_"
    CA_UNUSABLE = "panel_ca_unusable_"
    REST_TLS_UNTRUSTED = "panel_rest_tls_untrusted_"
    LEAF_NAME_MISMATCH = "panel_leaf_name_mismatch_"
    CIRCUIT_DEVICES_CONVERTED = "circuit_devices_converted_"
    RETIRED_EBUS_UPGRADE = "panel_upgraded_to_ebus_v1_"
    """The firmware-upgrade Repair, which nothing raises any more.

    An upgrade that took nothing away is not a defect, and the Repairs list
    stamped it with a severity and offered to ignore it, which told the user
    their panel was broken; `notices` carries it as a notification instead.
    Named here so the ones older releases raised are still cleared, at setup
    and by the sweep.
    """

    def issue_id(self, entry_id: str) -> str:
        """Return the id of this kind's issue for one entry."""
        return f"{self.value}{entry_id}"


class EntryIssueFamily(StrEnum):
    """A kind of issue an entry raises one of per key, by the prefix of its ids.

    Each id is the prefix, the entry's id, an underscore and the key. The
    underscore closes the entry's part, so no entry's ids start another's.
    """

    UNRESOLVED_FIELD = "unresolved_"
    UNIT_MISMATCH = "unit_mismatch_"
    BREAKER_SWITCH_IN_AREA = "breaker_switch_in_area_"
    RETIRED_NEW_ENTITIES = "new_entities_"
    """The new-entity Repair, which nothing raises any more.

    An addition is not a repair, and `additions` announces it as a notification
    instead. Older releases raised it persistent, so without being named here
    the ones standing on an upgraded install would never be cleared.
    """

    def entry_prefix(self, entry_id: str) -> str:
        """Return what the id of every issue of this kind for one entry starts with."""
        return f"{self.value}{entry_id}_"

    def issue_id(self, entry_id: str, key: str) -> str:
        """Return the id of this kind's issue for one entry and one key."""
        return f"{self.entry_prefix(entry_id)}{key}"


@callback
def async_create_entry_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    issue_id: str,
    *,
    is_fixable: bool,
    is_persistent: bool,
    severity: ir.IssueSeverity,
    translation_key: str,
    translation_placeholders: dict[str, str] | None = None,
    data: dict[str, str | int | float | None] | None = None,
) -> None:
    """Raise an issue of this integration's, or raise it again, linked to its entry.

    Home Assistant refuses to link an issue to an entry it does not know, so a
    caller that can outlive its entry asks `entry_removed` first.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        config_entry_id=entry.entry_id,
        data=data,
        is_fixable=is_fixable,
        is_persistent=is_persistent,
        severity=severity,
        translation_key=translation_key,
        translation_placeholders=translation_placeholders,
    )


@callback
def entry_removed(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Return whether Home Assistant no longer knows the entry, so nothing may be raised for it.

    A callback the panel's client fires can outlive the entry. An unload that
    fails leaves the client running and its callbacks registered, and removing
    the entry afterwards deletes it without unloading it again.
    """
    return hass.config_entries.async_get_entry(entry.entry_id) is None


@callback
def async_clear_unlinked_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the removed entry's issues that are not linked to it.

    Home Assistant deletes an entry's linked issues itself once the entry is
    removed, and never deletes an unlinked one. Every issue an older release
    raised stands unlinked until it is raised again, a persistent notice or a
    dismissed one most of all, so without this a removed panel would leave
    them behind for good.

    Matched by id, under every kind's prefix for this entry, so another entry's
    issues stay.
    """
    entry_id = entry.entry_id
    whole_ids = {kind.issue_id(entry_id) for kind in EntryIssue}
    family_prefixes = tuple(family.entry_prefix(entry_id) for family in EntryIssueFamily)
    registry = ir.async_get(hass)
    for issue_id in [
        issue.issue_id
        for issue in registry.issues.values()
        if issue.domain == DOMAIN
        and issue.config_entry_id is None
        and (issue.issue_id in whole_ids or issue.issue_id.startswith(family_prefixes))
    ]:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
