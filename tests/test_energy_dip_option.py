"""An entry that never stored the dip compensation option compensates nothing, and saving its options keeps it so.

Installs from before the option have no value for it, and the sensors read that
absence as off. The General Options form used to pre-fill the same absence as on,
so saving the form for any other reason turned compensation on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel.const import DOMAIN, ENABLE_ENERGY_DIP_COMPENSATION

from .helpers import shown_values


async def test_saving_general_options_unchanged_leaves_compensation_off(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={}, entry_id="entry-before-the-option")
    entry.add_to_hass(hass)

    with patch("custom_components.span_panel.async_apply_panel_registration", AsyncMock()):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        schema = result["data_schema"]
        assert schema is not None
        shown = shown_values(schema)
        assert shown[ENABLE_ENERGY_DIP_COMPENSATION] is False

        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=shown)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[ENABLE_ENERGY_DIP_COMPENSATION] is False


async def test_an_entry_that_stored_the_option_keeps_its_choice(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={ENABLE_ENERGY_DIP_COMPENSATION: True}, entry_id="entry-with-the-option"
    )
    entry.add_to_hass(hass)

    with patch("custom_components.span_panel.async_apply_panel_registration", AsyncMock()):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        schema = result["data_schema"]
        assert schema is not None
        shown = shown_values(schema)
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=shown)

    assert entry.options[ENABLE_ENERGY_DIP_COMPENSATION] is True
