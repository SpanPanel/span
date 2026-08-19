"""Base entity for Span Panel integration."""

from __future__ import annotations

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

    async def async_added_to_hass(self) -> None:
        """Tell the coordinator which snapshot field this entity reads.

        The entity is the only place both halves are known for certain: it holds
        its own `field_path` declaration and its own `entity_id`. Reconstructing
        the pair from entity descriptions instead would mean reimplementing three
        different unique_id builders — circuit sensors, panel-data sensors and
        binary sensors each use a different suffix rule — and would rot the next
        time one of them changed.

        Consumed by the schema Repairs, which name the entities a field the panel
        stopped producing has taken down with it.
        """
        await super().async_added_to_hass()
        field_path = self._declared_field_path()
        if field_path is not None:
            self.coordinator.async_register_field_path_entity(field_path, self.entity_id)

    async def async_will_remove_from_hass(self) -> None:
        """Stop counting this entity against its field."""
        field_path = self._declared_field_path()
        if field_path is not None:
            self.coordinator.async_unregister_field_path_entity(field_path, self.entity_id)
        await super().async_will_remove_from_hass()

    def _declared_field_path(self) -> str | None:
        """Return the snapshot field this entity reads, if its description declares one.

        Platforms that carry no entity description (the circuit switch) or whose
        description declares nothing (`derived` entities, which read several
        fields or none) return None and are simply not tracked.
        """
        description: object = getattr(self, "entity_description", None)
        if not isinstance(description, FieldPathDeclarationMixin) or description.derived:
            return None
        return description.field_path

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
