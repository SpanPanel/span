"""Tests for YAML regeneration when SPAN circuits change."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.span_panel.const import (
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.solar_synthetic_sensors import SolarSyntheticSensors
from tests.common import create_mock_config_entry


class TestYamlCircuitChanges:
    """Test YAML regeneration when SPAN panel circuits change names, are added, or removed."""

    @pytest.fixture
    def hass_with_solar_data(self, hass):
        """Create hass instance with mocked SPAN panel circuits."""
        # Create config entry with stable naming (friendly names)
        config_entry = create_mock_config_entry(
            {"host": "192.168.1.100"}, {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}
        )
        config_entry.entry_id = "test_entry_id"

        # Create coordinator with mock data
        coordinator = MagicMock()
        coordinator.data = MagicMock()

        # Create initial mock circuits
        mock_circuit_30 = MagicMock()
        mock_circuit_30.name = "Unmapped Tab 30"
        mock_circuit_30.circuit_id = "unmapped_tab_30"

        mock_circuit_32 = MagicMock()
        mock_circuit_32.name = "Unmapped Tab 32"
        mock_circuit_32.circuit_id = "unmapped_tab_32"

        coordinator.data.circuits = {
            "unmapped_tab_30": mock_circuit_30,
            "unmapped_tab_32": mock_circuit_32,
        }

        # Set up config entry in coordinator
        coordinator.config_entry = config_entry

        # Store in hass.data
        hass.data = {
            DOMAIN: {
                "test_entry_id": {"coordinator": coordinator},
            }
        }

        # Store for easy access in tests
        hass._test_coordinator = coordinator
        hass._test_config_entry = config_entry
        return hass

    async def test_yaml_variables_update_when_circuits_renamed(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that YAML variables for unmapped circuits remain stable when circuits are renamed."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = hass_with_solar_data._test_config_entry
            coordinator = hass_with_solar_data._test_coordinator

            # Generate initial YAML
            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)
            await solar_sensors.generate_config(30, 32)

            yaml_path = Path(temp_dir) / "span-ha-synthetic.yaml"
            with open(yaml_path, encoding="utf-8") as f:
                initial_yaml = yaml.safe_load(f)

            # Verify initial variables use unmapped tab names
            power_sensor = initial_yaml["sensors"]["span_panel_solar_inverter_instant_power"]
            assert "unmapped_tab_30" in power_sensor["variables"]["leg1_power"]
            assert "unmapped_tab_32" in power_sensor["variables"]["leg2_power"]

            # Simulate user renaming circuits in SPAN app
            coordinator.data.circuits["unmapped_tab_30"].name = "Solar East"
            coordinator.data.circuits["unmapped_tab_32"].name = "Solar West"

            # Regenerate YAML - variables should remain stable for unmapped circuits
            await solar_sensors.generate_config(30, 32)

            with open(yaml_path, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify variables still use stable unmapped tab names (not friendly names)
            updated_power_sensor = updated_yaml["sensors"][
                "span_panel_solar_inverter_instant_power"
            ]
            assert "unmapped_tab_30" in updated_power_sensor["variables"]["leg1_power"]
            assert "unmapped_tab_32" in updated_power_sensor["variables"]["leg2_power"]

            # Verify sensor keys remain stable
            assert "span_panel_solar_inverter_instant_power" in updated_yaml["sensors"]
            assert updated_power_sensor["entity_id"] == power_sensor["entity_id"]

    async def test_yaml_handles_circuit_addition_and_removal(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that YAML updates correctly when circuits are added or removed."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = hass_with_solar_data._test_config_entry

            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)

            # Start with single leg configuration
            await solar_sensors.generate_config(30, 0)

            yaml_path = Path(temp_dir) / "span-ha-synthetic.yaml"
            with open(yaml_path, encoding="utf-8") as f:
                single_leg_yaml = yaml.safe_load(f)

            # Verify single leg formula
            power_sensor = single_leg_yaml["sensors"]["span_panel_solar_inverter_instant_power"]
            assert power_sensor["formula"] == "leg1_power"
            assert "leg1_power" in power_sensor["variables"]
            assert "leg2_power" not in power_sensor["variables"]

            # Add second circuit (simulate adding circuit in SPAN app)
            await solar_sensors.generate_config(30, 32)

            with open(yaml_path, encoding="utf-8") as f:
                dual_leg_yaml = yaml.safe_load(f)

            # Verify dual leg formula
            dual_power_sensor = dual_leg_yaml["sensors"]["span_panel_solar_inverter_instant_power"]
            assert dual_power_sensor["formula"] == "leg1_power + leg2_power"
            assert "leg1_power" in dual_power_sensor["variables"]
            assert "leg2_power" in dual_power_sensor["variables"]

            # Verify sensor key and entity ID remain stable
            assert dual_power_sensor["entity_id"] == power_sensor["entity_id"]

    async def test_yaml_variables_survive_circuit_id_changes(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that variables update correctly when circuit IDs change."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = hass_with_solar_data._test_config_entry
            coordinator = hass_with_solar_data._test_coordinator

            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)

            # Generate initial YAML with circuits 30 and 32
            await solar_sensors.generate_config(30, 32)

            yaml_path = Path(temp_dir) / "span-ha-synthetic.yaml"
            with open(yaml_path, encoding="utf-8") as f:
                initial_yaml = yaml.safe_load(f)

            # Verify initial circuit variables
            power_sensor = initial_yaml["sensors"]["span_panel_solar_inverter_instant_power"]
            initial_leg1 = power_sensor["variables"]["leg1_power"]
            initial_leg2 = power_sensor["variables"]["leg2_power"]

            # Now simulate switching to different circuits (e.g., user changes solar setup)
            # Add new circuits to coordinator
            mock_circuit_28 = MagicMock()
            mock_circuit_28.name = "Solar Circuit A"
            mock_circuit_28.circuit_id = "unmapped_tab_28"

            mock_circuit_29 = MagicMock()
            mock_circuit_29.name = "Solar Circuit B"
            mock_circuit_29.circuit_id = "unmapped_tab_29"

            coordinator.data.circuits["unmapped_tab_28"] = mock_circuit_28
            coordinator.data.circuits["unmapped_tab_29"] = mock_circuit_29

            # Regenerate with new circuit numbers
            await solar_sensors.generate_config(28, 29)

            with open(yaml_path, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify variables now point to new circuits
            updated_power_sensor = updated_yaml["sensors"][
                "span_panel_solar_inverter_instant_power"
            ]
            updated_leg1 = updated_power_sensor["variables"]["leg1_power"]
            updated_leg2 = updated_power_sensor["variables"]["leg2_power"]

            # Variables should have changed to reference new circuits
            assert updated_leg1 != initial_leg1
            assert updated_leg2 != initial_leg2
            assert "solar_circuit_a" in updated_leg1 or "unmapped_tab_28" in updated_leg1
            assert "solar_circuit_b" in updated_leg2 or "unmapped_tab_29" in updated_leg2

    async def test_unmapped_circuits_maintain_stable_entity_ids(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that unmapped circuits maintain stable entity IDs when their names change.

        This is the correct behavior - unmapped circuits should NOT change their
        entity IDs when their friendly names are updated.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = hass_with_solar_data._test_config_entry

            # Get the coordinator and modify unmapped circuit names
            coordinator_data = hass_with_solar_data._test_coordinator

            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)

            # Generate initial YAML
            await solar_sensors.generate_config(30, 32)

            yaml_path = Path(temp_dir) / "span-ha-synthetic.yaml"
            with open(yaml_path, encoding="utf-8") as f:
                initial_yaml = yaml.safe_load(f)

            # Get initial variables
            power_sensor = initial_yaml["sensors"]["span_panel_solar_inverter_instant_power"]
            initial_leg1 = power_sensor["variables"]["leg1_power"]
            initial_leg2 = power_sensor["variables"]["leg2_power"]

            # Change the names of the unmapped circuits
            coordinator_data.data.circuits["unmapped_tab_30"].name = "Solar East Wing"
            coordinator_data.data.circuits["unmapped_tab_32"].name = "Solar West Wing"

            # Regenerate YAML
            yaml_path.unlink()
            await solar_sensors.generate_config(30, 32)

            with open(yaml_path, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify variables remain the same for unmapped circuits
            updated_power_sensor = updated_yaml["sensors"][
                "span_panel_solar_inverter_instant_power"
            ]
            updated_leg1 = updated_power_sensor["variables"]["leg1_power"]
            updated_leg2 = updated_power_sensor["variables"]["leg2_power"]

            # For unmapped circuits, entity IDs should remain stable
            assert updated_leg1 == initial_leg1, (
                "Unmapped circuit entity IDs should remain stable when names change"
            )
            assert updated_leg2 == initial_leg2, (
                "Unmapped circuit entity IDs should remain stable when names change"
            )

            # Should still use unmapped_tab pattern
            assert "unmapped_tab_30" in updated_leg1
            assert "unmapped_tab_32" in updated_leg2

            # Sensor key and entity ID remain stable
            assert updated_power_sensor["entity_id"] == power_sensor["entity_id"]
