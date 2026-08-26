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
from typing import NamedTuple

from ebus_sdk.homie import DiscoveredDevice
from span_panel_api.models import FieldMetadata, SpanPanelSnapshot, V2HomieSchema

from custom_components.span_panel.schema_validation import DiscoveredProperty

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"

SCHEMA_ONE_TREE = _FIXTURES / "schema_one_tree.json"
"""The vendored parent/child capture. See `tests/fixtures/README.md` for provenance."""

SCHEMA_ONE_TREE_SOURCE = _FIXTURES / "schema_one_tree.source"
"""The release `SCHEMA_ONE_TREE` was copied from, checked by `test_fixture_provenance.py`."""

SCHEMA_ONE_DISTRIBUTION = "span-panel-api-schema-1"
"""The distribution that publishes the capture, and whose installed version the claim names."""

CHECKOUT_VARIABLE = "SPAN_PANEL_API_DIR"
"""Names a `span-panel-api` checkout. Already in `.env.example` for editable installs."""

SCHEMA_ONE_SOURCE_PATHS = (
    pathlib.Path("tests") / "reference_payloads" / "parent_child_tree.json",
    pathlib.Path("packages")
    / "schema-1"
    / "src"
    / "span_panel_api_schema_1"
    / "reference_payloads"
    / "parent_child_tree.json",
)
"""Where the capture lives inside a `span-panel-api` checkout, newest location first.

Two of them because the file is moving. `span-panel-api#162` takes the reference
payloads out of the schema_1 wheel and makes them ordinary test fixtures -- which
is the whole reason this repository vendors a copy rather than importing one --
but that change is unmerged, so a checkout on `main` today still holds the file
under `packages/schema-1/`. Checking the new path first follows the file rather
than the merge, and works against a checkout on either side of it.

Delete the second entry once #162 is merged **and** no release this repository can
pin still predates it: the CI clone and `scripts/refresh-vendored-capture.py` both
position themselves at a recorded release, so the old path stays reachable for as
long as that release can be a pre-#162 one.

Here rather than in `test_fixture_provenance.py` because the refresh script needs
the same two paths. A second copy of this list is how the fallback silently
outlives the merge in one place and not the other.
"""

SCHEMA_ONE_RELEASE_DECLARATION = pathlib.Path("packages") / "schema-1" / "pyproject.toml"
"""Where a checkout declares which schema-1 release it is."""


class VendoredSource(NamedTuple):
    """The distribution and release a vendored fixture was copied from."""

    distribution: str
    version: str


def schema_one_source() -> VendoredSource:
    """Read the release recorded beside the vendored capture.

    Recorded as a pinned requirement -- `span-panel-api-schema-1==1.1.0` -- rather
    than a bare version, for two reasons. It names *which* distribution the claim
    is about, and this repository pins three of them; and it is written in the
    same vocabulary as the `manifest.json` requirement it has to agree with, so
    the two can be compared by eye during a bump as well as by
    `test_fixture_provenance.py`.

    It lives in its own file rather than inside the payload because the refresh
    procedure is a byte-for-byte copy: a key added to the JSON would be
    overwritten by every refresh, and a payload that differs from its source is
    exactly what the README forbids.
    """
    recorded = SCHEMA_ONE_TREE_SOURCE.read_text().strip()
    distribution, separator, version = recorded.partition("==")
    if not (separator and distribution and version):
        raise ValueError(
            f"{SCHEMA_ONE_TREE_SOURCE} must hold one pinned requirement naming the "
            f"release the vendored capture was copied from, such as "
            f"'span-panel-api-schema-1==1.1.0'. It holds {recorded!r}."
        )
    return VendoredSource(distribution, version)


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

    **Vendored here, not read from the library's package data.** The capture is
    test data; carrying it in the runtime wheel put 56 KB of it on every user's
    disk, and reading it by import made this suite depend on a distribution
    continuing to ship files nothing at runtime reads. The objection to a copy was
    that it goes stale in silence -- answered by `schema_one_source()` and
    `test_fixture_provenance.py`, which hold the recorded release against the one
    actually installed, so a moved pin with an unrefreshed copy fails CI by name.

    Copied per call, and one level deep, which is as deep as a topic goes: a test
    proves a reading came off the wire by republishing it and asserting the entity
    followed, and that is impossible against a shared immutable capture.

    `without` drops one device, which is how the batteryless and PV-less variants
    are made. They were separate files and are now derived, so they cannot drift
    from the base by construction -- the only difference each ever had was the one
    missing device.
    """
    capture: dict[str, dict[str, str]] = json.loads(SCHEMA_ONE_TREE.read_text())
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


SCHEMA_ZERO_SERIAL = "sp3-synthetic-0001"
"""Serial the flat fixture adapter is built for. Synthetic, like every id here."""

SCHEMA_ZERO_FIRMWARE = "r202612"
"""A release in the flat window (r202603-r202627), which is what makes it flat.

`V2HomieSchema.data_model_version` is left at its default `None` for the same
reason: absent is the flat discriminator, and the dispatcher reads it.
"""


def schema_zero_snapshot() -> SpanPanelSnapshot:
    """Build a snapshot as the real flat adapter builds it, before any topic arrives.

    The counterpart to `schema_one_snapshot`, and it exists for the fields the
    flat adapter never writes. Retained topics only ever *add* to this: a field
    the adapter does not reference at all keeps whatever value the snapshot
    dataclass gives it, and no message can move it.

    `lugs_at_service_entrance` is such a field. It is a plain `bool` defaulting
    to True, and `span_panel_api_schema_0` contains no reference to the name, so
    on flat firmware the value a consumer reads is the library's default rather
    than anything the panel said. Driving the adapter rather than the snapshot
    factory is what makes that a fact about the adapter instead of a fact the
    test wrote down itself.
    """
    from span_panel_api_schema_0.adapter import SchemaZeroAdapter

    raw = json.loads((_FIXTURES / "schema_zero_types.json").read_text())
    schema = V2HomieSchema(
        firmware_version=SCHEMA_ZERO_FIRMWARE,
        types_schema_hash="0" * 16,
        types=raw["types"],
    )
    return SchemaZeroAdapter(SCHEMA_ZERO_SERIAL, schema).build_snapshot()


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
