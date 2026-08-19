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

    derived: bool = False
    """True only when there is no single source field to declare.

    Exactly one of these three must hold, and the test is mechanical — count the
    snapshot fields the `value_fn` reads:

    1. it reads **no** snapshot field, or
    2. it reads **more than one**, or
    3. it reads exactly one that no adapter, or only one adapter, produces.

    Reading exactly one producible field is **always** a declaration, however
    much arithmetic, mapping or membership-testing is applied on top. "Computed
    from `status`" is not derivation: `field_path="evse.status"` with a
    `value_fn` of `status in {...}` declares its source correctly and still
    computes whatever it likes. `evse_ev_connected` was misclassified this way,
    which cost it both a Repair mention and its unavailability, while its
    sibling `evse_charging` — same field, same shape — got both.

    Case 3 keeps the producible gate satisfiable: it requires a path both
    adapters emit, so a schema-conditional field cannot be declared. Those are
    listed in `RESIDUAL_EXEMPT_PATHS` when the integration reads them outside a
    description.

    `test_no_derived_description_reads_one_producible_field` enforces this rule
    against every derived description rather than against any one instance.
    """


RESIDUAL_FIELD_PATHS: frozenset[str] = frozenset(
    {
        # switch.py reads this in entity code, not via a description value_fn
        "circuit.relay_state",
        # select.py uses a wrapper class rather than a frozen dataclass
        # description, so it cannot carry the field as a dataclass field
        "circuit.priority",
        # Consumed by entity naming and attributes rather than by any platform
        "circuit.name",
        "circuit.tabs",
        # sensor_circuit.py publishes this as a circuit attribute
        "circuit.relay_requester",
    }
)
"""Readers not carried on an entity description, and producible by both adapters.

Keep this small. A new entry is a hint that the reader belongs on a
description instead.
"""


class Producibility(Enum):
    """Which adapters publish a metadata row for an exempt residual path.

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

    paths: set[str] = set(RESIDUAL_FIELD_PATHS)
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
