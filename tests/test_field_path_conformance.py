"""Every declared field path must be producible by each adapter, or derived.

This is the test that would have caught the battery.product_name drift.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from span_panel_api.models import FieldMetadata

from custom_components.span_panel.field_paths import declared_field_paths
from tests.adapter_fixtures import schema_one_metadata, schema_zero_metadata

MetadataFn = Callable[[], dict[str, FieldMetadata]]

_ADAPTERS: list[tuple[str, MetadataFn]] = [
    ("schema_0", schema_zero_metadata),
    ("schema_1", schema_one_metadata),
]


@pytest.mark.parametrize(("adapter", "metadata_fn"), _ADAPTERS)
def test_every_declared_path_is_producible(adapter: str, metadata_fn: MetadataFn) -> None:
    metadata = metadata_fn()
    missing = sorted(path for path in declared_field_paths() if path not in metadata)
    assert not missing, (
        f"{adapter} does not produce declared field paths: {missing}. "
        "Either the declaration is stale, or the entity should be derived=True."
    )


@pytest.mark.parametrize(("adapter", "metadata_fn"), _ADAPTERS)
def test_gate_is_one_directional(adapter: str, metadata_fn: MetadataFn) -> None:
    """A produced path nothing reads must NOT fail the build.

    Additions are legal within a major version, so asserting the converse would
    turn correct upstream behaviour into a red CI every time SPAN ships a
    property. This test exists to stop someone "completing" the gate by adding
    that assertion.
    """
    metadata = metadata_fn()
    unread = sorted(set(metadata) - set(declared_field_paths()))
    # The CI inventory the spec asks for: printed so the build log carries it,
    # never asserted against. An addition is legal within a major version.
    print(f"\n[{adapter}] produced but unread ({len(unread)}):")
    for path in unread:
        print(f"  {path}")
