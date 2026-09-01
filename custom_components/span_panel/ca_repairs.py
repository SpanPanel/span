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
from .leaf_repairs import async_clear_leaf_name_mismatch

_LOGGER = logging.getLogger(__name__)

CA_CHANGED_ISSUE_PREFIX = "panel_ca_changed_"
CA_UNUSABLE_ISSUE_PREFIX = "panel_ca_unusable_"
REST_TLS_UNTRUSTED_ISSUE_PREFIX = "panel_rest_tls_untrusted_"


def ca_changed_issue_id(entry_id: str) -> str:
    """Issue id for one entry's CA change."""
    return f"{CA_CHANGED_ISSUE_PREFIX}{entry_id}"


def ca_unusable_issue_id(entry_id: str) -> str:
    """Issue id for one entry's unreadable stored CA."""
    return f"{CA_UNUSABLE_ISSUE_PREFIX}{entry_id}"


def rest_tls_untrusted_issue_id(entry_id: str) -> str:
    """Issue id for one entry's REST TLS port serving something the pin rejects."""
    return f"{REST_TLS_UNTRUSTED_ISSUE_PREFIX}{entry_id}"


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

    Supersedes a standing name mismatch on the same entry, and does it here
    rather than at each call site so no future one can forget. The two findings
    come out of the same failed handshake and its diagnosis, so both can stand
    against one panel; only this one carries a decision, and telling somebody to
    re-point their configuration at an address served by a certificate this
    integration has just refused to trust is the wrong instruction.
    """
    async_clear_leaf_name_mismatch(hass, entry)
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


@callback
def async_raise_ca_unusable(hass: HomeAssistant, entry: ConfigEntry, reason: str) -> None:
    """Raise the Repair for a stored anchor this system cannot read.

    Setup fails closed on this instead of downgrading to plaintext, because the
    downgrade would run unattended on every boot at exactly the call the pin
    exists to protect. The other REST callers that refuse this state — a
    credential rotation — could point at reauth; setup cannot fix a stored
    value, so the fix is the same flow the CA-change uses: fetch what the panel
    advertises, verify it signs the panel's own certificate, and ask a person to
    accept the fingerprint.

    Fixable and persistent for the CA-changed Repair's reasons: the fix *is* the
    resolution, and there is no live transport left to re-derive the finding
    from.
    """
    _LOGGER.error(
        "The stored certificate authority for SPAN panel %s cannot be read (%s), so its "
        "REST calls cannot be verified and setup has stopped rather than falling back to "
        "plaintext. Re-acquire the panel's certificate authority in Settings > Repairs",
        entry.title,
        reason,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        ca_unusable_issue_id(entry.entry_id),
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="panel_ca_unusable",
        translation_placeholders={"panel": entry.title},
        data={"entry_id": entry.entry_id},
    )


@callback
def async_clear_ca_unusable(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repair, on a setup whose stored anchor built a working transport."""
    ir.async_delete_issue(hass, DOMAIN, ca_unusable_issue_id(entry.entry_id))


@callback
def async_raise_rest_tls_untrusted(
    hass: HomeAssistant, entry: ConfigEntry, host: str, https_port: int, fingerprint: str
) -> None:
    """Raise the Repair for a REST TLS port answering with a certificate the pin rejects.

    Raised only after the diagnosis has ruled the alternatives out: the panel
    still advertises the pinned CA (a rotated CA takes the CA-changed Repair
    and its guided re-pin), and the leaf probe found a certificate the pin does
    not currently validate rather than one that merely names somewhere else (a
    moved panel takes the leaf-mismatch Repair). What is left is two conditions
    the probe cannot tell apart, and the text names both: something terminating
    TLS in front of the panel with a certificate of its own, or the panel's own
    certificate outside its validity window after a clock reset.

    Not fixable, because none of the remedies are this integration's to apply:
    the port is corrected by Reconfigure, the middlebox by whoever put it
    there, and the clock by the panel getting time. Retried rather than
    terminal — the clock case clears itself, plaintext is never fallen back
    to either way, and a matching fingerprint never escalates, which is the
    same stance the library's own diagnosis takes. `is_persistent=False` for
    the leaf-mismatch Repair's reason: re-derived on every retry, so a restart
    that resolves it must not resurrect it.

    Supersedes a standing leaf-name mismatch on the same entry, for the
    CA-changed Repair's reason: the two verdicts come out of the same probe and
    contradict each other — one promises recovery at a returning address, this
    one says what answers there is not trusted — so only the current one may
    stand. The reverse supersede lives at the verdict call site in `__init__`,
    because `leaf_repairs` cannot import this module back without a cycle.
    """
    async_clear_leaf_name_mismatch(hass, entry)
    _LOGGER.error(
        "Something at %s:%s is answering SPAN panel %s's HTTPS port with a certificate "
        "its pinned CA (SHA-256 %s) does not currently validate, while the panel still "
        "advertises that same CA. Retrying without connecting: check the entry's HTTPS "
        "port and whether anything terminates TLS between Home Assistant and the panel; "
        "a panel whose clock reset clears this on its own once it has time again",
        host,
        https_port,
        entry.title,
        fingerprint,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        rest_tls_untrusted_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="panel_rest_tls_untrusted",
        translation_placeholders={
            "panel": entry.title,
            "host": host,
            "https_port": str(https_port),
            "fingerprint": fingerprint,
        },
        data={"entry_id": entry.entry_id},
    )


@callback
def async_clear_rest_tls_untrusted(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repair, on a setup that reached the panel under the current pin."""
    ir.async_delete_issue(hass, DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id))
