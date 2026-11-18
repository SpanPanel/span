"""Every platform builds its entities from the snapshot setup decided from.

Setup resolves the PV binding from one snapshot and then forwards the
platforms, and the coordinator keeps receiving snapshots in between. A platform
that read `coordinator.data` could build an entity the binding never decided on
-- an inverter that arrived mid-setup would get a card of its own beside a
Solar card that reads it. So each platform reads `runtime_data.setup_snapshot`,
and whatever arrived later waits for the reload its capability token requests.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import ChildDeviceInfo
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.binary_sensor import async_setup_entry as binary_sensor_setup
from custom_components.span_panel.button import async_setup_entry as button_setup
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.helpers import circuit_device_name
from custom_components.span_panel.number import async_setup_entry as number_setup
from custom_components.span_panel.select import async_setup_entry as select_setup
from custom_components.span_panel.sensor import async_setup_entry as sensor_setup
from custom_components.span_panel.switch import async_setup_entry as switch_setup

from .adapter_fixtures import schema_one_snapshot
from .factories import SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.helpers.entity import Entity
    from span_panel_api import SpanPanelSnapshot

    PlatformSetup = Callable[..., Awaitable[None]]


async def _entities_built(
    hass: HomeAssistant, setup: PlatformSetup, decided: SpanPanelSnapshot, published: SpanPanelSnapshot
) -> list[Entity]:
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
        panel_device_id=register_panel_device(hass, entry, decided.serial_number),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(decided),
        setup_snapshot=decided,
    )
    added: list[Entity] = []
    await setup(hass, entry, lambda entities, *_args, **_kwargs: added.extend(entities))
    return added


async def _built(
    hass: HomeAssistant, setup: PlatformSetup, decided: SpanPanelSnapshot, published: SpanPanelSnapshot
) -> set[str | None]:
    """The unique ids of what one platform's setup builds; see `_entities_built`."""
    return {entity.unique_id for entity in await _entities_built(hass, setup, decided, published)}


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


@pytest.mark.parametrize(
    "setup",
    [sensor_setup, switch_setup, select_setup],
    ids=["sensor", "switch", "select"],
)
async def test_a_circuit_entity_is_named_by_the_snapshot_setup_decided_from(
    hass: HomeAssistant, setup: PlatformSetup
) -> None:
    """A circuit renamed on the panel during setup names no circuit's device until the reload.

    Every circuit entity names its circuit's device, and records what its id
    base is worked out from, from the circuit as setup's snapshot has it, which
    is where the listener that keeps circuit id bases current reads it too. The
    breaker switch and the priority select once read the coordinator's data
    instead, so a circuit renamed between the two had them name its device by
    the new name while the circuit's sensors and the listener used the old one.
    """
    decided = schema_one_snapshot()
    published = replace(
        decided,
        circuits={
            circuit_id: replace(circuit, name=f"{circuit.name} Renamed")
            for circuit_id, circuit in decided.circuits.items()
        },
    )
    decided_names = {
        circuit_device_name(circuit, circuit_id) for circuit_id, circuit in decided.circuits.items()
    }

    built = await _entities_built(hass, setup, decided, published)

    names = {
        info.name for entity in built if isinstance(info := entity.device_info, ChildDeviceInfo)
    }
    assert names, "the platform built no circuit entity, so this reads nothing"
    assert sorted(str(name) for name in names - decided_names) == []
