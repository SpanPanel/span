"""Compare adapter field metadata against what this integration declares it reads.

Consumes the library's three-way signal:

- entry, ``resolved=True`` — produced; the unit is meaningful
- entry, ``resolved=False`` — a device is present but does not declare the
  property. Degradation.
- **no entry** — no device of that type. Hardware absent; not a defect.

Because the adapter classifies absence, this module needs no capability table
and never infers hardware presence from telemetry.

**Two inventories, not one.** An adapter's metadata map carries curated rows —
snapshot field paths this integration knows about — and, under the library's
discovery namespace, rows for properties the panel declares and the adapter
reads nothing from. They answer opposite questions and must never mix:
`unread` below is "we produce this and render nothing from it", which is an
inventory of *our* backlog, while a discovered row is "the panel has something
we never modelled". Letting the second into the first would bury ten known
entries under whatever a firmware release happened to add, and would make the
conformance gate's producible set depend on the panel in front of the user.

So the metadata is partitioned by namespace before any other question is asked
of it — see `partition`.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.sensor import SensorEntityDescription
from span_panel_api.models import DiscoveredMetadata, FieldMetadata, is_discovery_path

from .field_paths import RESIDUAL_EXEMPT_PATHS, declared_field_paths

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UnitMismatch:
    """A declared unit that disagrees with the schema's."""

    field_path: str
    ha_unit: str
    schema_unit: str


@dataclass(frozen=True, slots=True)
class DiscoveredProperty:
    """A property the panel declares that the running adapter reads nothing from.

    The runtime half of `tests/test_declared_but_unread`, which asks the same
    question of the adapter's reference capture and therefore cannot see a panel
    that starts publishing something in the field.

    Maintainer-facing only. Nothing creates an entity, a Repair or a
    notification from one of these — it is carried in diagnostics so that
    triaging an issue shows what that panel declares and this integration
    ignores, and so the rate can be measured across a few attachments.

    **Declarations only, deliberately.** Diagnostics leave the house into GitHub
    issues and forum posts, and `diagnostics.TO_REDACT` is key-based: it knows
    the config entry's keys and nothing at all about wire property names, so it
    could not protect a value put here. `retained` is the only thing this says
    about a value, and it says whether one exists rather than what it is.
    """

    path: str
    """The library's namespaced path, ``discovered.{device type}/{node}/{property}``.

    Carried verbatim rather than trimmed to the wire path: it is the key the
    adapter emitted, so a maintainer cross-referencing a capability catalog or
    the unread baseline is looking at the same string the library is.
    """

    datatype: str
    unit: str | None
    retained: bool | None
    """Whether the panel has published a value, or None if the adapter did not say.

    None is the forward-compatible case: the namespace is the contract and the
    enriched row type is not, so an adapter that namespaces a row without
    carrying `DiscoveredMetadata` still reports its path, datatype and unit
    rather than being dropped.
    """


@dataclass(frozen=True, slots=True)
class SchemaFindings:
    """Outcome of one validation pass."""

    unresolved: frozenset[str]
    unit_mismatches: tuple[UnitMismatch, ...]
    unread: frozenset[str]
    discovered: tuple[DiscoveredProperty, ...] = ()
    """Properties the panel declares and the adapter reads nothing from.

    Defaulted because it is additive and because every other member is a
    finding about *our* declarations, which this is not: an adapter that emits
    no discovered rows leaves this empty, and that is a fact about the adapter
    rather than a clean bill of health.
    """


def partition(
    field_metadata: dict[str, FieldMetadata],
) -> tuple[dict[str, FieldMetadata], tuple[DiscoveredProperty, ...]]:
    """Split one adapter metadata map into the curated rows and the discovered ones.

    The single place the namespace is tested, so a caller cannot half-apply it.
    Everything downstream — the producible gate, the unread inventory, the unit
    check, the Repairs reconciler — takes the curated half and can therefore not
    be perturbed by what a panel happens to declare.
    """
    curated: dict[str, FieldMetadata] = {}
    discovered: list[DiscoveredProperty] = []
    for path, entry in field_metadata.items():
        if not is_discovery_path(path):
            curated[path] = entry
            continue
        discovered.append(
            DiscoveredProperty(
                path=path,
                datatype=entry.datatype,
                unit=entry.unit,
                retained=entry.retained if isinstance(entry, DiscoveredMetadata) else None,
            )
        )
    return curated, tuple(sorted(discovered, key=lambda item: item.path))


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
    # First, before anything reads the map: the discovered rows are a report
    # about the panel, not an inventory of what we produce, and every question
    # below is the second kind.
    curated, discovered = partition(field_metadata)
    # Every path the producible gate covers, and no other. A read exempt from
    # the gate is either one the adapter maps no row for, so there is nothing
    # to ask, or one it maps that validation is deliberately kept from acting
    # on -- see `Producibility.MAPPED_UNVALIDATED`.
    declared = declared_field_paths()
    sensor_defs = sensor_defs or {}

    unresolved: set[str] = set()
    mismatches: list[UnitMismatch] = []

    for field_path in declared:
        entry = curated.get(field_path)
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
        mismatches.append(UnitMismatch(field_path, str(ha_unit), schema_unit))

    # `RESIDUAL_EXEMPT_PATHS` are read by the integration; they are exempt from
    # the *producible* gate because the adapter maps no row for them, or maps
    # one validation must not act on, so they are absent from `declared`
    # without being unread. Only the paths matter here; each entry's
    # `Producibility` annotation is what the conformance tests verify.
    #
    # `curated` and never `field_metadata`, which still holds both halves. A
    # discovered path reaching this set would read as a produced field nothing
    # renders -- a defect's shape -- and would bury ten deliberate entries under
    # whatever the panel's firmware happens to declare.
    unread = frozenset(set(curated) - set(declared) - RESIDUAL_EXEMPT_PATHS.keys())
    for field_path in sorted(unread):
        # An addition is legal within a major version. This is an inventory for
        # us, never a user-facing finding.
        _LOGGER.debug("Schema: %s is produced but no platform reads it", field_path)

    for declaration in discovered:
        # The panel's side of the same question, and equally not user-facing:
        # the user-facing half of this would be adoption, which is not built.
        _LOGGER.debug(
            "Schema: %s is declared by the panel and read by nothing here (%s%s, retained=%s)",
            declaration.path,
            declaration.datatype,
            f" in {declaration.unit}" if declaration.unit else "",
            declaration.retained,
        )

    return SchemaFindings(frozenset(unresolved), tuple(mismatches), unread, discovered)
