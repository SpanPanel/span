"""Every platform builds its entities from the snapshot setup decided from.

Setup resolves the PV binding from one snapshot and then forwards the
platforms, and the coordinator keeps receiving snapshots in between. A platform
that read `coordinator.data` could build an entity the binding never decided on
-- an inverter that arrived mid-setup would get a card of its own beside a
Solar card that reads it. So each platform reads `runtime_data.setup_snapshot`,
and whatever arrived later waits for the reload its capability token requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.binary_sensor import async_setup_entry as binary_sensor_setup
from custom_components.span_panel.button import async_setup_entry as button_setup
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.number import async_setup_entry as number_setup
from custom_components.span_panel.select import async_setup_entry as select_setup
from custom_components.span_panel.sensor import async_setup_entry as sensor_setup
from custom_components.span_panel.switch import async_setup_entry as switch_setup

from .adapter_fixtures import schema_one_snapshot
from .factories import SpanPanelSnapshotFactory, pv_binding_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.helpers.entity import Entity
    from span_panel_api import SpanPanelSnapshot

    PlatformSetup = Callable[..., Awaitable[None]]


async def _built(
    hass: HomeAssistant, setup: PlatformSetup, decided: SpanPanelSnapshot, published: SpanPanelSnapshot
) -> set[str | None]:
    """Run one platform's setup decided from `decided` while the coordinator holds `published`."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={}, unique_id=decided.serial_number)
    entry.add_to_hass(hass)
    coordinator = MagicMock(data=published)
    coordinator.hass = hass
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = entry
    coordinator.async_request_refresh = AsyncMock()
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(decided),
        setup_snapshot=decided,
    )
    added: list[Entity] = []
    await setup(hass, entry, lambda entities, *_args, **_kwargs: added.extend(entities))
    return {entity.unique_id for entity in added}


@pytest.mark.parametrize(
    "setup",
    [binary_sensor_setup, button_setup, number_setup, select_setup, sensor_setup, switch_setup],
    ids=["binary_sensor", "button", "number", "select", "sensor", "switch"],
)
async def test_a_platform_builds_from_the_snapshot_setup_decided_from(
    hass: HomeAssistant, setup: PlatformSetup
) -> None:
    full = schema_one_snapshot()
    minimal = SpanPanelSnapshotFactory.create(serial_number=full.serial_number)

    from_minimal = await _built(hass, setup, minimal, minimal)
    from_full = await _built(hass, setup, full, full)
    # The capture gives this platform something the minimal panel does not, so
    # reading the wrong snapshot would show.
    assert from_full - from_minimal

    assert await _built(hass, setup, minimal, full) == from_minimal
