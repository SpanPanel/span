"""Fix flows for this integration's Repairs.

Discovered by name: Home Assistant looks for `repairs.async_create_fix_flow` in
the integration whenever an issue it raised is marked `is_fixable`.

One issue is fixable, and everything here exists for it. See `ca_repairs`.
"""

from __future__ import annotations

import logging

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from span_panel_api import ca_fingerprint
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
import voluptuous as vol

from .ca_repairs import CA_CHANGED_ISSUE_PREFIX
from .config_flow_validation import as_port, async_ca_signs_panel_leaf, async_fetch_panel_ca
from .const import (
    CONF_EBUS_BROKER_PORT,
    CONF_HTTP_PORT,
    CONF_PANEL_CA_PEM,
    DEFAULT_MQTTS_PORT,
    DOMAIN,
    PANEL_CA_PENDING,
)

_LOGGER = logging.getLogger(__name__)


def _fingerprint_or_reason(pem: object) -> str:
    """Name a stored PEM for a log line, without letting it raise into one."""
    if not isinstance(pem, str) or not pem:
        return "none"
    try:
        return ca_fingerprint(pem)
    except SpanPanelValidationError:
        return "unreadable"


class PanelCAChangedRepairFlow(RepairsFlow):
    """Re-pin a panel's CA, but only on an explicit human acceptance.

    The fingerprint is taken from a *fresh* fetch rather than from the issue's
    stored one. The two should agree, and if they do not the panel has changed
    its CA twice — at which point the value the user is being asked to accept
    has to be the one that will actually be stored, not a record of an earlier
    observation. Accepting a fingerprint and pinning a different certificate
    would make the confirmation meaningless.

    That fresh fetch has to earn its place in the dialog: it is plaintext and
    unauthenticated, so it is checked against the certificate the panel serves
    before any fingerprint is shown. Everything this flow asks of the user rests
    on the value being the panel's own.
    """

    def __init__(self, entry_id: str) -> None:
        """Hold the entry this flow re-pins."""
        self._entry_id = entry_id
        self._ca_pem: str | None = None

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Start at the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show the CA the panel is advertising now, and re-pin it if accepted."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_gone")

        if user_input is not None and self._ca_pem is not None:
            data = dict(entry.data)
            data[CONF_PANEL_CA_PEM] = self._ca_pem
            data.pop(PANEL_CA_PENDING, None)
            self.hass.config_entries.async_update_entry(entry, data=data)
            _LOGGER.warning(
                "Re-pinned the CA for SPAN panel %s to %s on explicit confirmation",
                entry.title,
                ca_fingerprint(self._ca_pem),
            )
            # The issue is cleared by the fix flow completing; the reload is what
            # puts the new anchor into a live transport.
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(data={})

        host = str(entry.data.get(CONF_HOST, ""))
        # Cleared before the fetch, not after: nothing accepted on an earlier
        # pass through this step may survive into a pass that refuses.
        self._ca_pem = None
        try:
            ca_pem = await async_fetch_panel_ca(
                self.hass, host, http_port=as_port(entry.data.get(CONF_HTTP_PORT), 80)
            )
            fingerprint = ca_fingerprint(ca_pem)
        except (
            SpanPanelAPIError,
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelValidationError,
        ) as err:
            _LOGGER.warning("Could not re-read the CA from panel %s: %s", host, err)
            return self.async_abort(reason="ca_unreadable")

        # The fetch is plaintext and unauthenticated, so whatever answered it
        # decides what this dialog would ask the user to trust. A CA that signs
        # nothing the panel serves cannot be the panel's, and its fingerprint is
        # not one to put in front of a person as the panel's own -- accepting it
        # would replace a working pin with one that can never connect.
        #
        # Checked on the broker's port, because that is the connection the pin
        # anchors and the one this repair exists to restore. Verifying somewhere
        # else would be answering a question nobody asked.
        mqtts_port = as_port(entry.data.get(CONF_EBUS_BROKER_PORT), DEFAULT_MQTTS_PORT)
        if not await async_ca_signs_panel_leaf(self.hass, host, mqtts_port, ca_pem):
            _LOGGER.warning(
                "SPAN panel %s published a CA (SHA-256 %s) that does not sign the "
                "certificate served by its broker at %s:%s; refusing to offer it. The "
                "pin in place is SHA-256 %s and has not been touched",
                entry.title,
                fingerprint,
                host,
                mqtts_port,
                _fingerprint_or_reason(entry.data.get(CONF_PANEL_CA_PEM)),
            )
            return self.async_abort(reason="ca_leaf_mismatch")

        self._ca_pem = ca_pem
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "panel": entry.title,
                "fingerprint": fingerprint,
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the flow that fixes `issue_id`."""
    if issue_id.startswith(CA_CHANGED_ISSUE_PREFIX):
        entry_id = issue_id.removeprefix(CA_CHANGED_ISSUE_PREFIX)
        return PanelCAChangedRepairFlow(entry_id)
    raise ValueError(f"{DOMAIN} raised no fixable issue with id {issue_id}")
