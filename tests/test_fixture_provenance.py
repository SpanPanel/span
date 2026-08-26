"""The vendored capture must name the release it came from, and be right about it.

`tests/fixtures/schema_one_tree.json` is a byte-identical copy of a payload that
lives in the `span-panel-api` repository. Committing it is what keeps this suite
free of a cross-repo dependency, and free of the expectation that a runtime wheel
keeps shipping test data. The price of a copy is that it can go stale, and a
stale capture is the worst kind of green: the conformance tests keep passing,
against a wire no panel sends.

Detecting that does not need the file, only the version claim. `schema_one_tree.source`
records the release the copy was taken from, and `importlib.metadata` reports the
release actually installed -- with no checkout, no network call and no second copy
to compare against. The moment the pin moves and the capture is not refreshed,
this test fails and says what to do about it.

This is one test rather than a check inside `adapter_fixtures.schema_one_tree()`
deliberately. Stale provenance is a maintenance fact, not a broken payload: the
capture still parses and still drives every adapter. A loader-level assertion
would turn one actionable failure into several hundred identical ones, burying
whatever else the run had to say -- and it would make the refresh itself
undebuggable, since the bump and the copy cannot land in the same keystroke.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json

import pytest

from .adapter_fixtures import SCHEMA_ONE_TREE, SCHEMA_ONE_TREE_SOURCE, schema_one_source


def _refresh(distribution: str, installed: str) -> str:
    """Say what to do about a stale copy, to someone who has never refreshed one."""
    return (
        f"Copy `parent_child_tree.json` from {distribution} at {installed} over "
        f"{SCHEMA_ONE_TREE.name} -- byte for byte, no reformatting -- and set "
        f"{SCHEMA_ONE_TREE_SOURCE.name} to '{distribution}=={installed}'. "
        f"tests/fixtures/README.md has the source path."
    )


def test_the_vendored_capture_records_where_it_came_from() -> None:
    """A copy with no provenance cannot be checked, which is how it goes stale."""
    source = schema_one_source()

    assert source.distribution == "span-panel-api-schema-1"
    assert source.version, "the recorded release must not be empty"


def test_the_vendored_capture_matches_the_installed_adapter() -> None:
    """The pin moved and the copy did not: the failure this whole arrangement exists for."""
    source = schema_one_source()

    try:
        installed = version(source.distribution)
    except PackageNotFoundError:
        pytest.fail(
            f"{SCHEMA_ONE_TREE_SOURCE.name} records {source.distribution!r}, which is not "
            f"installed. The vendored capture cannot be checked against a distribution "
            f"this environment does not have; correct the name or install it."
        )

    assert installed == source.version, (
        f"The vendored capture {SCHEMA_ONE_TREE.name} was copied from "
        f"{source.distribution} {source.version}, but {installed} is installed. "
        f"The adapter is not broken -- the copy is out of date. "
        f"{_refresh(source.distribution, installed)}"
    )


def test_the_vendored_capture_is_the_shape_the_loader_expects() -> None:
    """A dict keyed by device id, whose `$description` is a JSON string, not an object.

    Pinned here because a refresh is a manual copy, and the one thing a copy can
    get wrong that the version claim cannot see is a change of shape. The loader
    reads `$description` with `update_description`, which parses; a capture that
    had been helpfully pre-parsed would fail far from here.
    """
    capture: dict[str, dict[str, str]] = json.loads(SCHEMA_ONE_TREE.read_text())

    assert capture, "the capture is empty"
    for device_id, topics in capture.items():
        description = topics["$description"]
        assert isinstance(description, str), f"{device_id}: $description must be a JSON string"
        assert isinstance(json.loads(description), dict), f"{device_id}: $description must parse"
