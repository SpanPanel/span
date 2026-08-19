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
from span_panel_api.models import FieldMetadata

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _devices(name: str) -> list[DiscoveredDevice]:
    """Rebuild discovered devices from a retained-topic capture.

    Mirrors the library's own builder (test_schema_one_devices.py:26-37):
    `update_description` parses the JSON string, `update_property` stores each
    non-`$` topic as a property value.
    """
    tree = json.loads((_FIXTURES / name).read_text())
    devices: list[DiscoveredDevice] = []
    for device_id, topics in tree.items():
        device = DiscoveredDevice(device_id, "ebus")
        device.update_description(topics["$description"])
        device.update_state(topics.get("$state", "ready"))
        for topic, value in topics.items():
            if topic.startswith("$"):
                continue
            node, _, prop = topic.partition("/")
            if prop:
                device.update_property(node, prop, value)
        devices.append(device)
    return devices


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
