"""Sensor entities for Gen3 Span panels.

Creates power, voltage, current, and frequency sensors for both
the main feed and individual circuits. Uses CoordinatorEntity +
SensorEntity following standard HA patterns.
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from .coordinator import SpanGen3Coordinator
from .span_grpc_client import PanelData

_LOGGER = logging.getLogger(__name__)


def create_gen3_sensors(
    coordinator: SpanGen3Coordinator,
) -> list[SensorEntity]:
    """Create all Gen3 sensor entities for the panel.

    Returns a flat list of main feed sensors + per-circuit sensors.
    """
    host = coordinator.config_entry.data["host"]
    entities: list[SensorEntity] = []

    # Main feed sensors
    entities.extend(
        [
            SpanGen3MainPowerSensor(coordinator, host),
            SpanGen3MainVoltageSensor(coordinator, host),
            SpanGen3MainCurrentSensor(coordinator, host),
            SpanGen3MainFrequencySensor(coordinator, host),
        ]
    )

    # Per-circuit sensors
    data: PanelData = coordinator.data
    for circuit_id in data.circuits:
        entities.extend(
            [
                SpanGen3CircuitPowerSensor(coordinator, host, circuit_id),
                SpanGen3CircuitVoltageSensor(coordinator, host, circuit_id),
                SpanGen3CircuitCurrentSensor(coordinator, host, circuit_id),
                SpanGen3CircuitPositionSensor(coordinator, host, circuit_id),
            ]
        )

    return entities


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------


class SpanGen3SensorBase(CoordinatorEntity[SpanGen3Coordinator], SensorEntity):
    """Base class for Gen3 sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SpanGen3Coordinator, host: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._host = host

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the main panel device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name="SPAN Panel",
            manufacturer="Span",
            model="Gen3",
        )


class SpanGen3CircuitSensorBase(SpanGen3SensorBase):
    """Base class for per-circuit Gen3 sensors."""

    def __init__(
        self,
        coordinator: SpanGen3Coordinator,
        host: str,
        circuit_id: int,
    ) -> None:
        """Initialize the circuit sensor."""
        super().__init__(coordinator, host)
        self._circuit_id = circuit_id

    @property
    def _circuit_info(self):
        """Return circuit info."""
        return self.coordinator.data.circuits.get(self._circuit_id)

    @property
    def _circuit_metrics(self):
        """Return circuit metrics."""
        return self.coordinator.data.metrics.get(self._circuit_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — circuit as sub-device of panel."""
        info = self._circuit_info
        name = info.name if info else f"Circuit {self._circuit_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._host}_circuit_{self._circuit_id}")},
            name=name,
            manufacturer="Span",
            model="Circuit Breaker",
            via_device=(DOMAIN, self._host),
        )


# ---------------------------------------------------------------------------
# Main feed sensors
# ---------------------------------------------------------------------------


class SpanGen3MainPowerSensor(SpanGen3SensorBase):
    """Main feed power sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, host):
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}_gen3_main_power"
        self._attr_name = "Main Feed Power"

    @property
    def native_value(self) -> float | None:
        m = self.coordinator.data.main_feed
        return round(m.power_w, 1) if m else None


class SpanGen3MainVoltageSensor(SpanGen3SensorBase):
    """Main feed voltage sensor."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, host):
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}_gen3_main_voltage"
        self._attr_name = "Main Feed Voltage"

    @property
    def native_value(self) -> float | None:
        m = self.coordinator.data.main_feed
        return round(m.voltage_v, 1) if m else None


class SpanGen3MainCurrentSensor(SpanGen3SensorBase):
    """Main feed current sensor."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, host):
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}_gen3_main_current"
        self._attr_name = "Main Feed Current"

    @property
    def native_value(self) -> float | None:
        m = self.coordinator.data.main_feed
        return round(m.current_a, 1) if m else None


class SpanGen3MainFrequencySensor(SpanGen3SensorBase):
    """Main feed frequency sensor."""

    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, host):
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}_gen3_main_frequency"
        self._attr_name = "Main Feed Frequency"

    @property
    def native_value(self) -> float | None:
        m = self.coordinator.data.main_feed
        return round(m.frequency_hz, 2) if m and m.frequency_hz > 0 else None


# ---------------------------------------------------------------------------
# Per-circuit sensors
# ---------------------------------------------------------------------------


class SpanGen3CircuitPowerSensor(SpanGen3CircuitSensorBase):
    """Per-circuit power sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, host, circuit_id):
        super().__init__(coordinator, host, circuit_id)
        self._attr_unique_id = f"{host}_gen3_circuit_{circuit_id}_power"
        self._attr_name = "Power"

    @property
    def native_value(self) -> float | None:
        m = self._circuit_metrics
        return round(m.power_w, 1) if m else None


class SpanGen3CircuitVoltageSensor(SpanGen3CircuitSensorBase):
    """Per-circuit voltage sensor."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, host, circuit_id):
        super().__init__(coordinator, host, circuit_id)
        self._attr_unique_id = f"{host}_gen3_circuit_{circuit_id}_voltage"
        self._attr_name = "Voltage"

    @property
    def native_value(self) -> float | None:
        m = self._circuit_metrics
        return round(m.voltage_v, 1) if m else None


class SpanGen3CircuitCurrentSensor(SpanGen3CircuitSensorBase):
    """Per-circuit current sensor."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, host, circuit_id):
        super().__init__(coordinator, host, circuit_id)
        self._attr_unique_id = f"{host}_gen3_circuit_{circuit_id}_current"
        self._attr_name = "Current"

    @property
    def native_value(self) -> float | None:
        m = self._circuit_metrics
        return round(m.current_a, 3) if m else None


class SpanGen3CircuitPositionSensor(SpanGen3CircuitSensorBase):
    """Per-circuit panel position (breaker slot number) sensor."""

    _attr_icon = "mdi:electric-switch"
    _attr_state_class = None  # Static configuration value, not a time-series measurement

    def __init__(self, coordinator, host, circuit_id):
        super().__init__(coordinator, host, circuit_id)
        self._attr_unique_id = f"{host}_gen3_circuit_{circuit_id}_position"
        self._attr_name = "Panel Position"

    @property
    def native_value(self) -> int | None:
        info = self._circuit_info
        if info is None:
            return None
        pos = info.breaker_position
        return pos if pos > 0 else None
