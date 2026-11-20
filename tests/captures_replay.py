"""Replay a captured panel through the pinned library, as the broker delivers it.

The captures are panels' retained MQTT trees, the reference captures the pinned
emitter package ships and `ebus_panel_sim.load_reference_capture` reads, named
by their handles there: per device, its `$description` and the value of every
property it published. Nothing here interprets them. Each capture is turned
back into the retained topics the broker would replay, and those are handed
one message at a time to whichever adapter the library's own
dispatch picks for the panel's `info/data-model-version` -- the same parser,
selected the same way, that `SpanMqttClient` builds on connect. So a snapshot
taken here is the snapshot the pinned library produces for that panel, and the
pin is what moves it.

`ReplayClient` replaces `SpanMqttClient` whole with such a replay, so a test can
run the integration's real setup -- coordinator, platforms, registries -- over a
static tree: nothing reconnects, goes offline or changes schema, and a snapshot
streams only when a test pushes one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import functools
import json
from typing import Final

from ebus_panel_sim import Tree, load_reference_capture
from ebus_sdk import encode_empty_string
from span_panel_api import (
    ControlInterceptor,
    LeafNameMismatch,
    MaskedTree,
    SpanPanelSnapshot,
    TreeExportingAdapter,
)
from span_panel_api.adapters import resolve_adapter
from span_panel_api.dispatch import select_adapter_key
from span_panel_api.exceptions import SpanPanelError
from span_panel_api.models import FieldMetadata, V2HomieSchema
from span_panel_api.protocol import SchemaAdapter

from .adapter_fixtures import tree_snapshot

PANEL_TYPE: Final = "energy.ebus.device.distribution-enclosure"
HOMIE_PREFIX: Final = "ebus/5"

RetainedTree = dict[str, dict[str, str]]
"""Device id -> `{topic: payload}`, topics relative to the device (`meter/active-power`)."""


@dataclass(frozen=True, slots=True)
class Capture:
    """One reference capture, named by its handle in the emitter package."""

    name: str

    def tree(self) -> RetainedTree:
        """A fresh, mutable copy of the capture's retained topics."""
        return retained_tree(_published_tree(self.name))


MAIN32_CAPTURES: Final = tuple(
    Capture(name)
    for name in (
        "main32_r202633",
        "main32_r202639",
        "main32_r202639-upstream-pv",
        "main32_r202645",
        "main32_r202645-cascade",
    )
)
"""The MAIN 32 captures, whose expected files are the record no change may move.

`main32_r202645` is `main32_r202639`'s panel on firmware r202645, its device ids
unchanged; `main32_r202645-cascade` is another panel. `main32_r202639-upstream-pv`
has two inverters no circuit feeds.
"""

R202639_CAPTURES: Final = tuple(Capture(f"r202639-{handle}") for handle in "abcde")
"""The r202639 reference captures, by their opaque handles."""

R202645_CAPTURES: Final = tuple(Capture(f"r202645-{handle}") for handle in "aceg")
"""The r202645 reference captures, whose circuits and remote CTs carry the new device ids.

`r202645-a` and `r202645-e` are `r202639-a` and `r202639-e` after the update.
`r202645-c` is `r202639-c`'s panel recommissioned between the two, so its
circuits pair by trait instance only. `r202645-g` has no r202639 capture.
"""

CAPTURES: Final = (*MAIN32_CAPTURES, *R202639_CAPTURES, *R202645_CAPTURES)
"""The captures every replay runs over, by handle; each has its expected files on record."""


def capture(name: str) -> Capture:
    """The capture with this handle, or fail naming the ones there are."""
    for candidate in CAPTURES:
        if candidate.name == name:
            return candidate
    raise AssertionError(f"no capture {name!r}; there are {[c.name for c in CAPTURES]}")


@functools.cache
def _published_tree(name: str) -> Tree:
    """Read the tree from the package once per handle; callers get copies, never this one."""
    return load_reference_capture(name).tree


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{where} is not a JSON object")
    return {str(key): item for key, item in value.items()}


def retained_tree(tree: Tree) -> RetainedTree:
    """A captured tree back as the retained topics the broker replays.

    `$state` is `ready` for every device: the capture records what a device
    published while it was described and serving, and it carries no state of
    its own. The description is re-serialised rather than kept byte-for-byte,
    which the adapter cannot tell apart -- it parses the JSON, never the text.
    Every payload is the string the device published, as the emitter reads it.
    """
    return {
        device_id: {
            "$description": json.dumps(_mapping(device.description, f"{device_id} description")),
            "$state": "ready",
            **device.properties,
        }
        for device_id, device in tree.items()
    }


def description(tree: RetainedTree, device_id: str) -> Mapping[str, object]:
    """One device's parsed `$description`."""
    return _mapping(json.loads(tree[device_id]["$description"]), f"{device_id} $description")


def devices_of_type(tree: RetainedTree, device_type: str) -> list[str]:
    """The ids of every device whose `$description` declares this Homie type."""
    return [
        device_id for device_id in tree if description(tree, device_id).get("type") == device_type
    ]


def panel_device_id(tree: RetainedTree) -> str:
    """The enclosure's device id, which is the root the adapter is built for."""
    panels = devices_of_type(tree, PANEL_TYPE)
    if len(panels) != 1:
        raise ValueError(f"a capture holds exactly one enclosure; found {panels}")
    return panels[0]


def _subtree(tree: RetainedTree, device_id: str) -> set[str]:
    """The device and every device beneath it, by the `$description.children` it declares."""
    found = {device_id}
    children = description(tree, device_id).get("children")
    for child in children if isinstance(children, list) else []:
        if isinstance(child, str) and child in tree and child not in found:
            found |= _subtree(tree, child)
    return found


def without_device(tree: RetainedTree, device_id: str) -> RetainedTree:
    """The tree as a panel that never had this device publishes it.

    The device goes with everything beneath it -- a battery's islanding device
    is the battery's own child, and a panel with no battery has neither. So
    does every trace of them the rest of the tree publishes: the parent's
    `$description.children` entry (otherwise the tree never completes, as it
    would not on a real panel either) and any `connection/*-device-*` value
    naming one. The declarations stay, because a panel declares those
    properties whether or not anything is connected.
    """
    assert device_id in tree, f"{device_id!r} is not in the capture; nothing to drop"
    gone = _subtree(tree, device_id)
    remaining = {other: dict(topics) for other, topics in tree.items() if other not in gone}
    for other, topics in remaining.items():
        parsed = dict(description(remaining, other))
        children = parsed.get("children")
        if isinstance(children, list) and gone.intersection(children):
            parsed["children"] = [child for child in children if child not in gone]
            topics["$description"] = json.dumps(parsed)
        linked = [
            topic
            for topic, payload in topics.items()
            if topic.startswith("connection/") and topic.endswith("-device-id") and payload in gone
        ]
        for topic in linked:
            prefix = topic.removesuffix("-id")
            for suffix in ("-id", "-status", "-type"):
                topics.pop(f"{prefix}{suffix}", None)
    return remaining


def without_topics(tree: RetainedTree, device_id: str, *topics: str) -> RetainedTree:
    """The tree with these topics of one device never published; their declarations stay."""
    for topic in topics:
        assert topic in tree[device_id], f"{device_id} publishes no {topic}; nothing to drop"
    return {
        other: {
            topic: payload
            for topic, payload in values.items()
            if other != device_id or topic not in topics
        }
        for other, values in tree.items()
    }


def renamed_devices(tree: RetainedTree, renames: Mapping[str, str]) -> RetainedTree:
    """Return the tree as a panel that calls these devices by other ids publishes it.

    Each renamed device keeps every topic under its new id, and every reference
    to it the tree publishes follows: a parent's `$description.children` entry,
    a description's `parent` and `root`, its own `name` where that spelled its
    id, every `*-device-id` value and every element of a `*-device-ids` list.
    Nothing else changes. That is what a firmware update that re-ids a panel's
    circuits publishes, so one release's capture becomes the same panel under
    another's ids -- a tree no capture has, derived rather than vendored.
    """
    for device_id in renames:
        assert device_id in tree, f"{device_id!r} is not in the capture; nothing to rename"

    def renamed(device_id: str) -> str:
        return renames.get(device_id, device_id)

    result: RetainedTree = {}
    for device_id, topics in tree.items():
        parsed = dict(description(tree, device_id))
        children = parsed.get("children")
        if isinstance(children, list):
            parsed["children"] = [
                renamed(child) if isinstance(child, str) else child for child in children
            ]
        for field in ("parent", "root"):
            value = parsed.get(field)
            if isinstance(value, str):
                parsed[field] = renamed(value)
        if parsed.get("name") == device_id:
            parsed["name"] = renamed(device_id)
        moved = {"$description": json.dumps(parsed)}
        for topic, payload in topics.items():
            if topic == "$description":
                continue
            if topic.endswith("-device-id"):
                payload = renamed(payload)
            elif topic.endswith("-device-ids"):
                payload = ",".join(renamed(part.strip()) for part in payload.split(","))
            moved[topic] = payload
        result[renamed(device_id)] = moved
    return result


def with_serial(tree: RetainedTree, serial: str) -> RetainedTree:
    """Return the tree as the same panel publishes it under another serial.

    The enclosure's device id is its serial, and r202645 builds its circuits'
    ids on it, so the root and every device whose id it begins are renamed
    (`renamed_devices`, which carries every reference along) and the panel's
    `info/serial-number` follows. For a case no capture has, such as a serial
    with upper-case letters, which some unique-id builders lower-case and
    others keep.
    """
    root = panel_device_id(tree)
    renamed = renamed_devices(
        tree,
        {
            device_id: serial + device_id.removeprefix(root)
            for device_id in tree
            if device_id == root or device_id.startswith(f"{root}-")
        },
    )
    renamed[serial]["info/serial-number"] = serial
    return renamed


CONNECTION_POINT: Final = "connection-point"
"""Whether the enclosure hosts a circuit or only measures it, which r202645 circuits declare."""

_CONNECTION_POINT_DECLARATION: Final[Mapping[str, str]] = {
    "datatype": "enum",
    "format": "BRANCH,MONITORED",
    "name": "Whether the enclosure hosts this circuit or only measures it",
}
"""The declaration, as the r202645 reference captures spell it."""


def with_connection_point(tree: RetainedTree, points: Mapping[str, str]) -> RetainedTree:
    """Return the tree with these devices declaring and publishing `connection/connection-point`.

    `points` maps a device to its value: `BRANCH` for a circuit the enclosure
    hosts, `MONITORED` for one it only measures. A device with no `connection`
    node gets one holding this property alone.
    """
    result = {device_id: dict(topics) for device_id, topics in tree.items()}
    for device_id, point in points.items():
        parsed = dict(description(result, device_id))
        nodes_value = parsed.get("nodes")
        nodes = dict(nodes_value) if isinstance(nodes_value, dict) else {}
        node_value = nodes.get("connection")
        node = (
            dict(node_value)
            if isinstance(node_value, dict)
            else {"name": "connection", "type": "energy.ebus.capability.connection"}
        )
        properties_value = node.get("properties")
        properties = dict(properties_value) if isinstance(properties_value, dict) else {}
        properties[CONNECTION_POINT] = dict(_CONNECTION_POINT_DECLARATION)
        node["properties"] = properties
        nodes["connection"] = node
        parsed["nodes"] = nodes
        result[device_id]["$description"] = json.dumps(parsed)
        result[device_id][f"connection/{CONNECTION_POINT}"] = point
    return result


def retained_payload(value: str) -> str:
    """The payload a tree's value is replayed as: `""` as `0x00`, anything else as itself.

    A tree holds each value as the device published it, decoded, so `""` is an
    empty value. On the wire a zero-length payload is not one: it deletes the
    retained message, and the library reads it as a clear, the property
    unpublished. Homie 5 spells an empty value as a single `0x00`, which the
    library reads back as `""`, so that is what the broker would replay.
    """
    return encode_empty_string(value)


def replay(tree: RetainedTree) -> SchemaAdapter:
    """Deliver the tree to the adapter the library dispatches to, panel first.

    The schema the adapter is built with stands in for `GET /api/v2/homie/schema`
    and carries only what dispatch reads from it, the data-model version and the
    firmware; neither parent/child adapter reads its `types`.

    Every value goes through `retained_payload`, so a tree holding an empty value
    replays it as one rather than as a clear.
    """
    panel = panel_device_id(tree)
    data_model_version = tree[panel].get("info/data-model-version")
    key, reason = select_adapter_key(data_model_version)
    adapter = resolve_adapter(key, reason)(
        panel,
        V2HomieSchema(
            firmware_version=tree[panel].get("info/firmware-version", ""),
            types_schema_hash="",
            types={},
            data_model_version=data_model_version,
        ),
    )
    for device_id in [panel, *(other for other in tree if other != panel)]:
        prefix = f"{HOMIE_PREFIX}/{device_id}"
        topics = tree[device_id]
        adapter.handle_message(f"{prefix}/$description", topics["$description"])
        adapter.handle_message(f"{prefix}/$state", topics["$state"])
        for topic, payload in topics.items():
            if not topic.startswith("$"):
                adapter.handle_message(f"{prefix}/{topic}", retained_payload(payload))
    assert adapter.is_ready(), f"the replay of {panel} never became ready"
    return adapter


def snapshot(tree: RetainedTree) -> SpanPanelSnapshot:
    """The snapshot the pinned library builds from this tree, through the transport's path."""
    return replay(tree).build_snapshot()


def mapped_snapshot(tree: RetainedTree) -> SpanPanelSnapshot:
    """The same snapshot straight from the schema_1 mapper, without dispatch or routing.

    For experiments that rebuild a tree hundreds of times: the mapper is the
    part that turns properties into fields, and skipping the per-message routing
    makes each rebuild cheap. `test_declared_but_unread` holds the two paths to
    the same answer for every capture, which is what licenses the shortcut.
    """
    return tree_snapshot(tree, panel_device_id(tree))


def _unregister() -> None:
    """What every `register_*_callback` hands back; a replay never fires them."""


class ReplayClient:
    """`SpanMqttClient`'s surface over a replayed tree, and nothing more.

    Explicit rather than a `MagicMock`, so a call the integration starts making
    fails here by name instead of answering with a truthy mock. The tree is
    static until a test says otherwise: it can `serve` another tree, which the
    next poll reads, or `push` one, which the snapshot callback the coordinator
    registered receives as the library's stream would deliver it. Nothing
    reconnects or changes schema, and no other callback fires. Commands are not
    supported, because nothing that reads a capture sends one.
    """

    def __init__(self, adapter: SchemaAdapter) -> None:
        self._adapter = adapter
        self._snapshot_callback: Callable[[SpanPanelSnapshot], Awaitable[None]] | None = None
        self.interceptor: ControlInterceptor | None = None

    def serve(self, adapter: SchemaAdapter) -> None:
        """Answer from another replayed tree from now on, as a panel that changed underneath us."""
        self._adapter = adapter

    async def push(self, adapter: SchemaAdapter) -> None:
        """Serve another replayed tree and stream its snapshot to the registered callback."""
        callback = self._snapshot_callback
        assert callback is not None, "nothing registered for streamed snapshots"
        self.serve(adapter)
        await callback(adapter.build_snapshot())

    @property
    def schema_major(self) -> str:
        return self._adapter.schema_major

    @property
    def field_metadata(self) -> dict[str, FieldMetadata]:
        return self._adapter.build_field_metadata()

    def masked_tree(self) -> MaskedTree | None:
        """The library's masked export of the replayed tree, as `SpanMqttClient` offers it."""
        adapter = self._adapter
        if isinstance(adapter, TreeExportingAdapter):
            return adapter.export_masked_tree()
        return None

    async def connect(self) -> None:
        """Connected already: the replay is the retained tree a connect receives."""

    async def close(self) -> None:
        """Nothing to close."""

    async def get_snapshot(self) -> SpanPanelSnapshot:
        return self._adapter.build_snapshot()

    async def start_streaming(self) -> None:
        """Nothing streams from a capture."""

    async def stop_streaming(self) -> None:
        """Nothing streams from a capture."""

    def set_control_interceptor(self, interceptor: ControlInterceptor | None) -> None:
        self.interceptor = interceptor

    def register_leaf_mismatch_callback(
        self, _callback: Callable[[LeafNameMismatch], None]
    ) -> Callable[[], None]:
        return _unregister

    def register_fatal_error_callback(
        self, _callback: Callable[[SpanPanelError], None]
    ) -> Callable[[], None]:
        return _unregister

    def register_connection_callback(self, _callback: Callable[[bool], None]) -> Callable[[], None]:
        return _unregister

    def register_schema_change_callback(
        self, _callback: Callable[[str | None, str | None], None]
    ) -> Callable[[], None]:
        return _unregister

    def register_snapshot_callback(
        self, callback: Callable[[SpanPanelSnapshot], Awaitable[None]]
    ) -> Callable[[], None]:
        """Record the callback `push` streams to; unregistering forgets it."""
        self._snapshot_callback = callback

        def _forget() -> None:
            if self._snapshot_callback is callback:
                self._snapshot_callback = None

        return _forget
