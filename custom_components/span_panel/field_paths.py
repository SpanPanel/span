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

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

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
    """True when the value is computed from several fields, or none.

    Derived entities have no single source field, so they are exempt from the
    producible check.
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


RESIDUAL_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        # Homie `$target` values — a pending-command echo, not a schema field.
        # Neither adapter publishes a metadata row for them.
        "circuit.relay_state_target",
        "circuit.priority_target",
        # Assembled by the library from panel topology rather than read from a
        # schema property; no metadata row in either adapter.
        "circuit.device_type",
        "circuit.relative_position",
        # No metadata row in either adapter — the panel reports it outside the
        # typed field surface.
        "panel.panel_size",
        # The panel identity key behind every unique_id and the panel DeviceInfo
        # (~30 read sites). Neither adapter publishes a row for it.
        "panel.serial_number",
        # Gates button availability at button.py:115. No row in either adapter —
        # the same reason the `dsm_state` sensor is `derived=True`.
        "panel.dsm_state",
        # The circuit's own identity key, used for lookups and id construction
        # (helpers.py, coordinator.py, entity_resolver.py). No row in either
        # adapter.
        "circuit.circuit_id",
        # Neither adapter emits any `mid.*` metadata rows at all; util.py reads
        # these off the MID snapshot for device_info, and sensor_panel.py reads
        # the grid-forming name for an attribute.
        "mid.hardware_version",
        "mid.software_version",
        "mid.vendor_name",
        "mid.model",
        "mid.serial_number",
        "mid.grid_forming_device_name",
        # The EVSE's Homie node id — an addressing handle used to build the
        # sub-device identifier, not a published field. No row in either adapter.
        "evse.node_id",
        # Schema-conditional: schema_1 publishes it, schema_0 has no row.
        "circuit.is_user_controllable",
        # Schema-conditional: schema_0 publishes these, schema_1 has no row.
        "circuit.always_on",
        "circuit.is_sheddable",
        "panel.wifi_ssid",
        # Schema-conditional: schema_0 publishes these, schema_1 has no row.
        # util.py builds the EVSE DeviceInfo from them; entity_resolver.py and
        # sensor.py resolve the fed circuit through `feed_circuit_id`.
        "evse.vendor_name",
        "evse.model",
        "evse.serial_number",
        "evse.software_version",
        "evse.feed_circuit_id",
        # Schema-conditional: schema_0 publishes it, schema_1 derives islanding
        # via `resolve_grid_islandable(inverters)`. Read at binary_sensor.py:408
        # as an entity-creation gate, outside any description.
        "panel.grid_islandable",
        # Schema-conditional: schema_0 publishes it, schema_1's
        # `_PROPERTY_FIELD_MAP` has no `connected` row — the same gap that makes
        # the `bess_connected` binary sensor `derived=True`.
        "battery.connected",
    }
)
"""Residual readers exempt from the producible check, for one of two reasons.

**Not produced by any adapter** — Homie `$target` echoes, values the library
assembles from panel topology, and every `mid.*` field. There is no metadata row
to check against on either schema.

**Produced by only one adapter** — schema-conditional fields. The gate requires
a path to be producible by *both* adapters, so a field present on one schema and
absent from the other cannot satisfy it. Exempt is not the same as derived:
these are read straight off a snapshot field, that field just is not on both
schemas.

Deliberately **not** returned by `declared_field_paths()`. Recorded here so the
reads are still enumerated somewhere rather than being invisible. The per-entry
comments say which of the two reasons applies.
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
