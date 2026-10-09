"""A flat panel's empty breaker positions are not circuits, and no entity is built on one.

The pinned schema_0 adapter reports only the circuits the panel publishes. Earlier
releases added an `unmapped_tab_<n>` entry for every position no breaker occupied,
and the integration filtered those out by name; the filters went with them. This
holds the integration to that: the flat adapter, driven over the schema it ships,
with every position empty, through the real setup and every platform.
"""

from __future__ import annotations

import re
from typing import Final
from unittest.mock import patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
)
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)

from .adapter_fixtures import SCHEMA_ZERO_SERIAL, schema_zero_adapter
from .captures_replay import ReplayClient

UNOCCUPIED_POSITION: Final = re.compile(r"(^|_)unmapped_tab_\d+")
"""The synthesised circuit id, wherever it would appear in a circuit id or a unique id."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """The switch platform's relock and debounce timers may outlive a test."""
    return True


async def test_a_flat_panel_creates_no_unoccupied_position_entities(hass: HomeAssistant) -> None:
    adapter = schema_zero_adapter()
    snapshot = adapter.build_snapshot()
    assert snapshot.panel_size > 0, "the schema declares breaker positions, every one of them empty"
    assert not [circuit_id for circuit_id in snapshot.circuits if UNOCCUPIED_POSITION.search(circuit_id)]

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
    with patch("custom_components.span_panel.SpanMqttClient", return_value=ReplayClient(adapter)):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    unique_ids = [
        registered.unique_id
        for registered in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ]
    assert unique_ids, "setup created the panel's own entities"
    assert not [unique_id for unique_id in unique_ids if UNOCCUPIED_POSITION.search(unique_id)]
