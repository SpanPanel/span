"""The Repair raised when a pinned panel starts advertising a different CA.

Separate from `schema_repairs` because the two are reconciled on opposite terms.
A schema finding is re-derived from live state on every pass and deleted the
moment it stops being true; this one describes an event that already happened on
a transport that has stopped for good, and there is nothing left running to
re-derive it from. It is raised once, persists across restarts, and is cleared
only by the user accepting the new fingerprint or by a setup that connects
cleanly again.

It is also the only Repair here that is fixable, and the fix is the point. A
rotated CA and an intercepted connection are indistinguishable from inside the
integration — the library says so and refuses to choose — so the only honest
resolution is a person comparing the new fingerprint against a value they hold
and saying yes. Re-pinning automatically on reconnect would be precisely the
substitution the pin exists to prevent.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CA_CHANGED_ISSUE_PREFIX = "panel_ca_changed_"


def ca_changed_issue_id(entry_id: str) -> str:
    """Issue id for one entry's CA change."""
    return f"{CA_CHANGED_ISSUE_PREFIX}{entry_id}"


@callback
def async_raise_ca_changed(
    hass: HomeAssistant,
    entry: ConfigEntry,
    expected_fingerprint: str,
    observed_fingerprint: str,
) -> None:
    """Raise the Repair, carrying both fingerprints.

    Both, because the two remedies are opposite and only the user can choose
    between them: re-pin, if a firmware upgrade or a factory reset rotated the
    CA legitimately, or investigate, if nothing should have.

    `is_persistent` because the transport this describes is already dead. A
    non-persistent issue reloads as a tombstone, and this one has no live state
    to re-assert it from — the client that would have noticed is gone.
    """
    _LOGGER.error(
        "SPAN panel %s is advertising CA %s where %s was pinned. Not reconnecting: a "
        "rotated CA and an intercepted connection look identical from here. Confirm the "
        "new fingerprint out of band, then accept it in Settings > Repairs",
        entry.title,
        observed_fingerprint,
        expected_fingerprint,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        ca_changed_issue_id(entry.entry_id),
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="panel_ca_changed",
        translation_placeholders={
            "panel": entry.title,
            "expected_fingerprint": expected_fingerprint,
            "observed_fingerprint": observed_fingerprint,
        },
        data={
            "entry_id": entry.entry_id,
            "observed_fingerprint": observed_fingerprint,
        },
    )


@callback
def async_clear_ca_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repair, on a setup that reached the panel under the current pin."""
    ir.async_delete_issue(hass, DOMAIN, ca_changed_issue_id(entry.entry_id))
