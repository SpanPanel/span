"""The types that describe a Span Panel config entry's runtime data.

A leaf module on purpose. `SpanPanelConfigEntry` is the annotation every
platform, the coordinator, the migrations and the services need, and it used to
live in the package root -- which imports every platform. Naming the entry type
therefore meant importing the whole integration, so the modules that needed only
the annotation reached for it from inside functions instead, and `services.py`
grew five copies of the same runtime-data guard around five deferred imports.

Nothing here imports anything from this package at runtime except
`control_gate` and `curation`, and neither reaches back into the platforms:
`control_gate` sees `const` and `options`, `curation` sees `const` and `util`.
`SpanPanelCoordinator` is needed only as an annotation, so it is imported under
`TYPE_CHECKING`: a module that wants to say "this is a SPAN entry" should not
have to pull in the coordinator and, through it, the schema and sensor
machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .control_gate import ControlLock, ControlPolicy
from .curation import CurationOverlay

if TYPE_CHECKING:
    from .coordinator import SpanPanelCoordinator


def _default_control_lock() -> ControlLock:
    """Return the lock that belongs to the default control policy.

    Derived from the policy rather than written down again, so the two cannot
    say different things about whether the feature is on. `async_setup_entry`
    builds the pair the same way (`ControlLock(armed=policy.lock_enabled)`): a
    lock that exists as a user-facing thing starts armed, and one the entry has
    not enabled starts disarmed because there is no switch to open it with.
    """
    return ControlLock(armed=ControlPolicy.default().lock_enabled)


@dataclass
class SpanPanelRuntimeData:
    """Runtime data for a Span Panel config entry."""

    coordinator: SpanPanelCoordinator
    # Registry id of the panel device, which every sub-device (BESS, MID, EVSE)
    # hangs off with `via_device_id`. Resolved once during setup rather than
    # per platform: sub-devices are only ever built after the panel device
    # exists, so an id looked up here is one no caller has to handle the absence
    # of. See `ensure_device_registered`.
    panel_device_id: str
    # The user's curation overlay for adopted entities, loaded from .storage
    # before the platforms are forwarded so every adopted entity is *born* with
    # its curated metadata -- a state class that arrives after the first state is
    # written is a statistics reset rather than a metadata change. Required
    # rather than defaulted: a setup path that forgets the load has to fail here,
    # because an empty overlay is indistinguishable from a user who has curated
    # nothing, and the user's records would still be sitting on disk.
    curation: CurationOverlay
    # Resolved once at setup and read by every control platform, so a single
    # answer decides which entities exist and which callers may operate them.
    # Defaulted rather than required because the default *is* the policy an entry
    # with no control options has, and that is most of them.
    control_policy: ControlPolicy = field(default_factory=ControlPolicy.default)
    # Shared with the gate, and mutated by the lock entity. One object per entry:
    # the gate reads it on every publish and the entity writes it. The default
    # is the default policy's lock rather than a bare `ControlLock()`, whose
    # disarmed state would be a promise this class is in no position to make.
    control_lock: ControlLock = field(default_factory=_default_control_lock)


type SpanPanelConfigEntry = ConfigEntry[SpanPanelRuntimeData]


def loaded_runtime_data(entry: ConfigEntry) -> SpanPanelRuntimeData | None:
    """Return this entry's runtime data, or None if it is not one of ours.

    Every service is registered domain-wide and walks
    `async_loaded_entries(DOMAIN)`, so each of them has to answer this question
    before touching an entry -- and so does anything handed an entry by core:
    `async_unload_entry` after the platforms have gone, and
    `async_remove_config_entry_device`.

    Both halves of the check are real. `ConfigEntry.runtime_data` is a bare
    annotation that core deletes on unload (`config_entries.py:1044-1045`), so
    the attribute is genuinely absent on an entry that has not finished setting
    up -- hence `getattr` with a default rather than an attribute read. And what
    is there is whatever the owning integration put there, so `isinstance` is
    what says it is ours; a test double may put anything at all in it.

    One helper, one answer. It lives here rather than in `services` because
    `services` is not a leaf and half the callers are not services: this module
    already owns the type the check is against, and importing it costs nothing.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if isinstance(runtime_data, SpanPanelRuntimeData):
        return runtime_data
    return None
