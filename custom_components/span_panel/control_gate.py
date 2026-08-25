"""Who may operate this panel through Home Assistant, and a record of who did.

**What this is a boundary against.** Callers arriving through Home Assistant,
and nothing else. It does not constrain anything holding the broker credential:
a process with that credential publishes to the panel's broker directly and
never reaches this code, and that includes a malicious or buggy custom
integration running in the same Home Assistant process. Presenting this as a
security boundary around the *panel* would be the most damaging thing it could
do, because a user would stop looking for the real one — which is network
topology and a lock on the enclosure. The README says so too.

**Why it is split in two.** Authorization needs to know who is asking, and that
is knowable only at the entity: Home Assistant sets `Entity._context` from the
service call immediately before the handler runs. Completeness needs a single
choke point, and that exists only at the publish, inside the library. So
identity is captured at the entity into a `ContextVar` (see
`SpanPanelEntity._async_guarded_control`) and the decision is made in the
library's `ControlInterceptor`, which sees every control command including ones
a future code path forgets to route through the helper. Such a command arrives
with an empty `ContextVar` and is treated as contextless rather than silently
permitted — that is the fail-closed default, and it is the reason the gate lives
here rather than in the helper.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from span_panel_api import ControlCommand, PublishOutcome, PublishState

from .const import DOMAIN, EVENT_CONTROL_COMMAND
from .options import (
    ALLOW_CONTEXTLESS_CONTROL,
    CONTROL_LOCK_TIMEOUT,
    CONTROL_MODE,
    RELAY_DEBOUNCE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

RELAY_PROPERTY = "relay"
"""The property id both schema adapters use for a circuit relay.

Flat spells the address `(serial, circuit_id, "relay")` and v1.0 spells it
`(circuit_id, "switch", "relay")` — the device and node differ, the property does
not. That is what makes it usable here without teaching this module either
schema's topic grammar.
"""

DEFAULT_RELAY_DEBOUNCE_SECONDS = 2.0


class ControlMode(StrEnum):
    """Who may operate the panel's controls."""

    ALL_USERS = "all_users"
    """Home Assistant's own default: any user who can call a service."""

    ADMIN_ONLY = "admin_only"
    """Administrators only. Home Assistant's default user policy grants every
    non-admin control of every entity, so this is the only way to distinguish
    them."""

    DISABLED = "disabled"
    """No control entities are created at all. Their registry entries are kept —
    see the platforms."""


@dataclass(frozen=True, slots=True)
class ControlCaller:
    """Who issued the command currently being published, if anyone.

    `entity_id` is carried alongside the Context because the interceptor sees a
    wire address and cannot map it back to an entity — and the audit trail is
    worth far less without it.
    """

    context: Context | None
    entity_id: str | None


CONTROL_CALLER: ContextVar[ControlCaller | None] = ContextVar(
    "span_panel_control_caller", default=None
)
"""Bound around one awaited control call, and reset in a `finally`.

A `ContextVar` rather than an attribute on the entity because a mutable
attribute races across concurrent service calls, and rather than an override of
`async_set_context` because that would inherit core's five-second
`CONTEXT_RECENT_TIME_SECONDS` staleness window and would also capture paths that
are not service calls at all.
"""

_REFUSAL_REASON: ContextVar[str | None] = ContextVar("span_panel_control_refusal", default=None)
"""Why `before_publish` vetoed, read back by `after_publish`.

The library fires `after_publish` for a veto — so an audit built on it cannot
silently omit the refusals, which would make it worse than no audit — but hands
it only a `FAILED` outcome with `detail="vetoed"`, not the reason. The reason is
left here on the way out.

This works because the library fires that callback as a task created from the
same context this was set in, and `loop.create_task` copies the current context.
The entity's `finally` runs after the task exists, so the copy keeps the value.
"""


def _as_bool(value: object, default: bool) -> bool:
    """Read a bool out of untyped option data."""
    return value if isinstance(value, bool) else default


def _as_float(value: object, default: float) -> float:
    """Read a non-negative float out of untyped option data."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value) if value >= 0 else default
    return default


@dataclass(frozen=True, slots=True)
class ControlPolicy:
    """The four options, resolved once.

    Every default reproduces the behaviour an existing entry already has. A
    silent tightening on upgrade that breaks a household's automations is a worse
    outcome than the status quo, and the user cannot diagnose it from the
    entity's error.
    """

    mode: ControlMode
    allow_contextless: bool
    lock_timeout_minutes: float | None
    relay_debounce_seconds: float

    @property
    def lock_enabled(self) -> bool:
        """Whether the control-lock entity exists at all."""
        return self.lock_timeout_minutes is not None

    @classmethod
    def default(cls) -> ControlPolicy:
        """Return the policy an entry with no control options configured has.

        Written down once, here, so "the default" is a single object rather than
        four constants repeated at each reader.
        """
        return cls(
            mode=ControlMode.ALL_USERS,
            allow_contextless=True,
            lock_timeout_minutes=None,
            relay_debounce_seconds=DEFAULT_RELAY_DEBOUNCE_SECONDS,
        )

    @classmethod
    def from_options(cls, options: Mapping[str, object]) -> ControlPolicy:
        """Resolve the policy from one entry's options."""
        raw_mode = options.get(CONTROL_MODE)
        try:
            mode = ControlMode(raw_mode) if isinstance(raw_mode, str) else ControlMode.ALL_USERS
        except ValueError:
            _LOGGER.warning("Unknown control_mode %r; falling back to all_users", raw_mode)
            mode = ControlMode.ALL_USERS

        raw_timeout = options.get(CONTROL_LOCK_TIMEOUT)
        # Absent means the lock feature is off; 0 means armed until manually
        # disarmed. The two are different states and a plain float cannot hold
        # both, which is why this is nullable.
        timeout: float | None = None
        if isinstance(raw_timeout, int | float) and not isinstance(raw_timeout, bool):
            timeout = float(raw_timeout) if raw_timeout >= 0 else None

        return cls(
            mode=mode,
            allow_contextless=_as_bool(options.get(ALLOW_CONTEXTLESS_CONTROL), True),
            lock_timeout_minutes=timeout,
            relay_debounce_seconds=_as_float(
                options.get(RELAY_DEBOUNCE_SECONDS), DEFAULT_RELAY_DEBOUNCE_SECONDS
            ),
        )


class ControlLock:
    """An armed/disarmed flag that refuses every control while armed.

    Home Assistant has no per-entity admin flag, so "an admin-only switch entity"
    is not a thing that exists. This is the honest local substitute: the
    alarm-panel "armed" pattern, defending against misclicks and runaway
    automations, which is what a local second factor can actually do.

    Arming is permitted for anyone, including a contextless caller. Disarming
    requires an administrator, and is refused outright for a contextless caller
    regardless of `allow_contextless_control` — an automation that can unlock the
    panel defeats the purpose of the lock. Making a household safer should not
    require admin; making it less safe should.
    """

    def __init__(self) -> None:
        """Start disarmed."""
        self._armed = False
        self._relock_at: float | None = None

    @property
    def armed(self) -> bool:
        """Whether control is currently locked out.

        Auto-relock is evaluated on read rather than on a timer, so it cannot be
        missed by a restart or a suspended event loop, and so the lock never
        stays open longer than it was asked to.
        """
        if self._relock_at is not None and time.monotonic() >= self._relock_at:
            self._armed = True
            self._relock_at = None
        return self._armed

    def arm(self) -> None:
        """Lock control out."""
        self._armed = True
        self._relock_at = None

    def disarm(self, timeout_minutes: float) -> None:
        """Allow control, re-arming after `timeout_minutes` unless that is zero."""
        self._armed = False
        self._relock_at = None if timeout_minutes <= 0 else time.monotonic() + timeout_minutes * 60


class ControlGate:
    """One veto-and-observe point for every control command on one entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        policy: ControlPolicy,
        lock: ControlLock,
    ) -> None:
        """Hold what the decision needs."""
        self._hass = hass
        self._entry = entry
        self._policy = policy
        self._lock = lock
        # Monotonic timestamp of the last relay command allowed through, per
        # circuit. Keyed on the address the adapter produced rather than on an
        # entity id, so it holds for a caller that never went through an entity.
        self._last_relay: dict[tuple[str, str], float] = {}

    # -- ControlInterceptor -------------------------------------------------

    async def before_publish(self, command: ControlCommand) -> None:
        """Decide whether this command may be published. Raise to veto.

        The exception reaches the service caller unchanged — the library
        propagates it rather than translating it — which is what lets a
        `ServiceValidationError` carrying a translated message arrive intact.
        """
        caller = CONTROL_CALLER.get()
        context = caller.context if caller is not None else None
        user_id = context.user_id if context is not None else None

        if self._lock.armed:
            self._refuse(
                "control_locked",
                "The SPAN panel's control lock is armed. Disarm it before operating circuits.",
            )

        if user_id is None and not self._policy.allow_contextless:
            self._refuse(
                "contextless_control_refused",
                "This SPAN panel refuses control commands that do not come from a logged-in user.",
                parent_id=context.parent_id if context is not None else None,
            )

        if self._policy.mode is ControlMode.ADMIN_ONLY and user_id is not None:
            user = await self._hass.auth.async_get_user(user_id)
            if user is None or not user.is_admin:
                self._refuse(
                    "admin_only_control",
                    "Only a Home Assistant administrator may operate this SPAN panel.",
                )

        if command.property_id == RELAY_PROPERTY:
            self._check_relay_debounce(command)

    async def after_publish(self, command: ControlCommand, outcome: PublishOutcome) -> None:
        """Record what happened to every command, refusals included."""
        caller = CONTROL_CALLER.get()
        context = caller.context if caller is not None else None
        reason = _REFUSAL_REASON.get()

        result = f"refused:{reason}" if reason is not None else str(outcome.state)
        event = {
            "entry_id": self._entry.entry_id,
            "user_id": context.user_id if context is not None else None,
            "parent_id": context.parent_id if context is not None else None,
            "entity_id": caller.entity_id if caller is not None else None,
            "command": f"{command.node_id}/{command.property_id}",
            "topic": command.topic,
            "value": command.value,
            "outcome": result,
            "no_op": outcome.no_op,
        }
        self._hass.bus.async_fire(EVENT_CONTROL_COMMAND, event)

        # INFO on every publish, not only on failures. A panel that operates a
        # breaker is worth a log line whichever way it went, and `unconfirmed` in
        # particular is invisible otherwise.
        _LOGGER.info(
            "SPAN control %s = %s by %s: %s%s",
            command.topic,
            command.value,
            caller.entity_id if caller is not None and caller.entity_id else "an unknown caller",
            result,
            f" ({outcome.detail})" if outcome.detail else "",
        )

    # -- internals ----------------------------------------------------------

    def _check_relay_debounce(self, command: ControlCommand) -> None:
        """Refuse a relay command that follows too closely on the last one.

        Refused, never queued. A queued relay command firing seconds later
        against a changed panel state is worse than a refusal the automation
        author can see and fix.
        """
        window = self._policy.relay_debounce_seconds
        if window <= 0:
            return

        circuit = (command.device_id, command.node_id)
        now = time.monotonic()
        last = self._last_relay.get(circuit)
        if last is not None and now - last < window:
            self._refuse(
                "relay_debounced",
                f"That circuit's relay was operated less than {window:g} seconds ago. "
                "The command was refused rather than queued.",
            )
        self._last_relay[circuit] = now

    def _refuse(self, reason: str, message: str, parent_id: str | None = None) -> None:
        """Record the reason for `after_publish`, then veto."""
        _REFUSAL_REASON.set(reason)
        if parent_id is not None:
            # Names *what* asked when there is no *who* — the automation or
            # script the command came from. Without it a contextless refusal is
            # a blank line in the log.
            _LOGGER.warning(
                "Refused a SPAN control command (%s) originating from context %s",
                reason,
                parent_id,
            )
        raise ServiceValidationError(
            message,
            translation_domain=DOMAIN,
            translation_key=reason,
        )


@callback
def async_bind_caller(
    context: Context | None, entity_id: str | None
) -> Token[ControlCaller | None]:
    """Bind the caller for the duration of one control call.

    Returns the reset token; the caller must reset it in a `finally`. Kept here
    rather than inlined at the entity so the two `ContextVar`s are set and
    cleared in one place.
    """
    _REFUSAL_REASON.set(None)
    return CONTROL_CALLER.set(ControlCaller(context=context, entity_id=entity_id))


def outcome_is_failure(outcome: PublishOutcome) -> bool:
    """Whether this outcome means the command will never be delivered.

    Only `FAILED` is a promise about the future. `ACCEPTED` and `UNCONFIRMED`
    both mean the command was handed over and may already have been acted on —
    `UNCONFIRMED` most often means the value was already what was asked for — and
    presenting either as a failure would tell a user something untrue about their
    panel.
    """
    return outcome.state is PublishState.FAILED
