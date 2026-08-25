"""Render control commands in the logbook.

Discovered by name: Home Assistant looks for `logbook.async_describe_events` in
the integration and calls it once at startup. No manifest change is needed.

The audit event carries a wire address and a `user_id`; the logbook wants a
sentence. The translation is deliberately blunt about the four outcomes, because
the whole point of `PublishState` having four members rather than two is that
"the panel did it", "the broker took it and the panel did not act", "nothing came
back" and "this will never be delivered" send an investigation in four different
directions.

When there is no user, the originating automation or script is resolved from the
context so a contextless write is attributed to *what* rather than left blank —
which is the case the audit exists for.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_CONTROL_COMMAND

_OUTCOME_PHRASES: dict[str, str] = {
    "confirmed": "and the panel confirmed it",
    # Not a success and not a failure. The honest message is that the panel
    # accepted the command and never reported the change, which most often means
    # the value was already what was asked for.
    "accepted": "and the broker accepted it, but the panel reported no change",
    "unconfirmed": "with no acknowledgement and no reported change",
    # The one state that is a promise about the future.
    "failed": "but it was never sent",
}

_REFUSAL_PHRASES: dict[str, str] = {
    "control_locked": "but the control lock is armed",
    "contextless_control_refused": "but commands without a logged-in user are refused",
    "admin_only_control": "but only administrators may operate this panel",
    "relay_debounced": "but that circuit was operated moments ago",
}


def _describe_outcome(outcome: str) -> str:
    """Turn one `outcome` field into a clause."""
    if outcome.startswith("refused:"):
        reason = outcome.removeprefix("refused:")
        return _REFUSAL_PHRASES.get(reason, f"but it was refused ({reason})")
    return _OUTCOME_PHRASES.get(outcome, f"with an unrecognised outcome ({outcome})")


def _describe_actor(hass: HomeAssistant, user_id: str | None, parent_id: str | None) -> str:
    """Name who or what issued the command.

    A `parent_id` is the context of whatever caused this call, so an automation
    that operates a breaker is named rather than reduced to "no user". The
    lookup is best-effort: contexts are not retained forever, and a name that
    cannot be resolved is better rendered as "an automation or script" than as
    a raw id nobody can act on.
    """
    if user_id is not None:
        # The id rather than the name: resolving a name goes through
        # `hass.auth.async_get_user`, which is a coroutine, and a logbook
        # describer is a synchronous callback. Home Assistant's own logbook
        # renders the id here too.
        return f"user {user_id}"
    if parent_id is None:
        return "an unattended caller"
    for state in hass.states.async_all(("automation", "script")):
        if state.context.id == parent_id:
            return str(state.name) or state.entity_id
    return "an automation or script"


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Register the describer for this integration's control-command event."""

    @callback
    def describe(event: Event) -> dict[str, str]:
        data = event.data
        command = str(data.get("command", "a control"))
        value = str(data.get("value", ""))
        outcome = str(data.get("outcome", "unknown"))
        user_id = data.get("user_id")
        parent_id = data.get("parent_id")
        actor = _describe_actor(
            hass,
            str(user_id) if isinstance(user_id, str) else None,
            str(parent_id) if isinstance(parent_id, str) else None,
        )
        entity_id = data.get(ATTR_ENTITY_ID)

        described = {
            LOGBOOK_ENTRY_NAME: "SPAN Panel",
            LOGBOOK_ENTRY_MESSAGE: (
                f"{command} was set to {value} by {actor} {_describe_outcome(outcome)}"
            ),
        }
        if isinstance(entity_id, str):
            described[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
        return described

    async_describe_event(DOMAIN, EVENT_CONTROL_COMMAND, describe)
