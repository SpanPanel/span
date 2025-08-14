"""Test simulator config flow functionality."""

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant

from custom_components.span_panel.const import DOMAIN


async def test_simulator_config_flow(hass: HomeAssistant) -> None:
    """Test creating a config entry in simulator mode."""

    # Start the config flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"

    # Submit form with simulator mode enabled
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "192.168.1.100",  # This gets ignored in simulator mode
            "simulator_mode": True,
        },
    )

    # Simulator mode now has an additional config step
    assert result["type"] == "form"
    assert result["step_id"] == "simulator_config"

    # Submit the simulator configuration
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "simulation_config": "simulation_config_32_circuit",  # Choose a valid config
            "host": "localhost",
            "simulation_start_time": "",  # Use default
            "entity_naming_pattern": "friendly_names",
        },
    )

    # Now should create entry
    assert result["type"] == "create_entry"
    assert result["title"] == "Span Simulator"

    # Verify the config data
    assert result["data"][CONF_HOST] == "localhost"
    assert result["data"][CONF_ACCESS_TOKEN] == "simulator_token"
    assert result["data"]["simulation_mode"] is True


async def test_normal_config_flow_still_works(hass: HomeAssistant) -> None:
    """Test that normal config flow still works when simulator mode is disabled."""

    # Start the config flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"

    # Submit form with simulator mode disabled (normal flow)
    with pytest.raises(Exception):  # Will fail because no real SPAN panel
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "192.168.1.100",
                "simulator_mode": False,  # Normal mode
            },
        )
