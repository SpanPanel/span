"""Solar sensor classes for Span Panel integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers.typing import UNDEFINED

from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.helpers import (
    construct_panel_unique_id_for_entry,
)
from custom_components.span_panel.sensor_definitions import SpanSolarSensorEntityDescription
from custom_components.span_panel.span_panel import SpanPanel

from .base import SpanEnergySensorBase, SpanSensorBase

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanSolarSensor(SpanSensorBase[SpanSolarSensorEntityDescription, SpanPanel]):
    """Solar sensor that combines values from leg1 and leg2 circuits."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanSolarSensorEntityDescription,
        span_panel: SpanPanel,
        leg1_circuit_id: str,
        leg2_circuit_id: str,
    ) -> None:
        """Initialize the solar sensor."""
        self.leg1_circuit_id = leg1_circuit_id
        self.leg2_circuit_id = leg2_circuit_id
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate unique ID for solar sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate friendly name for solar sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Solar"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanel:
        """Get the data source for the solar sensor."""
        return span_panel

    def _update_native_value(self) -> None:
        """Update the native value by combining leg1 and leg2 circuit values."""
        if self.coordinator.panel_offline:
            self._handle_solar_offline_state()
            return

        if not self.coordinator.last_update_success or not self.coordinator.data:
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN
            return

        self._calculate_solar_value()

    def _handle_solar_offline_state(self) -> None:
        """Handle solar sensor state when panel is offline."""
        _LOGGER.debug("SOLAR_SENSOR_DEBUG: Panel is offline for %s", self._attr_name)
        # For solar power sensors, set to 0.0 when offline (instantaneous values)
        # For energy sensors, set to None when offline (HA will report as unknown)
        # For numeric sensors (with state_class), set to None when offline (HA will report as unknown)
        # For other sensors, set to STATE_UNKNOWN when offline
        device_class = getattr(self.entity_description, "device_class", None)
        state_class = getattr(self.entity_description, "state_class", None)

        if device_class == "power":
            self._attr_native_value = 0.0
        elif device_class == "energy":
            self._attr_native_value = None
        elif state_class is not None:
            # Any sensor with a state_class (measurement, total, etc.) expects numeric values
            self._attr_native_value = None
        else:
            self._attr_native_value = STATE_UNKNOWN

    def _calculate_solar_value(self) -> None:
        """Calculate the solar sensor value from leg circuits."""
        span_panel = self.coordinator.data
        leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
        leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

        if not leg1_circuit or not leg2_circuit:
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN
            return

        try:
            leg1_value, leg2_value = self._get_leg_values(leg1_circuit, leg2_circuit)
            # Combine the values
            description = self.entity_description
            assert isinstance(description, SpanSolarSensorEntityDescription)
            if hasattr(description, "calculation_type") and description.calculation_type == "sum":
                self._attr_native_value = float(leg1_value) + float(leg2_value)
            else:
                self._attr_native_value = float(leg1_value) + float(leg2_value)

        except (ValueError, TypeError, AttributeError) as e:
            _LOGGER.warning("Error calculating solar sensor value for %s: %s", description.key, e)
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN

    def _get_leg_values(self, leg1_circuit: str, leg2_circuit: str) -> tuple[float, float]:
        """Get values from both leg circuits based on sensor type."""
        description = self.entity_description
        assert isinstance(description, SpanSolarSensorEntityDescription)

        if description.key == "solar_current_power":
            leg1_value = getattr(leg1_circuit, "instant_power", 0) or 0
            leg2_value = getattr(leg2_circuit, "instant_power", 0) or 0
        elif description.key == "solar_produced_energy":
            leg1_value = getattr(leg1_circuit, "produced_energy", 0) or 0
            leg2_value = getattr(leg2_circuit, "produced_energy", 0) or 0
        elif description.key == "solar_consumed_energy":
            leg1_value = getattr(leg1_circuit, "consumed_energy", 0) or 0
            leg2_value = getattr(leg2_circuit, "consumed_energy", 0) or 0
        elif description.key == "solar_net_energy":
            # Net energy = produced - consumed for each leg, then sum
            leg1_produced = getattr(leg1_circuit, "produced_energy", 0) or 0
            leg1_consumed = getattr(leg1_circuit, "consumed_energy", 0) or 0
            leg2_produced = getattr(leg2_circuit, "produced_energy", 0) or 0
            leg2_consumed = getattr(leg2_circuit, "consumed_energy", 0) or 0
            leg1_value = leg1_produced - leg1_consumed
            leg2_value = leg2_produced - leg2_consumed
        else:
            leg1_value = 0
            leg2_value = 0

        return leg1_value, leg2_value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including tabs and voltage."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None

        span_panel = self.coordinator.data
        leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
        leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

        if not leg1_circuit or not leg2_circuit:
            return None

        return self._build_solar_attributes(leg1_circuit, leg2_circuit)

    def _build_solar_attributes(self, leg1_circuit: Any, leg2_circuit: Any) -> dict[str, Any]:
        """Build state attributes for solar sensor."""
        attributes = {}

        # Add tabs attribute combining both legs
        all_tabs = []
        if leg1_circuit.tabs:
            all_tabs.extend(leg1_circuit.tabs)
        if leg2_circuit.tabs:
            all_tabs.extend(leg2_circuit.tabs)

        if all_tabs:
            # Sort tabs for consistent ordering and remove duplicates
            sorted_unique_tabs = sorted(set(all_tabs))
            attributes["tabs"] = self._format_tabs_attribute(sorted_unique_tabs)

        # Add voltage attribute based on total number of unique tabs
        voltage = self._calculate_voltage_from_tabs(all_tabs, sorted_unique_tabs)
        attributes["voltage"] = str(voltage)

        # Calculate amperage for power sensors
        if (
            self.entity_description.key == "solar_current_power"
            and self.native_value is not None
            and isinstance(self.native_value, int | float)
        ):
            try:
                amperage = float(self.native_value) / float(voltage)
                attributes["amperage"] = str(round(amperage, 2))
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = "0.0"

        return attributes

    def _format_tabs_attribute(self, sorted_unique_tabs: list[int]) -> str:
        """Format the tabs attribute string."""
        if len(sorted_unique_tabs) == 1:
            return f"tabs [{sorted_unique_tabs[0]}]"
        elif len(sorted_unique_tabs) == 2:
            return f"tabs [{sorted_unique_tabs[0]}:{sorted_unique_tabs[1]}]"
        else:
            # Multiple non-contiguous tabs - list them
            tab_list = ", ".join(str(tab) for tab in sorted_unique_tabs)
            return f"tabs [{tab_list}]"

    def _calculate_voltage_from_tabs(
        self, all_tabs: list[int], sorted_unique_tabs: list[int]
    ) -> int:
        """Calculate voltage based on tab configuration."""
        if all_tabs:
            unique_tab_count = len(sorted_unique_tabs)
            if unique_tab_count == 1:
                return 120
            elif unique_tab_count == 2:
                return 240
            else:
                # More than 2 tabs is not valid for US electrical system
                return 240  # Default to 240V for invalid configurations
        else:
            return 240  # Default to 240V if no tabs information


class SpanSolarEnergySensor(SpanEnergySensorBase[SpanSolarSensorEntityDescription, SpanPanel]):
    """Solar energy sensor that combines values from leg1 and leg2 circuits with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanSolarSensorEntityDescription,
        span_panel: SpanPanel,
        leg1_circuit_id: str,
        leg2_circuit_id: str,
    ) -> None:
        """Initialize the solar energy sensor."""
        self.leg1_circuit_id = leg1_circuit_id
        self.leg2_circuit_id = leg2_circuit_id
        super().__init__(data_coordinator, description, span_panel)

    def _generate_unique_id(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate unique ID for solar energy sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, span_panel, description.key, self._device_name
        )

    def _generate_friendly_name(
        self, span_panel: SpanPanel, description: SpanSolarSensorEntityDescription
    ) -> str:
        """Generate friendly name for solar energy sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Solar Energy"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanel:
        """Get the data source for the solar energy sensor."""
        return span_panel

    def _update_native_value(self) -> None:
        """Update the native value by combining leg1 and leg2 circuit values."""
        if self.coordinator.panel_offline:
            _LOGGER.debug(
                "SOLAR_ENERGY_SENSOR_DEBUG: Panel is offline for %s, using grace period logic",
                self._attr_name,
            )
            # Use grace period logic when offline
            self._handle_offline_grace_period()
            return

        if not self.coordinator.last_update_success or not self.coordinator.data:
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN
            return

        self._calculate_solar_energy_value()

    def _calculate_solar_energy_value(self) -> None:
        """Calculate the solar energy sensor value from leg circuits."""
        span_panel = self.coordinator.data
        leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
        leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

        if not leg1_circuit or not leg2_circuit:
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN
            return

        try:
            leg1_value, leg2_value = self._get_energy_leg_values(leg1_circuit, leg2_circuit)
            # Combine the values
            description = self.entity_description
            assert isinstance(description, SpanSolarSensorEntityDescription)
            if hasattr(description, "calculation_type") and description.calculation_type == "sum":
                self._attr_native_value = float(leg1_value) + float(leg2_value)
            else:
                self._attr_native_value = float(leg1_value) + float(leg2_value)

            # Track valid state for grace period (only when we have a valid value)
            if self._attr_native_value is not None and isinstance(
                self._attr_native_value, int | float
            ):
                self._last_valid_state = float(self._attr_native_value)
                self._last_valid_changed = datetime.now()

        except (ValueError, TypeError, AttributeError) as e:
            _LOGGER.warning(
                "Error calculating solar energy sensor value for %s: %s", description.key, e
            )
            # For sensors with state_class, use None (HA reports as unknown)
            # For other sensors, use STATE_UNKNOWN string
            state_class = getattr(self.entity_description, "state_class", None)
            self._attr_native_value = None if state_class is not None else STATE_UNKNOWN

    def _get_energy_leg_values(self, leg1_circuit: Any, leg2_circuit: Any) -> tuple[float, float]:
        """Get energy values from both leg circuits based on sensor type."""
        description = self.entity_description
        assert isinstance(description, SpanSolarSensorEntityDescription)

        if description.key == "solar_produced_energy":
            leg1_value = getattr(leg1_circuit, "produced_energy", 0) or 0
            leg2_value = getattr(leg2_circuit, "produced_energy", 0) or 0
        elif description.key == "solar_consumed_energy":
            leg1_value = getattr(leg1_circuit, "consumed_energy", 0) or 0
            leg2_value = getattr(leg2_circuit, "consumed_energy", 0) or 0
        elif description.key == "solar_net_energy":
            # Net energy = produced - consumed for each leg, then sum
            leg1_produced = getattr(leg1_circuit, "produced_energy", 0) or 0
            leg1_consumed = getattr(leg1_circuit, "consumed_energy", 0) or 0
            leg2_produced = getattr(leg2_circuit, "produced_energy", 0) or 0
            leg2_consumed = getattr(leg2_circuit, "consumed_energy", 0) or 0
            leg1_value = leg1_produced - leg1_consumed
            leg2_value = leg2_produced - leg2_consumed
        else:
            leg1_value = 0
            leg2_value = 0

        return leg1_value, leg2_value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and solar info."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add solar-specific attributes if we have data
        if self.coordinator.data:
            span_panel = self.coordinator.data
            leg1_circuit = span_panel.circuits.get(self.leg1_circuit_id)
            leg2_circuit = span_panel.circuits.get(self.leg2_circuit_id)

            if leg1_circuit and leg2_circuit:
                # Add tabs attribute combining both legs
                all_tabs = []
                if leg1_circuit.tabs:
                    all_tabs.extend(leg1_circuit.tabs)
                if leg2_circuit.tabs:
                    all_tabs.extend(leg2_circuit.tabs)

                if all_tabs:
                    # Sort tabs for consistent ordering and remove duplicates
                    sorted_unique_tabs = sorted(set(all_tabs))
                    attributes["tabs"] = self._format_energy_tabs_attribute(sorted_unique_tabs)

                # Add voltage attribute based on total number of unique tabs
                voltage = self._calculate_energy_voltage_from_tabs(all_tabs, sorted_unique_tabs)
                attributes["voltage"] = str(voltage)

        return attributes if attributes else None

    def _format_energy_tabs_attribute(self, sorted_unique_tabs: list[int]) -> str:
        """Format the tabs attribute string for energy sensors."""
        if len(sorted_unique_tabs) == 1:
            return f"tabs [{sorted_unique_tabs[0]}]"
        elif len(sorted_unique_tabs) == 2:
            return f"tabs [{sorted_unique_tabs[0]}:{sorted_unique_tabs[1]}]"
        else:
            # Multiple non-contiguous tabs - list them
            tab_list = ", ".join(str(tab) for tab in sorted_unique_tabs)
            return f"tabs [{tab_list}]"

    def _calculate_energy_voltage_from_tabs(
        self, all_tabs: list[int], sorted_unique_tabs: list[int]
    ) -> int:
        """Calculate voltage based on tab configuration for energy sensors."""
        if all_tabs:
            unique_tab_count = len(sorted_unique_tabs)
            if unique_tab_count == 1:
                return 120
            elif unique_tab_count == 2:
                return 240
            else:
                # More than 2 tabs is not valid for US electrical system
                return 240  # Default to 240V for invalid configurations
        else:
            return 240  # Default to 240V if no tabs information
