"""Every entity that reads one snapshot field must say which field."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import dataclasses
import importlib
import pkgutil
from typing import Final

from homeassistant.helpers.entity import EntityDescription

import custom_components.span_panel as span_panel
from custom_components.span_panel.button import GFE_OVERRIDE_DESCRIPTION
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    FieldPathDeclarationMixin,
    Producibility,
    declared_field_paths,
    platform_descriptions,
    residual_field_paths,
)
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS


def test_circuit_power_declares_its_field_path() -> None:
    power = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")
    assert power.field_path == "circuit.instant_power_w"
    assert power.derived is None


def test_derived_sensor_declares_no_path() -> None:
    """dsm_state is a multi-signal derivation with no single source field."""
    from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

    dsm = next(d for d in PANEL_DATA_STATUS_SENSORS if d.key == "dsm_state")
    assert dsm.derived is DerivedReason.NO_SOURCE_FIELD
    assert dsm.field_path is None


def test_declared_field_paths_includes_residuals() -> None:
    """Readers that live in entity code rather than on a description still count."""
    paths = declared_field_paths()
    assert "circuit.relay_state" in paths
    assert "circuit.priority" in paths


def test_every_description_declares_exactly_one() -> None:
    """A description inheriting the mixin but setting neither field is invisible.

    The `TypeError` guard in `declared_field_paths` only catches a description
    that lacks the mixin entirely. Every new sensor inherits it automatically,
    so the likelier mistake is inheriting it and declaring nothing — which
    drops the entity from every gate with no signal. This is that signal.

    "Exactly one" is per `DerivedReason`, because the reasons differ on whether
    a single source field exists at all. `MULTIPLE_FIELDS` has none to name.
    `NO_SOURCE_FIELD` may name the one field it reads that the adapter maps no
    row for, and `test_a_no_source_field_description_names_only_an_unmapped_field`
    holds that name to the adapter. A description with no reason must name its
    field.
    """
    for description in platform_descriptions():
        assert isinstance(description, FieldPathDeclarationMixin), description
        if description.derived is DerivedReason.NO_SOURCE_FIELD:
            continue
        names_field = description.field_path is not None
        assert names_field != (description.derived is not None), (
            f"{description.key} must declare exactly one of field_path / a DerivedReason"
        )


def test_a_no_source_field_description_names_only_an_unmapped_field() -> None:
    """A field a `NO_SOURCE_FIELD` description names must be enumerated, as unmapped.

    Its path cannot enter `declared_field_paths()` — the description is derived
    — so `RESIDUAL_EXEMPT_PATHS` is the only place it is written down and the
    only place it is checked against the adapter. Absent from there, it would be
    read by an entity, gated by nothing, and reported by `evaluate_field_metadata`
    as produced-but-unread; `panel.dominant_power_source` was once exactly that.

    And it must be `UNMAPPED`. A path the adapter maps is a reading the entity
    could be validated on, so a description reading one is a plain declaration,
    not one with no source field.
    """
    named = 0
    for description in platform_descriptions():
        assert isinstance(description, FieldPathDeclarationMixin), description
        if description.derived is not DerivedReason.NO_SOURCE_FIELD:
            continue
        path = description.field_path
        if path is None:
            continue
        named += 1
        assert RESIDUAL_EXEMPT_PATHS.get(path) is Producibility.UNMAPPED, (
            f"{description.key} is NO_SOURCE_FIELD and names {path!r}, which "
            "RESIDUAL_EXEMPT_PATHS must enumerate as UNMAPPED; a mapped path makes "
            "it a plain declaration"
        )
    assert named, "no NO_SOURCE_FIELD description names a field, so this checks nothing"


def test_residual_buckets_are_disjoint() -> None:
    """A residual path is either producible or exempt, never both."""
    assert not (residual_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
    assert not (declared_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())


def _description_types() -> tuple[type, ...]:
    """Return the runtime classes an entity description can be an instance of.

    Two, because of how Home Assistant builds them. A description class this
    integration declares is a real dataclass subclassing `EntityDescription`. A
    class Core declares, such as `ButtonEntityDescription`, is built by its
    `FrozenOrThawed` metaclass, which constructs each direct instance from a
    parallel frozen dataclass hierarchy kept on the class as `_dataclass`. So
    `isinstance(GFE_OVERRIDE_DESCRIPTION, EntityDescription)` is false, and a
    walk testing that alone would never see a plain Core description.

    Read with a default, so a Core release that retires the shim leaves one
    class, which then answers for both.
    """
    thawed: object = getattr(EntityDescription, "_dataclass", None)
    return (EntityDescription, thawed) if isinstance(thawed, type) else (EntityDescription,)


def _descriptions_in(
    value: object, where: str, kinds: tuple[type, ...], seen: set[int]
) -> Iterator[tuple[str, object]]:
    """Yield ``(where, description)`` for every description reachable from `value`.

    Followed through tuples, lists, sets, mapping values and dataclass fields,
    which is everywhere this integration keeps a description:
    `DECLARED_PANEL_READINGS` holds its descriptions in a dataclass of its own.
    Nothing else is followed. A module is walked in its own right, no class here
    keeps a description as an attribute, and a description a function builds
    exists only while it runs, which the test below accounts for.

    `seen` is by identity, so a collection one module imports from another is
    walked once, under the name it was first found by.
    """
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, kinds):
        yield where, value
        return
    children: list[tuple[str, object]]
    if isinstance(value, Mapping):
        children = [(f"{where}[{key!r}]", child) for key, child in value.items()]
    elif isinstance(value, tuple | list | set | frozenset):
        children = [(f"{where}[{index}]", child) for index, child in enumerate(value)]
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        children = [
            (f"{where}.{field.name}", getattr(value, field.name))
            for field in dataclasses.fields(value)
        ]
    else:
        return
    for child_where, child in children:
        yield from _descriptions_in(child, child_where, kinds, seen)


def _module_level_descriptions() -> dict[int, tuple[str, object]]:
    """Every description reachable from a module-level name in the integration, by identity.

    Every module in the package, imported here rather than trusted to be
    imported already: a module no test happens to import would otherwise hide
    the descriptions it defines.
    """
    kinds = _description_types()
    seen: set[int] = set()
    found: dict[int, tuple[str, object]] = {}
    modules = [
        span_panel,
        *(
            importlib.import_module(module.name)
            for module in pkgutil.walk_packages(span_panel.__path__, f"{span_panel.__name__}.")
        ),
    ]
    for module in modules:
        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            for where, description in _descriptions_in(
                value, f"{module.__name__}.{name}", kinds, seen
            ):
                found.setdefault(id(description), (where, description))
    return found


_NOT_COLLECTED: Final[tuple[tuple[EntityDescription, str], ...]] = (
    (
        GFE_OVERRIDE_DESCRIPTION,
        "A plain `ButtonEntityDescription`. A button presents no reading, so it "
        "has no source field to declare, and `platform_descriptions()` holds only "
        "descriptions that carry the declaration. What the button reads decides "
        "its availability: `battery.communication_state`, declared by the "
        "battery's communication-state sensor, and `battery.connected` and "
        "`panel.dsm_state`, enumerated in `RESIDUAL_EXEMPT_PATHS`.",
    ),
)
"""What `platform_descriptions()` deliberately leaves out, each with why it is not validated."""


def test_every_description_the_integration_defines_is_collected() -> None:
    """A description defined outside `platform_descriptions()` fails the build.

    That list is kept by hand, and it is all that validation walks: a
    description it misses names a field that `evaluate_field_metadata` never
    asks about, so an unresolved row raises no Repair and leaves the entity
    showing a default as a reading. The three DER-link descriptions were once
    left out of it this way, while the introspection suite checked them against
    their readers, so nothing failed.

    Enumerated from the modules rather than from the list, which is the only
    way an omission can show: every module-level name in the package, followed
    through the collections that hold descriptions. A description built at run
    time is outside that sight, and none of those built today carries a
    declaration to miss: curation's, for the adopted and extension entities,
    which read what the panel declares outside the curated field-path
    vocabulary; the circuit select's, built by its wrapper, whose reads are
    declared on the entity itself; and the per-circuit copies `sensor_circuit`
    makes with `replace`, each carrying the declaration of a description
    collected here.
    """
    found = _module_level_descriptions()
    collected = {id(description) for description in platform_descriptions()}
    excused = {id(description) for description, _ in _NOT_COLLECTED}

    uncollected = sorted(
        f"{where} (key {getattr(description, 'key', None)!r})"
        for identity, (where, description) in found.items()
        if identity not in collected and identity not in excused
    )
    assert not uncollected, (
        "entity descriptions the integration defines that platform_descriptions() "
        f"does not collect, so validation never covers them: {uncollected}. Collect "
        "each one, or excuse it in _NOT_COLLECTED with the reason it is not validated."
    )

    # The excuses must each still be needed, and seen. Seeing the button's is
    # also what proves the walk reaches a plain Core description at all.
    assert excused <= found.keys(), "an excused description is no longer reached by the walk"
    assert not excused & collected, "an excused description is collected; drop its excuse"
    # And the walk must reach everything collected, or it would be checking a
    # smaller world than the one validation walks.
    assert collected <= found.keys(), "the walk misses descriptions platform_descriptions() holds"
