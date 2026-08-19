"""Compare adapter field metadata against what this integration declares it reads.

Consumes the library's three-way signal:

- entry, ``resolved=True`` — produced; the unit is meaningful
- entry, ``resolved=False`` — a device is present but does not declare the
  property. Degradation.
- **no entry** — no device of that type. Hardware absent; not a defect.

Because the adapter classifies absence, this module needs no capability table
and never infers hardware presence from telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.sensor import SensorEntityDescription
from span_panel_api.models import FieldMetadata

from .field_paths import declared_field_paths

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UnitMismatch:
    """A declared unit that disagrees with the schema's."""

    field_path: str
    ha_unit: str
    schema_unit: str


@dataclass(frozen=True, slots=True)
class SchemaFindings:
    """Outcome of one validation pass."""

    unresolved: frozenset[str]
    unit_mismatches: tuple[UnitMismatch, ...]
    unread: frozenset[str]


def evaluate_field_metadata(
    field_metadata: dict[str, FieldMetadata] | None,
    sensor_defs: dict[str, SensorEntityDescription] | None = None,
) -> SchemaFindings:
    """Classify one snapshot of adapter metadata against our declarations."""
    if field_metadata is None:
        return SchemaFindings(frozenset(), (), frozenset())

    declared = declared_field_paths()
    sensor_defs = sensor_defs or {}

    unresolved: set[str] = set()
    mismatches: list[UnitMismatch] = []

    for field_path in declared:
        entry = field_metadata.get(field_path)
        if entry is None:
            # Hardware not present. Not a defect, and deliberately silent.
            continue
        if not entry.resolved:
            unresolved.add(field_path)
            continue
        description = sensor_defs.get(field_path)
        if description is None:
            continue
        ha_unit = description.native_unit_of_measurement
        if ha_unit is None or entry.unit is None:
            continue
        if str(entry.unit) != str(ha_unit):
            mismatches.append(UnitMismatch(field_path, str(ha_unit), str(entry.unit)))

    unread = frozenset(set(field_metadata) - set(declared))
    for field_path in sorted(unread):
        # An addition is legal within a major version. This is an inventory for
        # us, never a user-facing finding.
        _LOGGER.debug("Schema: %s is produced but no platform reads it", field_path)

    return SchemaFindings(frozenset(unresolved), tuple(mismatches), unread)
