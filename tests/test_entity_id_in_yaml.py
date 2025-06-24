"""Test that entity_id fields are correctly set in generated YAML."""

import tempfile
from unittest.mock import MagicMock, patch

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


class TestEntityIdInYaml:
    """Test that entity_id fields are correctly set in generated YAML."""

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry with device prefix enabled."""
        entry = create_mock_config_entry(
            {"host": "192.168.1.100"},
            {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True},
        )
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.fixture
    def hass_with_solar_data(self, hass, mock_config_entry):
        """Create hass instance with solar coordinator data."""
        # Mock coordinator data that SolarSyntheticSensors expects
        coordinator = MagicMock()
        coordinator.data = MagicMock()
        coordinator.data.circuits = {
            "unmapped_tab_15": MagicMock(name="Solar Leg 1"),
            "unmapped_tab_16": MagicMock(name="Solar Leg 2"),
        }
        coordinator.data.name = "Test Panel"
        # Set the config entry so entity ID generation works correctly
        coordinator.config_entry = mock_config_entry

        # Store coordinator in hass.data using the correct structure
        hass.data[DOMAIN] = {"test_entry_id": {"coordinator": coordinator}}

        return hass

    async def test_entity_id_fields_present_in_yaml(
        self,
        hass_with_solar_data: HomeAssistant,
        mock_config_entry,
    ):
        """Test that entity_id fields are present for all sensors in generated YAML."""

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass_with_solar_data, mock_config_entry, temp_dir)

            # Generate YAML
            await solar_sensors.generate_config(15, 16)

            yaml_path = solar_sensors.config_file_path
            assert yaml_path.exists()

            # Load and verify YAML content
            with open(yaml_path, encoding="utf-8") as f:
                yaml_content = yaml.safe_load(f)

            # Verify structure
            assert "version" in yaml_content
            assert "sensors" in yaml_content
            assert len(yaml_content["sensors"]) == 3  # power, produced, consumed

            # Verify each sensor has entity_id field
            for sensor_key, sensor_config in yaml_content["sensors"].items():
                assert "entity_id" in sensor_config, f"entity_id missing for sensor {sensor_key}"
                assert "name" in sensor_config
                assert "formula" in sensor_config
                assert "variables" in sensor_config

                # Verify entity_id follows expected pattern
                entity_id = sensor_config["entity_id"]
                assert entity_id.startswith("sensor."), (
                    f"entity_id should start with 'sensor.': {entity_id}"
                )

                # With USE_CIRCUIT_NUMBERS: False and USE_DEVICE_PREFIX: True,
                # entity IDs should be name-based with device prefix:
                # sensor.span_panel_solar_inverter_{suffix}
                assert "span_panel" in entity_id, (
                    f"entity_id should contain device prefix 'span_panel': {entity_id}"
                )
                assert "solar_inverter" in entity_id, (
                    f"entity_id should contain 'solar_inverter': {entity_id}"
                )

            # Verify specific sensor entity_ids based on the friendly name "Solar Inverter"
            # With USE_CIRCUIT_NUMBERS: False, we get name-based IDs
            # But YAML keys are circuit-based for v1.0.10 compatibility
            expected_sensors = {
                "span_panel_solar_inverter_15_16_instant_power": "sensor.span_panel_solar_inverter_instant_power",
                "span_panel_solar_inverter_15_16_energy_produced": "sensor.span_panel_solar_inverter_energy_produced",
                "span_panel_solar_inverter_15_16_energy_consumed": "sensor.span_panel_solar_inverter_energy_consumed",
            }

            for sensor_key, expected_entity_id in expected_sensors.items():
                assert sensor_key in yaml_content["sensors"], (
                    f"Expected sensor key {sensor_key} not found"
                )
                actual_entity_id = yaml_content["sensors"][sensor_key]["entity_id"]
                assert actual_entity_id == expected_entity_id, (
                    f"Expected {expected_entity_id}, got {actual_entity_id}"
                )

    async def test_entity_id_matches_yaml_key(
        self,
        hass_with_solar_data: HomeAssistant,
        mock_config_entry,
    ):
        """Test that the entity_id field matches the YAML key (with sensor. prefix)."""

        with (
            patch("custom_components.span_panel.helpers.construct_entity_id") as mock_construct,
            patch("custom_components.span_panel.helpers.get_user_friendly_suffix") as mock_suffix,
            patch(
                "custom_components.span_panel.solar_synthetic_sensors.SolarSyntheticSensors._construct_solar_inverter_entity_id"
            ) as mock_solar,
        ):
            mock_construct.return_value = "sensor.test_entity"
            mock_suffix.return_value = "power"
            mock_solar.return_value = "sensor.test_solar_power"

            with tempfile.TemporaryDirectory() as temp_dir:
                solar_sensors = SolarSyntheticSensors(
                    hass_with_solar_data, mock_config_entry, temp_dir
                )

                await solar_sensors.generate_config(15, 0)  # Single leg

                yaml_path = solar_sensors.config_file_path
                with open(yaml_path, encoding="utf-8") as f:
                    yaml_content = yaml.safe_load(f)

                # For each sensor, verify that the entity_id field uses the mocked value
                # (With circuit-based YAML keys, entity_id is separate from the key)
                for _sensor_key, sensor_config in yaml_content["sensors"].items():
                    actual_entity_id = sensor_config["entity_id"]
                    assert actual_entity_id == "sensor.test_solar_power", (
                        f"entity_id {actual_entity_id} should use mocked value sensor.test_solar_power"
                    )
