"""Lock entities for the Span Panel — one per EV charger whose connector lock is settable.

**A control is offered only where the panel declares one.** The library sets
`SpanEvseSnapshot.lock_control` only where the charger declares its lock state
`$settable`, and refuses a value its `$format` does not list, so this module
names no wire property and no lock state of its own. A charger whose lock is
read-only keeps the `evse_lock_state` sensor it always had and gets no lock.

The lock is a new entity beside that sensor, not a replacement for it: the
sensor's entity id is permanent, and a dashboard reading it keeps working.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import (
    EvseLockControlProtocol,
    PublishOutcome,
    SpanEvseSnapshot,
    SpanPanelSnapshot,
)

from .const import CONF_DEVICE_NAME, USE_CIRCUIT_NUMBERS
from .control_gate import ControlMode
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .field_paths import FieldPathDeclarationMixin
from .helpers import build_evse_unique_id_for_entry, resolve_evse_display_suffix
from .runtime import SpanPanelConfigEntry
from .util import EMPTY_EVSE, evse_device_info, evse_display_name

_LOGGER: logging.Logger = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

_POSITIONS: Final[dict[str, bool]] = {"LOCKED": True, "UNLOCKED": False}
"""The lock states that are a position of the lock; anything else reads unknown."""


@dataclass(frozen=True)
class SpanEvseLockRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for EVSE lock entities."""

    settable_fn: Callable[[SpanEvseSnapshot], bool]
    set_fn: Callable[[EvseLockControlProtocol, str, bool], Awaitable[PublishOutcome]]


@dataclass(frozen=True, kw_only=True)
class SpanEvseLockEntityDescription(LockEntityDescription, SpanEvseLockRequiredKeysMixin):
    """Describes an EVSE lock entity."""


EVSE_LOCK: Final = SpanEvseLockEntityDescription(
    key="lock",
    # The field the read-only lock-state sensor already reads, which both
    # adapters produce; only the control is new, and the library resolves it
    # from the declaration rather than from this description.
    field_path="evse.lock_state",
    translation_key="evse_lock",
    settable_fn=lambda evse: evse.lock_control is not None,
    set_fn=lambda client, node_id, locked: client.set_evse_lock(node_id, locked),
)
"""The charger's connector lock, keyed `lock` so its unique id reads `..._evse_<id>_lock`."""


class SpanEvseLock(SpanPanelEntity, LockEntity):
    """The connector lock of one commissioned EV charger."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanEvseLockEntityDescription,
        evse_id: str,
    ) -> None:
        """Initialize the EVSE lock."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data
        self._evse_id = evse_id
        # The same object under two names, for the reason `SpanEvseNumber` gives.
        self.entity_description = description
        self._description = description

        panel_name = (
            data_coordinator.config_entry.data.get(
                CONF_DEVICE_NAME, data_coordinator.config_entry.title
            )
            or "Span Panel"
        )
        evse = snapshot.evse.get(evse_id, EMPTY_EVSE)
        use_circuit_numbers = data_coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        display_suffix = resolve_evse_display_suffix(evse, snapshot, use_circuit_numbers)
        # Named in the errors a person reads; `_evse_id` is the wire's node id.
        self._charger_name = evse_display_name(evse, panel_name, display_suffix)

        self._attr_device_info = evse_device_info(
            snapshot.serial_number,
            evse,
            panel_name,
            display_suffix,
            panel_device_id=data_coordinator.config_entry.runtime_data.panel_device_id,
        )
        self._attr_unique_id = build_evse_unique_id_for_entry(
            data_coordinator,
            snapshot,
            evse_id,
            description.key,
            data_coordinator.config_entry.data.get(
                CONF_DEVICE_NAME, data_coordinator.config_entry.title
            ),
        )
        self._apply(evse)

    def _evse(self) -> SpanEvseSnapshot:
        snapshot: SpanPanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return EMPTY_EVSE
        return snapshot.evse.get(self._evse_id, EMPTY_EVSE)

    def _apply(self, evse: SpanEvseSnapshot) -> None:
        """Take the lock state the charger reports: locked, unlocked, or not known.

        Anything else the charger reports, its own `UNKNOWN` included, is not a
        position of the lock, so the entity reads unknown rather than guessing.
        """
        self._attr_is_locked = _POSITIONS.get((evse.lock_state or "").upper())

    @property
    def available(self) -> bool:
        """False while the panel is offline, or the charger no longer offers its lock.

        A control that cannot reach the panel is not a control, and neither is one
        whose charger has left the snapshot or stopped declaring the lock settable.
        """
        if not self._transport_available:
            return False
        if self.coordinator.panel_offline:
            return False
        if self._evse().lock_control is None:
            return False
        return super().available

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the charger's connector."""
        await self._async_set(locked=True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the charger's connector."""
        await self._async_set(locked=False)

    async def _async_set(self, *, locked: bool) -> None:
        """Ask the panel to move the lock, through the control path every control shares.

        A refusal and an undelivered command both reach the caller; the state
        stays what the charger reports until it republishes.
        """
        await self._async_control(
            self._description.set_fn(self.coordinator.client, self._evse_id, locked),
            command=f"a lock command for {self._charger_name}",
            failed_key="evse_lock_failed",
            not_delivered_key="evse_lock_not_delivered",
            placeholders={"charger": self._charger_name},
        )
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._apply(self._evse())
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up lock entities for Span Panel."""
    # Under `disabled` no control entity is created and no registry entry is
    # removed. See `switch.async_setup_entry` for why the registry entries stay.
    if config_entry.runtime_data.control_policy.mode is ControlMode.DISABLED:
        return

    coordinator = config_entry.runtime_data.coordinator
    snapshot: SpanPanelSnapshot = config_entry.runtime_data.setup_snapshot

    async_add_entities(
        SpanEvseLock(coordinator, EVSE_LOCK, evse_id)
        for evse_id, evse in snapshot.evse.items()
        # The declaration is the gate, never the value: a lock declared settable
        # is a control before the charger reports its position.
        if EVSE_LOCK.settable_fn(evse)
    )
