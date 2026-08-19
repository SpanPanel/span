"""Every declared field path must be producible by each adapter, or derived.

This is the test that would have caught the battery.product_name drift.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
import pathlib

import pytest
from span_panel_api.models import FieldMetadata

from custom_components.span_panel import field_paths as field_paths_module
from custom_components.span_panel.field_paths import declared_field_paths
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
