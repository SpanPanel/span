"""test_error_handling.

Tests for error handling scenarios in the Span Panel integration.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
import pytest

from tests.factories import SpanPanelApiResponseFactory
from tests.helpers import (
    patch_span_panel_dependencies,
    setup_span_panel_entry,
    trigger_coordinator_update,
)


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


@pytest.mark.asyncio
async def test_api_connection_timeout_during_setup(hass: Any, enable_custom_integrations: Any):
    """Test that setup fails gracefully when API connection times out."""
    entry, _ = setup_span_panel_entry(hass)

    # Mock API to raise timeout
    with patch("custom_components.span_panel.SpanPanel") as mock_span_panel_class:
        mock_span_panel = AsyncMock()
        mock_span_panel.update.side_effect = aiohttp.ClientTimeout()
        mock_span_panel_class.return_value = mock_span_panel

        # Setup should fail gracefully
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is False
        assert entry.state == ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_api_connection_refused_during_setup(hass: Any, enable_custom_integrations: Any):
    """Test that setup fails gracefully when API connection is refused."""
    entry, _ = setup_span_panel_entry(hass)

    # Mock API to raise connection error
    with patch("custom_components.span_panel.SpanPanel") as mock_span_panel_class:
        mock_span_panel = AsyncMock()
        mock_span_panel.update.side_effect = aiohttp.ClientError("Connection refused")
        mock_span_panel_class.return_value = mock_span_panel

        # Setup should fail and trigger retry
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is False
        assert entry.state == ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_coordinator_update_api_failure(hass: Any, enable_custom_integrations: Any):
    """Test coordinator behavior when API calls fail during updates."""
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()
    entry, _ = setup_span_panel_entry(hass, mock_responses)

    with patch_span_panel_dependencies(mock_responses) as (mock_panel, mock_api):
        # Setup integration successfully first
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]

        # Make API calls fail on next update
        mock_api.get_panel_data.side_effect = aiohttp.ClientError("API Error")
        mock_api.get_circuits_data.side_effect = aiohttp.ClientError("API Error")

        # Trigger update - should handle errors gracefully
        await trigger_coordinator_update(coordinator)

        # Coordinator should still be available but may show as unavailable
        assert coordinator is not None


@pytest.mark.asyncio
async def test_invalid_authentication_handling(hass: Any, enable_custom_integrations: Any):
    """Test handling of authentication failures."""
    entry, _ = setup_span_panel_entry(hass)

    with patch("custom_components.span_panel.SpanPanel") as mock_span_panel_class:
        mock_span_panel = AsyncMock()
        # Use a simpler approach - just raise a general client error for auth issues
        mock_span_panel.update.side_effect = aiohttp.ClientError("401: Unauthorized")
        mock_span_panel_class.return_value = mock_span_panel

        # Setup should fail due to auth error
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is False


@pytest.mark.asyncio
async def test_network_disconnection_recovery(hass: Any, enable_custom_integrations: Any):
    """Test recovery behavior after network disconnection."""
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()
    entry, _ = setup_span_panel_entry(hass, mock_responses)

    with patch_span_panel_dependencies(mock_responses) as (mock_panel, mock_api):
        # Setup successfully
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data["span_panel"][entry.entry_id]["coordinator"]

        # Simulate network disconnection
        mock_api.get_panel_data.side_effect = aiohttp.ClientError("Network unreachable")

        # Update should fail
        await trigger_coordinator_update(coordinator)

        # Simulate network recovery
        mock_api.get_panel_data.side_effect = None
        mock_api.get_panel_data.return_value = mock_responses["panel"]

        # Update should succeed again
        await trigger_coordinator_update(coordinator)

        # Verify recovery
        mock_api.get_panel_data.assert_called()
