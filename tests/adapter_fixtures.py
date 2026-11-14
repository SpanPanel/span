"""Build real adapter field metadata from the adapter's own reference payload.

The library's own harness compares wire-level deltas between schemas; it cannot
know what this integration declares it reads. These helpers give the
integration's tests real adapter output to check declarations against.

The payload is package data of the parent/child adapter distribution, read
through `importlib.resources` exactly as the library's own suite reads it, so the
bytes replayed here are the bytes shipped by the wheel `manifest.json` pins. The
pin is the provenance: bumping it is what moves the capture, and nothing has to
keep a copy of it honest.

Uses the real `ebus_sdk.DiscoveredDevice` rather than a stand-in. A
description-only stand-in is not sufficient: `_downstream_lugs_metadata` ->
`find_lugs` reads property *values* via `device.get_property(...)`, so a stand-in
either raises or silently loses the five downstream/feedthrough paths. ebus-sdk
arrives transitively with span-panel-api-schema-1, so the import is free.
"""

from __future__ import annotations

from importlib.resources import files
import json

from ebus_sdk.homie import DiscoveredDevice
from span_panel_api.models import FieldMetadata, SpanPanelSnapshot

from custom_components.span_panel.schema_validation import DiscoveredProperty

SCHEMA_ONE_TREE = files("span_panel_api_schema_1") / "reference" / "parent_child_tree.json"
"""The parent/child capture, as `span-panel-api-schema-1` ships it."""

SCHEMA_ONE_PANEL = "example-40t-001"
"""Device id of the enclosure in the adapter's published capture.

Named rather than inlined because a test that mutates one of the panel's
published topics has to address the panel, and every such test would otherwise
carry its own copy of the simulator's device naming.
"""


def _device(device_id: str, topics: dict[str, str]) -> DiscoveredDevice:
    """Rebuild one discovered device from its retained topics.

    Mirrors the library's own builder (test_schema_one_devices.py:26-37):
    `update_description` parses the JSON string, `update_property` stores each
    non-`$` topic as a property value.
    """
    device = DiscoveredDevice(device_id, "ebus")
    device.update_description(topics["$description"])
    device.update_state(topics.get("$state", "ready"))
    for topic, value in topics.items():
        if topic.startswith("$"):
            continue
        node, _, prop = topic.partition("/")
        if prop:
            device.update_property(node, prop, value)
    return device


def _devices_from(tree: dict[str, dict[str, str]]) -> list[DiscoveredDevice]:
    """Rebuild discovered devices from a retained-topic capture."""
    return [_device(device_id, topics) for device_id, topics in tree.items()]


def schema_one_tree(without: str | None = None) -> dict[str, dict[str, str]]:
    """A mutable copy of the parent/child capture, ready to be rewritten.

    **Read from the library's package data, not copied into this repository.**
    `span-panel-api-schema-1` publishes the capture beside the adapter that parses
    it, so the pinned wheel is both the payload and the record of which release it
    came from; a copy here would need a guard to keep it honest, and the pin needs
    none.

    Copied per call, and one level deep, which is as deep as a topic goes: a test
    proves a reading came off the wire by republishing it and asserting the entity
    followed, and that is impossible against a shared immutable capture.

    `without` drops one device, which is how the batteryless and PV-less variants
    are made. They were separate files and are now derived, so they cannot drift
    from the base by construction -- the only difference each ever had was the one
    missing device.
    """
    capture: dict[str, dict[str, str]] = json.loads(SCHEMA_ONE_TREE.read_text(encoding="utf-8"))
    tree = {device_id: dict(topics) for device_id, topics in capture.items()}
    if without is not None:
        assert without in tree, f"{without!r} is not in the capture; nothing to drop"
        del tree[without]
    return tree


def schema_one_snapshot(tree: dict[str, dict[str, str]] | None = None) -> SpanPanelSnapshot:
    """Build a real snapshot from the capture, through the real schema_1 mapper.

    The point of going the long way round rather than through
    `SpanPanelSnapshotFactory`: a factory takes the value a test hands it, so an
    assertion against it proves only that the test and the entity agree. Driving
    the actual adapter over the actual capture makes the published topic the
    source of truth, so republishing one is a mutation the entity has to follow.
    """
    return tree_snapshot(schema_one_tree() if tree is None else tree, SCHEMA_ONE_PANEL)


BESS_INVERTER = "bess-inverter"
"""Device id of the inverter `add_grid_forming_inverter` hangs off the capture's BESS."""


def add_grid_forming_inverter(tree: dict[str, dict[str, str]], *, capable: bool) -> None:
    """Give the capture's BESS an inverter child that says whether it can form a grid.

    The capture's BESS declares no inverter, so `grid_islandable` reads `None`
    on it. BESS model 0.14 puts `grid-forming/capable` on the BESS's `inverter`
    child, and the adapter answers the panel-level question from every inverter
    it finds (`resolve_grid_islandable`), so this is the shape in which the
    field is a reading. The BESS lists the new child, as a described tree would.
    """
    description = {
        "homie": "5.0",
        "version": 1,
        "type": "energy.ebus.device.inverter",
        "name": "Battery inverter",
        "nodes": {
            "grid-forming": {
                "name": "grid-forming",
                "type": "energy.ebus.capability.grid-forming",
                "properties": {"capable": {"name": "Capable", "datatype": "boolean"}},
            }
        },
        "children": [],
        "root": SCHEMA_ONE_PANEL,
        "parent": "bess",
        "extensions": [],
    }
    tree[BESS_INVERTER] = {
        "$description": json.dumps(description),
        "$state": "ready",
        "grid-forming/capable": "true" if capable else "false",
    }
    bess = json.loads(tree["bess"]["$description"])
    bess["children"].append(BESS_INVERTER)
    tree["bess"]["$description"] = json.dumps(bess)


def tree_snapshot(tree: dict[str, dict[str, str]], panel_id: str) -> SpanPanelSnapshot:
    """Build a snapshot through the schema_1 mapper from any tree whose enclosure is `panel_id`.

    Every other device in the tree is handed over as a child, which is what a
    complete capture's declared children are.
    """
    from span_panel_api_schema_1.snapshot import build_snapshot

    panel = _device(panel_id, tree[panel_id])
    children = [
        _device(device_id, topics)
        for device_id, topics in tree.items()
        if device_id != panel_id
    ]
    return build_snapshot(panel, children)


def _curated(metadata: dict[str, FieldMetadata]) -> dict[str, FieldMetadata]:
    """The half of an adapter's map that names snapshot fields we curate.

    Every fixture below hands out the curated half, through the same
    `schema_validation.partition` the coordinator uses, so no test can be
    perturbed by what a panel declares and nobody reads. That is not tidiness:
    the producible gate, the exemption annotations, the derived-reason checks
    and the unit vocabulary all treat "in an adapter's map" as "this integration
    could read it", and a discovered path satisfies neither half of that.

    `schema_one_discovery` is how a test asks for the other half, and
    `test_schema_discovery` is where the partition itself is checked against the
    unpartitioned map.
    """
    from custom_components.span_panel.schema_validation import partition

    return partition(metadata)[0]


def schema_one_metadata(tree: dict[str, dict[str, str]] | None = None) -> dict[str, FieldMetadata]:
    """Curated field metadata as schema_1 builds it from a full parent/child tree.

    The capture's own by default; `tree` is a rewritten one, so a test can
    validate the metadata of the same tree it builds `schema_one_snapshot` from.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return _curated(
        build_field_metadata(_devices_from(schema_one_tree() if tree is None else tree))
    )


def schema_one_metadata_raw() -> dict[str, FieldMetadata]:
    """The adapter's map exactly as it returns it, both halves together.

    The one fixture that does *not* partition, because the partition is the
    thing under test in `test_schema_discovery`. Everywhere else, ask for a
    partitioned half by name.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return build_field_metadata(_devices_from(schema_one_tree()))


def schema_one_discovery() -> tuple[DiscoveredProperty, ...]:
    """What schema_1 declares in the reference tree that it reads nothing from.

    The other half of the same map. Held apart from `schema_one_metadata` so a
    test has to ask for it by name — a discovered path arriving unannounced in a
    curated inventory is the failure mode the namespace exists to prevent.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    from custom_components.span_panel.schema_validation import partition

    return partition(build_field_metadata(_devices_from(schema_one_tree())))[1]


def schema_one_metadata_batteryless() -> dict[str, FieldMetadata]:
    """Build the same tree with the BESS removed — no battery hardware present."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return _curated(build_field_metadata(_devices_from(schema_one_tree(without="bess"))))


def schema_one_metadata_no_pv() -> dict[str, FieldMetadata]:
    """Build the same tree with the PV device removed, power-flows still present."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return _curated(build_field_metadata(_devices_from(schema_one_tree(without="pv"))))
