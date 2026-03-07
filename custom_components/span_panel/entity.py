"""Base entity for Span Panel integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api import SpanPanelSnapshot

from .const import CONF_API_VERSION, CONF_DEVICE_NAME
from .coordinator import SpanPanelCoordinator
from .util import snapshot_to_device_info


class SpanPanelEntity(CoordinatorEntity[SpanPanelCoordinator]):
    """Base entity for all Span Panel platforms."""

    _attr_has_entity_name = True

    @staticmethod
    def _build_device_info(
        coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
    ) -> DeviceInfo:
        """Construct device info from coordinator and snapshot."""
        device_name = coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, coordinator.config_entry.title
        )
        is_simulator = coordinator.config_entry.data.get(CONF_API_VERSION) == "simulation"
        host = coordinator.config_entry.data.get(CONF_HOST)
        return snapshot_to_device_info(snapshot, device_name, is_simulator, host)
