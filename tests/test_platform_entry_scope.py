"""Every platform hands the builders the entry it is setting up.

The builders look device cards up within the entry they are given, so the entry
a platform passes decides which card each lookup finds. Nothing about a wrong
entry is loud: an adopted device resolves to no card and is re-minted under a
new identifier, which the registry reads as an entity replacement that takes
the history with it, and an extension row finds no card and is deferred, so its
entity never appears. Each platform is set up here against an entry that
already holds the cards, and asked for the entity only the right entry yields.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import AdoptedDevice, AdoptedProperty, ExtensionProperty, ExtensionSubject

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.adoption import adopted_identifier, adopted_unique_id
from custom_components.span_panel.binary_sensor import (
    async_setup_entry as binary_sensor_async_setup_entry,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.extension import (
    extension_device_identifier,
    extension_unique_id,
)
from custom_components.span_panel.number import async_setup_entry as number_async_setup_entry
from custom_components.span_panel.select import async_setup_entry as select_async_setup_entry
from custom_components.span_panel.sensor import async_setup_entry as sensor_async_setup_entry
from custom_components.span_panel.switch import async_setup_entry as switch_async_setup_entry

from .factories import SpanPanelSnapshotFactory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.helpers.entity import Entity
    from span_panel_api import SpanPanelSnapshot

    PlatformSetup = Callable[..., Awaitable[None]]

PANEL_SERIAL = "sp3-242424-001"
WIRE_ID = "generator-1"
SERIAL = "EX-0000-0001"
BATTERY = ExtensionSubject(kind="battery", instance_key=None)


@pytest.fixture
def loaded_panel(hass: HomeAssistant) -> tuple[MockConfigEntry, str]:
    """Return an entry holding the panel, a battery card, and a device frozen at first sighting.

    The adopted card is registered under the device's wire id, as a setup before
    its serial arrived would have left it. The snapshot each test builds carries
    that serial, so only a lookup in this entry keeps the wire id.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={}, unique_id=PANEL_SERIAL)
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    panel = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, PANEL_SERIAL)},
        name="Span Panel",
    )
    battery_identifier = extension_device_identifier(PANEL_SERIAL, BATTERY)
    assert battery_identifier is not None
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, battery_identifier)},
        name="Span Panel Battery",
        via_device_id=panel.id,
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, adopted_identifier(PANEL_SERIAL, WIRE_ID))},
        name="Backup Generator",
        via_device_id=panel.id,
    )
    return entry, panel.id


async def _set_up(
    hass: HomeAssistant,
    loaded_panel: tuple[MockConfigEntry, str],
    setup: PlatformSetup,
    snapshot: SpanPanelSnapshot,
) -> set[str | None]:
    """Run one platform's `async_setup_entry` and return the unique_ids it built."""
    entry, panel_device_id = loaded_panel
    coordinator = MagicMock(data=snapshot)
    coordinator.hass = hass
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = entry
    coordinator.async_request_refresh = AsyncMock()
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel_device_id,
        curation=CurationOverlay.empty(),
    )
    added: list[Entity] = []
    await setup(hass, entry, lambda entities, *_args, **_kwargs: added.extend(entities))
    return {entity.unique_id for entity in added}


def _adopted_snapshot(declaration: AdoptedProperty) -> SpanPanelSnapshot:
    device = AdoptedDevice(
        device_id=WIRE_ID,
        device_type="energy.ebus.device.generator",
        name="Backup Generator",
        model="GEN-9000",
        serial_number=SERIAL,
        properties=(declaration,),
    )
    return replace(
        SpanPanelSnapshotFactory.create_complete(serial_number=PANEL_SERIAL),
        adopted_devices=(device,),
    )


def _declaration(
    node_id: str,
    property_id: str,
    datatype: str,
    *,
    unit: str | None = None,
    fmt: str | None = None,
    settable: bool = False,
    value: str,
) -> AdoptedProperty:
    return AdoptedProperty(
        node_id=node_id,
        property_id=property_id,
        datatype=datatype,
        unit=unit,
        format=fmt,
        settable=settable,
        value=value,
    )


@pytest.mark.parametrize(
    ("setup", "declaration"),
    [
        pytest.param(
            sensor_async_setup_entry,
            _declaration("meter", "active-power", "float", unit="W", value="2400"),
            id="sensor",
        ),
        pytest.param(
            binary_sensor_async_setup_entry,
            _declaration("relay", "closed", "boolean", value="true"),
            id="binary_sensor",
        ),
        pytest.param(
            switch_async_setup_entry,
            _declaration("relay", "enabled", "boolean", settable=True, value="true"),
            id="switch",
        ),
        pytest.param(
            select_async_setup_entry,
            _declaration(
                "control", "mode", "enum", fmt="AUTO,MANUAL,OFF", settable=True, value="AUTO"
            ),
            id="select",
        ),
        pytest.param(
            number_async_setup_entry,
            _declaration(
                "control",
                "power-setpoint",
                "float",
                unit="W",
                fmt="0:5000:100",
                settable=True,
                value="2400",
            ),
            id="number",
        ),
    ],
)
async def test_an_adopted_device_keeps_the_identity_this_entry_froze(
    hass: HomeAssistant,
    loaded_panel: tuple[MockConfigEntry, str],
    setup: PlatformSetup,
    declaration: AdoptedProperty,
) -> None:
    """The wire id survives the serial's arrival on every platform an adopted row reaches.

    The frozen id's presence is asserted rather than the re-minted id's absence,
    because absence would pass just as well for a declaration that never reached
    this platform -- and then nothing here would be about this platform's scope.
    """
    unique_ids = await _set_up(hass, loaded_panel, setup, _adopted_snapshot(declaration))

    assert adopted_unique_id(adopted_identifier(PANEL_SERIAL, WIRE_ID), declaration) in unique_ids


@pytest.mark.parametrize(
    ("setup", "row"),
    [
        pytest.param(
            sensor_async_setup_entry,
            ExtensionProperty(
                subject=BATTERY,
                node_id="battery-2",
                property_id="cell-temperature",
                datatype="float",
                unit="°C",
                value="31.4",
            ),
            id="sensor",
        ),
        pytest.param(
            binary_sensor_async_setup_entry,
            ExtensionProperty(
                subject=BATTERY,
                node_id="battery-2",
                property_id="balancing",
                datatype="boolean",
                unit=None,
                value="true",
            ),
            id="binary_sensor",
        ),
    ],
)
async def test_an_extension_row_finds_the_card_this_entry_holds(
    hass: HomeAssistant,
    loaded_panel: tuple[MockConfigEntry, str],
    setup: PlatformSetup,
    row: ExtensionProperty,
) -> None:
    """A row on a registered card becomes an entity rather than waiting for a reload."""
    snapshot = replace(
        SpanPanelSnapshotFactory.create_complete(serial_number=PANEL_SERIAL),
        extension_properties=(row,),
    )

    unique_ids = await _set_up(hass, loaded_panel, setup, snapshot)

    assert (
        extension_unique_id(PANEL_SERIAL, row.subject, row.node_id, row.property_id) in unique_ids
    )
