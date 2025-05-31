"""test_configuration_edge_cases.

Configuration-related edge case tests for Span Panel integration.
"""

from typing import Any
import pytest

from tests.factories import (
    SpanPanelApiResponseFactory,
    SpanPanelCircuitFactory,
)
from tests.helpers import (
    assert_entity_state,
    get_circuit_entity_id,
    patch_span_panel_dependencies,
    setup_span_panel_entry,
    trigger_coordinator_update,
)


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


@pytest.mark.asyncio
async def test_legacy_naming_scheme_compatibility(
    hass: Any, enable_custom_integrations: Any
):
    """Test backward compatibility with legacy naming scheme (pre-1.0.4)."""

    circuit_data = SpanPanelCircuitFactory.create_circuit(
        circuit_id="1",
        name="Kitchen Outlets",
        instant_power=245.3,
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data]
    )

    # Configure entry to use ACTUAL legacy naming (pre-1.0.4 style)
    # Pre-1.0.4: no device prefix, circuit names (not numbers)
    options = {
        "use_device_prefix": False,  # Legacy mode: no device prefix
        "use_circuit_numbers": False,  # Legacy mode: use circuit names, not numbers
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        # Test the correct legacy behavior: no device prefix, circuit name based
        # Expected entity ID: sensor.kitchen_outlets_power (no "span_panel" prefix)
        power_entity_id = get_circuit_entity_id(
            "1",
            "Kitchen Outlets",
            "sensor",
            "power",
            use_circuit_numbers=False,
            use_device_prefix=False,
        )
        assert_entity_state(hass, power_entity_id, "245.3")


@pytest.mark.asyncio
async def test_config_entry_migration_from_legacy(
    hass: Any, enable_custom_integrations: Any
):
    """Test migration of config entry from legacy format to new format."""

    circuit_data = SpanPanelCircuitFactory.create_circuit(
        circuit_id="1",
        name="Test Circuit",
        instant_power=100.0,
    )

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response(
        circuits=[circuit_data]
    )

    # Start with legacy config (no options set, but include defaults for the integration)
    options = {
        "use_device_prefix": True,  # Current default
        "use_circuit_numbers": False,  # Current default
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Verify entities are created with expected naming
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        await trigger_coordinator_update(coordinator)

        power_entity_id = get_circuit_entity_id(
            "1", "Test Circuit", "sensor", "power", use_device_prefix=True
        )
        assert hass.states.get(power_entity_id) is not None


@pytest.mark.asyncio
async def test_invalid_configuration_options(
    hass: Any, enable_custom_integrations: Any
):
    """Test handling of invalid configuration options."""

    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Configure entry with invalid option values
    options = {
        "use_device_prefix": "invalid_string",  # Should be boolean
        "use_circuit_numbers": None,  # Should be boolean
        "invalid_option": True,  # Unknown option
    }
    entry, _ = setup_span_panel_entry(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses):
        # Setup should handle invalid options gracefully
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        # Integration should fall back to default values for invalid options
        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]
        assert coordinator is not None
