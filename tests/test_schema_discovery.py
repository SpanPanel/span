"""What the panel declares that nothing here reads, surfaced to a maintainer.

`test_declared_but_unread` asks this question of the reference capture and
answers it by experiment. It is the right check and it is fixture-bound: a real
panel that starts publishing a property fails nothing until somebody recaptures.
The adapter answers the same question at runtime, for the panel in front of the
user, and this module is the consumer half — the partition that keeps those rows
out of every curated inventory, and the diagnostics block that carries them to
whoever is triaging the issue.

Two properties are load-bearing and both are asserted here rather than reviewed:

**The partition.** Discovered rows arrive in the same map as curated ones. Every
inventory downstream — the producible gate, the unread set, the exemption
annotations, the unit vocabulary — reads "in an adapter's map" as "this
integration could read this", which a discovered path is not. One namespace test
applied once is what keeps that true, and `test_the_unread_inventory_is_deaf_to
_discovery` is the mutation proof that it is applied.

**No value leaves.** A diagnostics payload goes into GitHub issues and forum
posts. `TO_REDACT` is key-based over the config entry and knows nothing about
wire property names, so nothing downstream can protect a value put in this
block. It carries declarations only, and that is checked against the capture's
own published values.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api.models import (
    DiscoveredMetadata,
    FieldMetadata,
    SpanPanelSnapshot,
    is_discovery_path,
)

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    declared_field_paths,
)
from custom_components.span_panel.schema_validation import (
    SchemaFindings,
    evaluate_field_metadata,
    partition,
)

from .adapter_fixtures import (
    SCHEMA_ONE_PANEL,
    schema_one_discovery,
    schema_one_metadata,
    schema_one_metadata_raw,
    schema_one_tree,
)

BASELINE = pathlib.Path(__file__).parent / "fixtures" / "unread_declarations_baseline.json"

_SYNTHETIC = DiscoveredMetadata(unit="°C", datatype="float", retained=True)
"""A row for a property no firmware in the fixtures declares.

Used to prove the partition by mutation rather than by inspection: a curated
inventory that is genuinely deaf to discovery is unchanged by this, and one that
merely happens to contain nothing surprising is not.
"""
_SYNTHETIC_PATH = "discovered.distribution-enclosure/status/enclosure-temperature"


# --- the partition ---------------------------------------------------------


def test_the_adapter_emits_discovered_rows_at_all() -> None:
    """The floor. Every assertion below passes trivially against an empty report."""
    raw = schema_one_metadata_raw()
    namespaced = {path for path in raw if is_discovery_path(path)}
    assert namespaced, (
        "schema_1 emitted no discovered rows for the reference tree, so nothing "
        "below is being tested. If that is real, the adapter stopped emitting them."
    )
    assert all(isinstance(raw[path], DiscoveredMetadata) for path in namespaced)


def test_partition_splits_the_map_and_loses_nothing() -> None:
    raw = schema_one_metadata_raw()
    curated, discovered = partition(raw)

    assert len(curated) + len(discovered) == len(raw)
    assert not [path for path in curated if is_discovery_path(path)]
    assert {entry.path for entry in discovered} == {path for path in raw if is_discovery_path(path)}
    assert [entry.path for entry in discovered] == sorted(entry.path for entry in discovered)


def test_a_discovered_row_carries_the_declaration_and_the_retention() -> None:
    by_path = {entry.path: entry for entry in schema_one_discovery()}
    raw = schema_one_metadata_raw()
    for path, entry in by_path.items():
        row = raw[path]
        assert entry.datatype == row.datatype
        assert entry.unit == row.unit
    assert by_path["discovered.distribution-enclosure/status/time-zone"].retained is True
    # Both retention states have to appear, or the row is not carrying retention at
    # all. `lugs/connection/feeds-device-status` is the unretained one for the same
    # reason `circuit/connection/count` was before the capture dropped it: the
    # panel declares the property and no producer publishes a value for it.
    assert by_path["discovered.lugs/connection/feeds-device-status"].retained is False


def test_a_namespaced_row_without_the_enriched_type_still_reports() -> None:
    """The namespace is the contract; the row type is the enrichment.

    An adapter distribution built against a later library could namespace a row
    and carry a plain `FieldMetadata`. Dropping it would be the worst of both —
    absent from the curated inventory *and* absent from the report.
    """
    _curated, discovered = partition({_SYNTHETIC_PATH: FieldMetadata(unit="°C", datatype="float")})
    assert len(discovered) == 1
    assert discovered[0].retained is None
    assert discovered[0].unit == "°C"


# --- discovered paths reach no curated inventory ---------------------------


def test_no_curated_inventory_names_a_discovered_path() -> None:
    """The three enumerations the conformance gate and the unread gate consult."""
    for path in declared_field_paths():
        assert not is_discovery_path(path)
    for path in RESIDUAL_EXEMPT_PATHS:
        assert not is_discovery_path(path)
    for key in json.loads(BASELINE.read_text(encoding="utf-8")):
        assert not is_discovery_path(key), (
            f"{key} is a discovered path in the unread baseline. The baseline is the "
            "consumer's own backlog, decided per line; discovery is a report about "
            "the panel and nothing may be written into the baseline from it."
        )


def test_the_curated_fixture_hands_out_no_discovered_row() -> None:
    """Every other test module reads this fixture, so the guarantee lives here."""
    assert not [path for path in schema_one_metadata() if is_discovery_path(path)]


def test_the_unread_inventory_is_deaf_to_discovery() -> None:
    """The mutation proof, and the one that matters for cost #4.

    `unread` is "we produce this and render nothing from it" — ten known entries
    with reasons. A discovered row landing there would bury them under whatever a
    firmware release added, and would make the count depend on the panel in front
    of the user rather than on this integration's backlog.
    """
    raw = schema_one_metadata_raw()
    before = evaluate_field_metadata(raw)
    after = evaluate_field_metadata({**raw, _SYNTHETIC_PATH: _SYNTHETIC})

    assert after.unread == before.unread
    assert after.unresolved == before.unresolved
    assert after.unit_mismatches == before.unit_mismatches
    assert _SYNTHETIC_PATH in {entry.path for entry in after.discovered}
    assert len(after.discovered) == len(before.discovered) + 1


def test_a_discovered_row_raises_no_unit_mismatch() -> None:
    """A wire unit is not a claim about any sensor we declare.

    `°C` matches no sensor's declared unit, so an unpartitioned map would report
    it as a mismatch and, downstream, as a Repair the user cannot act on.
    """
    findings = evaluate_field_metadata({**schema_one_metadata_raw(), _SYNTHETIC_PATH: _SYNTHETIC})
    assert not [m for m in findings.unit_mismatches if is_discovery_path(m.field_path)]


# --- the diagnostics block -------------------------------------------------


def _entry(findings: SchemaFindings | None) -> MockConfigEntry:
    coordinator = MagicMock()
    coordinator.data = _snapshot()
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = findings
    entry = MockConfigEntry(domain=DOMAIN, title="SPAN Panel", unique_id="example-40t-001")
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )
    return entry


def _snapshot() -> SpanPanelSnapshot:
    from .adapter_fixtures import schema_one_snapshot

    return schema_one_snapshot()


async def test_diagnostics_carries_the_discovery_report(hass: HomeAssistant) -> None:
    """The block a maintainer reads off an issue attachment."""
    findings = evaluate_field_metadata(schema_one_metadata_raw())
    result = await async_get_config_entry_diagnostics(hass, _entry(findings))

    block = result["schema_discovery"]
    assert block["available"] is True
    assert block["count"] == len(findings.discovered)
    assert block["count"] > 0
    # Addressed by path rather than by position. The ordering is pinned once, in
    # `test_partition_splits_the_map_and_loses_nothing`; re-pinning it here meant
    # that a capture dropping any earlier-sorting property failed this test with a
    # diff about the wrong row, which is what happened when `connection/count` went
    # away.
    by_path = {row["path"]: row for row in block["properties"]}
    assert by_path["discovered.pv/info/serial-number"] == {
        "path": "discovered.pv/info/serial-number",
        "datatype": "string",
        "unit": None,
        "retained": False,
    }
    assert {key for row in block["properties"] for key in row} == {
        "path",
        "datatype",
        "unit",
        "retained",
    }


async def test_diagnostics_says_unavailable_rather_than_empty(hass: HomeAssistant) -> None:
    """No metadata yet is a real state on a reconnect, and it is not "nothing to report"."""
    block = (await async_get_config_entry_diagnostics(hass, _entry(None)))["schema_discovery"]
    assert block == {"available": False, "count": 0, "properties": []}


async def test_no_published_value_reaches_the_discovery_block(hass: HomeAssistant) -> None:
    """The privacy constraint, checked against the capture's own values.

    Diagnostics leave the house. `TO_REDACT` is key-based over the config entry
    and knows nothing about wire property names, so a value added here would be
    published verbatim — including the postal code and the time zone, which the
    reference panel does publish and this integration deliberately does not
    surface.
    """
    tree = schema_one_tree()
    published = {
        value
        for topics in tree.values()
        for topic, value in topics.items()
        if not topic.startswith("$") and value
    }
    # The declaration vocabulary a row is allowed to be built from: device type
    # tails, node ids and property ids all appear inside a `$description`. A
    # value that is also one of those cannot be told apart from its declaration,
    # and is excluded rather than scanned for.
    vocabulary: set[str] = set()
    for topics in tree.values():
        for token in json.dumps(json.loads(topics["$description"])).replace('"', " ").split():
            vocabulary.update(token.split("."))
            vocabulary.update(token.split(","))
            vocabulary.add(token)

    findings = evaluate_field_metadata(schema_one_metadata_raw())
    block = (await async_get_config_entry_diagnostics(hass, _entry(findings)))["schema_discovery"]

    # `retained` is scanned by type rather than by content: it is a bool, so it
    # has no room for a value, and its JSON rendering collides with the literal
    # "false" a boolean property publishes. The three string fields are where a
    # value could actually appear, and they are what the scan reads.
    strings = [
        text
        for row in block["properties"]
        for text in (row["path"], row["datatype"], row["unit"])
        if isinstance(text, str)
    ]
    leaked = sorted(
        value for value in published - vocabulary if any(value in text for text in strings)
    )
    assert not leaked, f"published values reached the diagnostics discovery block: {leaked}"
    for row in block["properties"]:
        assert isinstance(row["retained"], bool)
        assert row["path"].startswith("discovered.")


async def test_the_discovery_block_creates_nothing_a_user_sees(hass: HomeAssistant) -> None:
    """Maintainer-facing only: no path here is one an entity reads.

    The line between step 1 and step 3. A discovered path becoming an entity's
    source would mean adoption shipped by accident, and adoption is deliberately
    not built — its costs (notice aggregation, a denylist, the accumulator
    register) are unsettled.
    """
    readable = declared_field_paths() | frozenset(RESIDUAL_EXEMPT_PATHS)
    for entry in schema_one_discovery():
        assert entry.path not in readable


# --- it bites in both directions -------------------------------------------


def _tree_declaring(
    node: str, property_id: str, definition: dict[str, str], value: str
) -> dict[str, dict[str, str]]:
    tree = schema_one_tree()
    description: dict[str, dict[str, dict[str, dict[str, object]]]] = json.loads(
        tree[SCHEMA_ONE_PANEL]["$description"]
    )
    description["nodes"][node]["properties"][property_id] = dict(definition)
    tree[SCHEMA_ONE_PANEL]["$description"] = json.dumps(description)
    tree[SCHEMA_ONE_PANEL][f"{node}/{property_id}"] = value
    return tree


def _discovery_for(tree: dict[str, dict[str, str]]) -> dict[str, dict[str, object]]:
    from ebus_sdk.homie import DiscoveredDevice
    from span_panel_api_schema_1.field_metadata import build_field_metadata

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
    findings = evaluate_field_metadata(build_field_metadata(devices))
    return {
        entry.path: {"datatype": entry.datatype, "unit": entry.unit, "retained": entry.retained}
        for entry in findings.discovered
    }


def test_a_property_the_panel_adds_shows_up_with_its_declaration() -> None:
    """A firmware release that starts declaring something reaches the report.

    This is the whole of what step 1 buys over the fixture-bound gate: no
    recapture, no release, and the maintainer sees it on the next attachment.
    """
    tree = _tree_declaring(
        "status",
        "enclosure-temperature",
        {"name": "Enclosure temperature", "datatype": "float", "unit": "°C"},
        "41.5",
    )
    reported = _discovery_for(tree)
    assert reported[_SYNTHETIC_PATH] == {"datatype": "float", "unit": "°C", "retained": True}
    assert "41.5" not in json.dumps(reported)


def test_a_property_that_becomes_read_leaves_the_report() -> None:
    """The acceptance criterion for acting on a discovered row.

    Adding a `_PROPERTY_FIELD_MAP` row in the library is what a maintainer does
    next, and the row leaving this report is how they know it landed. Proved by
    the property that already has one: `status/cloud-connection` is declared,
    mapped, and absent from the report, while `status/postal-code` beside it on
    the same node is declared, unmapped, and present.
    """
    reported = _discovery_for(schema_one_tree())
    assert "discovered.distribution-enclosure/status/postal-code" in reported
    assert "discovered.distribution-enclosure/status/cloud-connection" not in reported
    assert "panel.vendor_cloud" in schema_one_metadata()
