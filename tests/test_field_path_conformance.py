"""Every declared field path must be producible by the parent/child adapter, or derived.

This is the test that would have caught the battery.product_name drift.

A read the adapter maps no metadata row for cannot satisfy that gate, and a few
it does map are kept from validation on purpose, so both are exempted in
`RESIDUAL_EXEMPT_PATHS`. The second half of this module holds those exemptions
to the same standard: each one states whether the adapter produces it, and that
statement is checked against the adapter rather than left as prose.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
import pathlib

import pytest

from custom_components.span_panel import field_paths as field_paths_module
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    Producibility,
    declared_field_paths,
    residual_field_paths,
)
from tests.adapter_fixtures import schema_one_metadata

_PACKAGE_ROOT = pathlib.Path(field_paths_module.__file__).parent


def _source_declared_paths() -> dict[str, str]:
    """Every ``field_path="..."`` literal in the integration source, by module.

    Read from the source text rather than from the platform collections, so the
    hand-written tuple `declared_field_paths` iterates is pinned against what
    the modules actually declare. A runtime scan of module attributes could not
    do this: most descriptions are inline literals inside their collection, so
    emptying the collection would hide them from the check as well as from the
    gate.
    """
    found: dict[str, str] = {}
    for source in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "field_path":
                    continue
                # Non-literal values are pass-throughs that copy an existing
                # description's declaration, not new declarations.
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    found.setdefault(keyword.value.value, source.name)
    return found


def test_every_declared_path_is_producible() -> None:
    metadata = schema_one_metadata()
    missing = sorted(path for path in declared_field_paths() if path not in metadata)
    assert not missing, (
        f"the parent/child adapter does not produce declared field paths: {missing}. "
        "Either the declaration is stale, or the entity should declare a DerivedReason."
    )


def test_gate_covers_every_declaration_in_the_source() -> None:
    """A declaration the gate stops iterating must fail, not shrink silently.

    `test_every_declared_path_is_producible` is monotone: a smaller declared set
    always passes, because any subset of a producible set is producible. So
    dropping a platform collection from `declared_field_paths`, or letting a
    description fall out of its collection, would silently retire the gate for
    every entity involved with no signal at all — the same invisible-omission
    failure this whole module exists to prevent, one level up.

    This is that signal. It compares against the source text, so it holds even
    when a collection is emptied rather than unreferenced.
    """
    declared = declared_field_paths()
    # A `NO_SOURCE_FIELD` description may name its source field too, and the
    # adapter maps no row for that path, so it is outside this gate by
    # definition. Each such path is covered instead by `RESIDUAL_EXEMPT_PATHS`,
    # whose annotation is checked against the adapter below — so being
    # enumerated there is the alternative to being in `declared`, not an escape
    # from being checked.
    uncovered = sorted(
        (path, module)
        for path, module in _source_declared_paths().items()
        if path not in declared and path not in RESIDUAL_EXEMPT_PATHS
    )
    assert not uncovered, (
        "declared_field_paths() does not cover field paths declared in the source: "
        f"{uncovered}. A platform collection likely stopped being iterated, or a "
        "description fell out of its collection — the gate is no longer checking "
        "those entities."
    )


def _iter_source_residuals() -> Iterator[tuple[str, str]]:
    """Yield ``(field_path, module_filename)`` for every residual literal in the source.

    The runtime counterpart, `residual_field_paths()`, unions the class
    attribute over a `SpanPanelEntity.__subclasses__()` walk, and a walk sees
    only what has been imported. This reads the same declarations out of the
    source text, where importedness is not a factor.

    A path declared by two modules yields twice, deliberately: which *paths*
    exist and which *modules* declare one are different questions, and collapsing
    to the first module that mentions a path answers the second wrongly.
    """
    for source in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            targets: list[ast.expr]
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "_residual_field_paths"
                for target in targets
            ):
                continue
            if not isinstance(node.value, ast.Tuple):
                continue
            for element in node.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    yield element.value, source.name


def _source_residual_paths() -> dict[str, str]:
    """Every residual path in the integration source, against a module declaring it."""
    found: dict[str, str] = {}
    for path, module in _iter_source_residuals():
        found.setdefault(path, module)
    return found


def _source_residual_modules() -> set[str]:
    """Every integration module that declares at least one residual path."""
    return {module.removesuffix(".py") for _, module in _iter_source_residuals()}


def test_source_residuals_match_the_subclass_walk() -> None:
    """A residual the walk cannot see must fail, not vanish.

    `residual_field_paths()` imports the platform modules that declare
    residuals so their classes exist to be walked. A class declaring one in a
    module it does not import — a new platform, or an existing one that stops
    being imported — would drop out of the producible gate and out of the
    Repair's affected-entity count with no signal at all: the derived set would
    simply be smaller, and every check downstream of it is monotone in exactly
    the direction that hides the loss.

    This is that signal, read from the source text where import order does not
    apply. The converse direction matters too: a residual assembled at runtime
    rather than written as a tuple literal is invisible to this scan, so the
    scan would silently stop pinning it.
    """
    from_source = _source_residual_paths()
    from_walk = residual_field_paths()

    unwalked = sorted(
        (path, module) for path, module in from_source.items() if path not in from_walk
    )
    assert not unwalked, (
        f"residual paths declared in the source but not reached by the subclass walk: "
        f"{unwalked}. `residual_field_paths()` does not import the declaring module, so "
        "the producible gate and the Repair's affected-entity count both miss these reads."
    )

    unscanned = sorted(from_walk - from_source.keys())
    assert not unscanned, (
        f"residual paths the source scan cannot see: {unscanned}. They are not written "
        "as string literals in a `_residual_field_paths` tuple, so this pin no longer "
        "covers them — declare them literally."
    )


def _walk_imported_modules() -> set[str]:
    """Return the sibling modules `residual_field_paths()` imports for its walk.

    Read out of the source rather than by calling the function, because what is
    under test is the import list itself: a module missing from it still gets
    walked in-process whenever some *other* importer has already pulled it in,
    which is true of every module in the test suite and is exactly why the walk
    test below cannot see the omission.
    """
    tree = ast.parse((_PACKAGE_ROOT / "field_paths.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "residual_field_paths"
    )
    return {
        alias.name
        for node in ast.walk(function)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None
        for alias in node.names
    }


def test_the_residual_walk_imports_exactly_the_modules_that_declare_one() -> None:
    """The walk's import list, pinned against the declarations it exists to reach.

    `residual_field_paths()` walks `SpanPanelEntity.__subclasses__()`, which sees
    only classes Python has already imported, so it imports the declaring
    platform modules itself. Nothing held that list to the declarations: a module
    dropped from it goes on being walked under pytest, where the platform modules
    are imported many times over for other reasons, and fails only in production
    where `field_paths` may be reached first. A path that leaves the walk leaves
    the producible gate and the Repair's affected-entity count with it, silently.

    Both directions. An unlisted module is the failure above; a listed module
    that declares nothing is a stale import, which is how the list stops meaning
    what its docstring says and starts being copied forward unread.
    """
    declaring = _source_residual_modules()
    imported = _walk_imported_modules()

    assert declaring, "the source scan found no residual declarations at all"
    assert imported == declaring, (
        f"`residual_field_paths()` imports {sorted(imported)} for its subclass walk but "
        f"residuals are declared in {sorted(declaring)}. Unlisted modules drop out of the "
        "producible gate in any process that reaches `field_paths` first; listed modules "
        "that declare nothing are stale."
    )


def test_gate_is_one_directional(capsys: pytest.CaptureFixture[str]) -> None:
    """A produced path nothing reads must NOT fail the build.

    Additions are legal within a major version, so asserting the converse would
    turn correct upstream behaviour into a red CI every time SPAN ships a
    property. This test exists to stop someone "completing" the gate by adding
    that assertion.
    """
    metadata = schema_one_metadata()
    unread = sorted(set(metadata) - set(declared_field_paths()))
    # The CI inventory the spec asks for: never asserted against, an addition is
    # legal within a major version. Written with capture suspended so it reaches
    # the build log under the plain `pytest` CI runs -- pytest swallows stdout
    # from a passing test, which would leave the inventory existing in code and
    # nowhere else.
    with capsys.disabled():
        print(f"\n[parent/child] produced but unread ({len(unread)}):")
        for path in unread:
            print(f"  {path}")


def _observed_producibility(path: str, produced: frozenset[str]) -> Producibility:
    """Classify a path by whether the adapter's reference metadata carries a row for it."""
    return Producibility.MAPPED_UNVALIDATED if path in produced else Producibility.UNMAPPED


def test_every_exempt_path_matches_its_annotation() -> None:
    """Each exemption's stated reason is checked against the adapter that runs.

    Before these annotations the exemptions were checked against nothing: the
    distinction lived in prose, so an entry could be mislabelled from the day it
    was written, or go stale when the library changed what it publishes, with
    no signal anywhere.

    An `UNMAPPED` path the library grows a row for fails here. That is the
    moment to promote it to a declaration -- a description's `field_path=`, or
    an entity's `_residual_field_paths` for a reader in entity code -- so the
    producible gate covers it, and only to re-annotate it `MAPPED_UNVALIDATED`
    with a reason written beside it if validation must not act on it.
    """
    produced = frozenset(schema_one_metadata())
    wrong = [
        (path, annotated.name, observed.name)
        for path, annotated in RESIDUAL_EXEMPT_PATHS.items()
        if (observed := _observed_producibility(path, produced)) is not annotated
    ]
    assert not wrong, (
        "RESIDUAL_EXEMPT_PATHS annotations disagree with the adapter "
        f"(path, annotated, actual): {wrong}. Either the annotation is stale, the "
        "library changed what it produces, or the path now has a row and should be "
        "promoted to a declaration."
    )


_EXPECTED_EXEMPT_COUNTS: dict[Producibility, int] = {
    # The 62, by group:
    #   2  `$target` echoes: `circuit.relay_state_target`, `circuit.priority_target`
    #   2  assembled from topology: `circuit.device_type`, `circuit.relative_position`
    #   1  `panel.panel_size`, outside the typed field surface
    #   3  the enclosure's device card: `panel.vendor_name`, `.model`, `.hardware_version`
    #   4  the shed policy and the three values parsed out of it
    #   2  identity keys: `panel.serial_number`, `circuit.circuit_id`
    #   1  `panel.dsm_state`
    #   6  the MID's device card and grid-forming name
    #   1  `mid.grid_state`
    #   1  `pv.software_version`, on the solar card
    #   1  `evse.node_id`, an addressing handle
    #   2  the charge-limit command facts: `$settable` and the `$target` echo
    #   3  the shed-forecast refinements
    #  13  the PCS arbitration inputs and `pcs.enabled`
    #   2  a circuit's PCS participation
    #   2  a circuit's meter and relay peers
    #   2  a circuit's nominal voltage and protection functions
    #   3  the panel readings created only where declared
    #   1  `circuit.feeds_role`
    #   2  a circuit's `always_on` and `is_sheddable`
    #   2  answers the library derives: `panel.dominant_power_source`, `panel.grid_islandable`
    #   5  the charger's device card and the circuit that feeds it
    #   1  `battery.connected`, the panel-side link record
    Producibility.UNMAPPED: 62,
    # The two reads the adapter maps and validation is kept from, each with its
    # reason beside it in `RESIDUAL_EXEMPT_PATHS`: the controllability gate and
    # the charge-limit ceiling.
    Producibility.MAPPED_UNVALIDATED: 2,
}
"""The exemption inventory, by reason. See `test_exempt_inventory_is_complete`."""


def test_exempt_inventory_is_complete() -> None:
    """An exemption disappearing must be a deliberate edit, not an accident.

    Every other check here is consistency-only: deleting an entry leaves the
    survivors perfectly annotated, so the set could quietly shrink and no test
    would notice — the read would simply stop being enumerated anywhere. The
    completeness of the set cannot be derived (a `Producibility.UNMAPPED` path
    has, by definition, no adapter row to discover it from, and matching reads
    by leaf attribute name collides across snapshot types), so it is pinned by
    size per reason instead. Changing a count is fine; doing it in the same
    commit as the entry, with a reason, is the point.
    """
    counts = Counter(RESIDUAL_EXEMPT_PATHS.values())
    assert counts == _EXPECTED_EXEMPT_COUNTS, (
        f"exemption inventory changed: {counts} != {_EXPECTED_EXEMPT_COUNTS}. An "
        "entry was added or removed; update the expected counts in the same "
        "commit if that was intended."
    )


def test_every_exempt_path_still_has_a_reader() -> None:
    """An exemption outlives its reader silently; the list only ever grows.

    Heuristic by necessity — an exempt path is read outside any description, so
    there is no declaration to match against, only source text. Matching the
    leaf attribute anywhere in the package is deliberately generous: it cannot
    accuse a live read, and it still catches the last reader of a field being
    deleted while its exemption stays behind.

    A ``field_path="..."`` literal counts as a reader too: the entity registers
    that path against its metadata row even when its value comes through a
    library property built on the field rather than the attribute itself.
    """
    declared = _source_declared_paths()
    read_attributes = {
        node.attr
        for source in sorted(_PACKAGE_ROOT.rglob("*.py"))
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)))
        if isinstance(node, ast.Attribute)
    }
    unread = sorted(
        path
        for path in RESIDUAL_EXEMPT_PATHS
        if path.split(".", 1)[1] not in read_attributes and path not in declared
    )
    assert not unread, (
        f"exempt paths no longer read anywhere in the package: {unread}. The "
        "reader was removed; drop the exemption with it."
    )
