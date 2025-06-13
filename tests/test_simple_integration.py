"""Simple test to check if integration loads."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.mark.asyncio
async def test_integration_loads(hass: HomeAssistant):
    """Test that the integration can be loaded."""
    # Try to set up the integration without a config entry
    result = await async_setup_component(hass, "span_panel", {})

    # This should succeed even without entries
    assert result is True

    # Check that the domain is loaded
    assert "span_panel" in hass.config.components


@pytest.mark.asyncio
async def test_config_flow_exists(hass: HomeAssistant):
    """Test that the config flow is properly registered."""
    from homeassistant.data_entry_flow import FlowManager

    # Get the config flow manager
    flow_manager: FlowManager = hass.config_entries.flow

    # Try to create a flow to test if the handler is registered
    try:
        handler = await flow_manager.async_create_flow(
            "span_panel", context={"source": "user"}
        )
        assert handler is not None
        print(f"Config flow created successfully: {handler}")
        # Don't try to abort - just let it clean up naturally
    except Exception as e:
        pytest.fail(f"Config flow not registered or failed to create: {e}")
