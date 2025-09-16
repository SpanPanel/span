"""Tests for entity_id_naming_patterns module.

This module tests the entity ID migration functionality when naming patterns change.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.span_panel.entity_id_naming_patterns import EntityIdMigrationManager
from custom_components.span_panel.const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.span_panel import SpanPanel


@pytest.fixture
def mock_sensor_manager():
    """Create a mock sensor manager."""
    manager = MagicMock()
    manager.export = AsyncMock()
    manager.modify = AsyncMock()
    manager.import_data = AsyncMock()
    return manager


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock(spec=SpanPanelCoordinator)
    coordinator.config_entry = MagicMock(spec=ConfigEntry)
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {"device_name": "SPAN Panel"}
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.data = MagicMock()  # Add missing data attribute
    return coordinator


@pytest.fixture
def sample_sensors_data():
    """Create sample sensor data for testing."""
    return {
        "test_sensor": {
            "entity_id": "sensor.test_entity",
            "name": "Test Sensor",
            "tabs": [3, 18]
        },
        "another_sensor": {
            "entity_id": "sensor.another_entity",
            "name": "Another Sensor"
        }
    }


class TestEntityIdMigrationManager:
    """Test the EntityIdMigrationManager class."""



    @pytest.fixture
    def migration_manager(self, hass):
        """Create a migration manager instance."""
        return EntityIdMigrationManager(hass, "test_entry_id")

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.options = {}
        coordinator.config_entry.data = {"device_name": "SPAN Panel"}
        coordinator.config_entry.title = "SPAN Panel"
        coordinator.data = MagicMock(spec=SpanPanel)
        return coordinator

    @pytest.fixture
    def mock_sensor_manager(self):
        """Create a mock sensor manager."""
        manager = MagicMock()
        manager.export = AsyncMock()
        manager.modify = AsyncMock()
        return manager

    @pytest.fixture
    def sample_sensors_data(self):
        """Create sample sensor data for testing."""
        return {
            "version": "1.0",
            "global_settings": {"device_identifier": "test_panel"},
            "sensors": {
                "solar_power": {
                    "entity_id": "sensor.solar_power",
                    "name": "Solar Power",
                    "formula": "leg1_power + leg2_power",
                    "variables": {
                        "leg1_power": "sensor.unmapped_tab_30_power",
                        "leg2_power": "sensor.unmapped_tab_32_power"
                    },
                    "attributes": {
                        "tabs": "tabs [30:32]",
                        "voltage": 240
                    }
                },
                "kitchen_lights_power": {
                    "entity_id": "sensor.kitchen_lights_power",
                    "name": "Kitchen Lights Power",
                    "formula": "state",
                    "attributes": {
                        "tabs": "tabs [3]",
                        "voltage": 120
                    }
                },
                "current_power": {
                    "entity_id": "sensor.current_power",
                    "name": "Current Power",
                    "formula": "state",
                    "attributes": {
                        "tabs": "panel",
                        "voltage": 240
                    }
                }
            }
        }

    def test_init(self, hass):
        """Test migration manager initialization."""
        manager = EntityIdMigrationManager(hass, "test_entry_id")

        assert manager.hass == hass
        assert manager.config_entry_id == "test_entry_id"

    async def test_migrate_synthetic_entities_legacy_migration(self, migration_manager):
        """Test synthetic entity migration for legacy to prefix pattern."""
        old_flags = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}

        with patch.object(migration_manager, '_migrate_legacy_to_prefix', return_value=True) as mock_legacy:
            result = await migration_manager.migrate_synthetic_entities(old_flags, new_flags)

            assert result is True
            mock_legacy.assert_called_once_with(old_flags, new_flags)

    async def test_migrate_synthetic_entities_non_legacy_migration(self, migration_manager):
        """Test synthetic entity migration for non-legacy patterns."""
        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        with patch.object(migration_manager, '_migrate_non_legacy_patterns', return_value=True) as mock_non_legacy:
            result = await migration_manager.migrate_synthetic_entities(old_flags, new_flags)

            assert result is True
            mock_non_legacy.assert_called_once_with(old_flags, new_flags)


class TestLegacyToPrefix:
    """Test legacy to prefix migration."""

    @pytest.fixture
    def migration_manager(self, hass):
        """Create a migration manager instance."""
        return EntityIdMigrationManager(hass, "test_entry_id")

    @pytest.fixture
    def hass_with_data(self, hass, mock_sensor_manager, mock_coordinator, sample_sensors_data):
        """Create hass with complete data setup."""
        mock_sensor_manager.export.return_value = {"sensors": sample_sensors_data}
        mock_sensor_manager.sensors = sample_sensors_data

        hass.data = {
            "ha_synthetic_sensors": {
                "sensor_managers": {
                    "test_entry_id": mock_sensor_manager
                }
            },
            "span_panel": {
                "test_entry_id": {
                    "coordinator": mock_coordinator
                }
            }
        }
        return hass

    async def test_migrate_legacy_to_prefix_success(self, hass_with_data, sample_sensors_data):
        """Test successful legacy to prefix migration."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        old_flags = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}

        # Mock entity registry with legacy entities
        mock_entity1 = MagicMock()
        mock_entity1.config_entry_id = "test_entry_id"
        mock_entity1.entity_id = "sensor.air_conditioner_energy_consumed"
        mock_entity2 = MagicMock()
        mock_entity2.config_entry_id = "test_entry_id"
        mock_entity2.entity_id = "sensor.kitchen_lights_power"

        mock_entities = [mock_entity1, mock_entity2]

        # Mock the entity registry
        mock_registry = MagicMock()
        mock_registry.async_update_entity = MagicMock()

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_registry):
            with patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=mock_entities):
                result = await manager._migrate_legacy_to_prefix(old_flags, new_flags)

                assert result is True
                # Verify that entities were updated
                assert mock_registry.async_update_entity.call_count == 2

    async def test_migrate_legacy_to_prefix_no_sensor_manager(self, hass):
        """Test legacy migration when sensor manager is not found."""
        hass.data = {
            "ha_synthetic_sensors": {
                "sensor_managers": {
                    "test_entry_id": None
                }
            }
        }
        manager = EntityIdMigrationManager(hass, "test_entry_id")

        old_flags = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}

        result = await manager._migrate_legacy_to_prefix(old_flags, new_flags)

        assert result is False

    async def test_migrate_legacy_to_prefix_no_sensors(self, hass_with_data):
        """Test legacy migration with no sensors to migrate."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        old_flags = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}

        # Mock entity registry with no entities
        mock_registry = MagicMock()
        mock_registry.async_update_entity = MagicMock()

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_registry):
            with patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]):
                result = await manager._migrate_legacy_to_prefix(old_flags, new_flags)

                assert result is True
                # Verify that no entities were updated
                mock_registry.async_update_entity.assert_not_called()

    async def test_migrate_legacy_to_prefix_exception(self, hass_with_data):
        """Test legacy migration with exception during process."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        old_flags = {USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}

        # Mock entity registry to raise an exception
        with patch("homeassistant.helpers.entity_registry.async_get", side_effect=Exception("Test error")):
            result = await manager._migrate_legacy_to_prefix(old_flags, new_flags)

            assert result is False


class TestNonLegacyPatterns:
    """Test non-legacy pattern migration."""

    @pytest.fixture
    def migration_manager(self, hass):
        """Create a migration manager instance."""
        return EntityIdMigrationManager(hass, "test_entry_id")

    @pytest.fixture
    def hass_with_data(self, hass, mock_sensor_manager, mock_coordinator, sample_sensors_data):
        """Create hass with complete data setup."""
        mock_sensor_manager.export.return_value = {"sensors": sample_sensors_data}
        mock_sensor_manager.sensors = sample_sensors_data

        hass.data = {
            "ha_synthetic_sensors": {
                "sensor_managers": {
                    "test_entry_id": mock_sensor_manager
                }
            },
            "span_panel": {
                "test_entry_id": {
                    "coordinator": mock_coordinator
                }
            }
        }
        return hass

    async def test_migrate_non_legacy_patterns_success(self, hass_with_data, sample_sensors_data):
        """Test successful non-legacy pattern migration."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        with patch('custom_components.span_panel.entity_id_naming_patterns.is_panel_level_sensor_key') as mock_panel_check:
            with patch.object(manager, '_is_entity_id_customized', new_callable=AsyncMock, return_value=False) as mock_customized:
                with patch.object(manager, '_generate_new_entity_id') as mock_generate:
                    mock_panel_check.side_effect = [False, False, True]  # Only current_power is panel-level
                    mock_generate.side_effect = [
                        "sensor.span_panel_circuit_30_32_power",
                        "sensor.span_panel_circuit_3_power"
                    ]

                    result = await manager._migrate_non_legacy_patterns(old_flags, new_flags)

                    assert result is True
                    sensor_manager = hass_with_data.data["ha_synthetic_sensors"]["sensor_managers"]["test_entry_id"]
                    sensor_manager.modify.assert_called_once()

    async def test_migrate_non_legacy_patterns_skip_panel_level(self, hass_with_data):
        """Test non-legacy migration skips panel-level sensors."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        with patch('custom_components.span_panel.entity_id_naming_patterns.is_panel_level_sensor_key', return_value=True):
            with patch.object(manager, '_generate_new_entity_id') as mock_generate:
                result = await manager._migrate_non_legacy_patterns(old_flags, new_flags)

                assert result is True
                # Should not call generate_new_entity_id for panel-level sensors
                mock_generate.assert_not_called()

    async def test_migrate_non_legacy_patterns_skip_customized(self, hass_with_data):
        """Test non-legacy migration skips customized entity IDs."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        with patch('custom_components.span_panel.entity_id_naming_patterns.is_panel_level_sensor_key', return_value=False):
            with patch.object(manager, '_is_entity_id_customized', return_value=True):
                with patch.object(manager, '_generate_new_entity_id') as mock_generate:
                    result = await manager._migrate_non_legacy_patterns(old_flags, new_flags)

                    assert result is True
                    # Should not call generate_new_entity_id for customized entities
                    mock_generate.assert_not_called()

    async def test_migrate_non_legacy_patterns_no_sensor_manager(self, hass):
        """Test non-legacy migration when sensor manager is not found."""
        hass.data = {
            "ha_synthetic_sensors": {
                "sensor_managers": {
                    "test_entry_id": None
                }
            }
        }
        manager = EntityIdMigrationManager(hass, "test_entry_id")

        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        result = await manager._migrate_non_legacy_patterns(old_flags, new_flags)

        assert result is False

    async def test_migrate_non_legacy_patterns_exception(self, hass_with_data):
        """Test non-legacy migration with exception during process."""
        manager = EntityIdMigrationManager(hass_with_data, "test_entry_id")
        sensor_manager = hass_with_data.data["ha_synthetic_sensors"]["sensor_managers"]["test_entry_id"]
        # Provide valid sensor data but cause an exception during processing
        sensor_manager.sensors = {"test_sensor": {"entity_id": "sensor.test"}}

        old_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
        new_flags = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

        # Mock the helper functions to cause an exception during processing
        with patch('custom_components.span_panel.entity_id_naming_patterns.is_panel_level_sensor_key', return_value=False):
            with patch.object(manager, '_is_entity_id_customized', side_effect=Exception("Test error")):
                result = await manager._migrate_non_legacy_patterns(old_flags, new_flags)

                assert result is False


class TestGenerateNewEntityId:
    """Test new entity ID generation."""

    @pytest.fixture
    def migration_manager(self, hass):
        """Create a migration manager instance."""
        return EntityIdMigrationManager(hass, "test_entry_id")

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.options = {}
        return coordinator

    @pytest.fixture
    def mock_span_panel(self):
        """Create a mock span panel."""
        return MagicMock(spec=SpanPanel)

    @pytest.fixture
    def sensor_config_with_tabs(self):
        """Create sensor config with tabs attribute."""
        return {
            "entity_id": "sensor.test_sensor",
            "name": "Test Sensor",
            "attributes": {
                "tabs": "tabs [3]",
                "voltage": 120
            }
        }

    @pytest.fixture
    def sensor_config_without_tabs(self):
        """Create sensor config without tabs attribute."""
        return {
            "entity_id": "sensor.test_sensor",
            "name": "Test Sensor",
            "attributes": {
                "voltage": 240
            }
        }

    async def test_generate_new_entity_id_with_tabs_and_circuit_numbers(self, migration_manager,
                                                                       mock_coordinator, mock_span_panel,
                                                                       sensor_config_with_tabs):
        """Test entity ID generation with tabs attribute and circuit numbers enabled."""
        flags = {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True}

        with patch('custom_components.span_panel.entity_id_naming_patterns.parse_tabs_attribute', return_value=[3]):
            with patch('custom_components.span_panel.entity_id_naming_patterns.construct_multi_tab_entity_id_from_key',
                      return_value="sensor.span_panel_circuit_3_power") as mock_construct:

                result = await migration_manager._generate_new_entity_id(
                    "test_sensor", sensor_config_with_tabs, mock_coordinator, mock_span_panel, flags
                )

                assert result == "sensor.span_panel_circuit_3_power"
                mock_construct.assert_called_once()

    async def test_generate_new_entity_id_without_tabs(self, migration_manager, mock_coordinator,
                                                      mock_span_panel, sensor_config_without_tabs):
        """Test entity ID generation without tabs attribute."""
        flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        with patch('custom_components.span_panel.entity_id_naming_patterns.construct_multi_tab_entity_id_from_key',
                  return_value="sensor.span_panel_test_sensor") as mock_construct:

            result = await migration_manager._generate_new_entity_id(
                "test_sensor", sensor_config_without_tabs, mock_coordinator, mock_span_panel, flags
            )

            assert result == "sensor.span_panel_test_sensor"
            mock_construct.assert_called_once()

    async def test_generate_new_entity_id_preserves_original_flags(self, migration_manager, mock_coordinator,
                                                                  mock_span_panel, sensor_config_without_tabs):
        """Test that original flags are preserved after entity ID generation."""
        original_flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: False}
        new_flags = {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True}

        mock_coordinator.config_entry.options = original_flags.copy()

        with patch('custom_components.span_panel.entity_id_naming_patterns.construct_multi_tab_entity_id_from_key',
                  return_value="sensor.test_result"):

            await migration_manager._generate_new_entity_id(
                "test_sensor", sensor_config_without_tabs, mock_coordinator, mock_span_panel, new_flags
            )

            # Verify original flags are restored
            assert mock_coordinator.config_entry.options == original_flags

    async def test_generate_new_entity_id_with_tabs_no_circuit_numbers(self, migration_manager,
                                                                      mock_coordinator, mock_span_panel,
                                                                      sensor_config_with_tabs):
        """Test entity ID generation with tabs but circuit numbers disabled."""
        flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        with patch('custom_components.span_panel.entity_id_naming_patterns.construct_multi_tab_entity_id_from_key',
                  return_value="sensor.span_panel_test_sensor") as mock_construct:

            result = await migration_manager._generate_new_entity_id(
                "test_sensor", sensor_config_with_tabs, mock_coordinator, mock_span_panel, flags
            )

            assert result == "sensor.span_panel_test_sensor"
            mock_construct.assert_called_once()


class TestEntityIdCustomization:
    """Test entity ID customization detection."""

    @pytest.fixture
    def migration_manager(self, hass):
        """Create a migration manager instance."""
        return EntityIdMigrationManager(hass, "test_entry_id")

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        return MagicMock(spec=SpanPanelCoordinator)

    @pytest.fixture
    def mock_span_panel(self):
        """Create a mock span panel."""
        return MagicMock(spec=SpanPanel)

    async def test_is_entity_id_customized_true(self, migration_manager, mock_coordinator, mock_span_panel):
        """Test detection of customized entity ID."""
        sensor_config = {"entity_id": "sensor.my_custom_name"}
        old_flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        with patch.object(migration_manager, '_generate_new_entity_id', return_value="sensor.expected_name"):
            result = await migration_manager._is_entity_id_customized(
                "test_sensor", sensor_config, mock_coordinator, mock_span_panel, old_flags
            )

            assert result is True

    async def test_is_entity_id_customized_false(self, migration_manager, mock_coordinator, mock_span_panel):
        """Test detection of non-customized entity ID."""
        sensor_config = {"entity_id": "sensor.expected_name"}
        old_flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        with patch.object(migration_manager, '_generate_new_entity_id', return_value="sensor.expected_name"):
            result = await migration_manager._is_entity_id_customized(
                "test_sensor", sensor_config, mock_coordinator, mock_span_panel, old_flags
            )

            assert result is False

    async def test_is_entity_id_customized_no_entity_id(self, migration_manager, mock_coordinator, mock_span_panel):
        """Test customization check with missing entity ID."""
        sensor_config = {}
        old_flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        result = await migration_manager._is_entity_id_customized(
            "test_sensor", sensor_config, mock_coordinator, mock_span_panel, old_flags
        )

        assert result is False

    async def test_is_entity_id_customized_generation_failed(self, migration_manager, mock_coordinator, mock_span_panel):
        """Test customization check when entity ID generation fails."""
        sensor_config = {"entity_id": "sensor.test_name"}
        old_flags = {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}

        with patch.object(migration_manager, '_generate_new_entity_id', return_value=None):
            result = await migration_manager._is_entity_id_customized(
                "test_sensor", sensor_config, mock_coordinator, mock_span_panel, old_flags
            )

            assert result is False
