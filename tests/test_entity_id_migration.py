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

    async def test_entity_id_migration_legacy_to_modern(
        self,
        hass_with_legacy_config: HomeAssistant,
    ):
        """Test migration from legacy entity IDs to modern device-prefixed entity IDs."""

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a legacy YAML file with old entity ID patterns
            legacy_yaml_content = {
                "version": "1.0",
                "sensors": {
                    "solar_inverter_instant_power": {
                        "name": "Solar Inverter Instant Power",
                        "entity_id": "sensor.span_panel_solar_inverter_instant_power",  # Legacy pattern
                        "formula": "leg1_power + leg2_power",
                        "variables": {
                            "leg1_power": "sensor.span_panel_unmapped_tab_30_power",
                            "leg2_power": "sensor.span_panel_unmapped_tab_32_power",
                        },
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                    },
                    "solar_inverter_energy_produced": {
                        "name": "Solar Inverter Energy Produced",
                        "entity_id": "sensor.solar_inverter_energy_produced",  # Legacy pattern
                        "formula": "leg1_produced + leg2_produced",
                        "variables": {
                            "leg1_produced": "sensor.span_panel_unmapped_tab_30_energy_produced",
                            "leg2_produced": "sensor.span_panel_unmapped_tab_32_energy_produced",
                        },
                        "unit_of_measurement": "Wh",
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                    "custom_sensor_with_legacy_reference": {
                        "name": "Custom Sensor",
                        "formula": "solar_power + other_power",
                        "variables": {
                            "solar_power": "sensor.solar_inverter_instant_power",  # Legacy reference
                            "other_power": "sensor.some_other_sensor",  # Non-SPAN reference
                        },
                        "unit_of_measurement": "W",
                        "device_class": "power",
                    },
                },
            }

            # Write legacy YAML to file
            yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(legacy_yaml_content, f, default_flow_style=False)

            # Create solar sensors instance with modern config (device prefix enabled)
            config_entry = list(hass_with_legacy_config.data[DOMAIN].values())[0][
                "coordinator"
            ].config_entry
            solar_sensors = SolarSyntheticSensors(hass_with_legacy_config, config_entry, temp_dir)

            # Trigger migration by generating config (this calls _migrate_entity_id_patterns_if_needed)
            await solar_sensors.generate_config(30, 32)

            # Read the updated YAML
            with open(yaml_path, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify entity_id migration
            solar_power_sensor = updated_yaml["sensors"]["solar_inverter_instant_power"]
            assert (
                solar_power_sensor["entity_id"] == "sensor.span_panel_solar_inverter_instant_power"
            )

            solar_energy_sensor = updated_yaml["sensors"]["solar_inverter_energy_produced"]
            assert (
                solar_energy_sensor["entity_id"]
                == "sensor.span_panel_solar_inverter_energy_produced"
            )

            # Verify variable reference migration
            custom_sensor = updated_yaml["sensors"]["custom_sensor_with_legacy_reference"]
            assert (
                custom_sensor["variables"]["solar_power"]
                == "sensor.span_panel_solar_inverter_instant_power"
            )
            # Non-SPAN reference should remain unchanged
            assert custom_sensor["variables"]["other_power"] == "sensor.some_other_sensor"

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
                    "solar_inverter_instant_power": {
                        "name": "Solar Inverter Instant Power",
                        "entity_id": "sensor.span_panel_solar_inverter_instant_power",  # Legacy pattern
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
            await solar_sensors.generate_config(30, 32)

            # Read the YAML
            with open(yaml_path, encoding="utf-8") as f:
                updated_yaml = yaml.safe_load(f)

            # Verify legacy entity_id is preserved (no migration)
            solar_power_sensor = updated_yaml["sensors"]["solar_inverter_instant_power"]
            assert solar_power_sensor["entity_id"] == "sensor.solar_inverter_instant_power"

    async def test_migration_handles_missing_yaml_gracefully(
        self,
        hass_with_legacy_config: HomeAssistant,
    ):
        """Test that migration handles missing YAML file gracefully."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_entry = list(hass_with_legacy_config.data[DOMAIN].values())[0][
                "coordinator"
            ].config_entry
            solar_sensors = SolarSyntheticSensors(hass_with_legacy_config, config_entry, temp_dir)

            # This should not raise an exception even though no YAML file exists
            await solar_sensors._migrate_entity_id_patterns_if_needed()

            # File should still not exist
            yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert not yaml_path.exists()
