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
    `field_paths.RESIDUAL_FIELD_PATHS` already names that exact set for the
    producible gate; declaring the same paths here is what lets a Repair say
    which entities a dead one takes with it, instead of "0 affected".

    `test_every_residual_field_path_is_claimed_by_an_entity` pins the two lists
    to each other in both directions.
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
        reads. A description that declares nothing (`derived` entities, which
        read several fields or none) contributes nothing, and a platform with no
        entity description at all — the circuit switch — contributes only its
        residual reads.
        """
        description: object = getattr(self, "entity_description", None)
        if (
            isinstance(description, FieldPathDeclarationMixin)
            and not description.derived
            and description.field_path is not None
        ):
            return (description.field_path, *self._residual_field_paths)
        return self._residual_field_paths

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
