"""`span_panel/panel_topology` tells the card each inverter's identity and power, over a real registry."""

from __future__ import annotations

from typing import Final
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import DOMAIN, PV_PANEL_LINK_KEY
from custom_components.span_panel.id_builder import (
    build_panel_unique_id,
    build_pv_inverter_unique_id,
)
from custom_components.span_panel.sensor_definitions import PV_POWER_SENSOR
from custom_components.span_panel.websocket import handle_panel_topology

from .adapter_fixtures import schema_one_snapshot
from .helpers import unwrap_websocket_command
from .test_pv_binding import _gateway_tree, _unfed_tree
from .test_pv_device import PV_DEVICE, SOLAR_CIRCUIT, _entry
from .test_pv_inverters import SECOND_SOLAR_CIRCUIT, _setup, _tree, _unload

_inner = unwrap_websocket_command(handle_panel_topology)

C: Final = SOLAR_CIRCUIT
C2: Final = SECOND_SOLAR_CIRCUIT


async def _topology(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, object]:
    entry.mock_state(hass, ConfigEntryState.LOADED)
    connection = MagicMock()
    await _inner(
        hass,
        connection,
        {
            "id": 1,
            "type": "span_panel/panel_topology",
            "device_id": entry.runtime_data.panel_device_id,
        },
    )
    connection.send_error.assert_not_called()
    result: object = connection.send_result.call_args.args[1]
    assert isinstance(result, dict)
    return result


def _solar_by_identifier(hass: HomeAssistant, result: dict[str, object]) -> dict[str, object]:
    """`{device identifier: solar block}` for every sub-device that carries one."""
    sub_devices = result["sub_devices"]
    assert isinstance(sub_devices, dict)
    registry = dr.async_get(hass)
    found: dict[str, object] = {}
    for device_id, record in sub_devices.items():
        assert isinstance(record, dict)
        device = registry.async_get(device_id)
        assert device is not None
        identifier = next(name for domain, name in device.identifiers if domain == DOMAIN)
        if "solar" in record:
            found[identifier] = record["solar"]
    return found


def _circuit_power(hass: HomeAssistant, entry: MockConfigEntry, circuit_id: str) -> str:
    matches = [
        row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.domain == "sensor" and row.unique_id.endswith(f"_{circuit_id}_power")
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _site_power(hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot) -> str:
    """PV Power, found on the Solar device itself rather than by the handler's own lookup.

    Required to exist, so that a lookup that found nothing cannot pass by
    matching a handler that also found nothing.
    """
    serial = snapshot.serial_number
    solar = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{serial}_pv"), entry.entry_id
    )
    assert solar is not None
    unique_id = build_panel_unique_id(serial, PV_POWER_SENSOR.key)
    matches = [
        row.entity_id
        for row in er.async_entries_for_device(er.async_get(hass), solar.id)
        if row.domain == "sensor" and row.unique_id == unique_id
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _no_inverter_published() -> SpanPanelSnapshot:
    tree = _unfed_tree()
    tree.pop(PV_DEVICE)
    return schema_one_snapshot(tree)


async def test_the_bound_inverter_is_the_site_tile_and_the_other_has_its_own(
    hass: HomeAssistant,
) -> None:
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-ws-a", one.serial_number)
    await _unload(await _setup(hass, entry, one))
    two = schema_one_snapshot(_tree())
    await _setup(hass, entry, two)

    solar = _solar_by_identifier(hass, await _topology(hass, entry))

    serial = two.serial_number
    assert solar == {
        f"{serial}_pv": {
            "role": "site",
            "vendor": two.pv_inverters[C].vendor_name,
            "model": two.pv_inverters[C].model,
            "feed_circuit_id": C,
            "power_entity_id": _circuit_power(hass, entry, C),
            "site_power_entity_id": _site_power(hass, entry, two),
        },
        f"{serial}_pv_{C2}": {
            "role": "inverter",
            "vendor": two.pv_inverters[C2].vendor_name,
            "model": two.pv_inverters[C2].model,
            "feed_circuit_id": C2,
            "power_entity_id": _circuit_power(hass, entry, C2),
            "site_power_entity_id": None,
        },
    }


async def test_inverters_behind_a_gateway_have_identity_and_no_individual_reading(
    hass: HomeAssistant,
) -> None:
    snapshot = schema_one_snapshot(
        _gateway_tree(
            ("panel-se7600h-us-1", "SE7600H-US", "11680"),
            ("panel-use7600h-us-2", "USE7600H-US", "11680"),
        )
    )
    entry = _entry(hass, "entry-ws-b", snapshot.serial_number)
    await _setup(hass, entry, snapshot)

    solar = _solar_by_identifier(hass, await _topology(hass, entry))

    serial = snapshot.serial_number
    site = solar[f"{serial}_pv"]
    assert isinstance(site, dict)
    assert site["power_entity_id"] is None and site["site_power_entity_id"] == _site_power(
        hass, entry, snapshot
    )
    for key in ("panel-se7600h-us-1", "panel-use7600h-us-2"):
        block = solar[f"{serial}_pv_{key}"]
        assert isinstance(block, dict)
        assert block["role"] == "inverter" and block["power_entity_id"] is None


async def test_a_single_tab_inverter_circuit_resolves_its_power(hass: HomeAssistant) -> None:
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-ws-tab", one.serial_number)
    await _unload(await _setup(hass, entry, one))
    tree = _tree()
    tree[C2]["info/spaces"] = "5"
    two = schema_one_snapshot(tree)
    assert len(two.circuits[C2].tabs) == 1
    await _setup(hass, entry, two)

    block = _solar_by_identifier(hass, await _topology(hass, entry))[f"{two.serial_number}_pv_{C2}"]

    assert isinstance(block, dict)
    assert block["power_entity_id"] == _circuit_power(hass, entry, C2)


async def test_inverter_card_entities_never_take_the_circuits_power_role(
    hass: HomeAssistant,
) -> None:
    """Their unique ids embed the circuit id (spec F8); only the circuit's own sensor is its power."""
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-ws-guard", one.serial_number)
    await _unload(await _setup(hass, entry, one))
    two = schema_one_snapshot(_tree())
    await _setup(hass, entry, two)
    registry = er.async_get(hass)
    serial = two.serial_number
    for domain, unique_id in (
        ("sensor", build_pv_inverter_unique_id(serial, C2, "pv_vendor")),
        ("binary_sensor", build_pv_inverter_unique_id(serial, C2, PV_PANEL_LINK_KEY)),
        ("sensor", f"span_{serial}_adopted_pv_{C2}/inverter/power"),
    ):
        registry.async_get_or_create(domain, DOMAIN, unique_id, config_entry=entry)

    result = await _topology(hass, entry)

    circuits = result["circuits"]
    assert isinstance(circuits, dict)
    entities = circuits[C2]["entities"]
    assert entities["power"] == _circuit_power(hass, entry, C2)
    block = _solar_by_identifier(hass, result)[f"{serial}_pv_{C2}"]
    assert isinstance(block, dict) and block["power_entity_id"] == _circuit_power(hass, entry, C2)


async def test_pv_with_no_inverter_published_still_gets_a_site_block(hass: HomeAssistant) -> None:
    """The Solar device exists whenever PV is commissioned, so the card must never meet it without a block (review I-1)."""
    snapshot = _no_inverter_published()
    entry = _entry(hass, "entry-ws-none", snapshot.serial_number)
    await _setup(hass, entry, snapshot)

    solar = _solar_by_identifier(hass, await _topology(hass, entry))

    assert solar == {
        f"{snapshot.serial_number}_pv": {
            "role": "site",
            "vendor": None,
            "model": None,
            "feed_circuit_id": None,
            "power_entity_id": None,
            "site_power_entity_id": _site_power(hass, entry, snapshot),
        }
    }


async def test_a_pending_record_with_no_inverter_published_keeps_the_bound_circuit(
    hass: HomeAssistant,
) -> None:
    one = schema_one_snapshot()
    entry = _entry(hass, "entry-ws-pending", one.serial_number)
    await _unload(await _setup(hass, entry, one))
    none = _no_inverter_published()
    await _setup(hass, entry, none)
    assert entry.runtime_data.pv_binding.bound_key == C

    site = _solar_by_identifier(hass, await _topology(hass, entry))[f"{none.serial_number}_pv"]

    assert isinstance(site, dict)
    assert site["feed_circuit_id"] == C and site["power_entity_id"] == _circuit_power(
        hass, entry, C
    )
