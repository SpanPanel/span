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
    adapters emit, so a schema-conditional field cannot satisfy it. If the
    other adapter ever grows the field, this stops being true and the
    verification fails, demanding promotion to a plain `field_path` declaration.

    Alone among the reasons, this one still names its source: the description
    sets `field_path` **as well as** `derived`, and
    `test_schema_conditional_descriptions_name_their_field` holds it to that.
    The two attributes answer different questions -- "what does this entity's
    value come from" and "why is that path outside the both-adapters gate" --
    and only the first is what a Repair and the availability probe need. Leaving
    it unset excused schema-conditional entities from both, which is the
    `evse_ev_connected` failure mode one level along: an entity whose field the
    panel stopped resolving would keep publishing a default, and the Repair
    naming that field would say "0 entities affected".
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
    """Snapshot field this entity's value comes from, e.g. "circuit.instant_power_w".

    Declared here rather than in a parallel map so the declaration and the
    reader are the same object. Verified against `value_fn` by the proxy test
    in tests/test_field_path_introspection.py.

    Set whenever the entity *has* a single source field — including when that
    field is schema-conditional and so cannot enter the producible gate. What
    keeps a schema-conditional path out of the gate is `derived`, not the
    absence of this. Consumers that ask "which field is this entity's value"
    (`SpanPanelEntity._declared_field_paths`, `_reads_an_unresolved_field`)
    therefore read this alone; the gate additionally consults `derived`.
    """

    derived: DerivedReason | None = None
    """Why this entity's source field is outside the producible gate, or `None`.

    Which reason applies is not a matter of opinion — see `DerivedReason`, whose
    members are each asserted against what the `value_fn` actually reads.

    Its relationship to `field_path` is per reason, pinned by
    `test_every_description_declares_exactly_one`: `NO_SOURCE_FIELD` and
    `MULTIPLE_FIELDS` have no single field to name, so `field_path` stays
    `None`; `SCHEMA_CONDITIONAL_FIELD` has exactly one and must name it.

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
        # The enclosure's build identity, read by `snapshot_to_device_info` for
        # the panel's device card. No row on either adapter, and deliberately:
        # flat declares none of the three, and schema_1 rows exist to carry a
        # unit and a datatype for a *reading*, which an identity string is not.
        # Same shape as the `mid.*` device-card reads below.
        "panel.vendor_name": Producibility.NEITHER,
        "panel.model": Producibility.NEITHER,
        "panel.hardware_version": Producibility.NEITHER,
        # `shed/policy` parsed, read for the attributes on `dsm_state`
        # (`SpanPanelPanelStatus.extra_state_attributes`). The raw document is
        # kept beside the parsed members because the policy schema is versioned
        # in its own `$id`: an algorithm this library does not recognise still
        # reaches a user as the string the panel published. No row on either
        # adapter -- flat has no `shed` node, and a JSON document has no unit
        # surface for a schema_1 row to describe.
        "panel.shed_policy": Producibility.NEITHER,
        "panel.shed_policy_algorithm": Producibility.NEITHER,
        "panel.shed_soc_threshold_shed_percent": Producibility.NEITHER,
        "panel.shed_soc_threshold_release_percent": Producibility.NEITHER,
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
        # `pv_device_info` reads the inverter's firmware version for its device
        # card. `pv.vendor_name` and `pv.model` are not here beside it because
        # they are `field_path` declarations on the three PV metadata sensors
        # already, and the card reads the same two fields those sensors do.
        #
        # `SCHEMA_0_ONLY`, and it says so because the annotation is checked
        # rather than asserted: this read `NEITHER` on the claim that "flat's
        # `pv` device class declares no firmware version at all", which was
        # wrong. Flat declares `software-version` on `energy.ebus.device.pv`,
        # and the library grew the mapping row for it once a producer valued the
        # v1.0 half. schema_1 still carries no row, for the reason the `mid.*`
        # and `panel.*` card reads above carry none: a row states a *reading's*
        # unit and datatype, and a version string is not a reading.
        #
        # `pv.serial_number` is deliberately absent -- from this table, from the
        # card and from the snapshot. See `pv_device_info`.
        "pv.software_version": Producibility.SCHEMA_0_ONLY,
        # The `mid_grid_state` sensor's source field — utility-supply health,
        # the one non-metadata entity the MID brings. Neither adapter maps the
        # MID at all, which is why the description is `NO_SOURCE_FIELD`.
        "mid.grid_state": Producibility.NEITHER,
        # The EVSE's Homie node id — an addressing handle used to build the
        # sub-device identifier, not a published field.
        "evse.node_id": Producibility.NEITHER,
        # The charge-current control's two non-readings, read by
        # `SpanEvseNumber`: `$settable` on the limit's declaration, which is the
        # entity-creation gate, and the Homie `$target` echo of a write the
        # panel has accepted but not yet applied, rendered as an attribute. Both
        # are facts about a command rather than readings, so no adapter carries
        # a metadata row for either — the same shape as the `circuit.*_target`
        # pair at the top of this map, and the same reason `panel.grid_islandable`
        # sits here as a creation gate.
        "evse.charge_current_limit_settable": Producibility.NEITHER,
        "evse.charge_current_limit_target_a": Producibility.NEITHER,
        # The shed-forecast refinements, read for attributes on the two forecast
        # sensors (`SpanShedForecastSensor.extra_state_attributes`). schema_1
        # reads all three into the snapshot but carries a `_PROPERTY_FIELD_MAP`
        # row for neither: they qualify the two live estimates rather than being
        # readings of their own, so there is no unit surface for a row to
        # describe. Same shape as the `mid.*` attribute reads above.
        "panel.shed_full_charge_time_to_priority_shed_min": Producibility.NEITHER,
        "panel.shed_full_charge_total_time_remaining_min": Producibility.NEITHER,
        "panel.shed_forecast_confidence": Producibility.NEITHER,
        # The PCS arbitration's *inputs*, read for the twelve attributes on
        # `pcs_import_limit` plus its `pcs_enabled` (`pcs_arbitration_attributes`
        # in sensor_definitions). schema_1 reads all thirteen into the snapshot
        # and carries a `_PROPERTY_FIELD_MAP` row for none of them, deliberately:
        # the capability calls `import-limit` and `binding-constraint` "the
        # result", and these explain that result rather than being readings of
        # their own, so there is no unit surface for a row to describe. Same
        # shape as the shed-forecast refinements above.
        "pcs.enabled": Producibility.NEITHER,
        "pcs.feed_import_limit_a": Producibility.NEITHER,
        "pcs.feed_import_limit_enablement": Producibility.NEITHER,
        "pcs.feed_import_limit_active": Producibility.NEITHER,
        "pcs.operator_import_limit_a": Producibility.NEITHER,
        "pcs.operator_import_limit_enablement": Producibility.NEITHER,
        "pcs.operator_import_limit_active": Producibility.NEITHER,
        "pcs.off_grid_import_limit_a": Producibility.NEITHER,
        "pcs.off_grid_import_limit_enablement": Producibility.NEITHER,
        "pcs.off_grid_import_limit_active": Producibility.NEITHER,
        "pcs.requested_import_limit_a": Producibility.NEITHER,
        "pcs.requested_import_limit_enablement": Producibility.NEITHER,
        "pcs.requested_import_limit_active": Producibility.NEITHER,
        # A circuit's *participation* in that PCS — `managed` and `priority`,
        # read as attributes on its power sensor (`sensor_circuit.py`). Not
        # `_residual_field_paths` on the entity: that feeds
        # `declared_field_paths()`, and no flat circuit declares a `pcs` node at
        # all, so the producible gate would reject both. schema_1 maps neither
        # for the reason above — they qualify a circuit's reading rather than
        # being one.
        "circuit.pcs_managed": Producibility.NEITHER,
        "circuit.pcs_priority": Producibility.NEITHER,
        "circuit.is_user_controllable": Producibility.SCHEMA_1_ONLY,
        # The two backup-planning estimates behind `time_to_priority_shed` and
        # `shed_total_time_remaining`, whose descriptions are
        # `SCHEMA_CONDITIONAL_FIELD` for exactly this reason: no flat panel
        # publishes `energy.ebus.capability.shed-forecast` at all, so the
        # producible gate — which demands both adapters — cannot be satisfied.
        # schema_1 does carry a metadata row for each, which is what makes the
        # annotation SCHEMA_1_ONLY rather than NEITHER and buys the pair unit
        # and datatype validation against the panel's own `$description`.
        "panel.shed_time_to_priority_shed_min": Producibility.SCHEMA_1_ONLY,
        "panel.shed_total_time_remaining_min": Producibility.SCHEMA_1_ONLY,
        # The BESS's own meter and its own link health, behind `bess_meter_power`
        # and `bess_communication_state`, whose descriptions are
        # `SCHEMA_CONDITIONAL_FIELD` for the usual reason: flat's BESS device
        # class declares neither property, so the both-adapters gate cannot be
        # satisfied. schema_1 carries a `_PROPERTY_FIELD_MAP` row for each, which
        # is what makes these SCHEMA_1_ONLY rather than NEITHER and buys them unit
        # and datatype validation against the BESS's own `$description`.
        #
        # `battery.power_w` carries `bess_meter_power`, and `has_bess` reads it to
        # decide whether a battery is commissioned. It agrees with `battery_power`
        # by construction: the two wire properties behind them carry the same sign
        # as each other on both the panel and the emitter, and each path negates
        # once. See `BESS_TELEMETRY_SENSORS` for which direction that shared
        # convention runs, which a live capture has not settled.
        "battery.power_w": Producibility.SCHEMA_1_ONLY,
        "battery.communication_state": Producibility.SCHEMA_1_ONLY,
        # The Power Control System's result, behind `pcs_import_limit`,
        # `pcs_binding_constraint` and the `pcs_active` binary sensor. Their
        # descriptions are `SCHEMA_CONDITIONAL_FIELD` for the usual reason: no
        # flat panel declares `energy.ebus.capability.pcs`, so the both-adapters
        # gate cannot be satisfied. schema_1 carries a `_PROPERTY_FIELD_MAP` row
        # for each of these three, which is what makes them SCHEMA_1_ONLY rather
        # than NEITHER and buys `pcs.import_limit_a` unit validation against the
        # panel's own `$description`.
        "pcs.import_limit_a": Producibility.SCHEMA_1_ONLY,
        "pcs.binding_constraint": Producibility.SCHEMA_1_ONLY,
        "pcs.active": Producibility.SCHEMA_1_ONLY,
        # The enclosure's view of the link to each circuit-fed DER, behind the
        # `pv_panel_link` and `evse_panel_link` binary sensors. Both descriptions
        # are `SCHEMA_CONDITIONAL_FIELD` for the usual reason: flat firmware
        # publishes `connected` on the BESS and on no other device class, so the
        # both-adapters gate cannot be satisfied. schema_1 maps both — from one
        # property, the feeding circuit's `connection/feeds-device-status` —
        # which is what makes these SCHEMA_1_ONLY rather than NEITHER.
        #
        # `battery.connected` is the same fact for the third DER and sits below
        # as SCHEMA_0_ONLY, which is the whole shape of this gap: the link the
        # enclosure reported through the lugs was read, and the one it reports
        # through a circuit was not.
        "pv.connected": Producibility.SCHEMA_1_ONLY,
        "evse.connected": Producibility.SCHEMA_1_ONLY,
        # The EVSE charge-current pair behind the `evse_charge_current_limit`
        # number: the settable limit the entity's value comes from — its
        # description is `SCHEMA_CONDITIONAL_FIELD` and names it — and the
        # commissioned ceiling the entity reads for `native_max_value`. Flat
        # firmware's `evse` device type publishes `advertised-current` and no
        # settable ceiling at all, so neither can ever satisfy the both-adapters
        # gate. schema_1 carries a metadata row for each, resolved from the
        # charger's own `$description` rather than from a table, which is what
        # makes these SCHEMA_1_ONLY rather than NEITHER and buys the entity unit
        # validation against the property the panel actually declares.
        #
        # The ceiling is deliberately not on `SpanEvseNumber._residual_field_paths`:
        # that feeds `declared_field_paths()`, which is the both-adapters gate,
        # and flat produces neither path.
        "evse.charge_current_limit_a": Producibility.SCHEMA_1_ONLY,
        "evse.charge_current_ceiling_a": Producibility.SCHEMA_1_ONLY,
        "circuit.always_on": Producibility.SCHEMA_0_ONLY,
        "circuit.is_sheddable": Producibility.SCHEMA_0_ONLY,
        # The `grid_forming_entity` sensor's source field. schema_1 answers the
        # same question through `resolve_dominant_power_source` over the MID's
        # `grid/grid-forming-entity` instead of publishing a row of its own,
        # which is what makes the sensor `SCHEMA_CONDITIONAL_FIELD`.
        "panel.dominant_power_source": Producibility.SCHEMA_0_ONLY,
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
builds both adapters' metadata from their reference payloads and asserts every
entry's annotation against what those adapters actually produce, so a stale
reason fails the build instead of misleading a reader. A path that becomes
producible by both fails there too, demanding promotion to a declaration.

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

    Every entity that has a single source field, whatever `derived` says about
    which adapters produce it.
    """
    for field_path, _, description in _iter_declared(descriptions):
        yield field_path, description


def iter_field_path_declarations[DescriptionT: EntityDescription](
    descriptions: Iterable[DescriptionT],
) -> Iterator[tuple[str, DescriptionT]]:
    """Yield only the declarations the producible gate covers.

    `derived` descriptions are skipped whether or not they name a field. That
    is the whole content of the exemption: a `SCHEMA_CONDITIONAL_FIELD`
    description does name its source field, and naming it is what gives the
    entity its Repair mention and its unavailability — but the path is still
    one adapter short of the both-adapters gate this function feeds, and
    `RESIDUAL_EXEMPT_PATHS` is where it is enumerated instead.
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

    One copy of the collection list. `declared_field_paths` and
    `conditional_field_paths` ask different questions of the same descriptions,
    and a second copy of the list is how a platform ends up answering one and
    not the other.
    """
    # Deferred: the platform modules import `FieldPathDeclarationMixin` from
    # here, so importing them at module scope would close that loop. (They no
    # longer reach the package root for the config-entry type -- that lives in
    # the leaf `runtime` module -- but this loop is the module's own and stays.)
    from .binary_sensor import (  # pylint: disable=import-outside-toplevel
        BESS_CONNECTED_SENSOR,
        BINARY_SENSORS,
        EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
        PCS_ACTIVE_SENSOR,
    )
    from .number import EVSE_NUMBERS  # pylint: disable=import-outside-toplevel
    from .sensor_definitions import (  # pylint: disable=import-outside-toplevel
        all_sensor_descriptions,
    )

    return (
        *all_sensor_descriptions(),
        *BINARY_SENSORS,
        *EVSE_BINARY_SENSORS,
        *EVSE_NUMBERS,
        GRID_ISLANDABLE_SENSOR,
        BESS_CONNECTED_SENSOR,
        PCS_ACTIVE_SENSOR,
    )


def declared_field_paths() -> frozenset[str]:
    """Field paths the integration reads that must be producible by an adapter.

    Derived entities are excluded: they have no single source field, or the one
    they have is not on both schemas, so there is nothing for both adapters to
    produce. Residual readers that no adapter (or only one) produces are
    excluded too, and are listed in `RESIDUAL_EXEMPT_PATHS`.
    """
    paths: set[str] = set(residual_field_paths())
    paths.update(
        field_path for field_path, _ in iter_field_path_declarations(platform_descriptions())
    )
    return frozenset(paths)


def conditional_field_paths() -> frozenset[str]:
    """Source fields of entities only one adapter produces the field for.

    The producible gate cannot cover these -- that is what
    `DerivedReason.SCHEMA_CONDITIONAL_FIELD` says -- but the adapter that *does*
    produce the field still publishes a metadata row for it, and that row can
    come back `resolved=False` when the panel drops the property. So the
    degradation half of the apparatus applies to them exactly as it does to a
    plain declaration, and `schema_validation.evaluate_field_metadata` asks
    about these paths alongside `declared_field_paths()`.

    Read off the descriptions rather than filtered out of
    `RESIDUAL_EXEMPT_PATHS`: most of that map is decoration -- device_info
    fields, circuit attributes, entity-creation gates -- whose loss does not
    make an entity's *reading* wrong, and which is deliberately excluded from
    the availability probe. What belongs here is the narrower thing the
    description states: the field this entity's value comes from.
    """
    return frozenset(
        field_path
        for field_path, declaration, _ in _iter_declared(platform_descriptions())
        if declaration.derived is DerivedReason.SCHEMA_CONDITIONAL_FIELD
    )
