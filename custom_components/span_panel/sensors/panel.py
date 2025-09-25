"""Panel-level sensors for Span Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.typing import UNDEFINED

from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.helpers import (
    construct_panel_unique_id_for_entry,
    construct_synthetic_unique_id_for_entry,
    get_panel_entity_suffix,
)
from custom_components.span_panel.sensor_definitions import (
    SpanPanelBatterySensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelStatusSensorEntityDescription,
)
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.span_panel_data import SpanPanelData
from custom_components.span_panel.span_panel_hardware_status import SpanPanelHardwareStatus
from custom_components.span_panel.span_panel_storage_battery import SpanPanelStorageBattery

from .base import SpanEnergySensorBase, SpanSensorBase

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelPanelStatus(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]):
    """Span Panel data status sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the Span Panel data status sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel data sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel data sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Sensor"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel data status sensor."""
        return span_panel.panel


class SpanPanelStatus(
    SpanSensorBase[SpanPanelStatusSensorEntityDescription, SpanPanelHardwareStatus]
):
    """Span Panel hardware status sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelStatusSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the Span Panel hardware status sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelStatusSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel status sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelStatusSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel status sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Status"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        """Get the data source for the panel status sensor."""
        try:
            result = span_panel.status
            return result
        except Exception as e:
            _LOGGER.error("HARDWARE_STATUS: Error getting status data: %s", e)
            raise


class SpanPanelBattery(
    SpanSensorBase[SpanPanelBatterySensorEntityDescription, SpanPanelStorageBattery]
):
    """Span Panel battery sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelBatterySensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the Span Panel battery sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelBatterySensorEntityDescription
    ) -> str:
        """Generate unique ID for battery sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelBatterySensorEntityDescription
    ) -> str:
        """Generate friendly name for battery sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Battery"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelStorageBattery:
        """Get the data source for the battery sensor."""
        _LOGGER.debug("BATTERY_DEBUG: get_data_source called for battery sensor")
        try:
            result = span_panel.storage_battery
            _LOGGER.debug("Successfully got battery data: %s", type(result).__name__)
            return result
        except Exception as e:
            _LOGGER.error("Error getting battery data: %s", e)
            raise


class SpanPanelPowerSensor(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]):
    """Enhanced panel power sensor with amperage attribute calculation."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the enhanced panel power sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel power sensors."""
        # Use the same logic as migration: get entity suffix and use synthetic unique_id

        entity_suffix = get_panel_entity_suffix(description.key)
        unique_id = construct_synthetic_unique_id_for_entry(
            self.coordinator, span_panel, entity_suffix, self._device_name
        )

        return unique_id

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel power sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Power"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel power sensor."""
        return span_panel.panel

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including amperage calculation."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None

        attributes = {}

        # Add voltage attribute (standard panel voltage)
        attributes["voltage"] = "240"

        # Calculate amperage from power (P = V * I, so I = P / V)
        if self.native_value is not None and isinstance(self.native_value, int | float):
            try:
                amperage = float(self.native_value) / 240.0
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"
        else:
            attributes["amperage"] = "0.0"

        return attributes


class SpanPanelEnergySensor(
    SpanEnergySensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]
):
    """Panel energy sensor with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the panel energy sensor."""
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate unique ID for panel energy sensors."""
        # Use the same logic as migration: get entity suffix and use synthetic unique_id

        entity_suffix = get_panel_entity_suffix(description.key)
        return construct_synthetic_unique_id_for_entry(
            self.coordinator, span_panel, entity_suffix, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelDataSensorEntityDescription
    ) -> str:
        """Generate friendly name for panel energy sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Energy"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel energy sensor."""
        return span_panel.panel

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and voltage."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add voltage attribute (standard panel voltage)
        attributes["voltage"] = "240"

        return attributes if attributes else None
