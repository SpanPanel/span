"""test_entity_naming_patterns.

Tests for Span Panel entity naming pattern functionality.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.span_panel.const import EntityNamingPattern
from custom_components.span_panel.config_flow import OptionsFlowHandler
from custom_components.span_panel.helpers import construct_entity_id
from custom_components.span_panel.helpers import sanitize_name_for_entity_id
from custom_components.span_panel.helpers import construct_synthetic_entity_id


@pytest.fixture(autouse=True)
def mock_frame_helper():
    with patch("homeassistant.helpers.frame.report_usage"):
        yield


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


def create_mock_config_entry(options: dict[str, Any]):
    """Create a mock config entry with specified options."""
    mock_entry = MagicMock()
    mock_entry.options = options
    return mock_entry


def create_mock_hass():
    """Create a mock Home Assistant instance."""
    mock_hass = MagicMock()
    mock_hass.config_entries = MagicMock()
    mock_hass.helpers = MagicMock()

    # Mock the create_task method to prevent issues with coroutines
    def mock_create_task(coro: Any) -> MagicMock:
        """Mock create_task to return a MagicMock and properly handle coroutines."""
        # Close the coroutine to prevent "was never awaited" warnings
        if hasattr(coro, "close"):
            coro.close()

        task_mock = MagicMock()
        task_mock.cancel = MagicMock()
        return task_mock

    mock_hass.async_create_task = mock_create_task
    return mock_hass


@pytest.mark.asyncio
async def test_circuit_numbers_pattern_detection():
    """Test detection of circuit numbers naming pattern."""

    mock_hass = create_mock_hass()
    mock_config_entry = create_mock_config_entry(
        {
            "use_circuit_numbers": True,
            "use_device_prefix": True,
        }
    )
    mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

    with patch.object(OptionsFlowHandler, "__init__", return_value=None):
        flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
        flow._config_entry = mock_config_entry
        flow.hass = mock_hass

        result = await flow.async_step_entity_naming()

        # Should detect circuit numbers pattern
        assert result.get("type") == "form"
        # Verify that the flow's _get_current_naming_pattern method detects the correct pattern
        current_pattern = flow._get_current_naming_pattern()
        assert current_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value


@pytest.mark.asyncio
async def test_friendly_names_pattern_detection():
    """Test detection of friendly names naming pattern."""

    mock_hass = create_mock_hass()
    mock_config_entry = create_mock_config_entry(
        {
            "use_circuit_numbers": False,
            "use_device_prefix": True,
        }
    )
    mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

    with patch.object(OptionsFlowHandler, "__init__", return_value=None):
        flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
        flow._config_entry = mock_config_entry
        flow.hass = mock_hass

        result = await flow.async_step_entity_naming()

        # Should detect friendly names pattern
        assert result.get("type") == "form"
        # Verify that the flow's _get_current_naming_pattern method detects the correct pattern
        current_pattern = flow._get_current_naming_pattern()
        assert current_pattern == EntityNamingPattern.FRIENDLY_NAMES.value


@pytest.mark.asyncio
async def test_legacy_names_pattern_detection():
    """Test detection of legacy naming pattern."""

    mock_hass = create_mock_hass()
    mock_config_entry = create_mock_config_entry(
        {
            "use_circuit_numbers": False,
            "use_device_prefix": False,  # Legacy pattern
        }
    )
    mock_hass.config_entries.async_get_entry.return_value = mock_config_entry

    with patch.object(OptionsFlowHandler, "__init__", return_value=None):
        flow = OptionsFlowHandler.__new__(OptionsFlowHandler)
        flow._config_entry = mock_config_entry
        flow.hass = mock_hass

        result = await flow.async_step_entity_naming()

        # Should detect legacy pattern
        assert result.get("type") == "form"
        # Verify that the flow's _get_current_naming_pattern method detects the correct pattern
        current_pattern = flow._get_current_naming_pattern()
        assert current_pattern == EntityNamingPattern.LEGACY_NAMES.value


@pytest.mark.asyncio
async def test_entity_id_construction_circuit_numbers():
    """Test entity ID construction with circuit numbers pattern."""
    # Mock coordinator with circuit numbers settings
    mock_coordinator = MagicMock()
    mock_coordinator.config_entry.options = {
        "use_circuit_numbers": True,
        "use_device_prefix": True,
    }

    # Mock span panel
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"

    with patch(
        "custom_components.span_panel.helpers.panel_to_device_info",
        return_value={"name": "Span Panel"},
    ):
        # Test circuit entity ID construction
        entity_id = construct_entity_id(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_name="Kitchen Outlets",
            circuit_number=1,
            suffix="power",
        )

        # Should use circuit numbers format
        assert entity_id == "sensor.span_panel_circuit_1_power"


@pytest.mark.asyncio
async def test_entity_id_construction_friendly_names():
    """Test entity ID construction with friendly names pattern."""
    # Mock coordinator with friendly names settings
    mock_coordinator = MagicMock()
    mock_coordinator.config_entry.options = {
        "use_circuit_numbers": False,
        "use_device_prefix": True,
    }

    # Mock span panel
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"

    with patch(
        "custom_components.span_panel.helpers.panel_to_device_info",
        return_value={"name": "Span Panel"},
    ):
        # Test circuit entity ID construction
        entity_id = construct_entity_id(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_name="Kitchen Outlets",
            circuit_number=1,
            suffix="power",
        )

        # Should use friendly names format
        assert entity_id == "sensor.span_panel_kitchen_outlets_power"


@pytest.mark.asyncio
async def test_entity_id_construction_legacy():
    """Test entity ID construction with legacy pattern."""
    # Mock coordinator with legacy settings
    mock_coordinator = MagicMock()
    mock_coordinator.config_entry.options = {
        "use_circuit_numbers": False,
        "use_device_prefix": False,  # Legacy pattern
    }

    # Mock span panel
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"

    with patch(
        "custom_components.span_panel.helpers.panel_to_device_info",
        return_value={"name": "Span Panel"},
    ):
        # Test circuit entity ID construction
        entity_id = construct_entity_id(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_name="Kitchen Outlets",
            circuit_number=1,
            suffix="power",
        )

        # Should use legacy format (no device prefix)
        assert entity_id == "sensor.kitchen_outlets_power"


@pytest.mark.asyncio
async def test_friendly_name_sanitization():
    """Test that friendly names are properly sanitized for entity IDs."""

    test_cases = [
        ("Kitchen Outlets", "kitchen_outlets"),
        ("Living Room - Lights", "living_room___lights"),
        ("HVAC System #1", "hvac_system_#1"),
        ("Garage Door (Main)", "garage_door_(main)"),
        ("Pool Pump & Filter", "pool_pump_&_filter"),
    ]

    for input_name, expected_suffix in test_cases:
        result = sanitize_name_for_entity_id(input_name)
        assert result == expected_suffix, (
            f"Failed for '{input_name}': expected '{expected_suffix}', got '{result}'"
        )


@pytest.mark.asyncio
async def test_synthetic_entity_naming():
    """Test synthetic entity naming (for solar inverters, etc.)."""
    # Mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.config_entry.options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }

    # Mock span panel
    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"

    with patch(
        "custom_components.span_panel.helpers.panel_to_device_info",
        return_value={"name": "Span Panel"},
    ):
        # Test synthetic entity ID construction
        entity_id = construct_synthetic_entity_id(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_numbers=[30, 32],
            suffix="power",
            friendly_name="solar_inverter",
        )

        # Should use device prefix for synthetic entities
        assert entity_id == "sensor.span_panel_solar_inverter_power"


@pytest.mark.asyncio
async def test_panel_level_entity_naming():
    """Test naming patterns for panel-level entities."""
    # Test with device prefix
    mock_coordinator = MagicMock()
    mock_coordinator.config_entry.options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }

    mock_span_panel = MagicMock()
    mock_span_panel.status.serial_number = "TEST123"
    mock_span_panel.status.model = "75A"
    mock_span_panel.status.firmware_version = "1.2.3"
    mock_span_panel.host = "192.168.1.100"

    with patch(
        "custom_components.span_panel.helpers.panel_to_device_info",
        return_value={"name": "Span Panel"},
    ):
        # For panel-level entities, we should use a meaningful circuit name
        entity_id = construct_entity_id(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_name="Panel System",
            circuit_number=0,
            suffix="instant_grid_power",
        )

        # Should include device prefix for panel entities
        assert entity_id == "sensor.span_panel_panel_system_instant_grid_power"

        # Test without device prefix (legacy)
        mock_coordinator.config_entry.options = {
            "use_device_prefix": False,
            "use_circuit_numbers": False,
        }

        entity_id_legacy = construct_entity_id(
            coordinator=mock_coordinator,
            span_panel=mock_span_panel,
            platform="sensor",
            circuit_name="Panel System",
            circuit_number=0,
            suffix="instant_grid_power",
        )

        # Should not include device prefix in legacy mode
        assert entity_id_legacy == "sensor.panel_system_instant_grid_power"
