"""Build real adapter field metadata from vendored fixtures.

The library's own harness compares wire-level deltas between schemas; it cannot
know what this integration declares it reads. These helpers give the
integration's tests real adapter output to check declarations against.

Uses the real `ebus_sdk.DiscoveredDevice` rather than a stand-in. A
description-only stand-in is not sufficient: `_downstream_lugs_metadata` ->
`find_lugs` reads property *values* via `device.get_property(...)`, so a stand-in
either raises or silently loses the five downstream/feedthrough paths. ebus-sdk
arrives transitively with span-panel-api-schema-1, so the import is free.
"""

from __future__ import annotations

import json
import pathlib

from ebus_sdk.homie import DiscoveredDevice
from span_panel_api.models import FieldMetadata, SpanPanelSnapshot

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"

SCHEMA_ONE_PANEL = "example-40t-001"
"""Device id of the enclosure in `schema_one_tree.json`.

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


def _devices(name: str) -> list[DiscoveredDevice]:
    """Rebuild discovered devices from a named retained-topic capture."""
    tree = json.loads((_FIXTURES / name).read_text())
    return [_device(device_id, topics) for device_id, topics in tree.items()]


def schema_one_tree() -> dict[str, dict[str, str]]:
    """A mutable copy of the parent/child capture, ready to be rewritten.

    Copied per call, and one level deep, which is as deep as a topic goes: a
    test proves a reading came off the wire by republishing it and asserting the
    entity followed, and that is impossible against a shared immutable capture.
    """
    tree = json.loads((_FIXTURES / "schema_one_tree.json").read_text())
    return {device_id: dict(topics) for device_id, topics in tree.items()}


def schema_one_snapshot(tree: dict[str, dict[str, str]] | None = None) -> SpanPanelSnapshot:
    """Build a real snapshot from the capture, through the real schema_1 mapper.

    The point of going the long way round rather than through
    `SpanPanelSnapshotFactory`: a factory takes the value a test hands it, so an
    assertion against it proves only that the test and the entity agree. Driving
    the actual adapter over the actual capture makes the published topic the
    source of truth, so republishing one is a mutation the entity has to follow.
    """
    from span_panel_api_schema_1.snapshot import build_snapshot

    tree = schema_one_tree() if tree is None else tree
    panel = _device(SCHEMA_ONE_PANEL, tree[SCHEMA_ONE_PANEL])
    children = [
        _device(device_id, topics)
        for device_id, topics in tree.items()
        if device_id != SCHEMA_ONE_PANEL
    ]
    return build_snapshot(panel, children)


def schema_zero_metadata() -> dict[str, FieldMetadata]:
    """Field metadata as schema_0 builds it from the flat REST schema."""
    from span_panel_api_schema_0.field_metadata import build_field_metadata

    raw = json.loads((_FIXTURES / "schema_zero_types.json").read_text())
    return build_field_metadata(raw["types"])


def schema_one_metadata() -> dict[str, FieldMetadata]:
    """Field metadata as schema_1 builds it from a full parent/child tree."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return build_field_metadata(_devices("schema_one_tree.json"))


def schema_one_metadata_batteryless() -> dict[str, FieldMetadata]:
    """Build the same tree with the BESS removed — no battery hardware present."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return build_field_metadata(_devices("schema_one_tree_batteryless.json"))


def schema_one_metadata_no_pv() -> dict[str, FieldMetadata]:
    """Build the same tree with the PV device removed, power-flows still present."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return build_field_metadata(_devices("schema_one_tree_no_pv.json"))
