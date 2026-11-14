"""Snapshot field paths this integration reads.

Most declarations live on the entity descriptions that read them, so they
cannot drift from the reader. A few readers are in entity code rather than on a
description; those are listed here.

This module replaced `schema_expectations.SENSOR_FIELD_MAP` (since deleted), a
hand-maintained parallel dict that had already drifted once (it pointed at
`battery.product_name` and `pv.product_name` after the library renamed those
fields to `battery.model` / `pv.model`).

Field path convention: ``{snapshot_type}.{field_name}`` — ``panel``,
``circuit``, ``battery``, ``pv``, ``evse``, ``mid`` and ``pcs``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from homeassistant.helpers.entity import EntityDescription


class DerivedReason(Enum):
    """Why an entity's value is not one producible field.

    A field is producible when the parent/child adapter publishes a metadata row
    for it. A description is derived for exactly one of these reasons, and which
    one is mechanical: count the producible fields its `value_fn` reads.
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
    conflation this enum exists to break: as a bare `bool`, every reason below
    and that mistake looked identical.
    """

    NO_SOURCE_FIELD = "no_source_field"
    """Reads no field the adapter publishes a metadata row for.

    Either it reads nothing off the snapshot at all (`panel_status` reports
    coordinator reachability), or the field it reads is one the library fills
    without a row: `dsm_state`, every `mid.*` read, and the answers it derives
    from other properties, such as `grid_islandable`. Deliberately one member
    rather than two: the recorder cannot tell those apart — both leave an empty
    intersection with what the adapter emits — and a variant nothing can verify
    is exactly what this enum replaces.

    Such a description may still name the one field it reads as `field_path`.
    Naming it changes nothing while the adapter has no row for the path: schema
    validation never asks about it, so neither a Repair nor unavailability can
    follow. The path must then be enumerated in `RESIDUAL_EXEMPT_PATHS` as
    `Producibility.UNMAPPED`, so a row the library later grows for it fails that
    annotation and this reason together, which is the signal to promote the
    description to a plain declaration.
    """

    MULTIPLE_FIELDS = "multiple_fields"
    """Combines two or more producible fields, so no one of them is the source.

    The net-energy sensors subtract produced from consumed; blaming either field
    alone for the entity would be wrong.
    """


class Producibility(Enum):
    """Whether the parent/child adapter publishes a metadata row for an exempt residual path.

    Judged against the adapter's metadata map as its reference payload builds
    it, which is what `tests/test_field_path_conformance.py` asserts every
    annotation against.

    A *path's* producibility, not a *description's* classification: kept beside
    `DerivedReason` because both are verified against the same adapter metadata,
    and deliberately separate because they describe different subjects.
    `bess_connected` is `NO_SOURCE_FIELD` while the `battery.connected` path it
    reads is `UNMAPPED` — two facts about two things.

    There is deliberately no member for a path the adapter maps and nothing
    keeps from validation. Such a path satisfies the producible gate, so it
    belongs in `declared_field_paths()` rather than in an exemption. The
    conformance suite fails an `UNMAPPED` annotation the adapter has since
    grown a row for, and `tests/test_schema_findings_captures.py` fails a
    `MAPPED_UNVALIDATED` one nobody has given a reason for, so neither member
    can become a quiet home for a path that should be promoted.
    """

    UNMAPPED = "unmapped"
    """No metadata row in the adapter's map, so there is nothing to validate.

    The read is enumerated anyway, so that it is written down somewhere and
    `evaluate_field_metadata` can tell it from a field nothing reads.
    """

    MAPPED_UNVALIDATED = "mapped_unvalidated"
    """A row in the adapter's map, which validation is deliberately kept from asking about.

    A mapped path would ordinarily be a declaration: `evaluate_field_metadata`
    asks about it, and an unresolved row raises a `schema_field_unresolved`
    Repair naming every entity that reads it and takes down any entity whose
    value it is. These are the reads for which that would be wrong, and each
    entry in `RESIDUAL_EXEMPT_PATHS` says why.

    Named for the two facts a reader needs before promoting one: that the row
    exists, which the conformance suite holds to the adapter, and that
    validation does not act on it.
    """


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
    """Snapshot field this entity's value comes from, e.g. "circuit.instant_power_w".

    Declared here rather than in a parallel map so the declaration and the
    reader are the same object. Verified against `value_fn` by the proxy test
    in tests/test_field_path_introspection.py.

    Set whenever the entity's value comes from a single producible field, and
    allowed on a `NO_SOURCE_FIELD` description to name the one field it reads
    that the adapter maps no row for. What keeps such a path out of the
    producible gate is `derived`, not the absence of this. Consumers that ask
    "which field is this entity's value" (`SpanPanelEntity._declared_field_paths`,
    `_reads_an_unresolved_field`) therefore read this alone; the gate
    additionally consults `derived`.
    """

    derived: DerivedReason | None = None
    """Why this entity's value is not one producible field, or `None`.

    Which reason applies is not a matter of opinion — see `DerivedReason`, whose
    members are each asserted against what the `value_fn` actually reads.

    Its relationship to `field_path` is per reason, pinned by
    `test_every_description_declares_exactly_one`: `MULTIPLE_FIELDS` has no
    single field to name, so `field_path` stays `None`; `NO_SOURCE_FIELD` may
    name the unmapped field it reads, and `DerivedReason.NO_SOURCE_FIELD` says
    what that obliges.

    A reason rather than a flag because `bool` conflated four situations, and
    that conflation is how `evse_ev_connected` — one producible field, marked
    derived — stayed invisible to both the Repair count and the availability
    probe. Every consumer tests this by truthiness, which an enum member and
    `None` answer exactly as `True` and `False` did.
    """


RESIDUAL_EXEMPT_PATHS: Mapping[str, Producibility] = MappingProxyType(
    {
        # Homie `$target` values — a pending-command echo, not a schema field.
        "circuit.relay_state_target": Producibility.UNMAPPED,
        "circuit.priority_target": Producibility.UNMAPPED,
        # Assembled by the library from panel topology rather than read from a
        # schema property.
        "circuit.device_type": Producibility.UNMAPPED,
        "circuit.relative_position": Producibility.UNMAPPED,
        # The panel reports it outside the typed field surface.
        "panel.panel_size": Producibility.UNMAPPED,
        # The enclosure's build identity, read by `snapshot_to_device_info` for
        # the panel's device card. No row, and deliberately: a row exists to
        # carry a unit and a datatype for a *reading*, which an identity string
        # is not. Same shape as the `mid.*` device-card reads below.
        "panel.vendor_name": Producibility.UNMAPPED,
        "panel.model": Producibility.UNMAPPED,
        "panel.hardware_version": Producibility.UNMAPPED,
        # `shed/policy` parsed, read for the attributes on `dsm_state`
        # (`SpanPanelPanelStatus.extra_state_attributes`). The raw document is
        # kept beside the parsed members because the policy schema is versioned
        # in its own `$id`: an algorithm this library does not recognise still
        # reaches a user as the string the panel published. No row: a JSON
        # document has no unit surface for a row to describe.
        "panel.shed_policy": Producibility.UNMAPPED,
        "panel.shed_policy_algorithm": Producibility.UNMAPPED,
        "panel.shed_soc_threshold_shed_percent": Producibility.UNMAPPED,
        "panel.shed_soc_threshold_release_percent": Producibility.UNMAPPED,
        # The panel identity key behind every unique_id and the panel DeviceInfo
        # (~30 read sites).
        "panel.serial_number": Producibility.UNMAPPED,
        # The `dsm_state` sensor's source, which is `NO_SOURCE_FIELD` because
        # this has no row, and a gate on the GFE override button's availability
        # (`SpanPanelGFEOverrideButton.available`).
        "panel.dsm_state": Producibility.UNMAPPED,
        # The circuit's own identity key, used for lookups and id construction
        # (helpers.py, coordinator.py, entity_resolver.py).
        "circuit.circuit_id": Producibility.UNMAPPED,
        # util.py reads these off the MID snapshot for device_info, and
        # sensor_panel.py reads the grid-forming name for an attribute.
        "mid.hardware_version": Producibility.UNMAPPED,
        "mid.software_version": Producibility.UNMAPPED,
        "mid.vendor_name": Producibility.UNMAPPED,
        "mid.model": Producibility.UNMAPPED,
        "mid.serial_number": Producibility.UNMAPPED,
        "mid.grid_forming_device_name": Producibility.UNMAPPED,
        # `pv_device_info` reads the inverter's firmware version for its device
        # card. `pv.vendor_name` and `pv.model` are not here beside it because
        # they are `field_path` declarations on the three PV metadata sensors
        # already, and the card reads the same two fields those sensors do. The
        # library reads `info/firmware-version` into the snapshot and maps no
        # row for it, for the reason the `mid.*` and `panel.*` card reads above
        # carry none: a row states a *reading's* unit and datatype, and a
        # version string is not a reading.
        #
        # `pv.serial_number` is deliberately absent -- from this table, from the
        # card and from the snapshot. See `pv_device_info`.
        "pv.software_version": Producibility.UNMAPPED,
        # The `mid_grid_state` sensor's source field — utility-supply health,
        # the one non-metadata entity the MID brings. The adapter maps nothing
        # on the MID, which is why the description is `NO_SOURCE_FIELD`.
        "mid.grid_state": Producibility.UNMAPPED,
        # The EVSE's Homie node id — an addressing handle used to build the
        # sub-device identifier, not a published field.
        "evse.node_id": Producibility.UNMAPPED,
        # The charge-current control's two non-readings, read by
        # `SpanEvseNumber`: `$settable` on the limit's declaration, which is the
        # entity-creation gate, and the Homie `$target` echo of a write the
        # panel has accepted but not yet applied, rendered as an attribute. Both
        # are facts about a command rather than readings, so the adapter maps no
        # row for either — the same shape as the `circuit.*_target` pair at the
        # top of this map.
        "evse.charge_current_limit_settable": Producibility.UNMAPPED,
        "evse.charge_current_limit_target_a": Producibility.UNMAPPED,
        # The shed-forecast refinements, read for attributes on the two forecast
        # sensors (`SpanShedForecastSensor.extra_state_attributes`). The library
        # reads all three into the snapshot but maps a `_PROPERTY_FIELD_MAP` row
        # for none: they qualify the two live estimates rather than being
        # readings of their own, so there is no unit surface for a row to
        # describe. Same shape as the `mid.*` attribute reads above.
        "panel.shed_full_charge_time_to_priority_shed_min": Producibility.UNMAPPED,
        "panel.shed_full_charge_total_time_remaining_min": Producibility.UNMAPPED,
        "panel.shed_forecast_confidence": Producibility.UNMAPPED,
        # The PCS arbitration's *inputs*, read for the twelve attributes on
        # `pcs_import_limit` plus its `pcs_enabled` (`pcs_arbitration_attributes`
        # in sensor_definitions). The library reads all thirteen into the
        # snapshot and maps a `_PROPERTY_FIELD_MAP` row for none of them,
        # deliberately: the capability calls `import-limit` and
        # `binding-constraint` "the result", and these explain that result
        # rather than being readings of their own, so there is no unit surface
        # for a row to describe. Same shape as the shed-forecast refinements
        # above.
        "pcs.enabled": Producibility.UNMAPPED,
        "pcs.feed_import_limit_a": Producibility.UNMAPPED,
        "pcs.feed_import_limit_enablement": Producibility.UNMAPPED,
        "pcs.feed_import_limit_active": Producibility.UNMAPPED,
        "pcs.operator_import_limit_a": Producibility.UNMAPPED,
        "pcs.operator_import_limit_enablement": Producibility.UNMAPPED,
        "pcs.operator_import_limit_active": Producibility.UNMAPPED,
        "pcs.off_grid_import_limit_a": Producibility.UNMAPPED,
        "pcs.off_grid_import_limit_enablement": Producibility.UNMAPPED,
        "pcs.off_grid_import_limit_active": Producibility.UNMAPPED,
        "pcs.requested_import_limit_a": Producibility.UNMAPPED,
        "pcs.requested_import_limit_enablement": Producibility.UNMAPPED,
        "pcs.requested_import_limit_active": Producibility.UNMAPPED,
        # A circuit's *participation* in that PCS — `managed` and `priority`,
        # read as attributes on its power sensor (`sensor_circuit.py`). The
        # adapter maps neither, for the reason above: they qualify a circuit's
        # reading rather than being one. So they are not `_residual_field_paths`
        # on the entity, which feed `declared_field_paths()`, where the
        # producible gate rejects a path with no row.
        "circuit.pcs_managed": Producibility.UNMAPPED,
        "circuit.pcs_priority": Producibility.UNMAPPED,
        # The peers a circuit's meter and relay are shared with, named in the
        # `meter_shared_with` and `relay_shared_with` attributes of its power
        # sensor and switch. Membership, not a reading, so the adapter maps no
        # row for either.
        "circuit.meter_shared_with": Producibility.UNMAPPED,
        "circuit.relay_shared_with": Producibility.UNMAPPED,
        # A circuit's declared nominal voltage, which its voltage attribute
        # prefers, and its breaker's protection functions, an attribute of its
        # power sensor.
        "circuit.nominal_voltage_v": Producibility.UNMAPPED,
        "circuit.protection_functions": Producibility.UNMAPPED,
        # Panel readings created only where the panel declares them, each read by
        # a `NO_SOURCE_FIELD` description: the adapter's reference payload
        # declares none of them. The upstream protection rating is also the
        # current monitor's mains limit where the panel has no main breaker.
        "panel.busbar_current_a": Producibility.UNMAPPED,
        "panel.frequency_hz": Producibility.UNMAPPED,
        "panel.upstream_protection_rating_a": Producibility.UNMAPPED,
        # What a circuit's `connection/feeds-role` declares, which orients its
        # power and Net Energy as generation where the role is solar
        # (`energy_orientation.circuit_is_generation`).
        "circuit.feeds_role": Producibility.UNMAPPED,
        # A circuit's relay and shedding facts, read for attributes on its power
        # sensor and, for `always_on`, its topology record. The library derives
        # both from the `switch` declaration, `switch/relay-controllable` and
        # the shed priority rather than reading a property of their own, so
        # there is no row to map.
        "circuit.always_on": Producibility.UNMAPPED,
        "circuit.is_sheddable": Producibility.UNMAPPED,
        # The `grid_forming_entity` sensor's source field. The library answers it
        # through `resolve_dominant_power_source` over the MID's
        # `grid/grid-forming-entity` rather than from a property of its own, so
        # there is no row, which is what makes the sensor `NO_SOURCE_FIELD`.
        "panel.dominant_power_source": Producibility.UNMAPPED,
        # The `grid_islandable` binary sensor's source field. The library answers
        # it through `resolve_grid_islandable` over the BESS inverters rather
        # than from a property of its own, so there is no row, which is what
        # makes the description `NO_SOURCE_FIELD`.
        "panel.grid_islandable": Producibility.UNMAPPED,
        # util.py builds the EVSE DeviceInfo from the first four. Two readers
        # resolve the feed through `feed_circuit_id`: sensor.py keys each
        # charger's DeviceInfo by it, which is what puts the feeding circuit's
        # sensors on the charger's card, and entity_resolver.py names a charger
        # after the circuit it resolves to. The identity strings carry no row for
        # the reason the `panel.*` and `mid.*` card reads above carry none, and
        # the feed is assembled from the feeding circuit's `connection` record
        # rather than read from a property of the charger.
        "evse.vendor_name": Producibility.UNMAPPED,
        "evse.model": Producibility.UNMAPPED,
        "evse.serial_number": Producibility.UNMAPPED,
        "evse.software_version": Producibility.UNMAPPED,
        "evse.feed_circuit_id": Producibility.UNMAPPED,
        # The `bess_connected` binary sensor's source field, and what decides the
        # GFE override button where the battery publishes no communication
        # state. The library takes it from the panel-side owner's
        # `connection/*-device-status` record for the battery rather than from a
        # property of the BESS, and maps no row for it, which is what makes
        # `bess_connected` `NO_SOURCE_FIELD`. `pv.connected` and
        # `evse.connected` carry the same fact for the inverter and the
        # charger, and those do have a row, so they are plain declarations
        # rather than entries here.
        "battery.connected": Producibility.UNMAPPED,
        # Whether a circuit's relay can be commanded, from
        # `switch/relay-controllable`. A gate rather than a reading: it decides
        # whether a circuit gets a switch and a priority select
        # (`helpers.circuit_has_a_breaker_switch`, `circuit_has_a_priority_select`)
        # and whether its power sensor carries the shed and PCS policy
        # attributes, and it rides in the topology record. Where the property is
        # missing, the library already answers from whether a `switch` is
        # declared, so validating it would raise a Repair for a fact the adapter
        # has filled in, on a field no entity presents as its reading.
        "circuit.is_user_controllable": Producibility.MAPPED_UNVALIDATED,
        # The commissioned ceiling the `evse_charge_current_limit` number reads
        # for `native_max_value`, resolved from the charger's own `$description`.
        # A bound on the control rather than its reading: a charger that
        # declares its limit without a ceiling is still bounded by the limit's
        # declared range, or by the control's own maximum, and its limit is still
        # worth setting. Validating the ceiling would raise a Repair naming the
        # control for that charger. It is therefore not on
        # `SpanEvseNumber._residual_field_paths` either, which feed
        # `declared_field_paths()`.
        "evse.charge_current_ceiling_a": Producibility.MAPPED_UNVALIDATED,
    }
)
"""Residual readers exempt from the producible check, and why each is exempt.

The gate requires a path the parent/child adapter maps a metadata row for, so a
read is exempt for one of two reasons, and the annotation says which:
`Producibility.UNMAPPED` for a value the adapter maps no row for — Homie
`$target` echoes, values the library assembles from panel topology or derives
from other properties, identity strings for a device card, every `mid.*` field;
`Producibility.MAPPED_UNVALIDATED` for a value it does map, which validation is
deliberately kept from asking about.

Exempt is not the same as derived: these are read straight off a snapshot field,
that field just has no row for the gate to check, or has one that validation
must not act on.

The annotations are not documentation. `tests/test_field_path_conformance.py`
builds the adapter's metadata from its reference payload and asserts every
entry's annotation against what it actually produces, so a stale reason fails
the build instead of misleading a reader. A path the library grows a row for
fails there too, and is then either promoted to a declaration or given a
written reason to stay out of validation.

Deliberately **not** returned by `declared_field_paths()`. Recorded here so the
reads are still enumerated somewhere rather than being invisible.
"""


def _iter_declared[DescriptionT: EntityDescription](
    descriptions: Iterable[DescriptionT],
) -> Iterator[tuple[str, FieldPathDeclarationMixin, DescriptionT]]:
    """Yield ``(field_path, declaration, description)`` for each named source field.

    The single copy of the traversal rule — which descriptions carry a
    declaration at all — so no caller can drift from another on it.

    Raises `TypeError` for a description that carries no
    `FieldPathDeclarationMixin`: such a description would be dropped silently,
    which is the drift this module exists to prevent. A description that carries
    the mixin and names no field is skipped, which is the declared-derived case
    rather than drift.

    `declaration` and `description` are the same object; they are yielded twice
    because a type variable bounded on `EntityDescription` cannot also be known
    to carry the mixin, and the alternative is a `cast` at every call site.
    """
    for description in descriptions:
        if not isinstance(description, FieldPathDeclarationMixin):
            raise TypeError(
                f"entity description '{description.key}' carries no field-path declaration"
            )
        if description.field_path is None:
            continue
        yield description.field_path, description, description


def iter_source_field_declarations[DescriptionT: EntityDescription](
    descriptions: Iterable[DescriptionT],
) -> Iterator[tuple[str, DescriptionT]]:
    """Yield ``(field_path, description)`` for each description that names one.

    Every entity that names a single source field, whatever `derived` says
    about whether the adapter maps a row for it.
    """
    for field_path, _, description in _iter_declared(descriptions):
        yield field_path, description


def iter_field_path_declarations[DescriptionT: EntityDescription](
    descriptions: Iterable[DescriptionT],
) -> Iterator[tuple[str, DescriptionT]]:
    """Yield only the declarations the producible gate covers.

    `derived` descriptions are skipped whether or not they name a field. That
    is the whole content of the exemption: a `NO_SOURCE_FIELD` description may
    name the field it reads, but the adapter maps no row for that path, so the
    gate this function feeds would reject it, and `RESIDUAL_EXEMPT_PATHS` is
    where it is enumerated instead.
    """
    for field_path, declaration, description in _iter_declared(descriptions):
        if declaration.derived:
            continue
        yield field_path, description


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
    that declare residuals are imported here explicitly -- exactly those, no
    more: a module listed here that declares nothing is a stale import, and one
    that declares something and is missing would go missing silently. Both
    directions are pinned by
    `test_the_residual_walk_imports_exactly_the_modules_that_declare_one`, which
    reads this list out of the source because under pytest the platform modules
    are already imported for other reasons and an omission here would still walk.
    """
    # Deferred for the same cycle-avoidance reason as `declared_field_paths()`
    # below: every platform module imports this one for the declaration mixin.
    from . import (  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
        binary_sensor,
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


def platform_descriptions() -> tuple[EntityDescription, ...]:
    """Every entity description this integration builds entities from.

    One copy of the collection list, so the producible gate and the tests that
    hold each description to its declaration walk the same descriptions: a
    second copy is how a platform ends up gated by one and checked by the other.

    Kept by hand, and held complete by
    `test_every_description_the_integration_defines_is_collected`, which walks
    every module in the package: a description a module defines and this list
    misses fails the build, unless that test excuses it with the reason it is
    not validated.
    """
    # Deferred: the platform modules import `FieldPathDeclarationMixin` from
    # here, so importing them at module scope would close that loop. (They no
    # longer reach the package root for the config-entry type -- that lives in
    # the leaf `runtime` module -- but this loop is the module's own and stays.)
    from .binary_sensor import (  # pylint: disable=import-outside-toplevel
        BESS_CONNECTED_SENSOR,
        BINARY_SENSORS,
        EVSE_BINARY_SENSORS,
        EVSE_PANEL_LINK_SENSOR,
        GRID_ISLANDABLE_SENSOR,
        PCS_ACTIVE_SENSOR,
        PV_INVERTER_LINK_SENSOR,
        PV_PANEL_LINK_SENSOR,
    )
    from .lock import EVSE_LOCK  # pylint: disable=import-outside-toplevel
    from .number import EVSE_NUMBERS  # pylint: disable=import-outside-toplevel
    from .sensor_definitions import (  # pylint: disable=import-outside-toplevel
        all_sensor_descriptions,
    )

    return (
        *all_sensor_descriptions(),
        *BINARY_SENSORS,
        *EVSE_BINARY_SENSORS,
        *EVSE_NUMBERS,
        EVSE_LOCK,
        GRID_ISLANDABLE_SENSOR,
        BESS_CONNECTED_SENSOR,
        PCS_ACTIVE_SENSOR,
        PV_PANEL_LINK_SENSOR,
        PV_INVERTER_LINK_SENSOR,
        EVSE_PANEL_LINK_SENSOR,
    )


def declared_field_paths() -> frozenset[str]:
    """Field paths the integration reads that the parent/child adapter must produce.

    Derived entities are excluded: their value is not one field the adapter maps
    a row for, so there is nothing for it to produce. Residual readers the
    adapter maps no row for, or whose row validation must not act on, are
    excluded too, and are listed in `RESIDUAL_EXEMPT_PATHS`.

    This is also exactly the set `schema_validation.evaluate_field_metadata`
    asks the adapter about, so a path here is one whose unresolved row raises a
    Repair.
    """
    paths: set[str] = set(residual_field_paths())
    paths.update(
        field_path for field_path, _ in iter_field_path_declarations(platform_descriptions())
    )
    return frozenset(paths)
