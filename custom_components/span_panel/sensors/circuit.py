"""Circuit-level sensors for Span Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from custom_components.span_panel.const import USE_CIRCUIT_NUMBERS
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.helpers import (
    construct_circuit_unique_id_for_entry,
    construct_tabs_attribute,
    construct_unmapped_friendly_name,
    construct_voltage_attribute,
)
from custom_components.span_panel.sensor_definitions import SpanPanelCircuitsSensorEntityDescription
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

from .base import SpanEnergySensorBase, SpanSensorBase

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanCircuitPowerSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Enhanced circuit power sensor with amperage and tabs attributes."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        span_panel: SpanPanel,
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

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for circuit power sensors."""
        # Use the original API key that migration normalized from
        api_key = "instantPowerW"  # This maps to "power" suffix
        return construct_circuit_unique_id_for_entry(
            self.coordinator, span_panel, self.circuit_id, api_key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for circuit power sensors based on user preferences."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if not circuit:
            return construct_unmapped_friendly_name(
                self.circuit_id, str(description.name or "Sensor")
            )

        # Respect user's naming preference
        use_circuit_numbers = self.coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

        if use_circuit_numbers:
            # Use circuit number format: "Circuit 15 Power"
            if circuit.tabs and len(circuit.tabs) == 2:
                # 240V circuit - use both tab numbers
                sorted_tabs = sorted(circuit.tabs)
                circuit_identifier = f"Circuit {sorted_tabs[0]} {sorted_tabs[1]}"
            elif circuit.tabs and len(circuit.tabs) == 1:
                # 120V circuit - use single tab number
                circuit_identifier = f"Circuit {circuit.tabs[0]}"
            else:
                # Fallback
                circuit_identifier = f"Circuit {self.circuit_id}"
        else:
            # Use friendly name format: "Kitchen Outlets Power"
            circuit_identifier = circuit.name

        return f"{circuit_identifier} {description.name or 'Sensor'}"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the circuit power sensor."""
        circuit = span_panel.circuits.get(self.circuit_id)
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

        attributes = {}

        # Add tabs attribute
        tabs_result = construct_tabs_attribute(circuit)
        if tabs_result is not None:
            attributes["tabs"] = str(tabs_result)

        # Add voltage attribute
        voltage = construct_voltage_attribute(circuit) or 240
        attributes["voltage"] = str(voltage)

        # Calculate amperage from power (P = V * I, so I = P / V)
        if self.native_value is not None and isinstance(self.native_value, int | float):
            try:
                amperage = float(self.native_value) / float(voltage)
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"
        else:
            attributes["amperage"] = "0.0"

        return attributes


class SpanCircuitEnergySensor(
    SpanEnergySensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Circuit energy sensor with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        span_panel: SpanPanel,
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

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
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
            self.coordinator, span_panel, self.circuit_id, api_key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for circuit energy sensors based on user preferences."""
        circuit = span_panel.circuits.get(self.circuit_id)
        if not circuit:
            return f"Circuit {self.circuit_id} {description.name}"

        # Respect user's naming preference
        use_circuit_numbers = self.coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)

        if use_circuit_numbers:
            # Use circuit number format: "Circuit 15 Power"
            if circuit.tabs and len(circuit.tabs) == 2:
                # 240V circuit - use both tab numbers
                sorted_tabs = sorted(circuit.tabs)
                circuit_identifier = f"Circuit {sorted_tabs[0]} {sorted_tabs[1]}"
            elif circuit.tabs and len(circuit.tabs) == 1:
                # 120V circuit - use single tab number
                circuit_identifier = f"Circuit {circuit.tabs[0]}"
            else:
                # Fallback
                circuit_identifier = f"Circuit {self.circuit_id}"
        else:
            # Use friendly name format: "Kitchen Outlets Power"
            circuit_identifier = circuit.name

        return f"{circuit_identifier} {description.name}"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the circuit energy sensor."""
        return span_panel.circuits[self.circuit_id]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and circuit info."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add circuit-specific attributes if we have data
        if self.coordinator.data:
            span_panel = self.coordinator.data
            circuit = span_panel.circuits.get(self.circuit_id)

            if circuit:
                # Add tabs and voltage attributes
                tabs = construct_tabs_attribute(circuit)
                if tabs is not None:
                    attributes["tabs"] = tabs

                voltage = construct_voltage_attribute(circuit) or 240
                attributes["voltage"] = voltage

        return attributes if attributes else None


class SpanUnmappedCircuitSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Span Panel unmapped circuit sensor entity - native sensors for synthetic calculations."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        span_panel: SpanPanel,
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

        super().__init__(data_coordinator, description_with_circuit, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate unique ID for unmapped circuit sensors."""
        # Unmapped tab sensors are regular circuit sensors, use standard circuit unique ID pattern
        # circuit_id is already "unmapped_tab_32", so this creates "span_{serial}_unmapped_tab_32_{suffix}"
        # Use the original key (e.g., "instantPowerW") instead of the modified description.key
        return construct_circuit_unique_id_for_entry(
            self.coordinator, span_panel, self.circuit_id, self.original_key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanPanelCircuitsSensorEntityDescription
    ) -> str:
        """Generate friendly name for unmapped circuit sensors."""
        tab_number = self.circuit_id.replace("unmapped_tab_", "")
        description_name = str(description.name) if description.name else "Sensor"
        return construct_unmapped_friendly_name(tab_number, description_name)

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the unmapped circuit sensor."""
        return span_panel.circuits[self.circuit_id]
