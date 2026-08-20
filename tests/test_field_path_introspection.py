"""Verify each declared field_path against what its value_fn actually reads.

Runs every value_fn against a proxy that records attribute access. The
declaration stays authoritative — this only stops it drifting from the reader.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol, get_args, get_type_hints, runtime_checkable

from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
    SpanPcsSnapshot,
)

from custom_components.span_panel.field_paths import (
    DerivedReason,
    declared_field_paths,
    residual_field_paths,
)
from custom_components.span_panel.sensor_definitions import all_sensor_descriptions
from tests.adapter_fixtures import schema_one_metadata, schema_zero_metadata

# Attributes of the panel snapshot that are themselves sub-snapshots. Their
# fields are addressed as "battery.x", not "panel.battery.x".
_SUB_SNAPSHOTS = {"battery", "pv", "evse", "mid", "pcs"}


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
    def derived(self) -> DerivedReason | None: ...

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
        PCS_ACTIVE_SENSOR,
    )

    for description in (
        *all_sensor_descriptions(),
        *BINARY_SENSORS,
        *EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
        BESS_CONNECTED_SENSOR,
        PCS_ACTIVE_SENSOR,
    ):
        if not isinstance(description, _DeclaringDescription):
            raise TypeError(
                f"entity description '{description.key}' carries no field-path declaration"
            )
        yield description


# Every snapshot type a value_fn can take, and the prefix its fields are
# addressed by. Keyed by the snapshot type rather than by description class
# name: the class name map had to be edited for every new description class —
# the recurring event — while this one grows only when the library grows a new
# snapshot type. A new description class over an existing snapshot type needs no
# edit here, and its prefix cannot be wrong, because it comes from the same
# annotation mypy checks the value_fn bodies against.
#
# PV has no entry: PV metadata value_fns take the whole panel snapshot and reach
# through `s.pv.x`, so their prefix is "panel" and `_SUB_SNAPSHOTS` rewrites it.
_SNAPSHOT_PREFIX: Mapping[type, str] = {
    SpanCircuitSnapshot: "circuit",
    SpanPanelSnapshot: "panel",
    SpanBatterySnapshot: "battery",
    SpanEvseSnapshot: "evse",
    SpanMidSnapshot: "mid",
    SpanPcsSnapshot: "pcs",
}


class _UndeterminedPrefix(Exception):
    """A description's snapshot prefix could not be determined.

    Raised, never swallowed: a description whose prefix is unknown must be
    reported by its caller as a mismatch. Skipping it is what let a description
    class absent from the old class-name map drop out of verification entirely.
    """


def _snapshot_type(description: _DeclaringDescription) -> type:
    """Return the snapshot type this description's `value_fn` is annotated to take.

    `from __future__ import annotations` stringifies the annotation, so this
    resolves it with `get_type_hints`, which evaluates it in the defining
    module's namespace — every mixin's module imports the snapshot types it
    names, so resolution succeeds.
    """
    cls = type(description)
    try:
        hints = get_type_hints(cls)
    except Exception as err:  # noqa: BLE001
        raise _UndeterminedPrefix(
            f"{cls.__name__}: value_fn annotation does not resolve ({err!r})"
        ) from err
    annotation = hints.get("value_fn")
    if annotation is None:
        raise _UndeterminedPrefix(f"{cls.__name__} carries no value_fn annotation")
    args = get_args(annotation)
    if len(args) != 2 or not isinstance(args[0], list) or not args[0]:
        raise _UndeterminedPrefix(
            f"{cls.__name__}: value_fn annotated {annotation!r} names no parameter type"
        )
    parameter = args[0][0]
    if not isinstance(parameter, type):
        raise _UndeterminedPrefix(
            f"{cls.__name__}: value_fn takes {parameter!r}, which is not a snapshot class"
        )
    return parameter


def _record_reads(description: _DeclaringDescription) -> set[str]:
    """Run a description's `value_fn` against the recorder, return what it read.

    Raises `_UndeterminedPrefix` when the snapshot type cannot be resolved or is
    absent from `_SNAPSHOT_PREFIX`; anything the `value_fn` itself raises
    propagates unchanged.
    """
    snapshot_type = _snapshot_type(description)
    prefix = _SNAPSHOT_PREFIX.get(snapshot_type)
    if prefix is None:
        raise _UndeterminedPrefix(
            f"{type(description).__name__}: value_fn takes {snapshot_type.__name__}, "
            "which is absent from _SNAPSHOT_PREFIX"
        )
    sink: set[str] = set()
    description.value_fn(_Recorder(sink, prefix, root=snapshot_type is SpanPanelSnapshot))
    return sink


def test_declared_paths_match_what_value_fns_read() -> None:
    """Every named source field must be one the `value_fn` actually reads.

    `derived` is not consulted: a `SCHEMA_CONDITIONAL_FIELD` description names
    its source field too, and that name is what the Repair and the availability
    probe act on. An unverified one would send both at the wrong path.
    """
    mismatches: list[str] = []

    for description in _declaring_descriptions():
        if description.field_path is None:
            continue
        try:
            sink = _record_reads(description)
        except _UndeterminedPrefix as err:
            mismatches.append(f"{description.key}: {err}, so its declaration would go unverified")
            continue
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
        try:
            sink = _record_reads(description)
        except _UndeterminedPrefix as err:
            offenders.append(
                f"{description.key}: {err}, so its derived classification would go unverified"
            )
            continue
        except Exception as err:  # noqa: BLE001
            offenders.append(f"{description.key}: value_fn raised {err!r}")
            continue
        read = sorted(sink & producible)
        if len(read) == 1:
            offenders.append(
                f"{description.key}: derived={description.derived} but reads exactly one "
                f"producible field, {read[0]!r} — that is a declaration, so set "
                f"field_path={read[0]!r}"
            )

    assert not offenders, "Misclassified derived descriptions:\n" + "\n".join(offenders)


def test_derived_reasons_match_what_value_fns_read() -> None:
    """Each derived description's stated reason must be the one its reads imply.

    `derived` used to be a `bool` covering four different situations, and it was
    that conflation which hid `evse_ev_connected`: a single producible field
    marked derived looked exactly like a genuine multi-field derivation. The
    reason is only worth its syntax if it is checked, so each variant is a claim
    about the recorder's output and is asserted as one:

    * `NO_SOURCE_FIELD` — reads nothing either adapter publishes,
    * `MULTIPLE_FIELDS` — reads two or more fields an adapter publishes,
    * `SCHEMA_CONDITIONAL_FIELD` — reads exactly one, produced by one adapter
      only. When the other adapter grows it, this fails and demands promotion to
      a `field_path` declaration.

    Intersecting with what the adapters emit is what makes the count meaningful:
    the recorder also picks up method names and other noise.
    """
    schema_0 = set(schema_zero_metadata())
    schema_1 = set(schema_one_metadata())
    produced = schema_0 | schema_1
    offenders: list[str] = []

    for description in _declaring_descriptions():
        reason = description.derived
        if reason is None:
            continue
        try:
            sink = _record_reads(description)
        except _UndeterminedPrefix as err:
            offenders.append(f"{description.key}: {err}, so its reason would go unverified")
            continue
        except Exception as err:  # noqa: BLE001
            offenders.append(f"{description.key}: value_fn raised {err!r}")
            continue
        read = sorted(sink & produced)
        if reason is DerivedReason.NO_SOURCE_FIELD and read:
            offenders.append(
                f"{description.key}: claims NO_SOURCE_FIELD but reads {read} — "
                "the reason is MULTIPLE_FIELDS, SCHEMA_CONDITIONAL_FIELD, or it is a "
                "declaration"
            )
        elif reason is DerivedReason.MULTIPLE_FIELDS and len(read) < 2:
            offenders.append(
                f"{description.key}: claims MULTIPLE_FIELDS but reads {read} — "
                "one field or none is a different reason"
            )
        elif reason is DerivedReason.SCHEMA_CONDITIONAL_FIELD:
            if len(read) != 1 or read[0] in schema_0 & schema_1:
                offenders.append(
                    f"{description.key}: claims SCHEMA_CONDITIONAL_FIELD but reads {read}, "
                    f"of which {sorted(set(read) & schema_0 & schema_1)} are produced by both "
                    "adapters"
                )
            elif description.field_path != read[0]:
                # The one reason that still names a field names the right one.
                # That name is what the Repair and the availability probe act
                # on, so a stale one degrades the wrong entity or none.
                offenders.append(
                    f"{description.key}: declares field_path={description.field_path!r} "
                    f"but reads {read[0]!r}"
                )

    assert not offenders, "Derived reasons disagree with readers:\n" + "\n".join(offenders)
