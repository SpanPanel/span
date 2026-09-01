"""Number entities for the Span Panel — today, one per commissioned EV charger.

The charge-current ceiling is the only settable property v1.0 puts outside the
panel and its circuits, and the only control this integration has ever offered
that carries a physical bound: the installer commissions a maximum from the
breaker rating and J1772 derating, and nothing a user does may exceed it.

So every number this platform builds is described by the panel rather than by
this module. The value, the maximum, and the fact that a control exists at all
come from the charger's own `$description`, resolved in the library
(`span_panel_api_schema_1.charge_limit`) because the node carrying the limit has
two spellings in circulation and the `$description` is the specification's
authority on which one a charger publishes. Nothing here names a wire property.

**A control is offered only where the panel declares one.** `$settable` on the
limit is what creates the entity; the commissioned ceiling is what bounds it.
A charger that declares neither gets no entity, which is the honest rendering of
`charge-limit.md`'s absence semantics — "the EVSE has no adjustable
charge-current ceiling (it charges at a fixed rate)".

**A pending write shows as an attribute, not as a state.** The panel echoes an
accepted command on the Homie `$target` topic and republishes the property when
it takes effect, and the priority select already renders that pair exactly this
way (`circuit.priority_target`). Reporting the requested value as the state
instead would show a limit the charger may never have accepted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import (
    EvseControlProtocol,
    PublishOutcome,
    SpanEvseSnapshot,
    SpanPanelSnapshot,
)

from .adoption import create_adopted_numbers
from .const import CONF_DEVICE_NAME, USE_CIRCUIT_NUMBERS
from .control_gate import ControlMode
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .field_paths import DerivedReason, FieldPathDeclarationMixin
from .helpers import build_evse_unique_id_for_entry, resolve_evse_display_suffix
from .runtime import SpanPanelConfigEntry
from .util import EMPTY_EVSE, evse_device_info, evse_display_name

_LOGGER: logging.Logger = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True)
class SpanEvseNumberRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for EVSE number entities."""

    value_fn: Callable[[SpanEvseSnapshot], int | None]
    maximum_fn: Callable[[SpanEvseSnapshot], int | None]
    target_fn: Callable[[SpanEvseSnapshot], int | None]
    settable_fn: Callable[[SpanEvseSnapshot], bool]
    set_fn: Callable[[EvseControlProtocol, str, int], Awaitable[PublishOutcome]]


@dataclass(frozen=True, kw_only=True)
class SpanEvseNumberEntityDescription(NumberEntityDescription, SpanEvseNumberRequiredKeysMixin):
    """Describes an EVSE number entity."""


EVSE_CHARGE_CURRENT_LIMIT: Final = SpanEvseNumberEntityDescription(
    key="evse_charge_current_limit",
    field_path="evse.charge_current_limit_a",
    # One field, produced by schema_1 alone: flat firmware's `evse` device type
    # carries `advertised-current` — what the charger is offering the vehicle,
    # read-only — and no settable ceiling anywhere, so the both-adapters gate
    # cannot be satisfied. schema_1 carries a metadata row for the field, which
    # is what makes the path SCHEMA_1_ONLY rather than NEITHER and buys the
    # entity unit validation against the charger's own `$description`.
    derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
    translation_key="evse_charge_current_limit",
    device_class=NumberDeviceClass.CURRENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    # Zero, because a charge-current ceiling has no meaning below it: this is a
    # charge-only EVSE, so lowering the ceiling can stop charging and can never
    # reverse it. Not read from the wire because there is nothing on the wire to
    # read — Homie expresses a bounded numeric as a `min:max` `$format`, and
    # neither the capture nor the `charge-limit` catalog declares one for this
    # property. See `native_max_value`, which *is* published and is read.
    native_min_value=0,
    # The declared datatype is `integer`, and a step is the granularity of the
    # quantity rather than a policy: a charger that accepts 16 A and 17 A does
    # not accept 16.5. Asserted against the declaration in
    # `test_evse_charge_limit.py` rather than assumed here.
    native_step=1,
    mode=NumberMode.BOX,
    entity_category=EntityCategory.CONFIG,
    value_fn=lambda evse: evse.charge_current_limit_a,
    maximum_fn=lambda evse: evse.charge_current_ceiling_a,
    target_fn=lambda evse: evse.charge_current_limit_target_a,
    settable_fn=lambda evse: evse.charge_current_limit_settable,
    set_fn=lambda client, node_id, amps: client.set_evse_charge_limit(node_id, amps),
)
"""The owner's charge-current ceiling.

`mode=BOX` rather than a slider: the useful values are a handful of amperages an
installer or an owner knows by name (16, 24, 32, 40), and a slider over an
installer-set range invites dragging past the value someone meant. The range is
still enforced — Home Assistant checks the service call against `min_value` and
`max_value`, and the library refuses anything above the commissioned ceiling
before it reaches the wire.

Deliberately `EntityCategory.CONFIG`, beside the circuit priority select: this
changes how the panel behaves rather than reporting how it is behaving.
"""

EVSE_NUMBERS: tuple[SpanEvseNumberEntityDescription, ...] = (EVSE_CHARGE_CURRENT_LIMIT,)


class SpanEvseNumber(SpanPanelEntity, NumberEntity):
    """One settable amperage on one commissioned EV charger."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanEvseNumberEntityDescription,
        evse_id: str,
    ) -> None:
        """Initialize the EVSE number."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data
        self._evse_id = evse_id
        # The same object under two names. `entity_description` is what Home
        # Assistant and `SpanPanelEntity._source_field_path` read, and its
        # declared type is `NumberEntityDescription`; `_description` is the same
        # instance at the type this platform actually declared, so the readers
        # below stay on the description rather than being copied off it onto the
        # entity. Copying is what splits a declaration from its reader, which is
        # the drift `field_paths` exists to prevent.
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

        # The charger's own name, kept because both ways this control can fail
        # are reported to a person. `_evse_id` is the node id the wire uses; it
        # names nothing the user can see on their dashboard.
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
        """Take the reading and the bound the panel currently publishes.

        The maximum moves with the panel because it can: an installer
        recommissioning a charger republishes `installer-max`, and a control
        still offering the old range would let a user ask for a current the
        hardware is no longer rated for. Left at whatever was last published
        when the value goes away, so the entity reports unavailable with its
        last known bound rather than briefly widening.
        """
        self._attr_native_value = self._description.value_fn(evse)
        maximum = self._description.maximum_fn(evse)
        if maximum is not None:
            self._attr_native_max_value = maximum

    @property
    def available(self) -> bool:
        """False while the panel is offline or the bound is unknown.

        Offline follows the priority select: a control that cannot reach the
        panel is not a control.

        The bound is the addition, and it is this entity's own hazard. A number
        must report *some* maximum, so an unpublished ceiling would otherwise be
        rendered as Home Assistant's default of 100 — a plausible-looking
        amperage that no installer commissioned. Reporting unavailable says the
        panel has not told us what the charger is rated for, which is the true
        statement; the entity comes back when the ceiling does.
        """
        if not self._transport_available:
            return False
        if self.coordinator.panel_offline:
            return False
        if self._description.maximum_fn(self._evse()) is None:
            return False
        return super().available

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """The pending write, while the panel is echoing one.

        The same rendering the priority select gives `priority_target`, and for
        the same reason: `$target` is a command in flight, and the state stays
        the value the charger is actually enforcing until it republishes.
        """
        target = self._description.target_fn(self._evse())
        if target is None:
            return None
        return {"charge_current_limit_target": target}

    async def async_set_native_value(self, value: float) -> None:
        """Ask the panel to lower (or restore) this charger's ceiling.

        Home Assistant has already rejected anything outside `min_value` /
        `max_value` by the time this runs, and the library refuses anything above
        the commissioned ceiling again before publishing. That is not redundancy:
        the first check is against the range this entity last reported, the second
        against what the panel is publishing now, and a recommissioning between
        the two is exactly when they differ.

        A fractional request truncates rather than rounds. The property is
        declared `integer`, so some whole number has to be chosen, and for a
        ceiling the safe direction is down: asking for 16.7 A and getting 16 is
        a slower charge, asking for it and getting 17 is a current the user did
        not request.

        Both ways the write does not happen reach the caller, through the
        `_async_control` every control in this integration shares. A refusal
        never resolved an address; a `FAILED` outcome resolved one and was never
        handed over, which the library promises means it will not arrive later.
        """
        client = self.coordinator.client
        await self._async_control(
            self._description.set_fn(client, self._evse_id, int(value)),
            command=f"a charge-current limit for {self._charger_name}",
            failed_key="evse_charge_limit_failed",
            not_delivered_key="evse_charge_limit_not_delivered",
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
    """Set up number entities for Span Panel."""
    _LOGGER.debug("ASYNC SETUP ENTRY NUMBER")

    # Under `disabled` no control entity is created and no registry entry is
    # removed. See `switch.async_setup_entry` for why the registry entries stay.
    if config_entry.runtime_data.control_policy.mode is ControlMode.DISABLED:
        return

    coordinator = config_entry.runtime_data.coordinator
    snapshot: SpanPanelSnapshot = coordinator.data

    curated: list[SpanEvseNumber] = [
        SpanEvseNumber(coordinator, description, evse_id)
        for evse_id, evse in snapshot.evse.items()
        for description in EVSE_NUMBERS
        # The declaration is the gate, never the value: a charger that
        # declares the property settable and has not published one yet still
        # has the control, and a charger that publishes a value it does not
        # declare settable does not.
        if description.settable_fn(evse)
    ]
    # Added before adoption is attempted, and in their own call, so that the
    # controls this integration models cannot be lost to a device it does not.
    # Adoption reads a vendor-extensible schema, so its inputs are the ones no
    # amount of care here fully constrains -- a malformed numeric `$format` once
    # raised out of `AdoptedNumber` and failed this whole coroutine, taking the
    # charge-current limits of curated chargers with it. Ordering is what makes
    # that structurally impossible; `classify` refusing an unreadable format is
    # what makes it not happen. Both, because only one of them is a rule about
    # data this integration controls.
    async_add_entities(curated)

    # Settable numerics on devices this integration models nothing for, whose
    # bounds come from the declaration -- which is what made them numbers rather
    # than readings in the first place.
    async_add_entities(
        create_adopted_numbers(
            coordinator,
            snapshot,
            dr.async_get(hass),
            panel_device_id=config_entry.runtime_data.panel_device_id,
            overlay=config_entry.runtime_data.curation,
        )
    )
