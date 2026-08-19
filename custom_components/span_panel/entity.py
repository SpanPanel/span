"""Base entity for Span Panel integration."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api import SpanPanelSnapshot

from .const import CONF_DEVICE_NAME
from .coordinator import SpanPanelCoordinator
from .field_paths import FieldPathDeclarationMixin
from .util import snapshot_to_device_info


class SpanPanelEntity(CoordinatorEntity[SpanPanelCoordinator]):
    """Base entity for all Span Panel platforms."""

    _attr_has_entity_name = True

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

    def _declared_field_paths(self) -> tuple[str, ...]:
        """Return every snapshot field this entity reads.

        The description's `field_path` when it declares one, plus any residual
        reads. A description that declares nothing (`derived` entities, whose
        `DerivedReason` says why no single field is theirs) contributes nothing,
        and a platform with no entity description at all — the circuit switch —
        contributes only its residual reads.
        """
        description: object = getattr(self, "entity_description", None)
        if (
            isinstance(description, FieldPathDeclarationMixin)
            and not description.derived
            and description.field_path is not None
        ):
            return (description.field_path, *self._residual_field_paths)
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
        """
        description: object = getattr(self, "entity_description", None)
        if (
            isinstance(description, FieldPathDeclarationMixin)
            and not description.derived
            and description.field_path is not None
        ):
            return description.field_path in self.coordinator.unresolved_paths
        return False

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
