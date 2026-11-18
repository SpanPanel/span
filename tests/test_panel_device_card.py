"""The panel's device card is the same whichever of its entities registers it last.

Home Assistant registers an entity's device info as the entity is added, and
writes every field that info sets over the device, a None included. So every
entity on the panel's device has to describe it the same way, or the card
depends on the order the platforms happen to finish in. The control-lock switch
described it without the host, and cleared the panel's Visit link whenever it
was the last to register.

Set up for real over a replayed capture, with the platforms forwarded one at a
time in a chosen order, so the order is the test's and not the event loop's.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
import contextlib
from typing import Final
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel import PLATFORMS
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
from custom_components.span_panel.options import CONTROL_LOCK_TIMEOUT

from .captures_replay import ReplayClient, capture, replay

CAPTURE: Final = "main32_r202639"
HOST: Final = "192.0.2.10"
PANEL_NAME: Final = "Span Panel"

SWITCH_FIRST: Final = (Platform.SWITCH, *(p for p in PLATFORMS if p is not Platform.SWITCH))
SWITCH_LAST: Final = (*(p for p in PLATFORMS if p is not Platform.SWITCH), Platform.SWITCH)


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """The switch platform's relock and debounce timers may outlive a test."""
    return True


@contextlib.asynccontextmanager
async def _installed(hass: HomeAssistant, order: Sequence[Platform]) -> AsyncIterator[str]:
    """Set the capture up with the control lock on, forwarding `order` one platform at a time.

    Yields the panel's serial.
    """
    adapter = replay(capture(CAPTURE).tree())
    serial = adapter.build_snapshot().serial_number
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: HOST,
            "device_name": PANEL_NAME,
            CONF_EBUS_BROKER_HOST: f"{serial}.local",
            CONF_EBUS_BROKER_USERNAME: "replay-user",
            CONF_EBUS_BROKER_PASSWORD: "replay-password",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        # Set at all, the lock exists; 0 is "no auto-relock".
        options={CONTROL_LOCK_TIMEOUT: 0},
        title=PANEL_NAME,
        entry_id="entry-panel-card",
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    forward = hass.config_entries.async_forward_entry_setups

    async def one_platform_at_a_time(
        forwarded: ConfigEntry, platforms: Iterable[Platform | str]
    ) -> None:
        assert sorted(platforms) == sorted(order)
        for platform in order:
            await forward(forwarded, [platform])

    with (
        patch("custom_components.span_panel.SpanMqttClient", return_value=ReplayClient(adapter)),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=one_platform_at_a_time
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        try:
            yield serial
        finally:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


@pytest.mark.parametrize("order", [SWITCH_FIRST, SWITCH_LAST], ids=["switch-first", "switch-last"])
async def test_the_panels_visit_link_survives_the_control_lock(
    hass: HomeAssistant, order: Sequence[Platform]
) -> None:
    async with _installed(hass, order) as serial:
        panel = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, serial), "entry-panel-card"
        )
        assert panel is not None

        # Not vacuous: the lock is there, and on the panel's device.
        lock_id = er.async_get(hass).async_get_entity_id(
            Platform.SWITCH, DOMAIN, f"span_{serial}_control_lock"
        )
        assert lock_id is not None
        lock = er.async_get(hass).async_get(lock_id)
        assert lock is not None
        assert lock.device_id == panel.id

        assert panel.configuration_url == f"http://{HOST}"
