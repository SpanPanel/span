"""Test entity ID migration when device prefix setting changes."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.span_panel.const import DOMAIN, USE_DEVICE_PREFIX
from custom_components.span_panel.solar_synthetic_sensors import SolarSyntheticSensors
from tests.common import create_mock_config_entry


class TestEntityIdMigration:
    """Test entity ID migration when device prefix setting changes."""

    @pytest.fixture
    def hass_with_legacy_config(self, hass):
        """Create hass instance for legacy configuration testing."""
        # Create config entry with modern settings (USE_DEVICE_PREFIX: True)
        config_entry = create_mock_config_entry(
            {"host": "192.168.1.100"}, {USE_DEVICE_PREFIX: True}
        )
        config_entry.entry_id = "test_entry_id"

        # Create coordinator with mock data
        coordinator = MagicMock()
        coordinator.data = MagicMock()
        coordinator.config_entry = config_entry

        # Set the status with proper serial number for device_identifier
        coordinator.data.status = MagicMock()
        coordinator.data.status.serial_number = "TEST123456"

        # Create mock circuits
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

        # Store in hass.data
        hass.data = {DOMAIN: {"test_entry_id": {"coordinator": coordinator}}}

        return hass

    async def test_entity_id_generation_with_device_prefix(
        self,
        hass_with_legacy_config: HomeAssistant,
    ):
        """Test that solar sensors generate entity IDs with device prefix when enabled."""

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create solar sensors instance with modern config (device prefix enabled)
            config_entry = list(hass_with_legacy_config.data[DOMAIN].values())[0][
                "coordinator"
            ].config_entry
            solar_sensors = SolarSyntheticSensors(hass_with_legacy_config, config_entry, temp_dir)

            # Generate config with device prefix enabled
            # Get coordinator and span panel data from the test setup
            coordinator_data = hass_with_legacy_config.data[DOMAIN]["test_entry_id"]
            coordinator = coordinator_data["coordinator"]
            span_panel = coordinator.data

            await solar_sensors._generate_solar_config(coordinator, span_panel, 30, 32)

            # Read the generated YAML
            yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            with open(yaml_path, encoding="utf-8") as f:
                generated_yaml = yaml.safe_load(f)

            # Verify entity IDs have device prefix when USE_DEVICE_PREFIX: True
            solar_power_sensor = generated_yaml["sensors"]["span_test123456_solar_inverter_power"]
            assert solar_power_sensor["entity_id"] == "sensor.span_panel_solar_inverter_power"

            solar_energy_sensor = generated_yaml["sensors"][
                "span_test123456_solar_inverter_energy_produced"
            ]
            assert (
                solar_energy_sensor["entity_id"]
                == "sensor.span_panel_solar_inverter_energy_produced"
            )

            solar_consumed_sensor = generated_yaml["sensors"][
                "span_test123456_solar_inverter_energy_consumed"
            ]
            assert (
                solar_consumed_sensor["entity_id"]
                == "sensor.span_panel_solar_inverter_energy_consumed"
            )

    async def test_migration_skipped_when_device_prefix_disabled(
        self,
        hass_with_legacy_config: HomeAssistant,
    ):
        """Test that migration is skipped when device prefix is disabled."""

        with tempfile.TemporaryDirectory() as temp_dir:
            # Update config to disable device prefix
            config_entry = list(hass_with_legacy_config.data[DOMAIN].values())[0][
                "coordinator"
            ].config_entry
            config_entry.options = {USE_DEVICE_PREFIX: False}

            # Create legacy YAML file
            legacy_yaml_content = {
                "version": "1.0",
                "sensors": {
                    "span_test123456_solar_inverter_power": {
                        "name": "Solar Inverter Power",
                        "entity_id": "sensor.solar_inverter_power",  # Legacy pattern (no device prefix)
                        "formula": "leg1_power + leg2_power",
                        "variables": {
                            "leg1_power": "sensor.span_panel_unmapped_tab_30_power",
                            "leg2_power": "sensor.span_panel_unmapped_tab_32_power",
                        },
                        "unit_of_measurement": "W",
                        "device_class": "power",
                    }
                },
            }

            yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(legacy_yaml_content, f, default_flow_style=False)

            # Create solar sensors instance
            solar_sensors = SolarSyntheticSensors(hass_with_legacy_config, config_entry, temp_dir)

            # Trigger migration
            # Get coordinator and span panel data from the test setup
            coordinator_data = hass_with_legacy_config.data[DOMAIN]["test_entry_id"]
            coordinator = coordinator_data["coordinator"]
            span_panel = coordinator.data

            await solar_sensors._generate_solar_config(coordinator, span_panel, 30, 32)

            # Read the YAML
            with open(yaml_path, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify legacy entity_id is preserved (no migration)
            solar_power_sensor = updated_yaml["sensors"]["span_test123456_solar_inverter_power"]
            assert solar_power_sensor["entity_id"] == "sensor.solar_inverter_power"

    async def test_migration_handles_missing_yaml_gracefully(
        self,
        hass_with_legacy_config: HomeAssistant,
    ):
        """Test that migration handles missing YAML file gracefully."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = list(hass_with_legacy_config.data[DOMAIN].values())[0][
                "coordinator"
            ].config_entry
            SolarSyntheticSensors(hass_with_legacy_config, config_entry, temp_dir)

            # This should not raise an exception even though no YAML file exists
            pass

            # File should still not exist
            yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert not yaml_path.exists()
