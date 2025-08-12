"""Integration Data Provider for bridging simulation data with SPAN Panel integration.

This module provides data using the integration's actual processing logic,
ensuring all data flows through the same code paths as production.
"""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.span_panel.coordinator import SpanPanelCoordinator
from tests.factories.span_panel_simulation_factory import SpanPanelSimulationFactory


class IntegrationDataProvider:
    """Provides data using integration's actual processing logic."""

    def __init__(self):
        """Initialize the data provider."""
        self._simulation_factory = SpanPanelSimulationFactory()

    async def create_coordinator_with_simulation_data(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        simulation_variations: dict | None = None,
        scenario_name: str | None = None
    ) -> SpanPanelCoordinator:
        """Create coordinator with simulation data processed through integration.

        Args:
            hass: Home Assistant instance
            config_entry: Configuration entry with naming flags
            simulation_variations: Direct simulation variations to apply
            scenario_name: Named scenario from simulation factory

        Returns:
            SpanPanelCoordinator with realistic simulation data
        """
        # Get simulation data
        if scenario_name:
            sim_data = await self._simulation_factory.get_panel_data_for_scenario(scenario_name)
        else:
            sim_data = await self._simulation_factory.get_realistic_panel_data(
                variations=simulation_variations
            )

        # Create a mock span panel client that returns simulation data
        mock_span_panel = self._create_mock_span_panel_from_sim_data(sim_data)

        # Create coordinator using integration's actual initialization
        coordinator = SpanPanelCoordinator(hass, mock_span_panel, config_entry)

        # Set data as if it came from real API calls
        coordinator.data = mock_span_panel

        return coordinator

    def _create_mock_span_panel_from_sim_data(self, sim_data: dict[str, Any]) -> MagicMock:
        """Convert simulation data to coordinator's expected format.

        This ensures the data structure matches exactly what the coordinator expects
        by using the same attribute names and structure as the real SpanPanel client.

        Args:
            sim_data: Dictionary containing simulation data from span-panel-api

        Returns:
            MagicMock configured to match SpanPanel client interface
        """
        mock_panel = MagicMock()

        # Extract data from simulation response
        circuits_data = sim_data['circuits']
        panel_state = sim_data['panel_state']
        status_data = sim_data['status']
        storage_data = sim_data['storage']

        # Set panel-level attributes from panel_state
        mock_panel.id = getattr(panel_state, 'panel_id', 'test_panel_123')
        mock_panel.name = "Simulation Panel"
        mock_panel.model = getattr(panel_state, 'panel_model', '32A')
        mock_panel.firmware_version = getattr(panel_state, 'firmware_version', '1.2.3')
        mock_panel.main_breaker_size = 200

        # Power and energy data from panel_state
        mock_panel.instant_grid_power_w = getattr(panel_state, 'instant_grid_power_w', 0)
        mock_panel.instant_load_power_w = getattr(panel_state, 'instant_load_power_w', 0)
        mock_panel.instant_production_power_w = getattr(panel_state, 'instant_production_power_w', 0)
        mock_panel.feedthrough_power = getattr(panel_state, 'feedthrough_power_w', 0)

        # Environmental data
        mock_panel.env_temp_c = getattr(panel_state, 'env_temp_c', 25.0)
        mock_panel.uptime_s = getattr(panel_state, 'uptime_s', 86400)

        # Status data
        mock_panel.door_state = getattr(status_data, 'door_state', 'CLOSED')
        mock_panel.main_relay_state = getattr(status_data, 'main_relay_state', 'CLOSED')

        # DSM data from panel_state
        mock_panel.dsmCurrentRms = getattr(panel_state, 'dsm_current_rms', [120.5, 118.3])
        mock_panel.dsmVoltageRms = getattr(panel_state, 'dsm_voltage_rms', [245.6, 244.1])
        mock_panel.grid_sample_start_ms = getattr(panel_state, 'grid_sample_start_ms', 1234567890123)

        # Convert circuits data to the format the integration expects
        mock_circuits = []
        for _circuit_id, circuit_data in circuits_data.circuits.additional_properties.items():
            circuit_mock = MagicMock()
            circuit_mock.id = circuit_data.id
            circuit_mock.name = circuit_data.name
            circuit_mock.instant_power_w = circuit_data.instant_power_w
            circuit_mock.produced_energy_wh = circuit_data.produced_energy_wh
            circuit_mock.consumed_energy_wh = circuit_data.consumed_energy_wh
            circuit_mock.relay_state = circuit_data.relay_state
            circuit_mock.priority = circuit_data.priority
            circuit_mock.tabs = circuit_data.tabs
            circuit_mock.is_user_controllable = circuit_data.is_user_controllable
            circuit_mock.is_sheddable = circuit_data.is_sheddable
            circuit_mock.is_never_backup = circuit_data.is_never_backup

            # Add properties that integration might expect
            circuit_mock.is_main = circuit_data.id == "main" or "main" in circuit_data.name.lower()
            circuit_mock.breaker_size = 20  # Default breaker size

            mock_circuits.append(circuit_mock)

        mock_panel.circuits = mock_circuits

        # Storage/battery data if available
        if storage_data:
            mock_panel.battery_soe = getattr(storage_data, 'soe', 0.5)
            mock_panel.max_energy_kwh = getattr(storage_data, 'max_energy_kwh', 10.0)

        return mock_panel

    async def create_config_entry_with_flags(
        self,
        naming_flags: dict[str, Any],
        entry_id: str = "test_entry_id"
    ) -> ConfigEntry:
        """Create a config entry with specific naming flags.

        Args:
            naming_flags: Dictionary of naming configuration flags
            entry_id: Unique identifier for the config entry

        Returns:
            ConfigEntry configured with the specified flags
        """
        # Import here to avoid circular imports
        from tests.common import create_mock_config_entry

        config_entry = create_mock_config_entry()
        config_entry.entry_id = entry_id
        config_entry.options = {
            **config_entry.options,
            **naming_flags
        }

        return config_entry

    async def create_full_integration_setup(
        self,
        hass: HomeAssistant,
        naming_flags: dict[str, Any],
        simulation_variations: dict | None = None,
        scenario_name: str | None = None
    ) -> tuple[SpanPanelCoordinator, ConfigEntry]:
        """Create a complete integration setup with simulation data.

        Args:
            hass: Home Assistant instance
            naming_flags: Entity naming configuration flags
            simulation_variations: Direct simulation variations
            scenario_name: Named simulation scenario

        Returns:
            Tuple of (coordinator, config_entry) ready for testing
        """
        # Create config entry with naming flags
        config_entry = await self.create_config_entry_with_flags(naming_flags)

        # Create coordinator with simulation data
        coordinator = await self.create_coordinator_with_simulation_data(
            hass,
            config_entry,
            simulation_variations=simulation_variations,
            scenario_name=scenario_name
        )

        return coordinator, config_entry

    async def get_circuit_data_for_naming_tests(
        self,
        circuit_types: list[str] | None = None
    ) -> dict[str, dict]:
        """Get circuit data specifically formatted for naming pattern tests.

        Args:
            circuit_types: List of circuit types to include (lights, ev_chargers, etc.)

        Returns:
            Dictionary with circuit data formatted for naming tests
        """
        # Get all circuit details
        circuit_details = await self._simulation_factory.get_circuit_details()

        if circuit_types:
            # Filter by circuit types
            circuit_ids_by_type = await self._simulation_factory.get_circuit_ids_by_type()
            included_ids = set()
            for circuit_type in circuit_types:
                if circuit_type in circuit_ids_by_type:
                    included_ids.update(circuit_ids_by_type[circuit_type])

            circuit_details = {
                circuit_id: details
                for circuit_id, details in circuit_details.items()
                if circuit_id in included_ids
            }

        return circuit_details
