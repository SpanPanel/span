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

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from custom_components.span_panel.websocket_panel import resolve_panel_device

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .helpers import (
    add_panel_entry,
    register_circuit_device,
    register_panel_device,
    unwrap_websocket_command,
)

# The registry is loaded from the store below rather than started empty.
pytestmark = pytest.mark.parametrize("load_registries", [False])

SERIAL = "sp3-242424-001"
SPAN_ENTRY_ID = "span_entry"
HELPER_ENTRY_ID = "helper_entry"
OLD_PANEL_ID = "0ld0pane1000000000000000000000ab"
BATTERY_ID = "batt000000000000000000000000000c"


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
    snapshot = SpanPanelSnapshotFactory.create(serial_number=SERIAL)
    coordinator = MagicMock()
    coordinator.data = snapshot
    # Unloaded at teardown, which awaits the coordinator's shutdown.
    coordinator.async_shutdown = AsyncMock()
    span.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel.id,
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
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

    await unwrap_websocket_command(handle_panel_topology)(
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

    await unwrap_websocket_command(handle_adopted_list)(
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

    await unwrap_websocket_command(handle_panel_topology)(
        hass, connection, {"id": 1, "type": "span_panel/panel_topology", "device_id": OLD_PANEL_ID}
    )

    connection.send_error.assert_called_once_with(
        1, "not_span_panel", "Device is not a SPAN Panel device"
    )


async def _empty_registries(hass: HomeAssistant) -> None:
    """Load the registries with nothing in them, which this module otherwise loads from a store."""
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)


async def test_circuit_device_id_is_not_a_panel(hass: HomeAssistant) -> None:
    """A circuit's own device is a part of the panel, and is refused as one.

    A SPAN entry owns it, so it is no stranger's device, and it is not the
    panel: the request has to carry the panel device's ID.
    """
    await _empty_registries(hass)
    entry = add_panel_entry(hass, SPAN_ENTRY_ID, SERIAL)
    circuit_device = register_circuit_device(
        hass,
        entry,
        SERIAL,
        SpanCircuitSnapshotFactory.create(circuit_id="circuit-7", name="Kitchen Outlets"),
        panel_device_id=register_panel_device(hass, entry, SERIAL),
    )
    connection = MagicMock()

    resolved = resolve_panel_device(hass, connection, {"id": 1, "device_id": circuit_device.id})

    assert resolved is None
    connection.send_error.assert_called_once_with(
        1, "not_panel_device", "Use the SPAN panel device registry ID, not a circuit device."
    )


async def test_another_integrations_child_device_is_not_a_span_panel(hass: HomeAssistant) -> None:
    """A child device is judged by the entry that owns it, as every other device is."""
    await _empty_registries(hass)
    other = MockConfigEntry(domain="other_domain", data={}, entry_id="other_entry")
    other.add_to_hass(hass)
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other_domain", "strip-1")}
    )
    outlet = registry.async_get_or_create_child(
        config_entry_id=other.entry_id,
        identifiers={("other_domain", "strip-1_outlet-1")},
        name="Outlet 1",
        parent_device_id=parent.id,
    )
    connection = MagicMock()

    resolved = resolve_panel_device(hass, connection, {"id": 1, "device_id": outlet.id})

    assert resolved is None
    connection.send_error.assert_called_once_with(
        1, "not_span_panel", "Device is not a SPAN Panel device"
    )


async def test_the_panel_device_id_answers_with_the_panel_and_its_entry(
    hass: HomeAssistant,
) -> None:
    """The id of the panel's own device is the one request this accepts."""
    await _empty_registries(hass)
    entry = add_panel_entry(hass, SPAN_ENTRY_ID, SERIAL)
    panel_device_id = register_panel_device(hass, entry, SERIAL)
    connection = MagicMock()

    resolved = resolve_panel_device(hass, connection, {"id": 1, "device_id": panel_device_id})

    assert resolved is not None
    device, owner = resolved
    assert device.id == panel_device_id
    assert owner is entry
    connection.send_error.assert_not_called()


async def test_an_id_no_device_has_is_not_found(hass: HomeAssistant) -> None:
    await _empty_registries(hass)
    add_panel_entry(hass, SPAN_ENTRY_ID, SERIAL)
    connection = MagicMock()

    resolved = resolve_panel_device(hass, connection, {"id": 1, "device_id": "no-such-device"})

    assert resolved is None
    connection.send_error.assert_called_once_with(1, "device_not_found", "Device not found")


@pytest.mark.parametrize("primary", [SPAN_ENTRY_ID, HELPER_ENTRY_ID])
async def test_the_device_is_looked_up_by_home_assistant_s_own_helper(
    hass: HomeAssistant, hass_storage: dict[str, Any], primary: str
) -> None:
    """One lookup, Core's, answers an id from before 2026.8 and a current one alike.

    Home Assistant resolves a device id to the device an integration's entry
    owns, an old id's split included. A second copy of that kept here could
    drift from it, so the request is handed to the helper and to nothing else.
    """
    panel = await _split_panel(hass, hass_storage, primary=primary)
    connection = MagicMock()

    with patch.object(
        dr,
        "async_get_device_and_config_entry_for_domain",
        wraps=dr.async_get_device_and_config_entry_for_domain,
    ) as lookup:
        resolved = resolve_panel_device(hass, connection, {"id": 1, "device_id": OLD_PANEL_ID})

    lookup.assert_called_once_with(hass, OLD_PANEL_ID, domain=DOMAIN)
    assert resolved is not None
    assert resolved[0].id == panel.id
    assert resolved[1].entry_id == SPAN_ENTRY_ID
    connection.send_error.assert_not_called()
