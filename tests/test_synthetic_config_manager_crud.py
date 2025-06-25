"""Test CRUD operations for SyntheticConfigManager."""

import asyncio
from pathlib import Path
import shutil
import tempfile

import pytest

from custom_components.span_panel.synthetic_config_manager import SyntheticConfigManager


class TestSyntheticConfigManagerCRUD:
    """Test CRUD operations for SyntheticConfigManager.

    Important: Each test uses its own isolated temporary directory to ensure
    test independence. This prevents test order dependencies and ensures
    clean state for all CRUD operations.
    """

    @pytest.fixture
    async def config_manager(self, hass):
        """Create a SyntheticConfigManager instance with isolated temp directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SyntheticConfigManager(
                hass, config_filename="test_synthetic_sensors.yaml", config_dir=temp_dir
            )
            yield manager

    @pytest.fixture
    async def populated_config_manager(self, hass):
        """Create a config manager with pre-populated data from fixture.

        Note: Uses its own isolated temp directory to ensure test independence.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Copy the multi-panel fixture to the temp directory
            fixtures_dir = Path(__file__).parent / "fixtures"
            source_fixture = fixtures_dir / "synthetic_config_multi_panel.yaml"
            target_file = Path(temp_dir) / "test_synthetic_sensors.yaml"

            shutil.copy(source_fixture, target_file)

            manager = SyntheticConfigManager(
                hass, config_filename="test_synthetic_sensors.yaml", config_dir=temp_dir
            )
            yield manager

    @pytest.fixture
    def sample_sensor_config(self):
        """Sample sensor configuration for testing."""
        return {
            "name": "Test Sensor",
            "entity_id": "sensor.test_sensor",
            "formula": "input1 + input2",
            "variables": {
                "input1": "sensor.span_panel_circuit_1_power",
                "input2": "sensor.span_panel_circuit_2_power",
            },
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "attributes": {
                "calculation_method": "sum",
                "data_source": "SPAN Panel Test",
            },
        }

    @pytest.fixture
    def complex_sensor_config(self):
        """Complex sensor configuration with attributes for testing."""
        return {
            "name": "Complex Test Sensor",
            "entity_id": "sensor.complex_test_sensor",
            "formula": "max(current_value, historical_max or 0)",
            "variables": {
                "current_value": "sensor.span_panel_main_power",
                "historical_max": "sensor.complex_test_sensor",
            },
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "attributes": {
                "peak_time": "{{ now().strftime('%Y-%m-%d %H:%M:%S') if current_value == value else state_attr('sensor.complex_test_sensor', 'peak_time') }}",
                "days_since_peak": "{{ ((now() - strptime(state_attr('sensor.complex_test_sensor', 'peak_time'), '%Y-%m-%d %H:%M:%S')).days) if state_attr('sensor.complex_test_sensor', 'peak_time') else 0 }}",
                "category": "{% if value < 1000 %}low{% elif value < 3000 %}medium{% else %}high{% endif %}",
                "metadata": {
                    "version": "1.0",
                    "created": "2025-01-01",
                    "source": "SPAN Panel Integration",
                },
            },
        }

    # CREATE Tests
    async def test_create_sensor_new_file(self, config_manager, sample_sensor_config):
        """Test creating a sensor in a new config file."""
        device_id = "TEST123"
        sensor_key = "test_sensor"

        # Verify file doesn't exist initially
        assert not await config_manager.config_file_exists()

        # Create the sensor
        await config_manager.create_sensor(device_id, sensor_key, sample_sensor_config)

        # Verify file was created
        assert await config_manager.config_file_exists()

        # Verify sensor was added with device_identifier
        config = await config_manager.read_config()
        assert "sensors" in config
        assert sensor_key in config["sensors"]

        created_sensor = config["sensors"][sensor_key]
        assert created_sensor["device_identifier"] == f"span_panel_{device_id}"
        assert created_sensor["name"] == sample_sensor_config["name"]
        assert created_sensor["formula"] == sample_sensor_config["formula"]
        assert created_sensor["attributes"] == sample_sensor_config["attributes"]

    async def test_create_sensor_existing_file(
        self, populated_config_manager, sample_sensor_config
    ):
        """Test creating a sensor in an existing config file."""
        device_id = "TEST789"
        sensor_key = "new_test_sensor"

        # Get initial sensor count
        initial_config = await populated_config_manager.read_config()
        initial_count = len(initial_config["sensors"])

        # Create the sensor
        await populated_config_manager.create_sensor(device_id, sensor_key, sample_sensor_config)

        # Verify sensor was added
        updated_config = await populated_config_manager.read_config()
        assert len(updated_config["sensors"]) == initial_count + 1
        assert sensor_key in updated_config["sensors"]

        created_sensor = updated_config["sensors"][sensor_key]
        assert created_sensor["device_identifier"] == f"span_panel_{device_id}"

    async def test_create_sensor_with_complex_attributes(
        self, config_manager, complex_sensor_config
    ):
        """Test creating a sensor with complex attributes."""
        device_id = "TEST123"
        sensor_key = "complex_sensor"

        await config_manager.create_sensor(device_id, sensor_key, complex_sensor_config)

        config = await config_manager.read_config()
        created_sensor = config["sensors"][sensor_key]

        # Verify complex attributes are preserved
        assert "attributes" in created_sensor
        assert "peak_time" in created_sensor["attributes"]
        assert "metadata" in created_sensor["attributes"]
        assert created_sensor["attributes"]["metadata"]["version"] == "1.0"

    # READ Tests
    async def test_read_sensor_exists(self, populated_config_manager):
        """Test reading an existing sensor."""
        device_id = "TEST123"
        sensor_key = "solar_inverter_instant_power"

        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)

        assert sensor_config is not None
        assert sensor_config["name"] == "Solar Inverter Instant Power"
        assert sensor_config["device_identifier"] == f"span_panel_{device_id}"
        assert "variables" in sensor_config
        assert "leg1_power" in sensor_config["variables"]

    async def test_read_sensor_not_exists(self, populated_config_manager):
        """Test reading a non-existent sensor."""
        device_id = "TEST123"
        sensor_key = "nonexistent_sensor"

        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert sensor_config is None

    async def test_read_sensor_wrong_device(self, populated_config_manager):
        """Test reading a sensor that belongs to a different device."""
        device_id = "WRONG_DEVICE"
        sensor_key = "solar_inverter_instant_power"  # This belongs to TEST123

        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert sensor_config is None

    async def test_read_sensor_with_attributes(self, populated_config_manager):
        """Test reading a sensor with attributes."""
        device_id = "TEST456"
        sensor_key = "workshop_total_consumption"

        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)

        assert sensor_config is not None
        assert "attributes" in sensor_config
        assert sensor_config["attributes"]["data_source"] == "SPAN Panel Workshop"
        assert sensor_config["attributes"]["calculation_method"] == "sum"

    # UPDATE Tests
    async def test_update_sensor_exists(self, populated_config_manager, sample_sensor_config):
        """Test updating an existing sensor."""
        device_id = "TEST123"
        sensor_key = "solar_inverter_instant_power"

        # Modify the sample config for update
        updated_config = sample_sensor_config.copy()
        updated_config["name"] = "Updated Solar Inverter Power"
        updated_config["formula"] = "leg1_power + leg2_power + boost"
        updated_config["variables"]["boost"] = "sensor.span_panel_boost_power"

        # Update the sensor
        result = await populated_config_manager.update_sensor(device_id, sensor_key, updated_config)
        assert result is True

        # Verify the update
        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert sensor_config["name"] == "Updated Solar Inverter Power"
        assert sensor_config["formula"] == "leg1_power + leg2_power + boost"
        assert "boost" in sensor_config["variables"]
        assert sensor_config["device_identifier"] == f"span_panel_{device_id}"

    async def test_update_sensor_not_exists(self, populated_config_manager, sample_sensor_config):
        """Test updating a non-existent sensor."""
        device_id = "TEST123"
        sensor_key = "nonexistent_sensor"

        result = await populated_config_manager.update_sensor(
            device_id, sensor_key, sample_sensor_config
        )
        assert result is False

    async def test_update_sensor_wrong_device(self, populated_config_manager, sample_sensor_config):
        """Test updating a sensor that belongs to a different device."""
        device_id = "WRONG_DEVICE"
        sensor_key = "solar_inverter_instant_power"  # This belongs to TEST123

        result = await populated_config_manager.update_sensor(
            device_id, sensor_key, sample_sensor_config
        )
        assert result is False

    async def test_update_sensor_attributes(self, populated_config_manager):
        """Test updating sensor attributes."""
        device_id = "TEST123"
        sensor_key = "daily_energy_summary"

        # Read current config
        current_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert current_config is not None

        # Update attributes
        updated_config = current_config.copy()
        updated_config["attributes"]["report_date"] = "2025-12-31"
        updated_config["attributes"]["new_attribute"] = "test_value"

        # Apply update
        result = await populated_config_manager.update_sensor(device_id, sensor_key, updated_config)
        assert result is True

        # Verify the update
        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert sensor_config["attributes"]["report_date"] == "2025-12-31"
        assert sensor_config["attributes"]["new_attribute"] == "test_value"

    # DELETE Tests
    async def test_delete_sensor_exists(self, populated_config_manager):
        """Test deleting an existing sensor."""
        device_id = "TEST123"
        sensor_key = "solar_inverter_instant_power"

        # Verify sensor exists
        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert sensor_config is not None

        # Delete the sensor
        result = await populated_config_manager.delete_sensor(device_id, sensor_key)
        assert result is True

        # Verify sensor is gone
        sensor_config = await populated_config_manager.read_sensor(device_id, sensor_key)
        assert sensor_config is None

        # Verify it's not in the config
        config = await populated_config_manager.read_config()
        assert sensor_key not in config["sensors"]

    async def test_delete_sensor_not_exists(self, populated_config_manager):
        """Test deleting a non-existent sensor."""
        device_id = "TEST123"
        sensor_key = "nonexistent_sensor"

        result = await populated_config_manager.delete_sensor(device_id, sensor_key)
        assert result is False

    async def test_delete_sensor_wrong_device(self, populated_config_manager):
        """Test deleting a sensor that belongs to a different device."""
        device_id = "WRONG_DEVICE"
        sensor_key = "solar_inverter_instant_power"  # This belongs to TEST123

        result = await populated_config_manager.delete_sensor(device_id, sensor_key)
        assert result is False

        # Verify sensor still exists for correct device
        sensor_config = await populated_config_manager.read_sensor("TEST123", sensor_key)
        assert sensor_config is not None

    # DELETE ALL Tests
    async def test_delete_all_device_sensors(self, populated_config_manager):
        """Test deleting all sensors for a device."""
        device_id = "TEST123"

        # Count initial sensors for this device
        initial_sensors = await populated_config_manager.list_device_sensors(device_id)
        initial_count = len(initial_sensors)
        assert initial_count > 0

        # Delete all sensors for this device
        deleted_count = await populated_config_manager.delete_all_device_sensors(device_id)
        assert deleted_count == initial_count

        # Verify no sensors remain for this device
        remaining_sensors = await populated_config_manager.list_device_sensors(device_id)
        assert len(remaining_sensors) == 0

        # Verify sensors for other devices still exist
        other_device_sensors = await populated_config_manager.list_device_sensors("TEST456")
        assert len(other_device_sensors) > 0

    async def test_delete_all_device_sensors_no_device(self, populated_config_manager):
        """Test deleting all sensors for a non-existent device."""
        device_id = "NONEXISTENT_DEVICE"

        deleted_count = await populated_config_manager.delete_all_device_sensors(device_id)
        assert deleted_count == 0

    async def test_delete_all_sensors_removes_empty_file(
        self, config_manager, sample_sensor_config
    ):
        """Test that deleting all sensors removes the config file entirely."""
        device_id = "TEST123"
        sensor_key = "only_sensor"

        # Create a single sensor
        await config_manager.create_sensor(device_id, sensor_key, sample_sensor_config)
        assert await config_manager.config_file_exists()

        # Delete all sensors for the device
        deleted_count = await config_manager.delete_all_device_sensors(device_id)
        assert deleted_count == 1

        # Verify file is removed when empty
        assert not await config_manager.config_file_exists()

    # LIST Tests
    async def test_list_device_sensors(self, populated_config_manager):
        """Test listing all sensors for a device."""
        device_id = "TEST123"

        sensors = await populated_config_manager.list_device_sensors(device_id)

        # Should have multiple sensors for TEST123
        assert len(sensors) >= 3
        assert "solar_inverter_instant_power" in sensors
        assert "solar_inverter_energy_produced" in sensors
        assert "house_total_consumption" in sensors

        # All sensors should have correct device_identifier
        for sensor_config in sensors.values():
            assert sensor_config["device_identifier"] == f"span_panel_{device_id}"

    async def test_list_device_sensors_no_device(self, populated_config_manager):
        """Test listing sensors for a non-existent device."""
        device_id = "NONEXISTENT_DEVICE"

        sensors = await populated_config_manager.list_device_sensors(device_id)
        assert len(sensors) == 0

    async def test_list_device_sensors_empty_file(self, config_manager):
        """Test listing sensors when config file doesn't exist."""
        device_id = "TEST123"

        sensors = await config_manager.list_device_sensors(device_id)
        assert len(sensors) == 0

    # UTILITY Tests
    async def test_config_file_exists_empty_manager(self, config_manager):
        """Test config file existence check for empty manager."""
        # New manager should not have file
        assert not await config_manager.config_file_exists()

    async def test_config_file_exists_populated_manager(self, populated_config_manager):
        """Test config file existence check for populated manager."""
        # Populated manager should have file
        assert await populated_config_manager.config_file_exists()

    async def test_get_config_file_path(self, hass):
        """Test getting config file path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SyntheticConfigManager(
                hass, config_filename="test_synthetic_sensors.yaml", config_dir=temp_dir
            )
            path = await manager.get_config_file_path()
            expected_path = Path(temp_dir) / "test_synthetic_sensors.yaml"
            assert path == expected_path

    async def test_read_config_full(self, populated_config_manager):
        """Test reading the full configuration."""
        config = await populated_config_manager.read_config()

        assert "version" in config
        assert config["version"] == "1.0"
        assert "sensors" in config
        assert len(config["sensors"]) > 0

        # Verify structure of a sensor with attributes
        daily_summary = config["sensors"]["daily_energy_summary"]
        assert "attributes" in daily_summary
        assert "source_sensors" in daily_summary["attributes"]
        assert isinstance(daily_summary["attributes"]["source_sensors"], list)

    # CONCURRENCY Tests
    async def test_concurrent_operations(self, config_manager, sample_sensor_config):
        """Test concurrent CRUD operations."""
        device_id = "TEST123"

        # Create multiple sensors concurrently
        tasks = []
        for i in range(5):
            sensor_key = f"concurrent_sensor_{i}"
            config = sample_sensor_config.copy()
            config["name"] = f"Concurrent Sensor {i}"
            config["entity_id"] = f"sensor.concurrent_sensor_{i}"
            tasks.append(config_manager.create_sensor(device_id, sensor_key, config))

        await asyncio.gather(*tasks)

        # Verify all sensors were created
        sensors = await config_manager.list_device_sensors(device_id)
        assert len(sensors) == 5

        # Test concurrent reads
        read_tasks = [
            config_manager.read_sensor(device_id, f"concurrent_sensor_{i}") for i in range(5)
        ]
        results = await asyncio.gather(*read_tasks)
        assert all(result is not None for result in results)

        # Test concurrent deletes
        delete_tasks = [
            config_manager.delete_sensor(device_id, f"concurrent_sensor_{i}") for i in range(5)
        ]
        delete_results = await asyncio.gather(*delete_tasks)
        assert all(result is True for result in delete_results)

        # Verify all sensors are gone
        sensors = await config_manager.list_device_sensors(device_id)
        assert len(sensors) == 0

    # ERROR HANDLING Tests
    async def test_invalid_yaml_handling(self, hass):
        """Test handling of invalid YAML files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SyntheticConfigManager(
                hass, config_filename="test_synthetic_sensors.yaml", config_dir=temp_dir
            )

            # Create an invalid YAML file
            config_file = Path(temp_dir) / "test_synthetic_sensors.yaml"
            config_file.write_text("invalid: yaml: content: [unclosed", encoding="utf-8")

            # Should return default config on invalid YAML
            config = await manager.read_config()
            assert config == {"version": "1.0", "sensors": {}}

    async def test_file_permission_handling(self, hass):
        """Test handling of file permission errors.

        Note: This test uses an invalid directory path to simulate permission errors.
        In a real scenario, this would occur when the Home Assistant config directory
        is not writable or the custom_components directory cannot be created.
        """
        # Use a directory that doesn't exist and can't be created
        invalid_dir = "/invalid/directory/that/cannot/be/created"
        manager = SyntheticConfigManager(hass, config_filename="test.yaml", config_dir=invalid_dir)

        # Should handle permission errors gracefully by raising OSError
        with pytest.raises(OSError):
            await manager.create_sensor("TEST123", "test_sensor", {"name": "Test"})
