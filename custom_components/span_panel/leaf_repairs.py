"""The Repair raised when the panel's certificate does not name the configured host.

The third outcome of the library's failed-handshake diagnosis, and the only one
that used to reach nobody. A pinned CA that still validates the panel's
certificate says the peer is the panel; a certificate that does not carry the
configured address says the panel is not where the entry claims it is. Most often
a new DHCP lease, sometimes an entry recorded under a name the panel does not
know itself by.

Separate from `ca_repairs` because the two are reconciled on opposite terms, and
this one is the `schema_repairs` model rather than the `ca_repairs` one:

- **Not persistent.** The transport is alive and still retrying. The library
  re-derives this on every attempt and re-arms the signal on the next successful
  connect, so there is live state to re-assert it from and a restart may sweep it
  away.
- **Severity `WARNING`, not `ERROR`.** Nothing is being intercepted and nothing
  has been refused -- the chain verified under the pin. The panel is simply
  somewhere other than where the entry says.
- **Not fixable.** The remedy is Reconfigure, which already exists, already
  refuses a host the leaf does not name, and already carries the FQDN
  registration path. A Repairs fix flow cannot hand off to a config flow, so a
  fixable Repair would have to re-implement a subset of Reconfigure -- host
  entry, verdict, store -- and `async_panel_leaf_host` warns in as many words
  that persisting a resolved address is where that subset goes wrong. One
  implementation of "change the host" is the point; this Repair's job is to make
  the condition visible and name the address to use.

Two translation keys rather than one, because a certificate that names nothing
at all is a different sentence and not a different value. Home Assistant
substitutes `translation_placeholders` into the description verbatim, so a phrase
passed as a placeholder would reach every user in English no matter what language
they read the rest of the notice in; the empty case therefore gets a description
of its own, and the phrase is translated with everything else. It also reads as
what it is -- a fault in the panel rather than in the address the user chose.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from span_panel_api import LeafNameMismatch

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LEAF_NAME_MISMATCH_ISSUE_PREFIX = "panel_leaf_name_mismatch_"

_NAMED_TRANSLATION_KEY = "panel_leaf_name_mismatch"
_UNNAMED_TRANSLATION_KEY = "panel_leaf_name_mismatch_no_names"


def leaf_name_mismatch_issue_id(entry_id: str) -> str:
    """Issue id for one entry's name mismatch."""
    return f"{LEAF_NAME_MISMATCH_ISSUE_PREFIX}{entry_id}"


@callback
def async_raise_leaf_name_mismatch(
    hass: HomeAssistant,
    entry: ConfigEntry,
    mismatch: LeafNameMismatch,
) -> None:
    """Raise the Repair, carrying the configured host and the names actually served.

    Both, because "the address is wrong" without saying which address is right
    leaves the user exactly where the log line left them. The names come from the
    certificate's SAN entries in certificate order, and one of them is what
    Reconfigure wants.

    `is_persistent=False` because the condition is derived from a transport that
    is still running: the library re-diagnoses it on every reconnect attempt and
    re-arms the signal on the next successful connect, so a restart that resolves
    nothing raises it again and a restart after the panel came back does not.
    """
    issue_id = leaf_name_mismatch_issue_id(entry.entry_id)
    if mismatch.leaf_names:
        leaf_names = ", ".join(mismatch.leaf_names)
        _LOGGER.warning(
            "SPAN panel %s is configured as %s, but the certificate it serves names only "
            "%s. The panel has probably moved. Use Reconfigure on the SPAN Panel entry to "
            "point it at one of those names; the integration keeps retrying meanwhile",
            entry.title,
            mismatch.host,
            leaf_names,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_NAMED_TRANSLATION_KEY,
            translation_placeholders={
                "panel": entry.title,
                "host": mismatch.host,
                "leaf_names": leaf_names,
            },
        )
        return

    _LOGGER.warning(
        "SPAN panel %s is configured as %s, but the certificate it serves names no address "
        "at all, so there is no address to re-point the entry at. The panel's certificate "
        "needs regenerating; the integration keeps retrying meanwhile",
        entry.title,
        mismatch.host,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_UNNAMED_TRANSLATION_KEY,
        translation_placeholders={
            "panel": entry.title,
            "host": mismatch.host,
        },
    )


@callback
def async_clear_leaf_name_mismatch(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repair.

    Cleared on any connection that succeeded under the current configuration --
    which is exactly when the library re-arms the signal, so the two never
    disagree about whether the condition still holds -- and when a CA change
    takes over the same entry, because that finding supersedes this one.
    """
    ir.async_delete_issue(hass, DOMAIN, leaf_name_mismatch_issue_id(entry.entry_id))
