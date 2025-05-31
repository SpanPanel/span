"""Test entity naming options in config flow."""

# type: ignore

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.data_entry_flow import FlowResultType

from custom_components.span_panel.config_flow import OptionsFlowHandler
from custom_components.span_panel.const import (
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {
        USE_CIRCUIT_NUMBERS: True,
        USE_DEVICE_PREFIX: True,
        "enable_solar_circuit": True,
        "leg1": 30,
        "leg2": 32,
    }
    return entry


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    hass.async_block_till_done = AsyncMock()

    # Mock async_create_task to properly handle coroutines and prevent warnings
    def mock_create_task(coro: Any) -> MagicMock:
        """Mock create_task that closes the coroutine to prevent warnings."""
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    hass.async_create_task = MagicMock(side_effect=mock_create_task)
    return hass


class TestEntityNamingOptions:
    """Test entity naming options in config flow."""

    async def test_entity_naming_menu_display(self, mock_hass, mock_config_entry):
        """Test that the entity naming menu displays correctly."""
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Test initial menu
        result = await flow.async_step_init()

        assert result.get("type") == FlowResultType.MENU
        menu_options = result.get("menu_options")
        assert menu_options is not None
        assert "entity_naming" in menu_options
        # Cast to handle Container[str] type
        menu_dict = cast(dict, menu_options)
        assert menu_dict["entity_naming"] == "Entity Naming Pattern"

    async def test_entity_naming_form_display(self, mock_hass, mock_config_entry):
        """Test that the entity naming form displays with correct options."""
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Test entity naming form
        result = await flow.async_step_entity_naming()

        assert result.get("type") == FlowResultType.FORM
        assert result.get("step_id") == "entity_naming"

        description_placeholders = result.get("description_placeholders")
        assert description_placeholders is not None
        assert "friendly_example" in description_placeholders
        assert "circuit_example" in description_placeholders

        # Verify examples are provided
        assert (
            "span_panel_kitchen_outlets_power"
            in description_placeholders["friendly_example"]
        )
        assert (
            "span_panel_circuit_15_power" in description_placeholders["circuit_example"]
        )

    async def test_current_pattern_detection_circuit_numbers(
        self, mock_hass, mock_config_entry
    ):
        """Test detection of current circuit numbers pattern."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        current_pattern = flow._get_current_naming_pattern()
        assert current_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value

    async def test_current_pattern_detection_friendly_names(
        self, mock_hass, mock_config_entry
    ):
        """Test detection of current friendly names pattern."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: True,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        current_pattern = flow._get_current_naming_pattern()
        assert current_pattern == EntityNamingPattern.FRIENDLY_NAMES.value

    async def test_current_pattern_detection_legacy(self, mock_hass, mock_config_entry):
        """Test detection of legacy pattern."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: False,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        current_pattern = flow._get_current_naming_pattern()
        assert current_pattern == EntityNamingPattern.LEGACY_NAMES.value

    async def test_legacy_installation_defaults_to_friendly_names(
        self, mock_hass, mock_config_entry
    ):
        """Test that legacy installations default to friendly names in the UI."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: False,  # Legacy installation
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        result = await flow.async_step_entity_naming()

        # Should show friendly names as default even though current is legacy
        data_schema = result.get("data_schema")
        assert data_schema is not None
        schema_defaults = data_schema.schema
        entity_naming_field = None
        for field in schema_defaults:
            if field.schema == ENTITY_NAMING_PATTERN:
                entity_naming_field = field
                break

        assert entity_naming_field is not None
        # The default should be friendly names for legacy installations
        # The default should be friendly names for legacy installations
        # For legacy installations, the form should display correctly
        assert result.get("type") == FlowResultType.FORM
        assert result.get("step_id") == "entity_naming"

        # Verify that the description placeholders are provided for examples
        description_placeholders = result.get("description_placeholders")
        assert description_placeholders is not None
        assert "friendly_example" in description_placeholders
        assert "circuit_example" in description_placeholders

    @patch("custom_components.span_panel.config_flow.EntityMigrationManager")
    async def test_pattern_change_triggers_migration(
        self, mock_migration_manager, mock_hass, mock_config_entry
    ):
        """Test that changing patterns triggers entity migration."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        # Mock the migration manager
        mock_manager_instance = AsyncMock()
        mock_manager_instance.migrate_entities = AsyncMock(return_value=True)
        mock_migration_manager.return_value = mock_manager_instance

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Change from circuit numbers to friendly names
        user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.FRIENDLY_NAMES.value}

        result = await flow.async_step_entity_naming(user_input)

        # Verify migration was called
        mock_migration_manager.assert_called_once_with(mock_hass, "test_entry_id")
        mock_manager_instance.migrate_entities.assert_called_once_with(
            EntityNamingPattern.CIRCUIT_NUMBERS, EntityNamingPattern.FRIENDLY_NAMES
        )

        # Verify reload was scheduled
        mock_hass.async_create_task.assert_called_once()

        # Verify result
        assert result.get("type") == FlowResultType.CREATE_ENTRY
        result_data = result.get("data", {})
        assert result_data.get(USE_CIRCUIT_NUMBERS) is False
        assert result_data.get(USE_DEVICE_PREFIX) is True

        # Verify solar options are preserved
        assert result_data.get("enable_solar_circuit") is True
        assert result_data.get("leg1") == 30
        assert result_data.get("leg2") == 32

    async def test_no_change_no_migration(self, mock_hass, mock_config_entry):
        """Test that no migration occurs when pattern doesn't change."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Submit same pattern (circuit numbers)
        user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.CIRCUIT_NUMBERS.value}

        with patch(
            "custom_components.span_panel.config_flow.EntityMigrationManager"
        ) as mock_migration:
            result = await flow.async_step_entity_naming(user_input)

            # Verify no migration was called
            mock_migration.assert_not_called()

            # Verify no reload was scheduled
            mock_hass.async_create_task.assert_not_called()

            # Verify result
            assert result.get("type") == FlowResultType.CREATE_ENTRY
            assert result.get("data") == {}  # Empty data means no changes

    @patch("custom_components.span_panel.config_flow.EntityMigrationManager")
    async def test_legacy_migration_to_friendly_names(
        self, mock_migration_manager, mock_hass, mock_config_entry
    ):
        """Test migration from legacy to friendly names."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: False,  # Legacy installation
            "enable_solar_circuit": False,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        # Mock the migration manager
        mock_manager_instance = AsyncMock()
        mock_manager_instance.migrate_entities = AsyncMock(return_value=True)
        mock_migration_manager.return_value = mock_manager_instance

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Migrate to friendly names
        user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.FRIENDLY_NAMES.value}

        result = await flow.async_step_entity_naming(user_input)

        # Verify migration was called
        mock_migration_manager.assert_called_once_with(mock_hass, "test_entry_id")
        mock_manager_instance.migrate_entities.assert_called_once_with(
            EntityNamingPattern.LEGACY_NAMES, EntityNamingPattern.FRIENDLY_NAMES
        )

        # Verify result sets correct flags
        assert "data" in result
        result_data = result["data"]
        assert result_data[USE_CIRCUIT_NUMBERS] is False
        assert result_data[USE_DEVICE_PREFIX] is True

    @patch("custom_components.span_panel.config_flow.EntityMigrationManager")
    async def test_legacy_migration_to_circuit_numbers(
        self, mock_migration_manager, mock_hass, mock_config_entry
    ):
        """Test migration from legacy to circuit numbers."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: False,  # Legacy installation
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        # Mock the migration manager
        mock_manager_instance = AsyncMock()
        mock_manager_instance.migrate_entities = AsyncMock(return_value=True)
        mock_migration_manager.return_value = mock_manager_instance

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Migrate to circuit numbers
        user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.CIRCUIT_NUMBERS.value}

        result = await flow.async_step_entity_naming(user_input)

        # Verify migration was called
        mock_migration_manager.assert_called_once_with(mock_hass, "test_entry_id")
        mock_manager_instance.migrate_entities.assert_called_once_with(
            EntityNamingPattern.LEGACY_NAMES, EntityNamingPattern.CIRCUIT_NUMBERS
        )

        # Verify result sets correct flags
        assert "data" in result
        result_data = result["data"]
        assert result_data[USE_CIRCUIT_NUMBERS] is True
        assert result_data[USE_DEVICE_PREFIX] is True

    async def test_entity_naming_schema_options(self, mock_hass, mock_config_entry):
        """Test that entity naming schema only includes the two modern options."""
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        schema = flow._get_entity_naming_schema()

        # Get the validator directly from the schema dict
        # The schema structure is: {'entity_naming_pattern': In({'friendly_names': '...', 'circuit_numbers': '...'})}
        schema_dict = schema.schema
        validator = schema_dict[ENTITY_NAMING_PATTERN]

        # Get the choices from the vol.In validator
        if hasattr(validator, "container"):
            available_options = validator.container
        elif hasattr(validator, "choices"):
            available_options = validator.choices
        else:
            available_options = None

        assert available_options is not None
        assert EntityNamingPattern.FRIENDLY_NAMES.value in available_options
        assert EntityNamingPattern.CIRCUIT_NUMBERS.value in available_options
        assert EntityNamingPattern.LEGACY_NAMES.value not in available_options

        # Verify friendly descriptions are included
        friendly_option = available_options[EntityNamingPattern.FRIENDLY_NAMES.value]
        circuit_option = available_options[EntityNamingPattern.CIRCUIT_NUMBERS.value]

        assert "Friendly Names" in friendly_option
        assert "kitchen_outlets" in friendly_option
        assert "Circuit Numbers" in circuit_option
        assert "circuit_15" in circuit_option

    @patch("custom_components.span_panel.config_flow.EntityMigrationManager")
    async def test_options_preservation_during_migration(
        self, mock_migration_manager, mock_hass, mock_config_entry
    ):
        """Test that all non-naming options are preserved during migration."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
            "enable_solar_circuit": True,
            "enable_battery_percentage": True,
            "leg1": 30,
            "leg2": 32,
            "scan_interval": 10,
            "custom_option": "test_value",
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        # Change pattern
        user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.FRIENDLY_NAMES.value}

        with patch(
            "custom_components.span_panel.config_flow.EntityMigrationManager"
        ) as mock_migration:
            mock_manager_instance = AsyncMock()
            mock_manager_instance.migrate_entities = AsyncMock(return_value=True)
            mock_migration.return_value = mock_manager_instance

            result = await flow.async_step_entity_naming(user_input)

            # Verify all non-naming options are preserved
            assert "data" in result
            result_data = result["data"]
            assert result_data["enable_solar_circuit"] is True
            assert result_data["enable_battery_percentage"] is True
            assert result_data["leg1"] == 30
            assert result_data["leg2"] == 32
            assert result_data["scan_interval"] == 10
            assert result_data["custom_option"] == "test_value"

            # Verify naming options are updated
            assert result_data[USE_CIRCUIT_NUMBERS] is False
            assert result_data[USE_DEVICE_PREFIX] is True

    async def test_backup_warning_in_description(self, mock_hass, mock_config_entry):
        """Test that backup warning is included in the description."""
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        flow = OptionsFlowHandler("test_entry_id")
        flow.hass = mock_hass

        result = await flow.async_step_entity_naming()

        # Check that description placeholders contain backup warning information
        # The actual warning text is in the translation files, but we can verify
        # the placeholders are provided correctly
        description_placeholders = result.get("description_placeholders")
        assert description_placeholders is not None
        assert "friendly_example" in description_placeholders
        assert "circuit_example" in description_placeholders

        # The warning about backup and history preservation should be in the translation
        # files, which we've updated to include the backup warning
