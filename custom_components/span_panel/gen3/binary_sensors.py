"""Binary sensor entities for Gen3 Span panels.

Provides breaker ON/OFF state detection for each circuit based on
voltage threshold. A breaker is considered ON if its voltage exceeds
5V (5000 mV).
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.span_panel.const import DOMAIN

from .coordinator import SpanGen3Coordinator
from .span_grpc_client import PanelData

_LOGGER = logging.getLogger(__name__)


def create_gen3_binary_sensors(
    coordinator: SpanGen3Coordinator,
) -> list[BinarySensorEntity]:
    """Create all Gen3 binary sensor entities for the panel."""
    host = coordinator.config_entry.data["host"]
    data: PanelData = coordinator.data
    entities: list[BinarySensorEntity] = []

    for circuit_id in data.circuits:
        entities.append(SpanGen3BreakerSensor(coordinator, host, circuit_id))

    return entities


class SpanGen3BreakerSensor(CoordinatorEntity[SpanGen3Coordinator], BinarySensorEntity):
    """Binary sensor for breaker state (ON/OFF based on voltage)."""

    _attr_has_entity_name = True
    _attr_device_class: BinarySensorDeviceClass | None = BinarySensorDeviceClass.POWER

    def __init__(
        self,
        coordinator: SpanGen3Coordinator,
        host: str,
        circuit_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._host = host
        self._circuit_id = circuit_id
        self._attr_unique_id = f"{host}_gen3_circuit_{circuit_id}_breaker"
        self._attr_name = "Breaker"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — circuit sub-device."""
        info = self.coordinator.data.circuits.get(self._circuit_id)
        name = info.name if info else f"Circuit {self._circuit_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._host}_circuit_{self._circuit_id}")},
            name=name,
            manufacturer="Span",
            model="Circuit Breaker",
            via_device=(DOMAIN, self._host),
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if breaker is ON (voltage present)."""
        m = self.coordinator.data.metrics.get(self._circuit_id)
        if m is None:
            return None
        is_on: bool = m.is_on
        return is_on
