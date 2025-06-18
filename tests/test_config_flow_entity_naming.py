"""Test entity naming options in config flow with coordinator-based migration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.data_entry_flow import FlowResultType

from custom_components.span_panel.config_flow import OptionsFlowHandler
from custom_components.span_panel.const import (
    DOMAIN,
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""

    class MockConfigEntry:
        def __init__(self):
            self.entry_id = "test_entry_id"
            self.options = {
                USE_CIRCUIT_NUMBERS: True,
                USE_DEVICE_PREFIX: True,
                "enable_solar_circuit": True,
                "leg1": 30,
                "leg2": 32,
            }

    return MockConfigEntry()


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    hass.async_block_till_done = AsyncMock()

    # Mock async_create_task to properly handle coroutines
    def mock_create_task(coro):
        """Mock async_create_task that properly closes coroutines."""
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    hass.async_create_task = MagicMock(side_effect=mock_create_task)

    return hass


class TestEntityNamingOptionsWithCoordinator:
    """Test entity naming options in the config flow using coordinator-based migration."""

    async def test_pattern_change_triggers_coordinator_migration(
        self, mock_hass, mock_config_entry
    ):
        """Test that changing patterns triggers coordinator migration."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        # Mock the coordinator with migrate_entities method
        mock_coordinator = AsyncMock()
        mock_coordinator.migrate_entities = AsyncMock(return_value=True)

        # Mock hass.data structure to provide the coordinator
        mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: {"coordinator": mock_coordinator}}}

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow._config_entry = mock_config_entry
            flow.hass = mock_hass

            # Change from circuit numbers to friendly names
            user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.FRIENDLY_NAMES.value}

            result = await flow.async_step_entity_naming(user_input)

            # Verify migration was called on the coordinator
            mock_coordinator.migrate_entities.assert_called_once_with(
                EntityNamingPattern.CIRCUIT_NUMBERS.value, EntityNamingPattern.FRIENDLY_NAMES.value
            )

            # Verify reload was scheduled (task creation was called)
            mock_hass.async_create_task.assert_called_once()

            # Wait for any pending tasks to complete
            await mock_hass.async_block_till_done()

            # Verify result
            assert result.get("type") == FlowResultType.CREATE_ENTRY
            result_data = result.get("data", {})
            assert result_data.get(USE_CIRCUIT_NUMBERS) is False
            assert result_data.get("leg1") == 30
            assert result_data.get("leg2") == 32

    async def test_no_change_no_migration(self, mock_hass, mock_config_entry):
        """Test that no migration occurs when pattern doesn't change."""
        mock_config_entry.options = {
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
        }
        mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

        # Mock the coordinator (even though migration shouldn't be called)
        mock_coordinator = AsyncMock()
        mock_coordinator.migrate_entities = AsyncMock(return_value=True)

        # Mock hass.data structure to provide the coordinator
        mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: {"coordinator": mock_coordinator}}}

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow._config_entry = mock_config_entry
            flow.hass = mock_hass

            # Submit same pattern (circuit numbers)
            user_input = {ENTITY_NAMING_PATTERN: EntityNamingPattern.CIRCUIT_NUMBERS.value}

            result = await flow.async_step_entity_naming(user_input)

            # Verify no migration was called on the coordinator
            mock_coordinator.migrate_entities.assert_not_called()

            # Verify no reload was scheduled
            mock_hass.async_create_task.assert_not_called()

            # Verify result
            assert result.get("type") == FlowResultType.CREATE_ENTRY
            assert result.get("data") == {}  # Empty data means no changes
