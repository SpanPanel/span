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
from span_panel_api_schema_1.reference_payloads import parent_child_tree

from custom_components.span_panel.schema_validation import DiscoveredProperty

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"

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

    **Read from the library's package data, not vendored here.** The adapter ships
    `parent_child_tree.json` precisely so a consumer can read it -- its own README
    says "never by path", and that a consumer pinning a version gets the bytes
    that version's parser was written against. This repository used to keep a
    byte-identical copy under `tests/fixtures/`, which is one more artifact to go
    stale and nothing checked the two still agreed. Reading the published one
    means the capture moves when the pinned adapter moves, and the library's own
    peer-conformance check against the producer covers it transitively.

    Copied per call, and one level deep, which is as deep as a topic goes: a test
    proves a reading came off the wire by republishing it and asserting the entity
    followed, and that is impossible against a shared immutable capture.

    `without` drops one device, which is how the batteryless and PV-less variants
    are made. They were separate files and are now derived, so they cannot drift
    from the base by construction -- the only difference each ever had was the one
    missing device.
    """
    tree = {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}
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
    """Curated field metadata as schema_0 builds it from the flat REST schema.

    Partitioned like its schema_1 counterpart even though the flat adapter emits
    no discovered rows: the fixtures state the rule, not the current contents of
    one adapter.
    """
    from span_panel_api_schema_0.field_metadata import build_field_metadata

    raw = json.loads((_FIXTURES / "schema_zero_types.json").read_text())
    return _curated(build_field_metadata(raw["types"]))


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


def schema_one_metadata() -> dict[str, FieldMetadata]:
    """Curated field metadata as schema_1 builds it from a full parent/child tree."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return _curated(build_field_metadata(_devices_from(schema_one_tree())))


def schema_one_metadata_raw() -> dict[str, FieldMetadata]:
    """The adapter's map exactly as it returns it, both halves together.

    The one fixture that does *not* partition, because the partition is the
    thing under test in `test_schema_discovery`. Everywhere else, ask for a
    partitioned half by name.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata

    return build_field_metadata(_devices_from(schema_one_tree()))


def schema_one_discovery() -> tuple[DiscoveredProperty, ...]:
    """What schema_1 declares in the vendored tree that it reads nothing from.

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
