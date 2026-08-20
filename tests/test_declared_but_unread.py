"""Every property the panel declares must reach a user, or say why it does not.

The consumer-side mirror of panelbench's `test_declared_but_unvalued`, which
asks the producer whether it ever publishes what it declares. This asks whether
anything ever reads what arrives.

**Why this and not the producible gate.** `test_field_path_conformance` starts
from what the integration declares it reads and checks an adapter produces it.
That direction cannot see a property nobody reads: an unread declaration is
absent from every list the gate consults, so it is invisible by construction.
`panel.wifi_ssid` is the worked example — flat surfaces it, v1.0 declares it,
schema_1 maps nothing to it, and its `RESIDUAL_EXEMPT_PATHS` annotation said
`SCHEMA_0_ONLY`, which was *true* and still sanctioned a silent regression.
Every check in the codebase agreed, and a user upgrading lost an attribute.

**How consumption is decided, and why not from a list.** A hand-kept map of
"properties we read" is what made a new property invisible in the first place,
so this derives the answer by experiment instead: republish one declared
property with a different value, rebuild the snapshot through the real schema_1
mapper, and see which snapshot fields moved. A property that moves a field the
integration reads is surfaced; one that moves nothing reaches nobody. Nothing
here restates `_PROPERTY_FIELD_MAP`, the lugs direction tables, the topology
readers or the device_info builders — the experiment sees all of them, and sees
them the way a user does, through what the panel actually renders.

That is deliberately stricter than "mapped by `_PROPERTY_FIELD_MAP`". A property
the library reads into a snapshot field no entity, attribute or device card ever
touches has not reached anybody: `circuit.is_240v`, `evse.part_number` and
`pv.software_version` are each one library line and no user-visible effect. They
are baselined here rather than counted as read.

**The two enumerations, and why neither can absorb a new property.**
`_INTERNAL_ROUTES` holds what the snapshot cannot express — adapter dispatch, a
shadowed fallback tier, a topology branch no producer reaches. The baseline file
holds what is genuinely unread, one reason per line. Both are compared as exact
sets, so an entry that stops being true fails just as loudly as a declaration
that arrives untriaged. A new property can only ever land in either by somebody
writing the line.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import dataclasses
import json
import pathlib
from typing import NamedTuple

from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
    SpanPVSnapshot,
)

from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    declared_field_paths,
)
from tests.adapter_fixtures import schema_one_snapshot, schema_one_tree

BASELINE = pathlib.Path(__file__).parent / "fixtures" / "unread_declarations_baseline.json"


class Declaration(NamedTuple):
    """One ``(device type, node, property)`` the fixture's `$description` declares.

    Keyed by device *type* rather than device id, matching the granularity of
    the capability catalogs and of the gap inventory: five circuits declare the
    same properties, and three of them going unread is one gap, not three.
    """

    device_type: str
    node: str
    property_id: str

    def __str__(self) -> str:
        """Render as the baseline file's key: ``device-type/node/property``."""
        return f"{self.device_type}/{self.node}/{self.property_id}"


_INTERNAL_ROUTES: Mapping[Declaration, str] = {
    Declaration("distribution-enclosure", "info", "data-model-version"): (
        "tier-1 adapter dispatch (span_panel_api/dispatch.py) — it chooses which "
        "adapter parses the tree, so it is consumed before any snapshot exists"
    ),
    Declaration("distribution-enclosure", "shed", "asserted-islanding-state"): (
        "tier 2 of resolve_islanding_state (schema_1 panel.py), shadowed in this "
        "fixture by the MID's tier-1 answer, and the write target of the existing "
        "dominant-power-source control (schema_1 adapter.py)"
    ),
    Declaration("lugs", "connection", "feeds-device-id"): (
        "the downstream-lugs feedthrough branch of resolve_relative_position "
        "(schema_1 devices.py), which no producer currently reaches"
    ),
}
"""Declarations consumed by a route no snapshot field can show.

Three, and each names the code that reads it. This is the category the
experiment cannot measure, so it is the category most at risk of becoming the
allowlist that swallowed the problem — hence
`test_no_internal_route_is_observable_after_all`, which fails the moment an
entry stops being needed.
"""


def _mapping(value: object) -> Mapping[str, object]:
    """Return `value` as a string-keyed mapping, or an empty one.

    `json.loads` answers `object`, and every level of a Homie `$description` is
    optional. Narrowing here rather than at each call site keeps the walk below
    readable and keeps `Any` out of the module.
    """
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


class _Property(NamedTuple):
    """One declared property, and the device that declares it."""

    device_id: str
    topic: str
    datatype: str
    format_spec: str


def _declared(tree: Mapping[str, Mapping[str, str]]) -> dict[Declaration, list[_Property]]:
    """Every property declared anywhere in the tree, by declaration.

    The authoritative property set is the `$description`, per the enclosure data
    model: what a device publishes is a subset of what it declares, and the gap
    between the two is precisely what this module is about.
    """
    found: dict[Declaration, list[_Property]] = {}
    for device_id, topics in tree.items():
        description = _mapping(json.loads(topics["$description"]))
        device_type = _text(description.get("type")).rsplit(".", 1)[-1]
        for node_id, node in _mapping(description.get("nodes")).items():
            for property_id, definition in _mapping(_mapping(node).get("properties")).items():
                body = _mapping(definition)
                found.setdefault(Declaration(device_type, node_id, property_id), []).append(
                    _Property(
                        device_id=device_id,
                        topic=f"{node_id}/{property_id}",
                        datatype=_text(body.get("datatype")),
                        format_spec=_text(body.get("format")),
                    )
                )
    return found


def _perturbed(declared: _Property, current: str | None) -> str:
    """Return a legal value for this property that differs from `current`.

    Legal matters: a parser that rejects the probe value would leave the field
    unchanged and the property would read as unconsumed. So the replacement is
    built from the declared `datatype` and `format`, which is the same
    information the adapter parses against.

    `current` is `None` for a property the fixture declares and never publishes
    — 19 of the 203 instances. Publishing one is the right probe for exactly
    those: it asks whether a value arriving would change anything, which is the
    question `status/wifi-ssid` needed answering.
    """
    if declared.datatype in {"float", "integer"}:
        try:
            number = float(current or "")
        except ValueError:
            return "7" if declared.datatype == "integer" else "7.5"
        return str(int(number) + 7) if declared.datatype == "integer" else str(number + 7.5)
    if declared.datatype == "boolean":
        return "false" if (current or "").lower() == "true" else "true"
    if declared.datatype == "enum":
        for option in declared.format_spec.split(","):
            if option and option != current:
                return option
    return "probe-value" if current != "probe-value" else "probe-value-2"


_SubSnapshot = (
    SpanCircuitSnapshot | SpanEvseSnapshot | SpanBatterySnapshot | SpanPVSnapshot | SpanMidSnapshot
)

_COLLECTIONS = frozenset({"circuits", "evse", "battery", "pv", "mid"})
"""Panel-snapshot attributes that hold sub-snapshots rather than a reading.

Their fields are addressed by their own prefix — `circuit.x`, not
`panel.circuits.x` — matching the field-path convention `field_paths` documents
and `RESIDUAL_EXEMPT_PATHS` is written in.
"""


def _record(fields: dict[str, str], prefix: str, obj: _SubSnapshot, suffix: str = "") -> None:
    for field in dataclasses.fields(obj):
        fields[f"{prefix}.{field.name}{suffix}"] = repr(getattr(obj, field.name))


def _snapshot_fields(snapshot: SpanPanelSnapshot) -> dict[str, str]:
    """Flatten a snapshot to ``{field path: value}``.

    The per-instance collections are keyed by circuit and EVSE id so two
    circuits cannot mask each other's change; `_bare` strips the key again for
    the reader lookup, which is per field and not per instance.

    Values are held as `repr` rather than compared by equality so the diff is a
    plain set operation over strings, whatever a field happens to hold.
    """
    fields: dict[str, str] = {}
    for field in dataclasses.fields(snapshot):
        if field.name not in _COLLECTIONS:
            fields[f"panel.{field.name}"] = repr(getattr(snapshot, field.name))
    for circuit_id, circuit in snapshot.circuits.items():
        _record(fields, "circuit", circuit, f"@{circuit_id}")
    for evse_key, evse in snapshot.evse.items():
        _record(fields, "evse", evse, f"@{evse_key}")
    _record(fields, "battery", snapshot.battery)
    _record(fields, "pv", snapshot.pv)
    if snapshot.mid is not None:
        _record(fields, "mid", snapshot.mid)
    return fields


def _bare(field_path: str) -> str:
    return field_path.split("@", 1)[0]


def _moved_fields() -> dict[Declaration, frozenset[str]]:
    """Republish each declared property once; return the snapshot fields it moved.

    One rebuild per declaring device, so the two lugs devices and the five
    circuits are probed separately and their results unioned: only the upstream
    lugs' `fed-by-*` properties are read, and a single probe against whichever
    came first would answer for both.
    """
    tree = schema_one_tree()
    baseline = _snapshot_fields(schema_one_snapshot(tree))
    moved: dict[Declaration, frozenset[str]] = {}

    for declaration, instances in _declared(tree).items():
        changed: set[str] = set()
        for instance in instances:
            current = tree[instance.device_id].get(instance.topic)
            replacement = _perturbed(instance, current)
            assert replacement != current, (
                f"{declaration} on {instance.device_id}: the probe value equals the "
                f"published one ({current!r}), so this property is not being tested"
            )
            mutated = {device_id: dict(topics) for device_id, topics in tree.items()}
            mutated[instance.device_id][instance.topic] = replacement
            after = _snapshot_fields(schema_one_snapshot(mutated))
            changed.update(path for path, value in after.items() if baseline.get(path) != value)
        moved[declaration] = frozenset(changed)
    return moved


def _read_field_paths() -> frozenset[str]:
    """Every snapshot field the integration reads, by any route.

    `declared_field_paths()` is descriptions plus residual entity-code reads;
    `RESIDUAL_EXEMPT_PATHS` is the rest — device_info fields, circuit
    attributes, entity-creation gates. Together they are the integration's
    complete enumeration of its own reads, which is the claim
    `test_every_exempt_path_still_has_a_reader` and the conformance suite hold
    it to. A field in neither is one nothing renders.
    """
    return declared_field_paths() | frozenset(RESIDUAL_EXEMPT_PATHS)


def _classified() -> tuple[dict[Declaration, frozenset[str]], dict[Declaration, frozenset[str]]]:
    """Split every declaration into (surfaced, unread)."""
    read = _read_field_paths()
    surfaced: dict[Declaration, frozenset[str]] = {}
    unread: dict[Declaration, frozenset[str]] = {}
    for declaration, moved in _moved_fields().items():
        if any(_bare(path) in read for path in moved):
            surfaced[declaration] = moved
        else:
            unread[declaration] = moved
    return surfaced, unread


def _baseline() -> dict[Declaration, str]:
    loaded: dict[str, str] = json.loads(BASELINE.read_text(encoding="utf-8"))
    entries: dict[Declaration, str] = {}
    for key, reason in loaded.items():
        device_type, node, property_id = key.split("/", 2)
        entries[Declaration(device_type, node, property_id)] = reason
    return entries


def _lines(declarations: Iterable[Declaration]) -> str:
    return "\n".join(f"  {declaration}" for declaration in sorted(declarations)) or "  (none)"


def test_the_unread_declarations_match_the_recorded_baseline() -> None:
    """Fails in both directions, so neither a gap nor a fix can land unnoticed.

    A declaration nothing reads must be triaged: surfaced by a catch-up task, or
    written into the baseline with the reason it stays unread. A declaration that
    starts being read must lose its baseline line in the same commit — otherwise
    the file drifts into a description of an older codebase and the count it
    reports stops meaning anything.
    """
    _, unread = _classified()
    expected = _baseline()

    appeared = sorted(set(unread) - set(expected) - set(_INTERNAL_ROUTES))
    resolved = sorted(set(expected) - set(unread))

    assert set(unread) - set(_INTERNAL_ROUTES) == set(expected), (
        "the set of declarations nothing reads moved.\n"
        f"  newly unread (nothing renders these):\n{_lines(appeared)}\n"
        f"  now read (delete their lines from {BASELINE.name}):\n{_lines(resolved)}\n\n"
        "A newly unread property is a declaration that reaches no entity, attribute "
        "or device card. Surface it, or record why it stays unread."
    )


def test_every_baseline_entry_carries_a_reason() -> None:
    """A line with no reason is an allowlist entry wearing a baseline's clothes."""
    empty = sorted(str(key) for key, reason in _baseline().items() if len(reason.split()) < 4)
    assert not empty, (
        f"baseline entries with no usable reason: {empty}. Each line says why the "
        "property is not surfaced, so a reader can tell a deliberate skip from a backlog item."
    )


def test_every_baseline_entry_is_still_declared() -> None:
    """A baseline outlives its declaration silently; the file only ever grows."""
    declared = set(_declared(schema_one_tree()))
    stale = sorted(str(key) for key in _baseline() if key not in declared)
    assert not stale, (
        f"baseline entries the fixture no longer declares: {stale}. The property "
        "went away; drop its line with it."
    )


def test_no_internal_route_is_observable_after_all() -> None:
    """An internal-route entry must be the only thing keeping its property out.

    This is the entry that could quietly become an allowlist: unlike the
    baseline it claims the property *is* consumed, and a claim the experiment
    could check is one it should. So the moment a route's property does move a
    field the integration reads, the entry has to go — otherwise the next
    property added beside it inherits an exemption nobody re-examined.
    """
    surfaced, _ = _classified()
    redundant = sorted(str(key) for key in _INTERNAL_ROUTES if key in surfaced)
    assert not redundant, (
        f"internal-route entries whose property now reaches a reader: {redundant}. "
        "The route is no longer the only thing consuming it; delete the entry."
    )


def test_every_internal_route_is_still_declared() -> None:
    declared = set(_declared(schema_one_tree()))
    stale = sorted(str(key) for key in _INTERNAL_ROUTES if key not in declared)
    assert not stale, f"internal-route entries the fixture no longer declares: {stale}"


def test_the_probe_moves_something_for_a_known_reading() -> None:
    """The experiment must be able to observe a change at all.

    Every assertion above is satisfied by a probe that changes nothing, ever:
    the unread set would simply be every declaration, matched by a baseline
    somebody had grown to fit. This is the floor under that — a property whose
    reading is unarguably rendered has to come back surfaced, and has to name
    the field it moved.
    """
    surfaced, _ = _classified()
    power = Declaration("circuit", "meter", "active-power")
    assert power in surfaced
    assert any(_bare(path) == "circuit.instant_power_w" for path in surfaced[power])
