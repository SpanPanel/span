"""Snapshot field paths this integration reads.

Most declarations live on the entity descriptions that read them, so they
cannot drift from the reader. A few readers are in entity code rather than on a
description; those are listed here.

This module replaces `schema_expectations.SENSOR_FIELD_MAP`, a hand-maintained
parallel dict that had already drifted once (it pointed at
`battery.product_name` and `pv.product_name` after the library renamed those
fields to `battery.model` / `pv.model`).

Field path convention: ``{snapshot_type}.{field_name}`` — ``panel``,
``circuit``, ``battery``, ``pv`` and ``evse``.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    }
)
"""Readers not carried on an entity description.

Keep this small. A new entry is a hint that the reader belongs on a
description instead.
"""


def declared_field_paths() -> dict[str, bool]:
    """Every field path the integration reads, mapped to whether it is derived.

    Derived paths are exempt from the producible check because they have no
    single source field.
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

    paths: dict[str, bool] = dict.fromkeys(RESIDUAL_FIELD_PATHS, False)
    for description in (
        *all_sensor_descriptions(),
        *BINARY_SENSORS,
        *EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
        BESS_CONNECTED_SENSOR,
    ):
        if not isinstance(description, FieldPathDeclarationMixin):
            # A description that cannot declare anything would be dropped
            # silently, which is the drift this module exists to prevent.
            raise TypeError(
                f"entity description '{description.key}' carries no field-path declaration"
            )
        if description.derived or description.field_path is None:
            continue
        paths[description.field_path] = False
    return paths
