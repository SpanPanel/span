"""Test solar synthetic sensor integration - comprehensive behavior testing."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock

import pytest
import yaml

from custom_components.span_panel.const import (
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.solar_synthetic_sensors import SolarSyntheticSensors
from tests.common import create_mock_config_entry


@pytest.fixture
def solar_test_fixtures():
    """Solar test fixtures containing all configuration scenarios and expected YAML files."""
    return {
        "dual_leg_modern": {
            "config": {
                "legs": (15, 16),
                "use_device_prefix": True,
                "serial_number": "TEST123456",
            },
            "expected_yaml_file": "tests/fixtures/solar_dual_leg_modern.yaml",
        },
        "single_leg_modern": {
            "config": {
                "legs": (30, 0),
                "use_device_prefix": True,
                "serial_number": "TEST123456",
            },
            "expected_yaml_file": "tests/fixtures/solar_single_leg_modern.yaml",
        },
        "dual_leg_legacy": {
            "config": {
                "legs": (15, 16),
                "use_device_prefix": False,
                "serial_number": "TEST123456",
            },
            "expected_yaml_file": "tests/fixtures/solar_dual_leg_legacy.yaml",
        },
        "no_legs": {
            "config": {
                "legs": (0, 0),
                "use_device_prefix": True,
                "serial_number": "TEST123456",
            },
            "expected_yaml_file": None,  # No YAML should be generated
        },
    }


@pytest.fixture
def mock_coordinator_data():
    """Create mock coordinator data for solar testing."""
    coordinator = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.status = MagicMock()
    coordinator.data.status.serial_number = "TEST123456"
    coordinator.data.circuits = {
        "unmapped_tab_15": MagicMock(name="Solar Leg 1"),
        "unmapped_tab_16": MagicMock(name="Solar Leg 2"),
        "unmapped_tab_30": MagicMock(name="Solar Single Leg"),
    }
    return coordinator


@pytest.fixture
def hass_with_solar_data(hass, mock_coordinator_data):
    """Create hass instance with solar coordinator data."""
    config_entry = create_mock_config_entry(
        {"host": "192.168.1.100"}, {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
    )
    config_entry.entry_id = "test_entry_id"
    mock_coordinator_data.config_entry = config_entry

    # Mock async_add_executor_job to execute functions immediately
    async def mock_async_add_executor_job(func, *args, **kwargs):
        return func(*args, **kwargs)

    hass.async_add_executor_job = mock_async_add_executor_job
    hass.data = {DOMAIN: {"test_entry_id": {"coordinator": mock_coordinator_data}}}
    return hass, config_entry


@pytest.fixture
def hass_with_legacy_solar_data(hass, mock_coordinator_data):
    """Create hass instance with legacy solar coordinator data (no device prefix)."""
    config_entry = create_mock_config_entry(
        {"host": "192.168.1.100"}, {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
    )
    config_entry.entry_id = "test_entry_id"
    mock_coordinator_data.config_entry = config_entry

    # Mock async_add_executor_job to execute functions immediately
    async def mock_async_add_executor_job(func, *args, **kwargs):
        return func(*args, **kwargs)

    hass.async_add_executor_job = mock_async_add_executor_job
    hass.data = {DOMAIN: {"test_entry_id": {"coordinator": mock_coordinator_data}}}
    return hass, config_entry


class TestSolarIntegration:
    """Test solar synthetic sensor integration with actual behavior verification."""

    @pytest.mark.asyncio
    async def test_dual_leg_solar_configuration(self, hass_with_solar_data, solar_test_fixtures):
        """Test dual leg solar configuration generates correct YAML."""
        hass, config_entry = hass_with_solar_data
        fixture = solar_test_fixtures["dual_leg_modern"]

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]

            # Generate solar config using fixture configuration
            legs = fixture["config"]["legs"]
            await solar_sensors._generate_solar_config(
                coordinator, coordinator.data, legs[0], legs[1]
            )

            # Verify file was created
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert yaml_file.exists(), "YAML file should be created"

            # Load and verify content matches expected structure
            with open(yaml_file, encoding="utf-8") as f:
                actual_yaml = yaml.safe_load(f)

            # Load expected YAML from fixture file
            with open(fixture["expected_yaml_file"], encoding="utf-8") as f:
                expected_yaml = yaml.safe_load(f)

            assert actual_yaml == expected_yaml, "Generated YAML should match expected structure"

    @pytest.mark.asyncio
    async def test_single_leg_solar_configuration(self, hass_with_solar_data, solar_test_fixtures):
        """Test single leg solar configuration generates correct YAML."""
        hass, config_entry = hass_with_solar_data
        fixture = solar_test_fixtures["single_leg_modern"]

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]

            # Generate solar config using fixture configuration
            legs = fixture["config"]["legs"]
            await solar_sensors._generate_solar_config(
                coordinator, coordinator.data, legs[0], legs[1]
            )

            # Verify file was created
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert yaml_file.exists(), "YAML file should be created"

            # Load and verify content matches expected structure
            with open(yaml_file, encoding="utf-8") as f:
                actual_yaml = yaml.safe_load(f)

            # Load expected YAML from fixture file
            with open(fixture["expected_yaml_file"], encoding="utf-8") as f:
                expected_yaml = yaml.safe_load(f)

            assert actual_yaml == expected_yaml, "Generated YAML should match expected structure"

    @pytest.mark.asyncio
    async def test_legacy_naming_solar_configuration(
        self, hass_with_legacy_solar_data, solar_test_fixtures
    ):
        """Test legacy naming (no device prefix) generates correct entity IDs."""
        hass, config_entry = hass_with_legacy_solar_data
        fixture = solar_test_fixtures["dual_leg_legacy"]

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]

            # Generate solar config using fixture configuration
            legs = fixture["config"]["legs"]
            await solar_sensors._generate_solar_config(
                coordinator, coordinator.data, legs[0], legs[1]
            )

            # Verify file was created
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert yaml_file.exists(), "YAML file should be created"

            # Load and verify content matches expected legacy structure
            with open(yaml_file, encoding="utf-8") as f:
                actual_yaml = yaml.safe_load(f)

            # Load expected YAML from fixture file
            with open(fixture["expected_yaml_file"], encoding="utf-8") as f:
                expected_yaml = yaml.safe_load(f)

            assert actual_yaml == expected_yaml, "Generated YAML should use legacy entity IDs"

    @pytest.mark.asyncio
    async def test_no_legs_configuration(self, hass_with_solar_data, solar_test_fixtures):
        """Test that no valid legs results in no YAML file."""
        hass, config_entry = hass_with_solar_data
        fixture = solar_test_fixtures["no_legs"]

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]

            # Generate solar config using fixture configuration
            legs = fixture["config"]["legs"]
            await solar_sensors._generate_solar_config(
                coordinator, coordinator.data, legs[0], legs[1]
            )

            # Verify no file was created
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert not yaml_file.exists(), "No YAML file should be created for invalid legs"

    @pytest.mark.asyncio
    async def test_solar_configuration_cleanup(self, hass_with_solar_data):
        """Test that solar configuration cleanup removes YAML file completely."""
        hass, config_entry = hass_with_solar_data

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]

            # First create a solar configuration
            await solar_sensors._generate_solar_config(coordinator, coordinator.data, 15, 16)
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"
            assert yaml_file.exists(), "YAML file should be created initially"

            # Now cleanup the configuration
            await solar_sensors.remove_config()

            # Verify file is completely removed
            assert not yaml_file.exists(), "YAML file should be completely removed after cleanup"

    @pytest.mark.asyncio
    async def test_yaml_validation_functionality(self, hass_with_solar_data):
        """Test YAML validation works for valid and invalid configurations."""
        hass, config_entry = hass_with_solar_data

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"

            # Test validation with no file
            assert await solar_sensors.validate_config() is False, "Should be invalid with no file"

            # Generate valid configuration
            await solar_sensors._generate_solar_config(coordinator, coordinator.data, 15, 16)
            assert await solar_sensors.validate_config() is True, (
                "Should be valid with proper config"
            )

            # Test validation with invalid YAML
            with open(yaml_file, "w", encoding="utf-8") as f:
                f.write("invalid: yaml: content:")
            assert await solar_sensors.validate_config() is False, (
                "Should be invalid with malformed YAML"
            )

            # Test validation with missing required fields
            with open(yaml_file, "w", encoding="utf-8") as f:
                yaml.dump({"version": "1.0"}, f)  # Missing "sensors"
            assert await solar_sensors.validate_config() is False, (
                "Should be invalid without sensors"
            )

    @pytest.mark.asyncio
    async def test_solar_sensors_initialization(self, hass_with_solar_data):
        """Test basic initialization of SolarSyntheticSensors."""
        hass, config_entry = hass_with_solar_data

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)

            assert solar_sensors._hass == hass
            assert solar_sensors._config_entry == config_entry
            assert solar_sensors.config_file_path.name == "solar_synthetic_sensors.yaml"
            assert str(temp_dir) in str(solar_sensors.config_file_path)

    @pytest.mark.asyncio
    async def test_configuration_reconfiguration(self, hass_with_solar_data, solar_test_fixtures):
        """Test that reconfiguring solar (dual to single leg) updates YAML correctly."""
        hass, config_entry = hass_with_solar_data
        dual_fixture = solar_test_fixtures["dual_leg_modern"]
        single_fixture = solar_test_fixtures["single_leg_modern"]

        with tempfile.TemporaryDirectory() as temp_dir:
            solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)
            coordinator = hass.data[DOMAIN]["test_entry_id"]["coordinator"]
            yaml_file = Path(temp_dir) / "solar_synthetic_sensors.yaml"

            # Start with dual leg configuration
            dual_legs = dual_fixture["config"]["legs"]
            await solar_sensors._generate_solar_config(
                coordinator, coordinator.data, dual_legs[0], dual_legs[1]
            )

            with open(yaml_file, encoding="utf-8") as f:
                dual_config = yaml.safe_load(f)

            # Load expected dual leg YAML
            with open(dual_fixture["expected_yaml_file"], encoding="utf-8") as f:
                expected_dual_config = yaml.safe_load(f)
            assert dual_config == expected_dual_config, "Should start with dual leg config"

            # Reconfigure to single leg
            single_legs = single_fixture["config"]["legs"]
            await solar_sensors._generate_solar_config(
                coordinator, coordinator.data, single_legs[0], single_legs[1]
            )

            with open(yaml_file, encoding="utf-8") as f:
                single_config = yaml.safe_load(f)

            # Load expected single leg YAML
            with open(single_fixture["expected_yaml_file"], encoding="utf-8") as f:
                expected_single_config = yaml.safe_load(f)
            assert single_config == expected_single_config, "Should update to single leg config"

            # Verify the file was updated, not just appended to
            assert single_config != dual_config, "Configuration should have changed"
            assert len(single_config["sensors"]) == 3, "Should still have 3 sensors"

            # Verify single leg formulas
            power_sensor = single_config["sensors"]["span_test123456_solar_inverter_power"]
            assert power_sensor["formula"] == "leg1_power", "Should use single leg formula"
            assert "leg2_power" not in power_sensor["variables"], "Should not have leg2 variables"
