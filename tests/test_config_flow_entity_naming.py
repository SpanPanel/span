"""Test entity naming migration with real synthetic sensors package and YAML fixtures."""

from pathlib import Path

import pytest
import yaml

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)


def load_yaml_fixture(fixture_name: str) -> dict:
    """Load a YAML fixture from the tests/fixtures directory."""
    fixture_path = Path(__file__).parent / "fixtures" / f"{fixture_name}.yaml"
    with open(fixture_path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry for testing."""

    class MockConfigEntry:
        def __init__(self):
            self.entry_id = "test_entry_id"
            self.domain = "span_panel"
            self.options = {
                USE_CIRCUIT_NUMBERS: False,
                USE_DEVICE_PREFIX: False,
                "enable_solar_circuit": True,
                "leg1": 30,
                "leg2": 32,
            }

    return MockConfigEntry()


class TestEntityNamingMigrationWithRealSyntheticSensors:
    """Test entity naming migration using real synthetic sensors package with YAML fixtures."""

    @pytest.mark.asyncio
    async def test_legacy_to_device_prefix_migration(
        self, hass, mock_ha_storage, synthetic_storage_manager, mock_synthetic_sensor_manager, mock_config_entry
    ):
        """Test migration from legacy (no prefix) to device prefix naming pattern."""
        # Set flags for legacy (no prefix) configuration
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: False,  # Legacy: no device prefix
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
        }

        # Load initial YAML fixture and set up initial state in storage
        initial_config = load_yaml_fixture("migration_legacy_no_prefix")

        # Access the storage manager directly (since we created it in conftest.py)
        # We need to work with the storage manager and sensor set directly for YAML import/export
        sensor_set_id = f"{mock_config_entry.entry_id}_test_sensors"

        # Create sensor set if it doesn't exist
        if not synthetic_storage_manager.sensor_set_exists(sensor_set_id):
            await synthetic_storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier="test_device_123",
                name="Test Migration Sensors"
            )

        # Get the sensor set and import the initial YAML
        sensor_set = synthetic_storage_manager.get_sensor_set(sensor_set_id)
        import yaml
        initial_yaml_str = yaml.dump(initial_config, default_flow_style=False)
        print("\n🔍 DEBUG: Initial YAML before migration:")
        print("=" * 60)
        print(initial_yaml_str)
        print("=" * 60)
        await sensor_set.async_import_yaml(initial_yaml_str)

        # Create real coordinator with the integration's actual implementation
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from tests.common import create_mock_span_panel_with_data
        span_panel = create_mock_span_panel_with_data()
        coordinator = SpanPanelCoordinator(hass, span_panel, mock_config_entry)

        # Set the data attribute manually for testing (normally set by framework)
        coordinator.data = span_panel

        # Debug: Print the coordinator's config_entry
        print(f"Mock config entry: {mock_config_entry}")
        print(f"Mock config entry options: {getattr(mock_config_entry, 'options', 'No options')}")
        print(f"Coordinator config_entry: {coordinator.config_entry}")
        print(f"Config entry options: {getattr(coordinator.config_entry, 'options', 'No options')}")

        # Set up the sensor manager in hass.data so migration can find it
        if "ha_synthetic_sensors" not in hass.data:
            hass.data["ha_synthetic_sensors"] = {}
        if "sensor_managers" not in hass.data["ha_synthetic_sensors"]:
            hass.data["ha_synthetic_sensors"]["sensor_managers"] = {}

        # Create a mock sensor manager that wraps our real sensor set
        class MockSensorManagerForMigration:
            def __init__(self, sensor_set):
                self.sensor_set = sensor_set

            async def export(self):
                # Export sensors in the format expected by migration
                yaml_data = self.sensor_set.export_yaml()
                export_result = yaml.safe_load(yaml_data)
                print("\n🔍 DEBUG: MockSensorManagerForMigration.export() called:")
                print("Export result:", export_result)
                return export_result

            async def modify(self, new_config):
                # Import the modified configuration
                print("\n🔍 DEBUG: MockSensorManagerForMigration.modify() called with:")
                print("New config:", new_config)
                yaml_data = yaml.dump(new_config, default_flow_style=False)
                print("YAML data to import:")
                print(yaml_data)
                await self.sensor_set.async_import_yaml(yaml_data)

        migration_sensor_manager = MockSensorManagerForMigration(sensor_set)
        hass.data["ha_synthetic_sensors"]["sensor_managers"][mock_config_entry.entry_id] = migration_sensor_manager

        # Register coordinator in hass.data for migration to find
        if "span_panel" not in hass.data:
            hass.data["span_panel"] = {}
        hass.data["span_panel"][mock_config_entry.entry_id] = coordinator

        # Perform real migration using the actual implementation
        old_flags = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}

        success = await coordinator.migrate_synthetic_entities(old_flags, new_flags)
        assert success, "Migration should succeed"

        # Export final state and compare with expected fixture
        final_yaml_str = await sensor_set.async_export_yaml()
        print("\n🔍 DEBUG: Final YAML after migration:")
        print("=" * 60)
        print(final_yaml_str)
        print("=" * 60)
        final_yaml = yaml.safe_load(final_yaml_str)

        # Compare the final migrated result with expected fixture
        expected_config = load_yaml_fixture("migration_after_legacy_to_prefix")

        # Remove version field and device_identifier for comparison since fixture doesn't have them
        if 'version' in final_yaml:
            del final_yaml['version']
        if 'sensors' in final_yaml:
            for sensor_data in final_yaml['sensors'].values():
                if 'device_identifier' in sensor_data:
                    del sensor_data['device_identifier']

        assert final_yaml == expected_config, "Migration results should match expected fixture"

        # Check that device prefix was added to at least some entities
        migrated_entities = final_yaml['sensors']
        device_prefix_count = sum(1 for sensor in migrated_entities.values()
                                if sensor.get('entity_id', '').startswith('sensor.span_panel_'))
        assert device_prefix_count >= 3, f"Expected device prefix on most entities, got {device_prefix_count}"

        print("✅ Legacy to device prefix migration completed successfully")

    @pytest.mark.asyncio
    async def test_friendly_names_to_circuit_numbers_migration(
        self, hass, mock_ha_storage, synthetic_storage_manager, mock_synthetic_sensor_manager, mock_config_entry
    ):
        """Test migration from friendly names to circuit numbers."""
        # Set flags for device prefix + friendly names configuration (starting state)
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,  # Currently using friendly names
            USE_DEVICE_PREFIX: True,     # Already using device prefix
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
        }

        # Start with device prefix + friendly names configuration
        initial_config = load_yaml_fixture("migration_after_legacy_to_prefix")

        # Access storage manager and sensor set directly
        sensor_set_id = f"{mock_config_entry.entry_id}_test_sensors"

        # Create sensor set if it doesn't exist
        if not synthetic_storage_manager.sensor_set_exists(sensor_set_id):
            await synthetic_storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier="test_device_123",
                name="Test Migration Sensors"
            )

        # Get the sensor set and import the initial YAML
        sensor_set = synthetic_storage_manager.get_sensor_set(sensor_set_id)
        import yaml
        initial_yaml_str = yaml.dump(initial_config, default_flow_style=False)
        await sensor_set.async_import_yaml(initial_yaml_str)

        # Set up config entry for friendly names to circuit numbers migration
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: False,  # Currently using friendly names
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
        }

        # Get the coordinator for migration
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from tests.common import create_mock_span_panel_with_data
        span_panel = create_mock_span_panel_with_data()

        coordinator = SpanPanelCoordinator(hass, span_panel, mock_config_entry)

        # Perform migration from friendly names to circuit numbers
        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        success = await coordinator.migrate_synthetic_entities(old_flags, new_flags)
        assert success, "Migration should succeed"

        # Export final state and verify migration results
        final_yaml_str = await sensor_set.async_export_yaml()
        final_yaml = yaml.safe_load(final_yaml_str)
        expected_config = load_yaml_fixture("migration_circuit_numbers")

        # Check expected results match fixture
        assert final_yaml == expected_config, "Migration results should match expected fixture"

        # Additional specific checks from the exported YAML
        final_sensors = final_yaml["sensors"]

        # Panel sensor should remain unchanged (panel sensors excluded from circuit migration)
        panel_sensor = final_sensors["panel_instant_power"]
        assert panel_sensor["entity_id"] == "sensor.span_panel_panel_instant_power"

        # Circuit 1 should change to circuit number pattern
        circuit_sensor = final_sensors["circuit_1_instant_power"]
        expected_circuit = expected_config["sensors"]["circuit_1_instant_power"]
        assert circuit_sensor["entity_id"] == expected_circuit["entity_id"]

        # Circuit 5 was customized - should NOT be migrated
        customized_sensor = final_sensors["circuit_5_instant_power"]
        assert customized_sensor["entity_id"] == "sensor.custom_kitchen_power"  # Unchanged

        # Solar sensor should change to circuit number pattern
        solar_sensor = final_sensors["solar_inverter_instant_power"]
        expected_solar = expected_config["sensors"]["solar_inverter_instant_power"]
        assert solar_sensor["entity_id"] == expected_solar["entity_id"]

        # Verify all user customizations are preserved
        assert circuit_sensor["attributes"]["custom_alert_threshold"] == 2000
        assert solar_sensor["attributes"]["solar_panel_count"] == 24

    @pytest.mark.asyncio
    async def test_no_migration_when_patterns_unchanged(
        self, hass, mock_ha_storage, synthetic_storage_manager, mock_synthetic_sensor_manager, mock_config_entry
    ):
        """Test that no migration occurs when naming patterns don't change."""
        # Start with any configuration
        initial_config = load_yaml_fixture("migration_circuit_numbers")

        # Access storage manager and sensor set directly
        sensor_set_id = f"{mock_config_entry.entry_id}_test_sensors"

        # Create sensor set if it doesn't exist
        if not synthetic_storage_manager.sensor_set_exists(sensor_set_id):
            await synthetic_storage_manager.async_create_sensor_set(
                sensor_set_id=sensor_set_id,
                device_identifier="test_device_123",
                name="Test Migration Sensors"
            )

        # Get the sensor set and import the initial YAML
        sensor_set = synthetic_storage_manager.get_sensor_set(sensor_set_id)
        import yaml
        initial_yaml_str = yaml.dump(initial_config, default_flow_style=False)
        await sensor_set.async_import_yaml(initial_yaml_str)

        # Store initial state for comparison
        initial_yaml_export = await sensor_set.async_export_yaml()
        initial_yaml_dict = yaml.safe_load(initial_yaml_export)

        # Set up config entry - same pattern as initial
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }

        # Get the coordinator for migration
        from custom_components.span_panel.coordinator import SpanPanelCoordinator
        from tests.common import create_mock_span_panel_with_data
        span_panel = create_mock_span_panel_with_data()

        coordinator = SpanPanelCoordinator(hass, span_panel, mock_config_entry)

        # Attempt migration with same flags (no change)
        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        success = await coordinator.migrate_synthetic_entities(old_flags, new_flags)
        assert success, "Migration should succeed but do nothing"

        # Verify nothing changed
        final_yaml_export = await sensor_set.async_export_yaml()
        final_yaml_dict = yaml.safe_load(final_yaml_export)

        # All configuration should be identical
        assert initial_yaml_dict == final_yaml_dict
