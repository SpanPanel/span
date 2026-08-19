"""Every declared field path must be producible by each adapter, or derived.

This is the test that would have caught the battery.product_name drift.

A read that no adapter, or only one, produces cannot satisfy that gate, so it is
exempted in `RESIDUAL_EXEMPT_PATHS`. The second half of this module holds those
exemptions to the same standard: each one states which adapters produce it, and
that statement is checked against the adapters rather than left as prose.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable
import pathlib

import pytest
from span_panel_api.models import FieldMetadata

from custom_components.span_panel import field_paths as field_paths_module
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    Producibility,
    declared_field_paths,
)
from tests.adapter_fixtures import schema_one_metadata, schema_zero_metadata

MetadataFn = Callable[[], dict[str, FieldMetadata]]

_ADAPTERS: list[tuple[str, MetadataFn]] = [
    ("schema_0", schema_zero_metadata),
    ("schema_1", schema_one_metadata),
]

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


@pytest.mark.parametrize(("adapter", "metadata_fn"), _ADAPTERS)
def test_every_declared_path_is_producible(adapter: str, metadata_fn: MetadataFn) -> None:
    metadata = metadata_fn()
    missing = sorted(path for path in declared_field_paths() if path not in metadata)
    assert not missing, (
        f"{adapter} does not produce declared field paths: {missing}. "
        "Either the declaration is stale, or the entity should be derived=True."
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
    uncovered = sorted(
        (path, module) for path, module in _source_declared_paths().items() if path not in declared
    )
    assert not uncovered, (
        "declared_field_paths() does not cover field paths declared in the source: "
        f"{uncovered}. A platform collection likely stopped being iterated, or a "
        "description fell out of its collection — the gate is no longer checking "
        "those entities."
    )


@pytest.mark.parametrize(("adapter", "metadata_fn"), _ADAPTERS)
def test_gate_is_one_directional(
    adapter: str, metadata_fn: MetadataFn, capsys: pytest.CaptureFixture[str]
) -> None:
    """A produced path nothing reads must NOT fail the build.

    Additions are legal within a major version, so asserting the converse would
    turn correct upstream behaviour into a red CI every time SPAN ships a
    property. This test exists to stop someone "completing" the gate by adding
    that assertion.
    """
    metadata = metadata_fn()
    unread = sorted(set(metadata) - set(declared_field_paths()))
    # The CI inventory the spec asks for: never asserted against, an addition is
    # legal within a major version. Written with capture suspended so it reaches
    # the build log under the plain `pytest` CI runs -- pytest swallows stdout
    # from a passing test, which would leave the inventory existing in code and
    # nowhere else.
    with capsys.disabled():
        print(f"\n[{adapter}] produced but unread ({len(unread)}):")
        for path in unread:
            print(f"  {path}")


def _adapter_paths() -> tuple[frozenset[str], frozenset[str]]:
    """Return the paths each adapter actually produces, from the vendored fixtures."""
    return frozenset(schema_zero_metadata()), frozenset(schema_one_metadata())


def _observed_producibility(
    path: str, s0: frozenset[str], s1: frozenset[str]
) -> Producibility | None:
    """Classify a path by which adapters produce it.

    `None` is the fourth, illegal case — produced by both — which has no
    `Producibility` member precisely because such a path is not exemptible.
    """
    match (path in s0, path in s1):
        case (True, True):
            return None
        case (True, False):
            return Producibility.SCHEMA_0_ONLY
        case (False, True):
            return Producibility.SCHEMA_1_ONLY
        case _:
            return Producibility.NEITHER


def test_every_exempt_path_matches_its_annotation() -> None:
    """Each exemption's stated reason is checked against the adapters that run.

    Before these annotations the 26 exemptions were checked against nothing:
    the neither/one-adapter distinction lived in prose, so an entry could be
    mislabelled from the day it was written, or go stale when the library
    changed what it publishes, with no signal anywhere.
    """
    s0, s1 = _adapter_paths()
    wrong = [
        (path, annotated.name, observed.name if observed else "BOTH")
        for path, annotated in RESIDUAL_EXEMPT_PATHS.items()
        if (observed := _observed_producibility(path, s0, s1)) is not annotated
    ]
    assert not wrong, (
        "RESIDUAL_EXEMPT_PATHS annotations disagree with the adapters "
        f"(path, annotated, actual): {wrong}. Either the annotation is stale or "
        "the library changed what it produces."
    )


def test_no_exempt_path_is_producible_by_both() -> None:
    """A path both adapters produce is a declaration, not an exemption.

    The exemption exists only because the gate demands producibility by both.
    Once both produce it, the reason has evaporated and the path should be
    declared — silently leaving it exempt would retire the gate for that read.
    """
    s0, s1 = _adapter_paths()
    promotable = sorted(RESIDUAL_EXEMPT_PATHS.keys() & s0 & s1)
    assert not promotable, (
        f"exempt paths are now producible by both adapters: {promotable}. "
        "Promote each to a declaration — a description's `field_path=`, or "
        "`RESIDUAL_FIELD_PATHS` for a reader in entity code — so the producible "
        "gate covers it again."
    )


_EXPECTED_EXEMPT_COUNTS: dict[Producibility, int] = {
    Producibility.NEITHER: 15,
    Producibility.SCHEMA_0_ONLY: 10,
    Producibility.SCHEMA_1_ONLY: 1,
}
"""The exemption inventory, by reason. See `test_exempt_inventory_is_complete`."""


def test_exempt_inventory_is_complete() -> None:
    """An exemption disappearing must be a deliberate edit, not an accident.

    Every other check here is consistency-only: deleting an entry leaves the
    survivors perfectly annotated, so the set could quietly shrink and no test
    would notice — the read would simply stop being enumerated anywhere. The
    completeness of the set cannot be derived (a `Producibility.NEITHER` path
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
    """
    read_attributes = {
        node.attr
        for source in sorted(_PACKAGE_ROOT.rglob("*.py"))
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)))
        if isinstance(node, ast.Attribute)
    }
    unread = sorted(
        path for path in RESIDUAL_EXEMPT_PATHS if path.split(".", 1)[1] not in read_attributes
    )
    assert not unread, (
        f"exempt paths no longer read anywhere in the package: {unread}. The "
        "reader was removed; drop the exemption with it."
    )
