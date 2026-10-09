"""Replay a captured panel through the pinned library, as the broker delivers it.

The captures in `tests/fixtures/captures/` are panels' retained MQTT trees,
recorded in the emitter's `tree-v1` form: per device, its `$description` and
the value of every property it published. Nothing here interprets them. Each
capture is turned back into the retained topics the broker would replay, and
those are handed one message at a time to whichever adapter the library's own
dispatch picks for the panel's `info/data-model-version` -- the same parser,
selected the same way, that `SpanMqttClient` builds on connect. So a snapshot
taken here is the snapshot the pinned library produces for that panel, and the
pin is what moves it.

`ReplayClient` replaces `SpanMqttClient` whole with such a replay, so a test can
run the integration's real setup -- coordinator, platforms, registries -- over a
static tree: nothing reconnects, goes offline, changes schema or streams.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Final

from span_panel_api import ControlInterceptor, LeafNameMismatch, SpanPanelSnapshot
from span_panel_api.adapters import resolve_adapter
from span_panel_api.dispatch import select_adapter_key
from span_panel_api.exceptions import SpanPanelError
from span_panel_api.models import FieldMetadata, V2HomieSchema
from span_panel_api.protocol import SchemaAdapter

from .adapter_fixtures import tree_snapshot

CAPTURES_DIR: Final = Path(__file__).parent / "fixtures" / "captures"
CAPTURE_SUFFIX: Final = "-tree-v1.json"
DIGESTS: Final = CAPTURES_DIR / "SHA256SUMS"
"""`sha256sum` lines for every capture, held to the upstream copies by a test."""

PANEL_TYPE: Final = "energy.ebus.device.distribution-enclosure"
HOMIE_PREFIX: Final = "ebus/5"

RetainedTree = dict[str, dict[str, str]]
"""Device id -> `{topic: payload}`, topics relative to the device (`meter/active-power`)."""


@dataclass(frozen=True, slots=True)
class Capture:
    """One vendored capture, named by its file's stem."""

    stem: str
    path: Path

    def tree(self) -> RetainedTree:
        """A fresh, mutable copy of the capture's retained topics."""
        return retained_tree(self.path)


def _captures() -> tuple[Capture, ...]:
    return tuple(
        Capture(path.name.removesuffix(CAPTURE_SUFFIX), path)
        for path in sorted(CAPTURES_DIR.glob(f"*{CAPTURE_SUFFIX}"))
    )


CAPTURES: Final = _captures()
"""Every capture in the directory: read rather than listed, so every test replays a new one."""


def capture(stem: str) -> Capture:
    """The capture with this stem, or fail naming the ones there are."""
    for candidate in CAPTURES:
        if candidate.stem == stem:
            return candidate
    raise AssertionError(f"no capture {stem!r}; there are {[c.stem for c in CAPTURES]}")


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{where} is not a JSON object")
    return {str(key): item for key, item in value.items()}


def _text_values(value: object, where: str) -> dict[str, str]:
    """A `{topic: payload}` map; the emitter records every payload as the string it published."""
    values: dict[str, str] = {}
    for topic, payload in _mapping(value, where).items():
        if not isinstance(payload, str):
            raise TypeError(f"{where}/{topic} is not a string payload")
        values[topic] = payload
    return values


def retained_tree(path: Path) -> RetainedTree:
    """Read a `tree-v1` capture back into the retained topics the broker replays.

    `$state` is `ready` for every device: the format records what a device
    published while it was described and serving, and it carries no state of
    its own. The description is re-serialised rather than kept byte-for-byte,
    which the adapter cannot tell apart -- it parses the JSON, never the text.
    """
    document = _mapping(json.loads(path.read_text(encoding="utf-8")), path.name)
    schema = _mapping(document.get("metadata"), f"{path.name} metadata").get("schema")
    if schema != "tree-v1":
        raise ValueError(f"{path.name} is {schema!r}, not a tree-v1 capture")
    tree: RetainedTree = {}
    for device_id, recorded in _mapping(document.get("devices"), f"{path.name} devices").items():
        device = _mapping(recorded, device_id)
        tree[device_id] = {
            "$description": json.dumps(
                _mapping(device.get("description"), f"{device_id} description")
            ),
            "$state": "ready",
            **_text_values(device.get("properties", {}), f"{device_id} properties"),
            **_text_values(device.get("numeric_properties", {}), f"{device_id} numeric_properties"),
        }
    return tree


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


def replay(tree: RetainedTree) -> SchemaAdapter:
    """Deliver the tree to the adapter the library dispatches to, panel first.

    The schema the adapter is built with stands in for `GET /api/v2/homie/schema`
    and carries only what dispatch reads from it, the data-model version and the
    firmware; neither parent/child adapter reads its `types`.
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
                adapter.handle_message(f"{prefix}/{topic}", payload)
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
    static: the snapshot never changes, nothing streams, and no callback fires.
    Commands are not supported, because nothing that reads a capture sends one.
    """

    def __init__(self, adapter: SchemaAdapter) -> None:
        self._adapter = adapter
        self.interceptor: ControlInterceptor | None = None

    @property
    def schema_major(self) -> str:
        return self._adapter.schema_major

    @property
    def field_metadata(self) -> dict[str, FieldMetadata]:
        return self._adapter.build_field_metadata()

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
        self, _callback: Callable[[SpanPanelSnapshot], Awaitable[None]]
    ) -> Callable[[], None]:
        return _unregister
