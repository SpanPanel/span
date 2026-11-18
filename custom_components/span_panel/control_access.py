"""Whether the user asking may operate the panel, as `span_panel/panel_topology` reports it.

The card disables a panel's relay and priority controls for a user this says may
not operate them, so it answers from the two rules that would refuse that user
and restates neither:

- **The control mode**, `ControlPolicy.refusal_for`, which the gate enforces on
  every command. It comes first, because it is the integration's own answer
  and holds for every control the entry has.
- **The user's Home Assistant permissions.** Core refuses an entity service
  call from a user who is not an administrator, with `Unauthorized`, for each
  entity that `user.permissions.check_entity(entity_id, POLICY_CONTROL)` does
  not allow, and administrators are not checked at all (`helpers/service.py`).
  That happens before any entity, and so before the gate, is asked, which is
  why the gate never answers it and this module has to.

`control_access` asks the mode, then the permissions, and reports the first
refusal.
"""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

from homeassistant.auth.permissions.const import POLICY_CONTROL

from .control_gate import ControlModeRefusal, ControlPolicy

if TYPE_CHECKING:
    from homeassistant.auth.models import User


class PermissionRefusal(StrEnum):
    """Why the user's own Home Assistant permissions keep them from operating the panel.

    A wire value beside `ControlModeRefusal`'s, in the same
    `control_access.reason` field. Separate because Core decides it, not the
    control mode, and the gate never refuses for it.
    """

    READ_ONLY = "read_only"
    """The user may control none of the panel's circuit controls, as a Read Only user may control nothing."""


type ControlAccessReason = ControlModeRefusal | PermissionRefusal
"""Every reason `control_access` can give: the control mode's, then Core's permissions'."""


class ControlAccessRecord(TypedDict):
    """Whether the user asking may operate the panel, and if not, why.

    `may_operate` is true exactly where `reason` is null. Neither says whether
    the control lock is armed, which changes while the card is open and which
    the card reads from the lock's own switch. A WebSocket connection always
    has a logged-in user, so whether control without one is allowed never comes
    into it.
    """

    may_operate: bool
    reason: ControlAccessReason | None


def control_access(
    policy: ControlPolicy, user: User, circuit_controls: Collection[str]
) -> ControlAccessRecord:
    """Return whether `user` may operate the panel whose policy is `policy`.

    `circuit_controls` is the entity id of every relay switch and priority
    select the topology offers for the panel's circuits. The permissions are
    checked against those, and only those, because they are what the card
    disables on this answer: a user able to control only the control lock's
    switch, or an EVSE's, could still operate none of them.

    `read_only` is reported where there is at least one such control and the
    user may control none of them. Where they may control some, the card keeps
    its controls and Core refuses the rest, one at a time, as it would anywhere
    else -- the answer is one flag for the whole panel, and a user who can
    operate something is not told they can operate nothing. Where the panel
    offers no circuit control there is nothing to disable and nothing for Core
    to refuse, so the permissions are not reported.
    """
    reason: ControlAccessReason | None = policy.refusal_for(is_admin=user.is_admin)
    if reason is None:
        reason = _permission_refusal(user, circuit_controls)
    return ControlAccessRecord(may_operate=reason is None, reason=reason)


def _permission_refusal(user: User, circuit_controls: Collection[str]) -> PermissionRefusal | None:
    """Core's answer for `user` over `circuit_controls`, by the check its entity services make."""
    if user.is_admin or not circuit_controls:
        return None
    if any(
        user.permissions.check_entity(entity_id, POLICY_CONTROL) for entity_id in circuit_controls
    ):
        return None
    return PermissionRefusal.READ_ONLY
