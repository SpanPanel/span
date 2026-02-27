"""Circuit-level sensors for Span Panel integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.const import USE_CIRCUIT_NUMBERS
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.helpers import (
    construct_circuit_identifier_from_tabs,
    construct_circuit_unique_id_for_entry,
    construct_tabs_attribute,
    construct_unmapped_friendly_name,
    construct_voltage_attribute,
)
from custom_components.span_panel.sensor_definitions import SpanPanelCircuitsSensorEntityDescription

from .base import SpanEnergySensorBase, SpanSensorBase

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Device types that use "Solar" as the fallback identifier when unnamed,
# matching v1 naming conventions (e.g., "Solar Power", "Solar Produced Energy").
_SOLAR_DEVICE_TYPES: frozenset[str] = frozenset({"pv"})


def _unnamed_circuit_fallback(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Return a descriptive identifier for an unnamed circuit.

    PV circuits use "Solar" (matching v1 naming), all others use tab-based naming.
    """
    if getattr(circuit, "device_type", "circuit") in _SOLAR_DEVICE_TYPES:
        return "Solar"
    return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)


def _resolve_circuit_identifier(
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    options: Mapping[str, Any],
) -> str | None:
    """Resolve the circuit identifier respecting user naming preference.

    Returns None when the circuit has no name and user is in friendly-name mode,
    matching v1 behavior where HA handles default naming.
    """
    use_circuit_numbers = options.get(USE_CIRCUIT_NUMBERS, False)

    if use_circuit_numbers:
        return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)

    name: str = circuit.name
    if name:
        return name

    return None


def _resolve_circuit_identifier_for_sync(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Resolve the circuit identifier for name-sync (always panel name, with fallback)."""
    name: str = circuit.name
    if name:
        return name
    return _unnamed_circuit_fallback(circuit, circuit_id)


class SpanCircuitPowerSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanCircuitSnapshot]
):
    """Enhanced circuit power sensor with amperage and tabs attributes."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
    ) -> None:
        """Initialize the enhanced circuit power sensor."""
        self.circuit_id = circuit_id
        self.original_key = description.key

        # Override the description key to use the circuit_id for data lookup
        description_with_circuit = SpanPanelCircuitsSensorEntityDescription(
            key=circuit_id,
            name=description.name,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            device_class=description.device_class,
            value_fn=description.value_fn,
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
        )

        super().__init__(data_coordinator, description_with_circuit, snapshot)

    def _generate_unique_id(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for circuit power sensors."""
        # Use the original API key that migration normalized from
        api_key = "instantPowerW"  # This maps to "power" suffix
        return construct_circuit_unique_id_for_entry(
            self.coordinator, snapshot, self.circuit_id, api_key, self._device_name
        )

    def _generate_friendly_name(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str | None:
        """Generate friendly name for circuit power sensors based on user preferences.

        Returns None when circuit has no name in friendly-name mode,
        matching v1 behavior where HA handles default naming.
        """
        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return construct_unmapped_friendly_name(
                self.circuit_id, str(description.name or "Sensor")
            )

        circuit_identifier = _resolve_circuit_identifier(
            circuit, self.circuit_id, self.coordinator.config_entry.options
        )
        if circuit_identifier is None:
            return None
        return f"{circuit_identifier} {description.name or 'Sensor'}"

    def _generate_panel_name(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate panel name for circuit sensors (always uses panel circuit name)."""
        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return construct_unmapped_friendly_name(
                self.circuit_id, str(description.name or "Sensor")
            )

        circuit_identifier = _resolve_circuit_identifier_for_sync(circuit, self.circuit_id)
        return f"{circuit_identifier} {description.name or 'Sensor'}"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanCircuitSnapshot:
        """Get the data source for the circuit power sensor."""
        circuit = snapshot.circuits.get(self.circuit_id)
        if circuit is None:
            raise ValueError(f"Circuit {self.circuit_id} not found in panel data")
        return circuit

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including amperage and tabs."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None

        circuit = self.coordinator.data.circuits.get(self.circuit_id)
        if not circuit:
            return None

        attributes: dict[str, Any] = {}

        # Add tabs attribute
        tabs_result = construct_tabs_attribute(circuit)
        if tabs_result is not None:
            attributes["tabs"] = str(tabs_result)

        # Add voltage attribute
        voltage = construct_voltage_attribute(circuit) or 240
        attributes["voltage"] = str(voltage)

        # Prefer measured current when available, otherwise calculate from power
        if circuit.current_a is not None:
            attributes["amperage"] = str(round(circuit.current_a, 2))
        elif self.native_value is not None and isinstance(self.native_value, int | float):
            try:
                amperage = float(self.native_value) / float(voltage)
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"
        else:
            attributes["amperage"] = "0.0"

        # v2 circuit attributes
        if circuit.breaker_rating_a is not None:
            attributes["breaker_rating"] = circuit.breaker_rating_a
        device_type = getattr(circuit, "device_type", "circuit") or "circuit"
        attributes["device_type"] = device_type
        attributes["always_on"] = circuit.always_on
        attributes["relay_state"] = circuit.relay_state
        attributes["relay_requester"] = circuit.relay_requester
        attributes["shed_priority"] = circuit.priority
        attributes["is_sheddable"] = circuit.is_sheddable

        # PV inverter metadata for PV circuits
        snapshot = self.coordinator.data
        if device_type == "pv":
            if snapshot.pv.vendor_name is not None:
                attributes["vendor_name"] = snapshot.pv.vendor_name
            if snapshot.pv.product_name is not None:
                attributes["product_name"] = snapshot.pv.product_name
            if snapshot.pv.nameplate_capacity_kw is not None:
                attributes["nameplate_capacity_kw"] = snapshot.pv.nameplate_capacity_kw

        return attributes


class SpanCircuitEnergySensor(
    SpanEnergySensorBase[SpanPanelCircuitsSensorEntityDescription, SpanCircuitSnapshot]
):
    """Circuit energy sensor with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
    ) -> None:
        """Initialize the circuit energy sensor."""
        self.circuit_id = circuit_id
        self.original_key = description.key

        # Override the description key to use the circuit_id for data lookup
        description_with_circuit = SpanPanelCircuitsSensorEntityDescription(
            key=circuit_id,
            name=description.name,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            device_class=description.device_class,
            value_fn=description.value_fn,
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
        )

        super().__init__(data_coordinator, description_with_circuit, snapshot)

    def _generate_unique_id(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for circuit energy sensors."""
        # Map new description keys to original API keys that migration normalized from
        api_key_mapping = {
            "circuit_energy_produced": "producedEnergyWh",
            "circuit_energy_consumed": "consumedEnergyWh",
            "circuit_energy_net": "netEnergyWh",
        }
        api_key = api_key_mapping.get(self.original_key, self.original_key)
        return construct_circuit_unique_id_for_entry(
            self.coordinator, snapshot, self.circuit_id, api_key, self._device_name
        )

    def _generate_friendly_name(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str | None:
        """Generate friendly name for circuit energy sensors based on user preferences.

        Returns None when circuit has no name in friendly-name mode,
        matching v1 behavior where HA handles default naming.
        """
        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return f"Circuit {self.circuit_id} {description.name}"

        circuit_identifier = _resolve_circuit_identifier(
            circuit, self.circuit_id, self.coordinator.config_entry.options
        )
        if circuit_identifier is None:
            return None
        return f"{circuit_identifier} {description.name}"

    def _generate_panel_name(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate panel name for circuit energy sensors (always uses panel circuit name)."""
        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return f"Circuit {self.circuit_id} {description.name}"

        circuit_identifier = _resolve_circuit_identifier_for_sync(circuit, self.circuit_id)
        return f"{circuit_identifier} {description.name}"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanCircuitSnapshot:
        """Get the data source for the circuit energy sensor."""
        return snapshot.circuits[self.circuit_id]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and circuit info."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add circuit-specific attributes if we have data
        if self.coordinator.data:
            snapshot = self.coordinator.data
            circuit = snapshot.circuits.get(self.circuit_id)

            if circuit:
                # Add tabs and voltage attributes
                tabs = construct_tabs_attribute(circuit)
                if tabs is not None:
                    attributes["tabs"] = tabs

                voltage = construct_voltage_attribute(circuit) or 240
                attributes["voltage"] = voltage

        return attributes if attributes else None


class SpanUnmappedCircuitSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanCircuitSnapshot]
):
    """Span Panel unmapped circuit sensor entity - native sensors for synthetic calculations."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
    ) -> None:
        """Initialize the Span Panel unmapped circuit sensor."""
        self.circuit_id = circuit_id
        # Store the original description key for unique ID and entity ID generation
        self.original_key = description.key

        # Override the description key to use the circuit_id for data lookup
        description_with_circuit = SpanPanelCircuitsSensorEntityDescription(
            key=circuit_id,
            name=description.name,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            device_class=description.device_class,
            value_fn=description.value_fn,
            entity_registry_enabled_default=True,
            entity_registry_visible_default=False,
        )

        super().__init__(data_coordinator, description_with_circuit, snapshot)

    def _generate_unique_id(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for unmapped circuit sensors."""
        return construct_circuit_unique_id_for_entry(
            self.coordinator, snapshot, self.circuit_id, self.original_key, self._device_name
        )

    def _generate_friendly_name(
        self, snapshot: SpanPanelSnapshot, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for unmapped circuit sensors."""
        tab_number = self.circuit_id.replace("unmapped_tab_", "")
        description_name = str(description.name) if description.name else "Sensor"
        return construct_unmapped_friendly_name(tab_number, description_name)

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanCircuitSnapshot:
        """Get the data source for the unmapped circuit sensor."""
        return snapshot.circuits[self.circuit_id]
