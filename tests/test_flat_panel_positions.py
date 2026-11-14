"""A flat panel that a leftover schema-0 package still parses is refused at setup, and builds nothing.

An install upgraded from an older release can keep the schema-0 package, which is
never upgraded with this one. When the schema read before connecting could not
be had, that package parses the flat panel and the connect succeeds, so the
adapter the client reached is the only evidence left. This holds setup to
refusing on it: the real flat adapter, driven over the schema it ships, through
the real setup, ends retrying with the flat-firmware Repair and no entity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
)
from custom_components.span_panel.flat_firmware_repairs import flat_firmware_issue_id
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)

from .adapter_fixtures import SCHEMA_ZERO_SERIAL, schema_zero_adapter
from .captures_replay import ReplayClient


async def test_a_flat_panel_the_leftover_adapter_parses_is_refused(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    adapter = schema_zero_adapter()
    client = ReplayClient(adapter)
    assert client.schema_major == "schema_0", "the replay connects on the real flat adapter"
    panel_schema.side_effect = TimeoutError()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.0.2.10",
            CONF_EBUS_BROKER_HOST: f"{SCHEMA_ZERO_SERIAL}.local",
            CONF_EBUS_BROKER_USERNAME: "replay-user",
            CONF_EBUS_BROKER_PASSWORD: "replay-password",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        title="Span Panel",
        unique_id=SCHEMA_ZERO_SERIAL,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch.object(client, "close", AsyncMock()) as close,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key == "panel_firmware_flat"
    close.assert_awaited_once()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, flat_firmware_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_placeholders == {"panel": "Span Panel"}
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []
