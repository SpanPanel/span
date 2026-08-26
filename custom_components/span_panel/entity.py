"""Base entity for Span Panel integration."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import ClassVar

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api import PublishOutcome, SpanPanelSnapshot

from .const import CONF_DEVICE_NAME
from .control_gate import CONTROL_CALLER, async_bind_caller
from .coordinator import SpanPanelCoordinator
from .field_paths import FieldPathDeclarationMixin
from .naming import generated_panel_device_name
from .util import snapshot_to_device_info


class SpanPanelEntity(CoordinatorEntity[SpanPanelCoordinator]):
    """Base entity for all Span Panel platforms."""

    _attr_has_entity_name = True

    _span_object_id_base: str | None = None
    """The base Home Assistant composes this entity's id from, when set.

    Circuit entities set it from `naming.circuit_object_id_base`; every other
    entity leaves it None and Core composes from the display name as it
    always has. Core reads `suggested_object_id` only for an entity that has
    not preset `entity_id`, which after this release is every entity here.
    """

    @property
    def suggested_object_id(self) -> str | None:
        """Hand Core the base, or the stock answer when there is none."""
        if self._span_object_id_base is not None:
            return self._span_object_id_base
        return super().suggested_object_id

    _residual_field_paths: ClassVar[tuple[str, ...]] = ()
    """Snapshot fields this entity reads from entity code, not from a description.

    A handful of reads cannot be expressed as a description `field_path`: the
    switch has no entity description at all, the select wraps one, and a circuit
    entity's name, tabs and attributes are read outside any `value_fn`.
    Declaring them here is what lets a Repair say which entities a dead field
    takes with it, instead of "0 affected".

    This is the only place these paths are written down:
    `field_paths.residual_field_paths()` collects them from every subclass, so
    the producible gate covers exactly what the entities claim.

    Keep the list short. A new entry is a hint that the reader belongs on a
    description instead, where the declaration and the reader are one object.
    """

    async def _async_guarded_control(self, action: Awaitable[PublishOutcome]) -> PublishOutcome:
        """Run one control call with this entity's caller bound to it.

        `self._context` is set by core immediately before a service handler runs
        (`helpers/service.py`), in the same task, so reading it here is reading
        the call that is happening right now. It is read explicitly rather than
        captured by overriding `async_set_context`, which would inherit core's
        five-second `CONTEXT_RECENT_TIME_SECONDS` staleness window and would also
        fire for paths that are not service calls.

        The binding is around the awaited call and reset in a `finally`, so it
        cannot leak into an unrelated task the way a mutable attribute would. The
        decision itself is made in `ControlGate`, at the publish — see that
        module for why the two halves are separate.
        """
        token = async_bind_caller(self._context, self.entity_id)
        try:
            return await action
        finally:
            CONTROL_CALLER.reset(token)

    async def async_added_to_hass(self) -> None:
        """Tell the coordinator which snapshot fields this entity reads.

        The entity is the only place both halves are known for certain: it holds
        its own declarations and its own `entity_id`. Reconstructing the pair
        from entity descriptions instead would mean reimplementing three
        different unique_id builders — circuit sensors, panel-data sensors and
        binary sensors each use a different suffix rule — and would rot the next
        time one of them changed.

        Consumed by the schema Repairs, which name the entities a field the panel
        stopped producing has taken down with it.
        """
        await super().async_added_to_hass()
        for field_path in self._declared_field_paths():
            self.coordinator.async_register_field_path_entity(field_path, self.entity_id)

    async def async_will_remove_from_hass(self) -> None:
        """Stop counting this entity against its fields."""
        for field_path in self._declared_field_paths():
            self.coordinator.async_unregister_field_path_entity(field_path, self.entity_id)
        await super().async_will_remove_from_hass()

    def _source_field_path(self) -> str | None:
        """Return the snapshot field this entity's value comes from, if any.

        The description's `field_path`, regardless of `derived`. A
        `SCHEMA_CONDITIONAL_FIELD` description is exempt from the *producible*
        gate, which is a statement about the other adapter, not about this
        entity: the adapter that does produce the field publishes a metadata
        row for it, that row can come back unresolved, and when it does this
        entity's reading is a default rather than a measurement — exactly as
        for a plain declaration.

        `None` for `MULTIPLE_FIELDS` and `NO_SOURCE_FIELD` descriptions, which
        have no single field to blame, and for the circuit switch, which has no
        entity description at all.
        """
        description: object = getattr(self, "entity_description", None)
        if isinstance(description, FieldPathDeclarationMixin):
            return description.field_path
        return None

    def _declared_field_paths(self) -> tuple[str, ...]:
        """Return every snapshot field this entity reads.

        Its source field when it has one, plus any residual reads. A platform
        with no entity description at all — the circuit switch — contributes
        only its residual reads.
        """
        source = self._source_field_path()
        if source is not None:
            return (source, *self._residual_field_paths)
        return self._residual_field_paths

    @property
    def _reads_an_unresolved_field(self) -> bool:
        """True when this entity's own source field could not be resolved.

        An O(1) membership test against a set that is empty on a healthy panel,
        so the normal case costs one hash lookup per availability read.

        Only the description's `field_path` counts -- the entity's value source.
        Residual paths are deliberately excluded: `circuit.name` and
        `circuit.tabs` feed naming and attributes, and a circuit's power reading
        is still true when they are gone. The switch and select read their state
        through residual paths and so are not covered here; the Repair still
        names them.

        `derived` is deliberately not consulted. It says why a path is outside
        the both-adapters producible gate, which is a fact about the *other*
        adapter; the adapter running here still resolves the field or fails to.
        Excluding schema-conditional entities from this probe left them with a
        default they present as a reading -- the `evse_ev_connected` failure
        mode, reached by a different route.
        """
        source = self._source_field_path()
        return source is not None and source in self.coordinator.unresolved_paths

    @property
    def available(self) -> bool:
        """False when this entity's snapshot field could not be resolved.

        Any value we would report for an unresolvable field is a default rather
        than a reading, and a default is indistinguishable from a real one at
        the dashboard. Reporting unavailable is reporting, not correcting: the
        entity keeps its shape and comes back when the field does.

        This covers the resolution-failure case only. A field the adapter
        resolves but the device stops publishing still reaches HA as a parsed
        default; that needs the snapshot model to admit None.
        """
        if self._reads_an_unresolved_field:
            return False
        return super().available

    @staticmethod
    def _build_device_info(
        coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
    ) -> DeviceInfo:
        """Construct device info from coordinator and snapshot."""
        device_name = coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, coordinator.config_entry.title
        )
        host = coordinator.config_entry.data.get(CONF_HOST)
        return snapshot_to_device_info(snapshot, device_name, host=host)

    @staticmethod
    def _generated_panel_device_name(
        coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
    ) -> str | None:
        """Return the panel device's name where this integration generated it.

        The registry's answer, not the config entry's: the rule turns on which
        registry field the name sits in, `name` being ours and `name_by_user`
        the user's, and a `DeviceInfo` cannot tell the two apart. Read from the
        panel's own device even for an entity shown on a sub-device card, since
        the ids at stake are the ones the preset builder spelled with the panel.

        Static, and taking the coordinator, so the three circuit platforms can
        ask before `super().__init__` has run -- which is where the switch
        decides its id, and so where `self.coordinator` does not exist yet.
        """
        return generated_panel_device_name(
            coordinator.hass, coordinator.config_entry.entry_id, snapshot.serial_number
        )
