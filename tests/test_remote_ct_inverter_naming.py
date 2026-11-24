"""An inverter a remote CT feeds is named by the remote CT's label, never by its wire id.

A meter outside the panel has no breaker positions, so the positions fallback
would name it `Circuit {circuit id}`: `Circuit remote-ct-3` on r202639, and the
remote CT's long id on r202645. Its own device and entities are named by
`outside_meter_label` (`Remote CT 3`), and so are the card of an inverter it
feeds and any message naming it.

No capture has a remote CT feeding an inverter, so the tree is derived from
`r202645-a`, whose remote CT is real: two inverters, grafted from
`main32_r202639-upstream-pv`, one fed by the remote CT and one by a hosted
circuit, so each has a card of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import json
from typing import Final
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from span_panel_api import SpanPanelSnapshot, SpanPVSnapshot

from custom_components.span_panel.const import DOMAIN, USE_CIRCUIT_NUMBERS
from custom_components.span_panel.helpers import (
    construct_circuit_identifier_from_tabs,
    construct_circuit_label,
    construct_shared_with_attribute,
)
from custom_components.span_panel.util import pv_inverter_device_identifier

from .captures_replay import RetainedTree, capture, panel_device_id, snapshot
from .replayed_panel import Panel, replayed_panels
from .test_expected_entities import FRESH_INSTALL_OPTIONS

TREE: Final = "r202645-a"
DONOR: Final = "main32_r202639-upstream-pv"
"""The one capture with inverters a circuit could feed, two of them."""

PV_TYPE: Final = "energy.ebus.device.pv"
FEEDS: Final = ("feeds-device-id", "feeds-device-status", "feeds-device-type")


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


def remote_ct(tree: RetainedTree) -> str:
    """Return the tree's one meter outside the panel."""
    (circuit_id,) = (
        cid for cid, circuit in snapshot(tree).circuits.items() if circuit.measures_outside_panel
    )
    return circuit_id


def with_inverters(tree: RetainedTree, feeds: Mapping[str, str]) -> RetainedTree:
    """Return the tree with an inverter fed by each circuit of `feeds`, keyed circuit to inverter id.

    Each inverter is the donor capture's first, its description and published
    values, renamed and reparented under this tree's enclosure. Each feeding
    circuit declares and publishes the `connection/feeds-device-*` record, as
    a hosted circuit already declares it. A remote CT declares at most its
    connection point, so it gains the declarations a hosted circuit has, and
    the node too where it has none.
    """
    donor_tree = capture(DONOR).tree()
    donor = next(
        device
        for device, topics in donor_tree.items()
        if json.loads(topics["$description"]).get("type") == PV_TYPE
    )
    root = panel_device_id(tree)
    hosted = next(
        cid
        for cid, circuit in snapshot(tree).circuits.items()
        if not circuit.measures_outside_panel
    )
    hosted_connection = json.loads(tree[hosted]["$description"])["nodes"]["connection"]
    result = {device: dict(topics) for device, topics in tree.items()}
    root_description = json.loads(result[root]["$description"])
    children = list(root_description["children"])
    for circuit_id, inverter_id in feeds.items():
        inverter = json.loads(donor_tree[donor]["$description"])
        inverter.update(name=inverter_id, parent=root, root=root)
        result[inverter_id] = {
            **{
                topic: value
                for topic, value in donor_tree[donor].items()
                if topic != "$description"
            },
            "$description": json.dumps(inverter),
        }
        children.append(inverter_id)
        circuit = json.loads(result[circuit_id]["$description"])
        node = circuit.setdefault("nodes", {}).setdefault(
            "connection", {key: hosted_connection[key] for key in ("name", "type")}
        )
        declared = node.setdefault("properties", {})
        for prop in FEEDS:
            declared.setdefault(prop, hosted_connection["properties"][prop])
        result[circuit_id]["$description"] = json.dumps(circuit)
        result[circuit_id]["connection/feeds-device-id"] = inverter_id
        result[circuit_id]["connection/feeds-device-status"] = "OK"
        result[circuit_id]["connection/feeds-device-type"] = PV_TYPE
    root_description["children"] = children
    result[root]["$description"] = json.dumps(root_description)
    return result


def derived_tree(name: str = TREE) -> tuple[RetainedTree, str]:
    """Return the tree derived from capture `name` and its remote CT's id."""
    tree = capture(name).tree()
    root = panel_device_id(tree)
    remote = remote_ct(tree)
    hosted = sorted(
        cid
        for cid, circuit in snapshot(tree).circuits.items()
        if not circuit.measures_outside_panel and circuit.device_type == "circuit"
    )[0]
    return with_inverters(
        tree, {remote: f"{root}-se7600h-us-1", hosted: f"{root}-se7600h-us-2"}
    ), remote


def card_and_rows(panel: Panel, inverter_key: str) -> tuple[dr.DeviceEntry, list[er.RegistryEntry]]:
    """Return an inverter's card and every row on it."""
    devices = dr.async_get(panel.hass)
    card = devices.async_get_device_by_identifier(
        (DOMAIN, pv_inverter_device_identifier(panel.serial, inverter_key)), panel.entry.entry_id
    )
    assert card is not None, f"{inverter_key} has no card of its own"
    rows = er.async_entries_for_device(
        er.async_get(panel.hass), card.id, include_disabled_entities=True
    )
    return card, sorted(rows, key=lambda row: row.entity_id)


CARD_ENTITY_IDS: Final = (
    "binary_sensor.span_panel_solar_inverter_{}_pv_panel_link",
    "sensor.span_panel_solar_inverter_{}_pv_nameplate_capacity",
    "sensor.span_panel_solar_inverter_{}_pv_product",
    "sensor.span_panel_solar_inverter_{}_pv_vendor",
)
"""The card's four entity ids, by the card name's slug."""


@pytest.mark.parametrize("numbers", [False, True], ids=["circuit-names", "circuit-numbers"])
async def test_an_inverter_a_remote_ct_feeds_is_named_by_its_label(
    panels: Callable[..., Panel], numbers: bool
) -> None:
    """Its card is `Span Panel Solar Inverter (Remote CT 3)` in either mode, and its entity ids follow.

    No device or entity of the entry is named by, or has an id built from, the
    remote CT's wire id; the remote CT has no breaker switch or priority select.
    Fails when the inverter's suffix falls back to the breaker positions a
    remote CT does not have.
    """
    tree, remote = derived_tree()
    panel = panels(tree, options={**FRESH_INSTALL_OPTIONS, USE_CIRCUIT_NUMBERS: numbers})
    await panel.set_up(tree)

    card, rows = card_and_rows(panel, remote)
    assert card.name == "Span Panel Solar Inverter (Remote CT 3)"
    assert [row.entity_id for row in rows] == [
        form.format("remote_ct_3") for form in CARD_ENTITY_IDS
    ]
    wire = remote.replace("-", "_")
    devices = [
        *dr.async_entries_for_config_entry(dr.async_get(panel.hass), panel.entry.entry_id),
        *dr.async_child_entries_for_config_entry(dr.async_get(panel.hass), panel.entry.entry_id),
    ]
    assert not [device.name for device in devices if device.name and remote in device.name]
    entities = er.async_entries_for_config_entry(er.async_get(panel.hass), panel.entry.entry_id)
    assert not [row.entity_id for row in entities if wire in row.entity_id]
    remote_device = panel.circuit_device(remote)
    assert {
        row.domain
        for row in er.async_entries_for_device(
            er.async_get(panel.hass), remote_device.id, include_disabled_entities=True
        )
    } == {"sensor"}


def _earlier_suffix(
    pv: SpanPVSnapshot, panel_snapshot: SpanPanelSnapshot, use_circuit_numbers: bool
) -> str | None:
    """Name an inverter as an earlier release did: by the positions fallback, a remote CT's included."""
    circuit = panel_snapshot.circuits.get(pv.feed_circuit_id) if pv.feed_circuit_id else None
    if circuit is None:
        return None
    if not use_circuit_numbers and circuit.name:
        return str(circuit.name)
    return construct_circuit_identifier_from_tabs(circuit.tabs, circuit.circuit_id)


@pytest.mark.parametrize("numbers", [False, True], ids=["circuit-names", "circuit-numbers"])
@pytest.mark.parametrize("name", [TREE, "r202639-a"])
async def test_an_install_the_earlier_naming_built_keeps_its_entity_ids(
    panels: Callable[..., Panel], name: str, numbers: bool
) -> None:
    """The card is renamed at the upgrade, its entity ids stay, and Recreate offers ids from the new name.

    An earlier release named the card by the remote CT's wire id, and composed
    the card's entity ids from that name at creation. After the upgrade the
    card is `Span Panel Solar Inverter (Remote CT 3)`; the rows keep their ids
    (an existing entity id never moves) and their names, which Home Assistant
    composes from the card's, read with it. "Recreate entity IDs" then offers
    each the id composed from the new name. On r202645's id, and on the
    `remote-ct-3` of `r202639-a`, the same panel before the update, in either
    naming mode. Fails when the upgrade moves an entity id, or the card keeps
    the wire id.
    """
    tree, remote = derived_tree(name)
    panel = panels(tree, options={**FRESH_INSTALL_OPTIONS, USE_CIRCUIT_NUMBERS: numbers})
    with patch("custom_components.span_panel.helpers.resolve_pv_display_suffix", _earlier_suffix):
        await panel.set_up(tree)
    card, rows = card_and_rows(panel, remote)
    wire = remote.replace("-", "_")
    assert card.name == f"Span Panel Solar Inverter (Circuit {remote})"
    before = [row.entity_id for row in rows]
    assert before == [form.format(f"circuit_{wire}") for form in CARD_ENTITY_IDS]
    vendor = before[-1]
    state = panel.hass.states.get(vendor)
    assert state is not None
    assert state.attributes["friendly_name"] == f"{card.name} PV Vendor"

    await panel.restart_on(tree)

    card, rows = card_and_rows(panel, remote)
    assert card.name == "Span Panel Solar Inverter (Remote CT 3)"
    assert [row.entity_id for row in rows] == before
    state = panel.hass.states.get(vendor)
    assert state is not None
    assert state.attributes["friendly_name"] == "Span Panel Solar Inverter (Remote CT 3) PV Vendor"
    registry = er.async_get(panel.hass)
    assert [registry.async_regenerate_entity_id(row) for row in rows] == [
        form.format("remote_ct_3") for form in CARD_ENTITY_IDS
    ]


def test_a_remote_ct_is_named_by_its_label_in_a_message_or_an_attribute() -> None:
    """A message naming it and a peer list holding it read `Remote CT 3`, as its device does.

    Fails when the label falls back to positions a remote CT does not have.
    """
    panel_snapshot = snapshot(capture(TREE).tree())
    remote = next(
        cid for cid, circuit in panel_snapshot.circuits.items() if circuit.measures_outside_panel
    )

    assert construct_circuit_label(panel_snapshot.circuits[remote], remote) == "Remote CT 3"
    assert construct_shared_with_attribute(panel_snapshot, (remote,)) == ["Remote CT 3"]
