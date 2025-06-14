"""Test upgrade scenarios to ensure existing installations are preserved."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.span_panel.config_flow import OptionsFlowHandler
from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from custom_components.span_panel.helpers import (
    construct_entity_id,
    construct_synthetic_entity_id,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    hass.config_entries.async_get_entry = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""

    class MockConfigEntry:
        def __init__(self):
            self.entry_id = "test_entry_id"
            self.options = {}

    return MockConfigEntry()


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.config_entry = None  # Will be set in tests
    return coordinator


@pytest.fixture
def mock_span_panel():
    """Create a mock span panel."""
    panel = MagicMock()
    panel.status.serial_number = "TEST123"
    return panel


class TestUpgradeScenarios:
    """Test upgrade scenarios for different installation types."""

    def test_legacy_installation_preserved_on_upgrade(self, mock_hass, mock_config_entry):
        """Test that legacy installations (pre-1.0.4) are preserved during upgrades."""
        # Legacy installation: no device prefix, no circuit numbers
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: False,
            USE_CIRCUIT_NUMBERS: False,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly instead of going through the registry
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                # Should detect as legacy pattern
                current_pattern = flow._get_current_naming_pattern()
                assert current_pattern == EntityNamingPattern.LEGACY_NAMES.value

    def test_post_104_friendly_names_preserved_on_upgrade(
        self, mock_hass, mock_config_entry
    ):
        """Test that post-1.0.4 friendly names installations are preserved during upgrades."""
        # Post-1.0.4 with friendly names: device prefix enabled, circuit numbers disabled
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: False,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                # Should detect as friendly names pattern
                current_pattern = flow._get_current_naming_pattern()
                assert current_pattern == EntityNamingPattern.FRIENDLY_NAMES.value

    def test_modern_circuit_numbers_preserved_on_upgrade(
        self, mock_hass, mock_config_entry
    ):
        """Test that modern circuit numbers installations are preserved during upgrades."""
        # Modern installation: both device prefix and circuit numbers enabled
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                # Should detect as circuit numbers pattern
                current_pattern = flow._get_current_naming_pattern()
                assert current_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value

    def test_missing_options_default_to_new_installation_behavior(
        self, mock_hass, mock_config_entry
    ):
        """Test that missing options default to existing installation behavior (legacy)."""
        # Empty options (like an existing installation with missing flags)
        mock_config_entry.options = {}

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                # Should default to legacy pattern (existing installation behavior)
                current_pattern = flow._get_current_naming_pattern()
                assert current_pattern == EntityNamingPattern.LEGACY_NAMES.value

    def test_partial_options_default_correctly(self, mock_hass, mock_config_entry):
        """Test that partial options still work correctly with defaults."""
        # Only one option set (edge case)
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: False,
            # USE_CIRCUIT_NUMBERS missing - defaults to False
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                # With defaults: USE_DEVICE_PREFIX=False, USE_CIRCUIT_NUMBERS=False (default)
                # This results in legacy pattern
                current_pattern = flow._get_current_naming_pattern()
                assert current_pattern == EntityNamingPattern.LEGACY_NAMES.value

    def test_new_installation_gets_modern_defaults(self, mock_hass, mock_config_entry):
        """Test that new installations get modern defaults (circuit numbers)."""
        # New installation: explicit modern defaults (as set by create_new_entry)
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                # Should detect as circuit numbers pattern (new installation default)
                current_pattern = flow._get_current_naming_pattern()
                assert current_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value


class TestEntityIdConstructionUpgradeScenarios:
    """Test entity ID construction preserves existing patterns during upgrades."""

    def test_legacy_entity_id_construction_preserved(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that legacy entity ID construction is preserved."""
        # Legacy installation
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: False,
            USE_CIRCUIT_NUMBERS: False,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                "Kitchen Outlets",
                15,
                "power",
            )

            # Legacy format: no device prefix, use circuit name
            assert entity_id == "sensor.kitchen_outlets_power"

    def test_post_104_friendly_names_entity_id_construction_preserved(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that post-1.0.4 friendly names entity ID construction is preserved."""
        # Post-1.0.4 friendly names installation
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: False,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                "Kitchen Outlets",
                15,
                "power",
            )

            # Post-1.0.4 format: device prefix + circuit name
            assert entity_id == "sensor.span_panel_kitchen_outlets_power"

    def test_modern_circuit_numbers_entity_id_construction_preserved(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that modern circuit numbers entity ID construction is preserved."""
        # Modern circuit numbers installation
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                "Kitchen Outlets",
                15,
                "power",
            )

            # Modern format: device prefix + circuit number
            assert entity_id == "sensor.span_panel_circuit_15_power"

    def test_missing_options_use_new_installation_defaults(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that new installations with explicit options use modern defaults."""
        # New installation: explicit modern defaults (as set by create_new_entry)
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                "Kitchen Outlets",
                15,
                "power",
            )

            # New installation defaults: device prefix + circuit numbers
            assert entity_id == "sensor.span_panel_circuit_15_power"


class TestSyntheticEntityUpgradeScenarios:
    """Test synthetic entity construction preserves existing patterns during upgrades."""

    def test_legacy_synthetic_entity_construction_preserved(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that legacy synthetic entity construction is preserved."""
        # Legacy installation
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: False,
            USE_CIRCUIT_NUMBERS: False,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_synthetic_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                [30, 32],
                "power",
                "Solar Inverter",
            )

            # Legacy format: no device prefix, use friendly name
            assert entity_id == "sensor.solar_inverter_power"

    def test_post_104_synthetic_entity_construction_preserved(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that post-1.0.4 synthetic entity construction is preserved."""
        # Post-1.0.4 friendly names installation
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: False,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_synthetic_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                [30, 32],
                "power",
                "Solar Inverter",
            )

            # Post-1.0.4 format: device prefix + friendly name
            assert entity_id == "sensor.span_panel_solar_inverter_power"

    def test_modern_synthetic_entity_construction_preserved(
        self, mock_coordinator, mock_span_panel
    ):
        """Test that modern synthetic entity construction is preserved."""
        # Modern circuit numbers installation
        mock_config_entry = MagicMock()
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }
        mock_coordinator.config_entry = mock_config_entry

        # Mock device info
        with patch(
            "custom_components.span_panel.helpers.panel_to_device_info"
        ) as mock_device_info:
            mock_device_info.return_value = {"name": "Span Panel"}

            entity_id = construct_synthetic_entity_id(
                mock_coordinator,
                mock_span_panel,
                "sensor",
                [30, 32],
                "power",
                "Solar Inverter",
            )

            # Modern format: device prefix + circuit numbers
            assert entity_id == "sensor.span_panel_circuit_30_32_power"


class TestGeneralOptionsPreservesNamingFlags:
    """Test that general options flow preserves naming flags."""

    async def test_general_options_preserves_legacy_flags(
        self, mock_hass, mock_config_entry
    ):
        """Test that general options flow preserves legacy naming flags."""
        # Legacy installation flags
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: False,
            USE_CIRCUIT_NUMBERS: False,
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Simulate general options form submission (only changing solar settings)
            user_input = {
                "enable_solar_circuit": False,  # Changed
                "leg1": 30,  # Unchanged
                "leg2": 32,  # Unchanged
            }

            # Mock the config_entry property and async_step_general_options method
            with (
                patch.object(
                    OptionsFlowHandler,
                    "config_entry",
                    new_callable=lambda: mock_config_entry,
                ),
                patch.object(flow, "async_create_entry") as mock_create_entry,
            ):
                await flow.async_step_general_options(user_input)

                # Verify that naming flags were preserved
                mock_create_entry.assert_called_once()
                result_data = mock_create_entry.call_args[1]["data"]
                assert result_data.get(USE_DEVICE_PREFIX) is False  # Preserved
                assert result_data.get(USE_CIRCUIT_NUMBERS) is False  # Preserved

    async def test_general_options_preserves_modern_flags(
        self, mock_hass, mock_config_entry
    ):
        """Test that general options flow preserves modern naming flags."""
        # Modern installation flags
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
            "enable_solar_circuit": False,
            "leg1": 30,
            "leg2": 32,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Simulate general options form submission (only changing solar settings)
            user_input = {
                "enable_solar_circuit": True,  # Changed
                "leg1": 28,  # Changed
                "leg2": 30,  # Changed
            }

            # Mock the config_entry property and async_step_general_options method
            with (
                patch.object(
                    OptionsFlowHandler,
                    "config_entry",
                    new_callable=lambda: mock_config_entry,
                ),
                patch.object(flow, "async_create_entry") as mock_create_entry,
            ):
                await flow.async_step_general_options(user_input)

                # Verify that naming flags were preserved
                mock_create_entry.assert_called_once()
                result_data = mock_create_entry.call_args[1]["data"]
                assert result_data.get(USE_DEVICE_PREFIX) is True  # Preserved
                assert result_data.get(USE_CIRCUIT_NUMBERS) is True  # Preserved

    async def test_general_options_handles_missing_flags_with_defaults(
        self, mock_hass, mock_config_entry
    ):
        """Test that general options flow handles missing flags with defaults."""
        # Installation with missing naming flags (edge case)
        mock_config_entry.options = {
            "enable_solar_circuit": True,
            "leg1": 30,
            "leg2": 32,
            # Missing USE_DEVICE_PREFIX and USE_CIRCUIT_NUMBERS
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Simulate general options form submission
            user_input = {
                "enable_solar_circuit": False,  # Changed
                "leg1": 30,
                "leg2": 32,
            }

            # Mock the config_entry property and async_step_general_options method
            with (
                patch.object(
                    OptionsFlowHandler,
                    "config_entry",
                    new_callable=lambda: mock_config_entry,
                ),
                patch.object(flow, "async_create_entry") as mock_create_entry,
            ):
                await flow.async_step_general_options(user_input)

                # Verify that missing flags get defaults (False for existing installations)
                mock_create_entry.assert_called_once()
                result_data = mock_create_entry.call_args[1]["data"]
                assert (
                    result_data.get(USE_DEVICE_PREFIX) is False
                )  # Default for existing installations
                assert (
                    result_data.get(USE_CIRCUIT_NUMBERS) is False
                )  # Default for existing installations


class TestUpgradeDocumentationCompliance:
    """Test that upgrade scenarios comply with documentation."""

    def test_readme_compliance_legacy_pattern(self, mock_hass, mock_config_entry):
        """Test that README examples match actual legacy pattern behavior."""
        # Legacy installation (pre-1.0.4 or upgraded without options)
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: False,
            USE_CIRCUIT_NUMBERS: False,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                pattern = flow._get_current_naming_pattern()
                assert pattern == EntityNamingPattern.LEGACY_NAMES.value

    def test_readme_compliance_friendly_names_pattern(self, mock_hass, mock_config_entry):
        """Test that README examples match actual friendly names pattern behavior."""
        # Post-1.0.4 installation with friendly names
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: False,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                pattern = flow._get_current_naming_pattern()
                assert pattern == EntityNamingPattern.FRIENDLY_NAMES.value

    def test_readme_compliance_circuit_numbers_pattern(self, mock_hass, mock_config_entry):
        """Test that README examples match actual circuit numbers pattern behavior."""
        # Post-1.0.9 installation with circuit numbers
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                pattern = flow._get_current_naming_pattern()
                assert pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value

    def test_readme_compliance_new_installation_default(self, mock_hass, mock_config_entry):
        """Test that new installations default to circuit numbers as documented."""
        # New installation (post-1.0.9) - explicit defaults set by create_new_entry
        mock_config_entry.options = {
            USE_DEVICE_PREFIX: True,
            USE_CIRCUIT_NUMBERS: True,
        }

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                pattern = flow._get_current_naming_pattern()
                assert pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value

    def test_readme_compliance_existing_installation_empty_options(
        self, mock_hass, mock_config_entry
    ):
        """Test that existing installations with empty options default to legacy as documented."""
        # Existing installation with no options (pre-1.0.4 or upgraded without options)
        mock_config_entry.options = {}

        with patch.object(OptionsFlowHandler, "__init__", return_value=None):
            flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
            flow.hass = mock_hass

            # Mock the config_entry property directly
            with patch.object(
                OptionsFlowHandler,
                "config_entry",
                new_callable=lambda: mock_config_entry,
            ):
                pattern = flow._get_current_naming_pattern()
                assert pattern == EntityNamingPattern.LEGACY_NAMES.value
