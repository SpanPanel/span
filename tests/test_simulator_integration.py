"""Test simulator integration functionality."""

import os
import pytest
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from unittest.mock import AsyncMock, patch
from homeassistant.helpers import config_entry_oauth2_flow

from custom_components.span_panel.const import DOMAIN

# Import MockConfigEntry from pytest-homeassistant-custom-component
try:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
except ImportError:
    from homeassistant.config_entries import ConfigEntry as MockConfigEntry


async def test_integration_setup_with_simulator(hass: HomeAssistant) -> None:
    """Test that integration sets up correctly in simulator mode."""

    # Skip this test if span_panel_api is mocked (conftest.py interference)
    import span_panel_api
    if str(type(span_panel_api)) == "<class 'unittest.mock.MagicMock'>":
        pytest.skip("Test requires real span_panel_api, but it's mocked")

    # Create a config entry with simulator mode
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="SPAN Panel (Simulator)",
        data={
            CONF_HOST: "localhost",
            CONF_ACCESS_TOKEN: "simulator_token",
            "simulation_mode": True,
        },
        options={},
    )

    # Add the config entry to hass
    config_entry.add_to_hass(hass)

    # Setup the integration
    result = await hass.config_entries.async_setup(config_entry.entry_id)

    # Should setup successfully
    assert result is True

    # Verify the integration is loaded
    assert config_entry.state.name == "LOADED"

    # Verify integration data exists
    assert DOMAIN in hass.data
    assert config_entry.entry_id in hass.data[DOMAIN]

    # Verify coordinator exists
    coordinator = hass.data[DOMAIN][config_entry.entry_id].get("coordinator")
    assert coordinator is not None

    # Verify span panel has simulation mode enabled
    span_panel = coordinator.data
    assert span_panel is not None
    assert span_panel.api.simulation_mode is True


async def test_simulator_vs_normal_mode_api_creation(hass: HomeAssistant) -> None:
    """Test that SpanPanelApi is created correctly for simulator vs normal mode."""

    # Skip this test if span_panel_api is mocked (conftest.py interference)
    import span_panel_api
    if str(type(span_panel_api)) == "<class 'unittest.mock.MagicMock'>":
        pytest.skip("Test requires real span_panel_api, but it's mocked")

    from custom_components.span_panel.span_panel_api import SpanPanelApi

    # Test simulator mode
    simulator_api = SpanPanelApi(
        host="192.168.1.100",
        access_token="test_token",
        simulation_mode=True
    )

    assert simulator_api.simulation_mode is True
    assert simulator_api.host == "localhost"  # Should be overridden

    # Test normal mode
    normal_api = SpanPanelApi(
        host="192.168.1.100",
        access_token="test_token",
        simulation_mode=False
    )

    assert normal_api.simulation_mode is False
    assert normal_api.host == "192.168.1.100"  # Should not be overridden
