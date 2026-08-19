"""Snapshot field paths this integration reads.

Most declarations live on the entity descriptions that read them, so they
cannot drift from the reader. A few readers are in entity code rather than on a
description; those are listed here.

This module replaced `schema_expectations.SENSOR_FIELD_MAP` (since deleted), a
hand-maintained parallel dict that had already drifted once (it pointed at
`battery.product_name` and `pv.product_name` after the library renamed those
fields to `battery.model` / `pv.model`).

Field path convention: ``{snapshot_type}.{field_name}`` — ``panel``,
``circuit``, ``battery``, ``pv``, ``evse`` and ``mid``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from homeassistant.helpers.entity import EntityDescription


class DerivedReason(Enum):
    """Why an entity description declares no single source field.

    A description is derived for exactly one of these reasons, and which one is
    mechanical — count the snapshot fields its `value_fn` reads.
    `test_derived_reasons_match_what_value_fns_read` runs every derived
    description against the recorder and asserts the reason it claims, so a
    wrong reason fails the build rather than misinforming a reader.

    Reading exactly one producible field is **always** a declaration, however
    much arithmetic, mapping or membership-testing is applied on top. "Computed
    from `status`" is not derivation: `field_path="evse.status"` with a
    `value_fn` of `status in {...}` declares its source correctly and still
    computes whatever it likes. `evse_ev_connected` was misclassified as derived
    this way, which cost it both a Repair mention and its unavailability, while
    its sibling `evse_charging` — same field, same shape — got both. That is the
    conflation this enum exists to break: as a bare `bool`, all three reasons
    below and that mistake looked identical.
    """

    NO_SOURCE_FIELD = "no_source_field"
    """Reads no field either adapter publishes a metadata row for.

    Either it reads nothing off the snapshot at all (`panel_status` reports
    coordinator reachability) or the field it reads is one no adapter produces
    (`dsm_state`, every `mid.*` read). Deliberately one member rather than two:
    the recorder cannot tell those apart — both leave an empty intersection with
    what the adapters emit — and a variant nothing can verify is exactly what
    this enum replaces.
    """

    MULTIPLE_FIELDS = "multiple_fields"
    """Combines two or more producible fields, so no one of them is the source.

    The net-energy sensors subtract produced from consumed; blaming either field
    alone for the entity would be wrong.
    """

    SCHEMA_CONDITIONAL_FIELD = "schema_conditional_field"
    """Reads exactly one field, which only one adapter produces.

    Keeps the producible gate satisfiable: the gate requires a path both
    adapters emit, so a schema-conditional field cannot be declared. If the
    other adapter ever grows the field, this stops being true and the
    verification fails, demanding promotion to a `field_path` declaration.
    """


class Producibility(Enum):
    """Which adapters publish a metadata row for an exempt residual path.

    A *path's* producibility, not a *description's* classification: kept beside
    `DerivedReason` because the two are verified from the same pair of adapter
    metadata sets, and deliberately separate because they describe different
    subjects. `bess_connected` is `SCHEMA_CONDITIONAL_FIELD` while the
    `battery.connected` path it reads is `SCHEMA_0_ONLY` — two facts about two
    things.

    There is deliberately no `BOTH` member. A path both adapters produce
    satisfies the producible gate, so it belongs in `declared_field_paths()`
    rather than in an exemption; `test_no_exempt_path_is_producible_by_both`
    turns that missing member into a failure naming the path to promote.
    """

    NEITHER = "neither"
    """No metadata row on either adapter."""

    SCHEMA_0_ONLY = "schema_0_only"
    """Produced by the schema_0 adapter, absent from schema_1."""

    SCHEMA_1_ONLY = "schema_1_only"
    """Produced by the schema_1 adapter, absent from schema_0."""


@dataclass(frozen=True, kw_only=True)
class FieldPathDeclarationMixin:
    """Declares which snapshot field an entity description reads.

    Mixed into every required-keys mixin so the declaration and the reader are
    the same object. Defined once here rather than repeated on each mixin, so
    the two fields cannot themselves drift apart across platforms.

    The fields are keyword-only: an entity description flattens this mixin's
    fields ahead of ``EntityDescription.key``, which has no default, so a
    positional pair here would make every description unconstructable.
    """

    field_path: str | None = None
    """Snapshot field this entity reads, e.g. "circuit.instant_power_w".

    Declared here rather than in a parallel map so the declaration and the
    reader are the same object. Verified against `value_fn` by the proxy test
    in tests/test_field_path_introspection.py.
    """

    derived: DerivedReason | None = None
    """Why this entity has no single source field to declare, or `None`.

    Set only when `field_path` is not: the two are alternatives, pinned by
    `test_every_description_declares_exactly_one`. Which reason applies is not
    a matter of opinion — see `DerivedReason`, whose members are each asserted
    against what the `value_fn` actually reads.

    A reason rather than a flag because `bool` conflated four situations, and
    that conflation is how `evse_ev_connected` — one producible field, marked
    derived — stayed invisible to both the Repair count and the availability
    probe. Every consumer tests this by truthiness, which an enum member and
    `None` answer exactly as `True` and `False` did.
    """


RESIDUAL_EXEMPT_PATHS: Mapping[str, Producibility] = MappingProxyType(
    {
        # Homie `$target` values — a pending-command echo, not a schema field.
        "circuit.relay_state_target": Producibility.NEITHER,
        "circuit.priority_target": Producibility.NEITHER,
        # Assembled by the library from panel topology rather than read from a
        # schema property.
        "circuit.device_type": Producibility.NEITHER,
        "circuit.relative_position": Producibility.NEITHER,
        # The panel reports it outside the typed field surface.
        "panel.panel_size": Producibility.NEITHER,
        # The panel identity key behind every unique_id and the panel DeviceInfo
        # (~30 read sites).
        "panel.serial_number": Producibility.NEITHER,
        # Gates button availability at button.py:115 — the same reason the
        # `dsm_state` sensor is `derived=True`.
        "panel.dsm_state": Producibility.NEITHER,
        # The circuit's own identity key, used for lookups and id construction
        # (helpers.py, coordinator.py, entity_resolver.py).
        "circuit.circuit_id": Producibility.NEITHER,
        # util.py reads these off the MID snapshot for device_info, and
        # sensor_panel.py reads the grid-forming name for an attribute.
        "mid.hardware_version": Producibility.NEITHER,
        "mid.software_version": Producibility.NEITHER,
        "mid.vendor_name": Producibility.NEITHER,
        "mid.model": Producibility.NEITHER,
        "mid.serial_number": Producibility.NEITHER,
        "mid.grid_forming_device_name": Producibility.NEITHER,
        # The EVSE's Homie node id — an addressing handle used to build the
        # sub-device identifier, not a published field.
        "evse.node_id": Producibility.NEITHER,
        "circuit.is_user_controllable": Producibility.SCHEMA_1_ONLY,
        "circuit.always_on": Producibility.SCHEMA_0_ONLY,
        "circuit.is_sheddable": Producibility.SCHEMA_0_ONLY,
        "panel.wifi_ssid": Producibility.SCHEMA_0_ONLY,
        # util.py builds the EVSE DeviceInfo from these; entity_resolver.py and
        # sensor.py resolve the fed circuit through `feed_circuit_id`.
        "evse.vendor_name": Producibility.SCHEMA_0_ONLY,
        "evse.model": Producibility.SCHEMA_0_ONLY,
        "evse.serial_number": Producibility.SCHEMA_0_ONLY,
        "evse.software_version": Producibility.SCHEMA_0_ONLY,
        "evse.feed_circuit_id": Producibility.SCHEMA_0_ONLY,
        # schema_1 derives islanding via `resolve_grid_islandable(inverters)`
        # instead. Read at binary_sensor.py:408 as an entity-creation gate,
        # outside any description.
        "panel.grid_islandable": Producibility.SCHEMA_0_ONLY,
        # schema_1's `_PROPERTY_FIELD_MAP` has no `connected` row — the same gap
        # that makes the `bess_connected` binary sensor `derived=True`.
        "battery.connected": Producibility.SCHEMA_0_ONLY,
    }
)
"""Residual readers exempt from the producible check, and why each is exempt.

The gate requires a path to be producible by *both* adapters, so a read is
exempt for one of two reasons, and the annotation says which:
`Producibility.NEITHER` for values no adapter publishes a metadata row for —
Homie `$target` echoes, values the library assembles from panel topology, every
`mid.*` field; `SCHEMA_0_ONLY` / `SCHEMA_1_ONLY` for schema-conditional fields
present on one schema and absent from the other.

Exempt is not the same as derived: these are read straight off a snapshot field,
that field just is not on both schemas.

The annotations are not documentation. `tests/test_field_path_conformance.py`
builds both adapters' metadata from the vendored fixtures and asserts every
entry's annotation against what those adapters actually produce, so a stale
reason fails the build instead of misleading a reader. A path that becomes
producible by both fails there too, demanding promotion to a declaration.

Deliberately **not** returned by `declared_field_paths()`. Recorded here so the
reads are still enumerated somewhere rather than being invisible.
"""


def iter_field_path_declarations[DescriptionT: EntityDescription](
    descriptions: Iterable[DescriptionT],
) -> Iterator[tuple[str, DescriptionT]]:
    """Yield ``(field_path, description)`` for each description that declares one.

    The single copy of the traversal rule — which descriptions declare a field,
    and which are exempt — so a caller that only wants the paths and a caller
    that wants the descriptions cannot drift apart on it.

    Raises `TypeError` for a description that carries no
    `FieldPathDeclarationMixin` at all: such a description would be dropped
    silently, which is the drift this module exists to prevent. Descriptions
    that carry the mixin but declare nothing (`derived`, or `field_path is
    None`) are skipped, which is the declared-exempt case rather than drift.
    """
    for description in descriptions:
        if not isinstance(description, FieldPathDeclarationMixin):
            raise TypeError(
                f"entity description '{description.key}' carries no field-path declaration"
            )
        if description.derived or description.field_path is None:
            continue
        yield description.field_path, description


def _walk_subclasses[EntityT](root: type[EntityT]) -> Iterator[type[EntityT]]:
    """Yield every subclass of `root`, transitively.

    `__subclasses__()` is one level deep; platform entities sit two or three
    levels below the base (`SpanSensorBase` -> `SpanCircuitPowerSensor`), so a
    single level would miss exactly the classes that carry residual reads.
    """
    for subclass in root.__subclasses__():
        yield subclass
        yield from _walk_subclasses(subclass)


def residual_field_paths() -> frozenset[str]:
    """Field paths read from entity code rather than from a description.

    A handful of reads cannot be expressed as a description `field_path`: the
    switch has no entity description at all, the select wraps one rather than
    being a frozen dataclass, and a circuit entity's name, tabs and attributes
    are read outside any `value_fn`. Each such read is declared on the entity
    that makes it -- `SpanPanelEntity._residual_field_paths` -- because the
    entity is what a Repair has to name when the field dies.

    Collected from those declarations rather than restated as a constant here:
    a second copy would need a test to hold it against the first, and the copy
    that the Repair actually consumes is the entity's.

    The walk sees only classes Python has imported, so the platform modules
    that declare residuals are imported here explicitly. A residual declared in
    a module this function does not reach would go missing silently, so
    `test_source_residuals_match_the_subclass_walk` scans the package source
    for `_residual_field_paths` assignments and fails on any the walk missed.
    """
    # Deferred for the same cycle-avoidance reason as `declared_field_paths()`
    # below: every platform module imports this one for the declaration mixin.
    from . import (  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
        select,
        sensor_circuit,
        switch,
    )
    from .entity import SpanPanelEntity  # pylint: disable=import-outside-toplevel

    return frozenset(
        path
        for entity_class in _walk_subclasses(SpanPanelEntity)
        for path in entity_class._residual_field_paths  # pylint: disable=protected-access
    )


def declared_field_paths() -> frozenset[str]:
    """Field paths the integration reads that must be producible by an adapter.

    Derived entities are excluded: they have no single source field, so there is
    nothing for an adapter to produce. Residual readers that no adapter (or only
    one) produces are excluded too, and are listed in `RESIDUAL_EXEMPT_PATHS`.
    """
    # Deferred: the platform modules import `FieldPathDeclarationMixin` from
    # here, and `binary_sensor` reaches the package root for its config-entry
    # type. Importing them at module scope would close both loops.
    from .binary_sensor import (  # pylint: disable=import-outside-toplevel
        BESS_CONNECTED_SENSOR,
        BINARY_SENSORS,
        EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
    )
    from .sensor_definitions import (  # pylint: disable=import-outside-toplevel
        all_sensor_descriptions,
    )

    paths: set[str] = set(residual_field_paths())
    paths.update(
        field_path
        for field_path, _ in iter_field_path_declarations(
            (
                *all_sensor_descriptions(),
                *BINARY_SENSORS,
                *EVSE_BINARY_SENSORS,
                GRID_ISLANDABLE_SENSOR,
                BESS_CONNECTED_SENSOR,
            )
        )
    )
    return frozenset(paths)
