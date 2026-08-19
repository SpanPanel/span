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

from .field_paths import RESIDUAL_EXEMPT_PATHS, declared_field_paths

_LOGGER = logging.getLogger(__name__)


KNOWN_BAD_SCHEMA_UNITS: dict[str, str] = {
    # SPAN firmware declares the circuit `active-power` property as "kW" while
    # publishing watts. Three things agree that the label, not the reading, is
    # wrong: the sibling `lugs` device declares the same quantity as "W"; the
    # library consumes the value unscaled ("active-power is in watts",
    # span_panel_api_schema_0/consumer.py:244); and the independent `span-hass`
    # integration documents the same defect under "Known SPAN API Issue" and
    # hardcodes the same override. Our `UnitOfPower.WATT` declaration is correct.
    "circuit.instant_power_w": "kW",
}
"""Schema unit declarations this integration knowingly ignores, by field path.

Every entry is a firmware defect worked around deliberately — the panel labels a
property with a unit it does not publish — never a sensor whose unit we gave up
on checking. Without this, a panel running the affected firmware raises a
mismatch its owner cannot act on and that reflects no real defect.

The match is exact. If firmware later declares something OTHER than the value
here for the same field, that is new information and is still reported.
"""


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
    field_metadata: dict[str, FieldMetadata],
    sensor_defs: dict[str, SensorEntityDescription] | None = None,
) -> SchemaFindings:
    """Classify one snapshot of adapter metadata against our declarations.

    `field_metadata` is deliberately not optional. The client returns None until
    its adapter is ready, and that sentinel means "unknown", not "healthy" —
    answering it with empty findings would tell a reconciler every issue is
    resolved. Callers interpret the sentinel themselves; see
    `SpanPanelCoordinator._run_schema_validation`.
    """
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
        schema_unit = str(entry.unit)
        if schema_unit == str(ha_unit):
            continue
        if KNOWN_BAD_SCHEMA_UNITS.get(field_path) == schema_unit:
            # A firmware mislabel we already work around. See the constant.
            continue
        mismatches.append(UnitMismatch(field_path, str(ha_unit), schema_unit))

    # `RESIDUAL_EXEMPT_PATHS` are read by the integration; they are exempt from
    # the *producible* gate because only one adapter emits them, or neither
    # does, so they are absent from `declared` without being unread. Only the
    # paths matter here; each entry's `Producibility` annotation is what the
    # conformance tests verify.
    unread = frozenset(set(field_metadata) - set(declared) - RESIDUAL_EXEMPT_PATHS.keys())
    for field_path in sorted(unread):
        # An addition is legal within a major version. This is an inventory for
        # us, never a user-facing finding.
        _LOGGER.debug("Schema: %s is produced but no platform reads it", field_path)

    return SchemaFindings(frozenset(unresolved), tuple(mismatches), unread)
