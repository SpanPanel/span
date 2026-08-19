"""Verify each declared field_path against what its value_fn actually reads.

Runs every value_fn against a proxy that records attribute access. The
declaration stays authoritative — this only stops it drifting from the reader.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Protocol, runtime_checkable

from custom_components.span_panel.field_paths import (
    declared_field_paths,
    residual_field_paths,
)
from custom_components.span_panel.sensor_definitions import all_sensor_descriptions
from tests.adapter_fixtures import schema_one_metadata, schema_zero_metadata

# Attributes of the panel snapshot that are themselves sub-snapshots. Their
# fields are addressed as "battery.x", not "panel.battery.x".
_SUB_SNAPSHOTS = {"battery", "pv", "evse", "mid"}


class _Recorder:
    """Records every attribute path touched, and survives arithmetic.

    value_fns do real work — `or "unknown"`, `a - b`, unary minus for PV sign
    flips — so the proxy has to absorb those without raising and without
    ending the recording.
    """

    def __init__(self, sink: set[str], prefix: str, root: bool = False) -> None:
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_prefix", prefix)
        object.__setattr__(self, "_root", root)

    def __getattr__(self, name: str) -> _Recorder:
        if name.startswith("_"):
            raise AttributeError(name)
        sink: set[str] = object.__getattribute__(self, "_sink")
        prefix: str = object.__getattribute__(self, "_prefix")
        root: bool = object.__getattribute__(self, "_root")
        if root and name in _SUB_SNAPSHOTS:
            return _Recorder(sink, name)
        path = f"{prefix}.{name}" if prefix else name
        sink.add(path)
        return _Recorder(sink, path)

    # Absorb the operations value_fns perform on the values they read.
    def __bool__(self) -> bool:
        return True

    def __sub__(self, other: Any) -> _Recorder:
        return self

    def __rsub__(self, other: Any) -> _Recorder:
        return self

    def __neg__(self) -> _Recorder:
        return self

    def __or__(self, other: Any) -> _Recorder:
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> _Recorder:
        """Absorb method calls, e.g. `(m.grid_state or "unknown").lower()`.

        The extra path this records (`mid.grid_state.lower`) is harmless: the
        check is membership of the declared path, not set equality.
        """
        return self

    def __eq__(self, other: Any) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


@runtime_checkable
class _DeclaringDescription(Protocol):
    """The surface this test needs from an entity description.

    A protocol rather than a concrete type because the eight sensor classes and
    the two binary-sensor classes share no base beyond
    `FieldPathDeclarationMixin`, which does not carry `value_fn`.
    """

    @property
    def key(self) -> str: ...

    @property
    def field_path(self) -> str | None: ...

    @property
    def derived(self) -> bool: ...

    @property
    def value_fn(self) -> Callable[[Any], object]: ...


def _declaring_descriptions() -> Iterator[_DeclaringDescription]:
    """Every entity description that carries a field-path declaration.

    Mirrors the collections `declared_field_paths()` walks, so the gate and this
    verifier cover the same descriptions;
    `test_introspection_covers_every_declared_path` pins that they still do.
    """
    # Deferred for the same reason `field_paths` defers it: `binary_sensor`
    # reaches the package root, and the root imports the platforms.
    from custom_components.span_panel.binary_sensor import (  # noqa: PLC0415
        BESS_CONNECTED_SENSOR,
        BINARY_SENSORS,
        EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
    )

    for description in (
        *all_sensor_descriptions(),
        *BINARY_SENSORS,
        *EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
        BESS_CONNECTED_SENSOR,
    ):
        if not isinstance(description, _DeclaringDescription):
            raise TypeError(
                f"entity description '{description.key}' carries no field-path declaration"
            )
        yield description


# Every description class, and the snapshot type its value_fn receives.
# A class missing here is reported as a mismatch rather than skipped: a silent
# skip is exactly the hole this test exists to close.
_ROOT_PREFIX = {
    "SpanPanelCircuitsSensorEntityDescription": "circuit",
    "SpanPanelDataSensorEntityDescription": "panel",
    "SpanPanelStatusSensorEntityDescription": "panel",
    "SpanPanelBatterySensorEntityDescription": "battery",
    "SpanBessMetadataSensorEntityDescription": "battery",
    # PV metadata value_fns take the whole panel snapshot and reach through
    # `s.pv.x`, so the root prefix is "panel" and _SUB_SNAPSHOTS rewrites it.
    "SpanPVMetadataSensorEntityDescription": "panel",
    "SpanEvseSensorEntityDescription": "evse",
    "SpanMidSensorEntityDescription": "mid",
    "SpanPanelBinarySensorEntityDescription": "panel",
    "SpanEvseBinarySensorEntityDescription": "evse",
}


def test_declared_paths_match_what_value_fns_read() -> None:
    mismatches: list[str] = []

    for description in _declaring_descriptions():
        if description.derived or description.field_path is None:
            continue
        class_name = type(description).__name__
        prefix = _ROOT_PREFIX.get(class_name)
        if prefix is None:
            mismatches.append(
                f"{description.key}: {class_name} is absent from _ROOT_PREFIX, so its "
                "declaration would go unverified"
            )
            continue
        sink: set[str] = set()
        proxy = _Recorder(sink, prefix, root=(prefix == "panel"))
        try:
            description.value_fn(proxy)
        except Exception as err:  # noqa: BLE001
            mismatches.append(f"{description.key}: value_fn raised {err!r}")
            continue
        if description.field_path not in sink:
            mismatches.append(
                f"{description.key}: declares {description.field_path!r} but reads {sorted(sink)}"
            )

    assert not mismatches, "Declarations disagree with readers:\n" + "\n".join(mismatches)


def test_introspection_covers_every_declared_path() -> None:
    """Every path the gate accepts must be one this test verified, or residual.

    `_declaring_descriptions` restates the collections `declared_field_paths()`
    walks. Without this, a platform collection added to the gate but not here
    would be gated for producibility and never checked against its reader.
    """
    introspected = {
        description.field_path
        for description in _declaring_descriptions()
        if not description.derived and description.field_path is not None
    }
    assert declared_field_paths() == frozenset(introspected | residual_field_paths())


def test_no_derived_description_reads_one_producible_field() -> None:
    """`derived` must mean no field, several fields, or an unproducible one.

    Pins the rule, not the instance that broke it. `evse_ev_connected` read
    exactly `evse.status` — one field both adapters produce — while declaring
    itself derived, so `_declared_field_paths` skipped it: the Repair for a dead
    `evse.status` never named it and the availability probe never fired for it,
    though its sibling `evse_charging` got both from the very same field.

    Producibility is what makes this checkable: the recorder also picks up
    method names and other noise, and intersecting with what both adapters
    actually emit leaves only real fields.
    """
    producible = set(schema_zero_metadata()) & set(schema_one_metadata())
    offenders: list[str] = []

    for description in _declaring_descriptions():
        if not description.derived:
            continue
        class_name = type(description).__name__
        prefix = _ROOT_PREFIX.get(class_name)
        if prefix is None:
            offenders.append(
                f"{description.key}: {class_name} is absent from _ROOT_PREFIX, so its "
                "derived classification would go unverified"
            )
            continue
        sink: set[str] = set()
        proxy = _Recorder(sink, prefix, root=(prefix == "panel"))
        try:
            description.value_fn(proxy)
        except Exception as err:  # noqa: BLE001
            offenders.append(f"{description.key}: value_fn raised {err!r}")
            continue
        read = sorted(sink & producible)
        if len(read) == 1:
            offenders.append(
                f"{description.key}: derived=True but reads exactly one producible field, "
                f"{read[0]!r} — that is a declaration, so set field_path={read[0]!r}"
            )

    assert not offenders, "Misclassified derived descriptions:\n" + "\n".join(offenders)
