"""Test CRUD operations for synthetic sensors using ha-synthetic-sensors package."""

import asyncio
from pathlib import Path
import tempfile

import pytest


# Mock the obsolete class to prevent import errors
class SyntheticConfigManager:
    """Mock class for obsolete SyntheticConfigManager."""

    def __init__(self, *args, **kwargs):
        pass


@pytest.mark.skip(reason="SyntheticConfigManager has been replaced by ha-synthetic-sensors package")
class TestSyntheticConfigManagerCRUD:
    """Test CRUD operations for SyntheticConfigManager.

    Important: Each test uses its own isolated temporary directory to ensure
    test independence. This prevents test order dependencies and ensures
    clean state for all CRUD operations.
    """

    @pytest.fixture
    async def config_manager(self, hass):
        """Create a SyntheticConfigManager instance with isolated temp directory."""
        with tempfile.TemporaryDirectory():
            manager = SyntheticConfigManager(
                hass, sensor_set_id="test_synthetic_sensors", device_identifier="TEST123"
            )
            yield manager

    @pytest.fixture
    async def populated_config_manager(self, hass):
        """Create a config manager with pre-populated data from fixture.

        Note: Uses storage-based approach instead of file copying.
        """
        manager = SyntheticConfigManager(
            hass, sensor_set_id="test_synthetic_sensors", device_identifier="TEST123"
        )

        # Load the unified fixture and write it to storage
        fixtures_dir = Path(__file__).parent / "fixtures"
        source_fixture = fixtures_dir / "synthetic_config_unified.yaml"

        import yaml

        with open(source_fixture) as f:
            config_data = yaml.safe_load(f)

        await manager.write_config(config_data)
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
        """Test creating a sensor in a new sensor set."""
        sensor_key = "test_sensor"

        # Verify sensor set doesn't exist initially
        assert not await config_manager.sensor_set_exists()

        # Create the sensor
        await config_manager.create_sensor(sensor_key, sample_sensor_config)

        # Verify sensor set was created
        assert await config_manager.sensor_set_exists()

        # Verify sensor was added
        config = await config_manager.read_config()
        assert "sensors" in config
        assert sensor_key in config["sensors"]

        created_sensor = config["sensors"][sensor_key]
        assert created_sensor["name"] == sample_sensor_config["name"]
        # Check formula structure (should be flat format from YAML export)
        assert "formula" in created_sensor
        assert created_sensor["formula"] == sample_sensor_config["formula"]

    async def test_create_sensor_existing_file(
        self, populated_config_manager, sample_sensor_config
    ):
        """Test creating a sensor in an existing sensor set."""
        sensor_key = "new_test_sensor"

        # Get initial sensor count
        initial_config = await populated_config_manager.read_config()
        initial_count = len(initial_config["sensors"])

        # Create the sensor
        await populated_config_manager.create_sensor(sensor_key, sample_sensor_config)

        # Verify sensor was added
        updated_config = await populated_config_manager.read_config()
        assert len(updated_config["sensors"]) == initial_count + 1
        assert sensor_key in updated_config["sensors"]

        created_sensor = updated_config["sensors"][sensor_key]
        assert created_sensor["name"] == sample_sensor_config["name"]

    async def test_create_sensor_with_complex_attributes(
        self, config_manager, complex_sensor_config
    ):
        """Test creating a sensor with complex attributes."""
        sensor_key = "complex_sensor"

        await config_manager.create_sensor(sensor_key, complex_sensor_config)

        config = await config_manager.read_config()
        created_sensor = config["sensors"][sensor_key]

        # Verify sensor was created with proper structure
        assert created_sensor["name"] == complex_sensor_config["name"]
        assert "formula" in created_sensor

    # READ Tests
    async def test_read_sensor_exists(self, populated_config_manager):
        """Test reading an existing sensor."""
        sensor_key = "solar_inverter_instant_power"

        sensor_config = await populated_config_manager.read_sensor(sensor_key)

        assert sensor_config is not None
        assert sensor_config["name"] == "Solar Inverter Instant Power"
        assert "formula" in sensor_config
        assert "variables" in sensor_config["formula"]
        assert "leg1_power" in sensor_config["formula"]["variables"]

    async def test_read_sensor_not_exists(self, populated_config_manager):
        """Test reading a non-existent sensor."""
        sensor_key = "nonexistent_sensor"

        sensor_config = await populated_config_manager.read_sensor(sensor_key)
        assert sensor_config is None

    async def test_read_sensor_wrong_device(self, populated_config_manager):
        """Test reading a sensor from different sensor set (not applicable in new architecture)."""
        # In the new architecture, each manager is bound to one sensor set
        # So this test is not applicable anymore - skip it
        pass

    async def test_read_sensor_with_attributes(self, populated_config_manager):
        """Test reading a sensor with attributes."""
        sensor_key = "net_energy_flow"  # This sensor has attributes in our unified fixture

        sensor_config = await populated_config_manager.read_sensor(sensor_key)

        assert sensor_config is not None
        assert sensor_config["name"] == "Net Energy Flow"

    # UPDATE Tests
    async def test_update_sensor_exists(self, populated_config_manager, sample_sensor_config):
        """Test updating an existing sensor."""
        sensor_key = "solar_inverter_instant_power"

        # Modify the sample config for update
        updated_config = sample_sensor_config.copy()
        updated_config["name"] = "Updated Solar Inverter Power"
        updated_config["formula"] = "leg1_power + leg2_power + boost"
        updated_config["variables"]["boost"] = "sensor.span_panel_boost_power"

        # Update the sensor
        result = await populated_config_manager.update_sensor(sensor_key, updated_config)
        assert result is True

        # Verify the update
        sensor_config = await populated_config_manager.read_sensor(sensor_key)
        assert sensor_config["name"] == "Updated Solar Inverter Power"
        assert sensor_config["formula"]["formula"] == "leg1_power + leg2_power + boost"
        assert "boost" in sensor_config["formula"]["variables"]

    async def test_update_sensor_not_exists(self, populated_config_manager, sample_sensor_config):
        """Test updating a non-existent sensor."""
        sensor_key = "nonexistent_sensor"

        result = await populated_config_manager.update_sensor(sensor_key, sample_sensor_config)
        assert result is False

    async def test_update_sensor_wrong_device(self, populated_config_manager, sample_sensor_config):
        """Test updating a sensor (device isolation not applicable in new architecture)."""
        # In the new architecture, each manager is bound to one sensor set
        # So this test is not applicable anymore - skip it
        pass

    async def test_update_sensor_attributes(self, populated_config_manager):
        """Test updating sensor attributes."""
        sensor_key = "net_energy_flow"  # This sensor exists in our unified fixture

        # Read current config
        current_config = await populated_config_manager.read_sensor(sensor_key)
        assert current_config is not None

        # Update the sensor with new name
        updated_config = current_config.copy()
        updated_config["name"] = "Updated Net Energy Flow"

        # Apply update
        result = await populated_config_manager.update_sensor(sensor_key, updated_config)
        assert result is True

        # Verify the update
        sensor_config = await populated_config_manager.read_sensor(sensor_key)
        assert sensor_config["name"] == "Updated Net Energy Flow"

    # DELETE Tests
    async def test_delete_sensor_exists(self, populated_config_manager):
        """Test deleting an existing sensor."""
        sensor_key = "solar_inverter_instant_power"

        # Verify sensor exists
        sensor_config = await populated_config_manager.read_sensor(sensor_key)
        assert sensor_config is not None

        # Delete the sensor
        result = await populated_config_manager.delete_sensor(sensor_key)
        assert result is True

        # Verify sensor is gone
        sensor_config = await populated_config_manager.read_sensor(sensor_key)
        assert sensor_config is None

        # Verify it's not in the config
        config = await populated_config_manager.read_config()
        assert sensor_key not in config["sensors"]

    async def test_delete_sensor_not_exists(self, populated_config_manager):
        """Test deleting a non-existent sensor."""
        sensor_key = "nonexistent_sensor"

        result = await populated_config_manager.delete_sensor(sensor_key)
        assert result is False

    async def test_delete_sensor_wrong_device(self, populated_config_manager):
        """Test deleting a sensor (device isolation not applicable in new architecture)."""
        # In the new architecture, each manager is bound to one sensor set
        # So this test is not applicable anymore - skip it
        pass

    # DELETE ALL Tests
    async def test_delete_all_device_sensors(self, populated_config_manager):
        """Test deleting all sensors in the sensor set."""
        # Count initial sensors
        initial_sensors = await populated_config_manager.list_sensors()
        initial_count = len(initial_sensors)
        assert initial_count > 0

        # Delete all sensors in the sensor set
        deleted_count = await populated_config_manager.delete_all_sensors()
        assert deleted_count == initial_count

        # Verify no sensors remain
        remaining_sensors = await populated_config_manager.list_sensors()
        assert len(remaining_sensors) == 0

    async def test_delete_all_device_sensors_no_device(self, populated_config_manager):
        """Test deleting all sensors when sensor set is empty."""
        # First delete all sensors
        await populated_config_manager.delete_all_sensors()

        # Try to delete again - should return 0
        deleted_count = await populated_config_manager.delete_all_sensors()
        assert deleted_count == 0

    async def test_delete_all_sensors_removes_empty_file(
        self, config_manager, sample_sensor_config
    ):
        """Test that deleting all sensors removes the sensor set entirely."""
        sensor_key = "only_sensor"

        # Create a single sensor
        await config_manager.create_sensor(sensor_key, sample_sensor_config)
        assert await config_manager.sensor_set_exists()

        # Delete all sensors
        deleted_count = await config_manager.delete_all_sensors()
        assert deleted_count == 1

        # Verify sensor set is removed when empty
        assert not await config_manager.sensor_set_exists()

    # LIST Tests
    async def test_list_device_sensors(self, populated_config_manager):
        """Test listing all sensors in the sensor set."""
        sensors = await populated_config_manager.list_sensors()

        # Should have multiple sensors
        assert len(sensors) >= 3
        assert "solar_inverter_instant_power" in sensors
        assert "solar_inverter_energy_produced" in sensors
        assert "net_energy_flow" in sensors

        # All sensors should have proper structure
        for sensor_config in sensors.values():
            assert "name" in sensor_config
            assert "entity_id" in sensor_config

    async def test_list_device_sensors_no_device(self, populated_config_manager):
        """Test listing sensors when sensor set is empty."""
        # First delete all sensors
        await populated_config_manager.delete_all_sensors()

        sensors = await populated_config_manager.list_sensors()
        assert len(sensors) == 0

    async def test_list_device_sensors_empty_file(self, config_manager):
        """Test listing sensors when sensor set doesn't exist."""
        sensors = await config_manager.list_sensors()
        assert len(sensors) == 0

    # UTILITY Tests
    async def test_sensor_set_exists_empty_manager(self, config_manager):
        """Test sensor set existence check for empty manager."""
        # New manager should not have sensor set
        assert not await config_manager.sensor_set_exists()

    async def test_sensor_set_exists_populated_manager(self, populated_config_manager):
        """Test sensor set existence check for populated manager."""
        # Populated manager should have sensor set
        assert await populated_config_manager.sensor_set_exists()

    async def test_get_sensor_set_id(self, hass):
        """Test getting sensor set ID."""
        manager = SyntheticConfigManager(
            hass, sensor_set_id="test_sensors", device_identifier="TEST123"
        )
        sensor_set_id = await manager.get_sensor_set_id()
        assert sensor_set_id == "test_sensors"

    async def test_read_config_full(self, populated_config_manager):
        """Test reading the full configuration."""
        config = await populated_config_manager.read_config()

        assert "version" in config
        assert config["version"] == "1.0"
        assert "sensors" in config
        assert len(config["sensors"]) > 0

        # Verify structure of a sensor
        net_energy_flow = config["sensors"]["net_energy_flow"]
        assert "name" in net_energy_flow
        assert "entity_id" in net_energy_flow
        assert "formula" in net_energy_flow

    # CONCURRENCY Tests
    async def test_concurrent_operations(self, config_manager, sample_sensor_config):
        """Test concurrent CRUD operations."""
        # Create multiple sensors concurrently
        tasks = []
        for i in range(5):
            sensor_key = f"concurrent_sensor_{i}"
            config = sample_sensor_config.copy()
            config["name"] = f"Concurrent Sensor {i}"
            config["entity_id"] = f"sensor.concurrent_sensor_{i}"
            tasks.append(config_manager.create_sensor(sensor_key, config))

        await asyncio.gather(*tasks)

        # Verify all sensors were created
        sensors = await config_manager.list_sensors()
        assert len(sensors) == 5

        # Test concurrent reads
        read_tasks = [config_manager.read_sensor(f"concurrent_sensor_{i}") for i in range(5)]
        results = await asyncio.gather(*read_tasks)
        assert all(result is not None for result in results)

        # Test concurrent deletes
        delete_tasks = [config_manager.delete_sensor(f"concurrent_sensor_{i}") for i in range(5)]
        delete_results = await asyncio.gather(*delete_tasks)
        assert all(result is True for result in delete_results)

        # Verify all sensors are gone
        sensors = await config_manager.list_sensors()
        assert len(sensors) == 0

    # ERROR HANDLING Tests
    async def test_invalid_yaml_handling(self, hass):
        """Test handling of storage errors."""
        manager = SyntheticConfigManager(
            hass, sensor_set_id="test_sensors", device_identifier="TEST123"
        )

        # Should return default config when sensor set doesn't exist
        config = await manager.read_config()
        assert config == {"version": "1.0", "sensors": {}}

    async def test_file_permission_handling(self, hass):
        """Test handling of sensor creation errors."""
        manager = SyntheticConfigManager(
            hass, sensor_set_id="test_sensors", device_identifier="TEST123"
        )

        # Try to create a sensor with empty configuration
        with pytest.raises(ValueError, match="missing required field 'name'"):
            await manager.create_sensor("test_sensor", {})

        # Try to create a sensor missing entity_id
        with pytest.raises(ValueError, match="missing required field 'entity_id'"):
            await manager.create_sensor("test_sensor", {"name": "Test Sensor"})

        # Try to create a sensor missing formula
        with pytest.raises(ValueError, match="missing required field 'formula'"):
            await manager.create_sensor(
                "test_sensor", {"name": "Test Sensor", "entity_id": "sensor.test_sensor"}
            )

        # Try to create a sensor with invalid formula type
        with pytest.raises(TypeError, match="has invalid formula type"):
            await manager.create_sensor(
                "test_sensor",
                {
                    "name": "Test Sensor",
                    "entity_id": "sensor.test_sensor",
                    "formula": 123,  # Invalid type
                },
            )
