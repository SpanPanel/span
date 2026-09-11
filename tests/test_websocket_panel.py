"""A device id saved before Home Assistant 2026.8 still names this panel.

2026.8 gave every device a single owning config entry. A device that belonged to
several was split into one device per entry, and every split was given a new id.
A SPAN panel was such a device whenever a helper built on one of its sensors -- a
utility meter, a Riemann integral -- had linked itself to the panel's device, which
helpers did until that release. The dashboard card keeps the device id it was
configured with, so it can send the old one.

The registry here is reached the way a real install reaches it: a pre-2026.8 store
is handed to Home Assistant's own migration, which performs the split and remaps
the battery's `via_device_id` onto the split its own entry owns. Both commands
must then resolve the old id to the panel device SPAN owns, whichever entry was
the old device's primary.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.util import SUB_DEVICE_BESS
from custom_components.span_panel.websocket import handle_panel_topology
from custom_components.span_panel.websocket_adopted import handle_adopted_list

from .factories import SpanPanelSnapshotFactory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    Handler = Callable[[HomeAssistant, MagicMock, dict[str, Any]], Awaitable[None]]

# The registry is loaded from the store below rather than started empty.
pytestmark = pytest.mark.parametrize("load_registries", [False])

SERIAL = "sp3-242424-001"
SPAN_ENTRY_ID = "span_entry"
HELPER_ENTRY_ID = "helper_entry"
OLD_PANEL_ID = "0ld0pane1000000000000000000000ab"
BATTERY_ID = "batt000000000000000000000000000c"


def _inner(handler: Callable[..., object]) -> Handler:
    """Unwrap a websocket command to the coroutine it decorates."""
    inner: Handler = inspect.unwrap(handler, stop=inspect.iscoroutinefunction)
    return inner


def _device(**fields: Any) -> dict[str, Any]:
    """Return one device as a v1.10 store records it."""
    return {
        "area_id": None,
        "configuration_url": None,
        "connections": [],
        "created_at": "1970-01-01T00:00:00+00:00",
        "disabled_by": None,
        "entry_type": None,
        "hw_version": None,
        "labels": [],
        "model_id": None,
        "modified_at": "1970-01-01T00:00:00+00:00",
        "name_by_user": None,
        "serial_number": None,
        "sw_version": None,
        **fields,
    }


def _pre_2026_8_store(*, primary: str) -> dict[str, Any]:
    """Return a device registry store from before devices were split.

    The panel's device is shared with a helper entry, as a helper linked to one of
    its sensors left it, and the battery hangs off the panel's old id.
    """
    return {
        "version": 1,
        "minor_version": 10,
        "data": {
            "devices": [
                _device(
                    config_entries=[SPAN_ENTRY_ID, HELPER_ENTRY_ID],
                    config_entries_subentries={SPAN_ENTRY_ID: [None], HELPER_ENTRY_ID: [None]},
                    id=OLD_PANEL_ID,
                    identifiers=[[DOMAIN, SERIAL]],
                    manufacturer="Span",
                    model="SPAN Panel",
                    name="SPAN Panel",
                    primary_config_entry=primary,
                    via_device_id=None,
                ),
                _device(
                    config_entries=[SPAN_ENTRY_ID],
                    config_entries_subentries={SPAN_ENTRY_ID: [None]},
                    id=BATTERY_ID,
                    identifiers=[[DOMAIN, f"{SERIAL}_{SUB_DEVICE_BESS}"]],
                    manufacturer="Span",
                    model="Battery",
                    name="SPAN Panel Battery",
                    primary_config_entry=SPAN_ENTRY_ID,
                    via_device_id=OLD_PANEL_ID,
                ),
            ],
            "deleted_devices": [],
        },
    }


async def _split_panel(
    hass: HomeAssistant, hass_storage: dict[str, Any], *, primary: str
) -> dr.DeviceEntry:
    """Load the pre-2026.8 store through the migration and return the panel SPAN owns."""
    span = MockConfigEntry(domain=DOMAIN, data={}, entry_id=SPAN_ENTRY_ID, unique_id=SERIAL)
    span.add_to_hass(hass)
    MockConfigEntry(domain="utility_meter", data={}, entry_id=HELPER_ENTRY_ID).add_to_hass(hass)
    hass_storage[dr.STORAGE_KEY] = _pre_2026_8_store(primary=primary)
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)

    registry = dr.async_get(hass)
    panel = registry.async_get_device_by_identifier((DOMAIN, SERIAL), SPAN_ENTRY_ID)
    assert panel is not None
    # The split this test is about: the old id names no device of its own any more,
    # only the devices it was split into -- the panel SPAN owns among them.
    assert panel.id != OLD_PANEL_ID
    splits = registry.async_get_devices_for_composite_device_id(OLD_PANEL_ID)
    assert panel.id in {split.id for split in splits}

    span.mock_state(hass, ConfigEntryState.LOADED)
    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number=SERIAL)
    # Unloaded at teardown, which awaits the coordinator's shutdown.
    coordinator.async_shutdown = AsyncMock()
    span.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel.id,
        curation=CurationOverlay.empty(),
    )
    return panel


@pytest.mark.parametrize("primary", [SPAN_ENTRY_ID, HELPER_ENTRY_ID])
async def test_topology_answers_an_old_panel_id_with_the_panel_it_became(
    hass: HomeAssistant, hass_storage: dict[str, Any], primary: str
) -> None:
    """The battery is found under the old id, which is echoed, beside the device it became.

    The migration remapped the battery onto the new panel device, so matching
    sub-devices against the id the card sent finds none, and the old device's
    entries include the helper's, which is no SPAN panel.
    """
    panel = await _split_panel(hass, hass_storage, primary=primary)
    connection = MagicMock()

    await _inner(handle_panel_topology)(
        hass, connection, {"id": 1, "type": "span_panel/panel_topology", "device_id": OLD_PANEL_ID}
    )

    connection.send_error.assert_not_called()
    result = connection.send_result.call_args.args[1]
    assert result["device_id"] == OLD_PANEL_ID
    assert list(result["sub_devices"]) == [BATTERY_ID]
    # The device the old id became, and its entry: what the card needs and cannot
    # find by looking the old id up in the device list, which no longer holds it.
    assert result["panel_device_id"] == panel.id
    assert result["config_entry_id"] == SPAN_ENTRY_ID


@pytest.mark.parametrize("primary", [SPAN_ENTRY_ID, HELPER_ENTRY_ID])
async def test_the_adopted_list_answers_an_old_panel_id(
    hass: HomeAssistant, hass_storage: dict[str, Any], primary: str
) -> None:
    """Resolved to SPAN's entry even when the old device's primary was the helper's.

    The old device reports its former primary as `config_entry_id`, so asking that
    field alone refuses the id whenever the helper was primary.
    """
    await _split_panel(hass, hass_storage, primary=primary)
    connection = MagicMock()

    await _inner(handle_adopted_list)(
        hass, connection, {"id": 1, "type": "span_panel/adopted/list", "device_id": OLD_PANEL_ID}
    )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()


async def test_an_old_id_split_into_devices_span_owns_none_of_is_not_a_panel(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """An old id is resolved only to a split a SPAN entry owns, never to another's."""
    other_a = MockConfigEntry(domain="utility_meter", data={}, entry_id="other_a")
    other_a.add_to_hass(hass)
    other_b = MockConfigEntry(domain="integration", data={}, entry_id="other_b")
    other_b.add_to_hass(hass)
    hass_storage[dr.STORAGE_KEY] = {
        "version": 1,
        "minor_version": 10,
        "data": {
            "devices": [
                _device(
                    config_entries=["other_a", "other_b"],
                    config_entries_subentries={"other_a": [None], "other_b": [None]},
                    id=OLD_PANEL_ID,
                    identifiers=[["utility_meter", "meter-1"]],
                    manufacturer=None,
                    model=None,
                    name="Somebody else's device",
                    primary_config_entry="other_a",
                    via_device_id=None,
                )
            ],
            "deleted_devices": [],
        },
    }
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    connection = MagicMock()

    await _inner(handle_panel_topology)(
        hass, connection, {"id": 1, "type": "span_panel/panel_topology", "device_id": OLD_PANEL_ID}
    )

    connection.send_error.assert_called_once_with(
        1, "not_span_panel", "Device is not a SPAN Panel device"
    )
