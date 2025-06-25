"""Test YAML regeneration when entity naming patterns change."""

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


class TestYamlRegeneration:
    """Test YAML regeneration when entity naming patterns change."""

    @pytest.fixture
    def hass_with_solar_data(self, hass):
        """Create hass instance with solar coordinator data for both patterns."""
        # Create coordinators for both patterns
        coordinators = {}

        for pattern_name, config_flags in [
            ("friendly", {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}),
            ("circuit", {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True}),
        ]:
            # Create config entry
            config_entry = create_mock_config_entry({"host": "192.168.1.100"}, config_flags)
            config_entry.entry_id = f"test_entry_id_{pattern_name}"

            # Create coordinator with mock data
            coordinator = MagicMock()
            coordinator.data = MagicMock()

            # Set up status with serial number
            coordinator.data.status = MagicMock()
            coordinator.data.status.serial_number = "TEST12345"

            # Create proper mock circuits that SolarSyntheticSensors expects
            # The bridge looks for circuits with IDs like "unmapped_tab_15", "unmapped_tab_30", etc.
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

            # Set up config entry in coordinator for entity ID construction
            coordinator.config_entry = config_entry

            # Store both coordinator and config entry
            coordinators[pattern_name] = {"coordinator": coordinator, "config_entry": config_entry}

        # Store coordinators in hass.data using the correct structure
        hass.data = {
            DOMAIN: {
                "test_entry_id_friendly": {"coordinator": coordinators["friendly"]["coordinator"]},
                "test_entry_id_circuit": {"coordinator": coordinators["circuit"]["coordinator"]},
            }
        }

        # Store coordinators info for easy access in tests
        hass._test_coordinators = coordinators
        return hass

    async def test_yaml_variables_update_when_circuits_change(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that SolarSyntheticSensors updates variable entity IDs when underlying circuits change."""

        with tempfile.TemporaryDirectory() as temp_dir:
            # Use a fixture with mapped circuits instead of unmapped ones
            fixtures_dir = Path(__file__).parent / "fixtures"
            source_yaml = fixtures_dir / "span-ha-synthetic-mixed-references.yaml"
            test_yaml = Path(temp_dir) / "solar_synthetic_sensors.yaml"

            # Copy the fixture to our test directory
            import shutil

            shutil.copy(source_yaml, test_yaml)

            # Get any config entry since we now use stable naming
            config_entry = hass_with_solar_data._test_coordinators["friendly"]["config_entry"]

            # Create a solar sensors instance
            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)

            # Read the original YAML
            with open(test_yaml, encoding="utf-8") as f:
                initial_yaml = yaml.safe_load(f)

            # Verify initial variables point to the original circuit names
            power_sensor = initial_yaml["sensors"]["solar_inverter_instant_power"]
            assert power_sensor["variables"]["leg1_power"] == "sensor.span_panel_main_kitchen_power"
            assert power_sensor["variables"]["leg2_power"] == "sensor.span_panel_main_garage_power"

            # Now simulate circuit name changes by updating the coordinator data
            coordinator = hass_with_solar_data.data[DOMAIN][config_entry.entry_id]["coordinator"]

            # Add mapped circuits that would appear in the YAML variables
            mock_kitchen_circuit = MagicMock()
            mock_kitchen_circuit.name = "Solar East"  # Changed from "Main Kitchen"
            mock_kitchen_circuit.circuit_id = "main_kitchen"
            mock_kitchen_circuit.id = 1

            mock_garage_circuit = MagicMock()
            mock_garage_circuit.name = "Solar West"  # Changed from "Main Garage"
            mock_garage_circuit.circuit_id = "main_garage"
            mock_garage_circuit.id = 2

            # Update coordinator data to include these mapped circuits
            coordinator.data.circuits.update(
                {
                    "main_kitchen": mock_kitchen_circuit,
                    "main_garage": mock_garage_circuit,
                }
            )

            # Update the YAML variables to reflect the new names
            await solar_sensors._update_yaml_variables_from_coordinator()

            with open(test_yaml, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify variables now point to the new friendly names
            updated_power_sensor = updated_yaml["sensors"]["solar_inverter_instant_power"]
            # The entity IDs should now be based on the friendly names, not the original names
            assert "solar_east" in updated_power_sensor["variables"]["leg1_power"]
            assert "solar_west" in updated_power_sensor["variables"]["leg2_power"]

            # But the sensor key itself should remain stable
            assert "solar_inverter_instant_power" in updated_yaml["sensors"]

    async def test_yaml_variables_update_for_single_vs_dual_leg(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that solar sensor variables change correctly between single and dual leg configurations."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = hass_with_solar_data._test_coordinators["friendly"]["config_entry"]
            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)

            # Start with dual leg configuration
            await solar_sensors.generate_config(30, 32)

            yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            with open(yaml_path, encoding="utf-8") as f:
                dual_leg_yaml = yaml.safe_load(f)

            # Verify dual leg formula and variables
            power_sensor = dual_leg_yaml["sensors"]["solar_inverter_instant_power"]
            assert power_sensor["formula"] == "leg1_power + leg2_power"
            assert "leg1_power" in power_sensor["variables"]
            assert "leg2_power" in power_sensor["variables"]

            # Switch to single leg configuration
            await solar_sensors.generate_config(30, 0)  # Only leg 1

            with open(yaml_path, encoding="utf-8") as f:
                single_leg_yaml = yaml.safe_load(f)

            # Verify single leg formula and variables
            single_power_sensor = single_leg_yaml["sensors"]["solar_inverter_instant_power"]
            assert single_power_sensor["formula"] == "leg1_power"
            assert "leg1_power" in single_power_sensor["variables"]
            assert "leg2_power" not in single_power_sensor["variables"]

            # The entity ID and sensor key should remain stable
            assert single_power_sensor["entity_id"] == power_sensor["entity_id"]

    async def test_yaml_variable_updates_with_mixed_references(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that YAML variables update correctly for SPAN sensors but not for non-SPAN sensors."""

        with tempfile.TemporaryDirectory() as temp_dir:
            # Use a fixture with mixed SPAN and non-SPAN references
            fixtures_dir = Path(__file__).parent / "fixtures"
            source_yaml = fixtures_dir / "span-ha-synthetic-mixed-references.yaml"
            test_yaml = Path(temp_dir) / "solar_synthetic_sensors.yaml"

            # Copy the fixture to our test directory
            import shutil

            shutil.copy(source_yaml, test_yaml)

            # Create a solar sensors instance
            config_entry = hass_with_solar_data._test_coordinators["friendly"]["config_entry"]
            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, config_entry, temp_dir)

            # Read the original YAML
            with open(test_yaml, encoding="utf-8") as f:
                original_yaml = yaml.safe_load(f)

            # Simulate circuit name changes in the coordinator data
            # Change "Main Kitchen" -> "Kitchen Updated" and "Main Garage" -> "Garage Updated"
            coordinator = hass_with_solar_data.data[DOMAIN][config_entry.entry_id]["coordinator"]

            # Add some mapped circuits that would appear in the YAML variables
            mock_kitchen_circuit = MagicMock()
            mock_kitchen_circuit.name = "Kitchen Updated"  # Changed from "Main Kitchen"
            mock_kitchen_circuit.circuit_id = "main_kitchen"

            mock_garage_circuit = MagicMock()
            mock_garage_circuit.name = "Garage Updated"  # Changed from "Main Garage"
            mock_garage_circuit.circuit_id = "main_garage"

            mock_hvac_circuit = MagicMock()
            mock_hvac_circuit.name = "HVAC System Updated"  # Changed from "HVAC Circuit"
            mock_hvac_circuit.circuit_id = "hvac_circuit"

            # Update coordinator data to include these mapped circuits
            coordinator.data.circuits.update(
                {
                    "main_kitchen": mock_kitchen_circuit,
                    "main_garage": mock_garage_circuit,
                    "hvac_circuit": mock_hvac_circuit,
                }
            )

            # Update the YAML with the new circuit names
            await solar_sensors._update_yaml_variables_from_coordinator()

            # Read the updated YAML
            with open(test_yaml, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify SPAN circuit variables were updated
            solar_power = updated_yaml["sensors"]["solar_inverter_instant_power"]
            assert (
                solar_power["variables"]["leg1_power"] == "sensor.span_panel_kitchen_updated_power"
            )
            assert (
                solar_power["variables"]["leg2_power"] == "sensor.span_panel_garage_updated_power"
            )

            solar_energy = updated_yaml["sensors"]["solar_inverter_energy_produced"]
            assert (
                solar_energy["variables"]["leg1_produced"]
                == "sensor.span_panel_kitchen_updated_energy_produced"
            )
            assert (
                solar_energy["variables"]["leg2_produced"]
                == "sensor.span_panel_garage_updated_energy_produced"
            )

            # Verify mixed variables sensor
            house_consumption = updated_yaml["sensors"]["span_panel_total_house_consumption"]
            assert (
                house_consumption["variables"]["main_consumption"]
                == "sensor.span_panel_main_panel_instant_power"
            )  # Direct sensor - should update if panel name changes
            assert (
                house_consumption["variables"]["hvac_consumption"]
                == "sensor.span_panel_hvac_system_updated_power"
            )  # Circuit name updated
            assert (
                house_consumption["variables"]["outdoor_sensor"]
                == "sensor.outdoor_temperature_sensor"
            )  # Non-SPAN - unchanged

            # Verify non-SPAN sensor remains completely untouched
            weather_calc = updated_yaml["sensors"]["external_weather_calculation"]
            assert weather_calc == original_yaml["sensors"]["external_weather_calculation"]

            # Verify unmapped circuits remain unchanged (stable entity IDs)
            unmapped_total = updated_yaml["sensors"]["span_panel_unmapped_total"]
            assert (
                unmapped_total["variables"]["unmapped1"]
                == "sensor.span_panel_unmapped_tab_15_power"
            )
            assert (
                unmapped_total["variables"]["unmapped2"]
                == "sensor.span_panel_unmapped_tab_16_power"
            )

            # Verify the sensor keys themselves remain stable (solar inverter naming)
            expected_sensor_keys = {
                "solar_inverter_instant_power",
                "solar_inverter_energy_produced",
                "span_panel_total_house_consumption",
                "external_weather_calculation",
                "span_panel_unmapped_total",
            }
            assert set(updated_yaml["sensors"].keys()) == expected_sensor_keys
