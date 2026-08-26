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

That is half the problem. A version claim catches a copy that was never
refreshed; it cannot catch one that was refreshed *wrongly* -- reformatted,
hand-edited, or taken from a working tree ahead of the release it names -- because
those leave the claim intact and change only the bytes. So the second guard here
compares the bytes, against the capture in a `span-panel-api` checkout named by
`SPAN_PANEL_API_DIR`. It needs something the version guard deliberately does not,
and therefore skips when no checkout is configured -- but **fails** rather than
skips under `CI`, where the workflow clones one. The library states the reason
plainly, having paid for it: "A skip reads in a summary line exactly like a pass,
and that is how a stale vendored capture went unnoticed for nine days"
(`span-panel-api`, DEVELOPMENT.md, "A skip here is not a pass"). Anyone tempted to
make the skip unconditional should read that first.

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
import os
from pathlib import Path
import re
from typing import NoReturn

import pytest

from .adapter_fixtures import SCHEMA_ONE_TREE, SCHEMA_ONE_TREE_SOURCE, schema_one_source

CHECKOUT_VARIABLE = "SPAN_PANEL_API_DIR"
"""Names a `span-panel-api` checkout. Already defined in `.env.example` for editable installs."""

_REPO_ROOT = Path(__file__).resolve().parent.parent

_SOURCE_PATHS = (
    Path("tests") / "reference_payloads" / "parent_child_tree.json",
    Path("packages")
    / "schema-1"
    / "src"
    / "span_panel_api_schema_1"
    / "reference_payloads"
    / "parent_child_tree.json",
)
"""Where the capture lives in `span-panel-api`, newest location first.

Two of them because the file is moving. `span-panel-api#162` takes the reference
payloads out of the schema_1 wheel and makes them ordinary test fixtures, which
is the whole reason this repository vendors a copy rather than importing one --
but that change is unmerged, so a checkout on `main` today still holds the file
under `packages/schema-1/`. Checking the new path first means the comparison
follows the file rather than the merge, and works against a checkout on either
side of it.

Delete the second entry once #162 is merged **and** no release this repository
can pin still predates it -- the CI clone is positioned at the release
`schema_one_tree.source` records, so the old path stays reachable for as long as
that pin can name a pre-#162 release.
"""

_RELEASE_DECLARATION = Path("packages") / "schema-1" / "pyproject.toml"
"""Where the checkout declares which release it is. Read only to explain a failure."""

_VERSION = re.compile(r'^version = "([^"]+)"', re.MULTILINE)


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


def _unconfigured(reason: str) -> NoReturn:
    """Not configured: skip on a developer machine, fail in CI.

    Locally, skipping is right. Not every developer keeps a `span-panel-api`
    checkout beside this one, and a provenance check is not what they are running
    the suite for. Failing on them is what makes a developer delete the check
    rather than configure it.

    In CI it is the opposite. The workflow clones the library at the release
    `schema_one_tree.source` names and exports `SPAN_PANEL_API_DIR`, so an unset
    or unusable path there does not mean "unavailable", it means the wiring that
    makes this check run has come undone. Skipping on that reads in the summary
    line exactly like passing -- which is how the equivalent check in
    `span-panel-api` stayed silent for the nine days it took a vendored capture to
    go stale (DEVELOPMENT.md, "A skip here is not a pass"). A check an environment
    can switch off by omitting a variable is a check nobody can rely on.

    `CI` rather than a variable of our own, because it is what GitHub Actions and
    every other runner already set: an environment that stops supplying a path has
    to opt *out* of being an environment, which is not something a workflow edit
    does by accident.
    """
    if os.environ.get("CI"):
        pytest.fail(
            f"{reason}. CI clones span-panel-api and exports {CHECKOUT_VARIABLE}, so this "
            f"is the provenance wiring being broken rather than a check that is "
            f"unavailable -- and a skip here is indistinguishable from a pass."
        )
    pytest.skip(reason)


def _checkout() -> Path:
    """Return the `span-panel-api` checkout named by the environment, or take the unconfigured exit.

    A variable that is unset, one pointing at a directory that is gone, and one
    pointing at a directory that is not a checkout of this library are all the
    same situation -- the source is not available to compare against -- and all
    take the same exit. Letting a bad path through instead produces a
    `FileNotFoundError` from inside a comparison, which reads as a broken test
    rather than an unconfigured one.

    The third state is not hypothetical: `SPAN_PANEL_API_DIR` predates this check
    and exists to point `pip install -e` at a library checkout, so it is exactly
    the kind of variable someone has already set to something almost right.
    `packages/schema-1/pyproject.toml` is what distinguishes a real checkout, and
    it is also where a failure reads the release the checkout is on.

    A relative value is resolved against the repository root rather than the
    working directory, because that is what `.env.example`'s `../span-panel-api`
    means -- and pytest can be run from anywhere.
    """
    configured = os.environ.get(CHECKOUT_VARIABLE)
    if not configured:
        _unconfigured(
            f"set {CHECKOUT_VARIABLE} to a span-panel-api checkout to compare the "
            f"vendored capture against its source"
        )
    path = Path(configured)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    if not path.is_dir():
        _unconfigured(f"{CHECKOUT_VARIABLE}={configured} does not exist; point it at a checkout")
    if not (path / _RELEASE_DECLARATION).is_file():
        _unconfigured(
            f"{CHECKOUT_VARIABLE}={configured} has no {_RELEASE_DECLARATION}, so it is not a "
            f"span-panel-api checkout (or not a complete one); re-clone it"
        )
    return path


def _checkout_release(checkout: Path) -> str:
    """Read the release a checkout declares, from the package it would build."""
    declared = _VERSION.search((checkout / _RELEASE_DECLARATION).read_text(encoding="utf-8"))
    return declared.group(1) if declared else "an undeclared release"


def _source_capture(checkout: Path, recorded: str) -> Path:
    """Return the capture inside a checkout on the release that `recorded` names.

    The position check is not decoration, and this is not a hypothetical: written
    without it, this comparison passed against a checkout sitting on the previous
    release, whose bytes happened to be the ones vendored here. A byte comparison
    that will accept whichever revision it is handed proves only that the copy
    matches *something* -- and "something" includes the working tree the copy was
    mistakenly taken from, which is the exact defect it is here to find.

    Being on the wrong release takes the same exit as having no checkout at all,
    rather than failing outright, because it is the same situation: the release
    this capture claims to come from is not available to compare against. Locally
    that is ordinary -- `SPAN_PANEL_API_DIR` predates this check and points at
    whatever a developer is working on -- and failing them for it is what makes a
    developer delete the check rather than move a checkout. In CI it cannot happen
    innocently: the workflow clones at the tag `schema_one_tree.source` names, so a
    checkout on any other release means the derivation has come undone, and
    `_unconfigured` fails there for that reason.
    """
    declared = _checkout_release(checkout)
    if declared != recorded:
        _unconfigured(
            f"{CHECKOUT_VARIABLE} names a checkout on span-panel-api-schema-1 {declared}, and "
            f"{SCHEMA_ONE_TREE_SOURCE.name} records the capture as a copy of {recorded}. Move it "
            f"to the schema-1-v{recorded} tag; comparing against another release reports drift "
            f"rather than corruption"
        )

    for relative in _SOURCE_PATHS:
        source = checkout / relative
        if source.is_file():
            return source
    pytest.fail(
        f"{checkout} is span-panel-api-schema-1 {declared} as recorded, but holds the capture at "
        f"neither {_SOURCE_PATHS[0]} nor {_SOURCE_PATHS[1]}. Either the checkout is incomplete, "
        f"or that release moved the file again and this list needs the new path."
    )


def test_the_vendored_capture_is_byte_identical_to_its_source() -> None:
    """The bytes, against the release the copy says it came from.

    The check the version guard cannot make. `schema_one_tree.source` agreeing
    with the installed distribution proves only that somebody bumped a string; it
    says nothing about whether the accompanying copy was faithful, and a capture
    that was reformatted on the way in, or taken from a working tree ahead of the
    release it names, satisfies the version guard completely.

    Byte for byte, with no reformatting tolerance, because the two repositories
    have to be able to compare these files trivially and because the library pins
    what this capture leaves unvalued against its producer's own baseline. A copy
    that has been re-serialised -- reindented, reordered, floats rounded -- is one
    nothing upstream can hold to that baseline any more, and it puts these tests
    on a wire no panel sends.
    """
    recorded = schema_one_source()
    source = _source_capture(_checkout(), recorded.version)

    assert source.read_bytes() == SCHEMA_ONE_TREE.read_bytes(), (
        f"{SCHEMA_ONE_TREE.name} is not a faithful copy of {source}, which is "
        f"{recorded.distribution} {recorded.version} -- the release "
        f"{SCHEMA_ONE_TREE_SOURCE.name} records it as a copy of. The version claim is right and "
        f"the bytes are not, so the copy was edited, reformatted, or taken from a working tree "
        f"rather than from that release. {_refresh(recorded.distribution, recorded.version)}"
    )


def _stub_checkout(root: Path, release: str) -> Path:
    """Build the smallest thing `_checkout` accepts: a tree declaring a schema-1 release."""
    declaration = root / _RELEASE_DECLARATION
    declaration.parent.mkdir(parents=True, exist_ok=True)
    declaration.write_text(
        f'[project]\nname = "span-panel-api-schema-1"\nversion = "{release}"\n', encoding="utf-8"
    )
    return root


def test_a_checkout_on_another_release_fails_in_ci_and_skips_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A present checkout on the wrong release is unavailable, not acceptable.

    Written as its own test because it is the state that actually occurred: a
    real, complete, perfectly healthy checkout, sitting one release behind the
    one `schema_one_tree.source` names, holding bytes that matched. Without the
    position check the comparison passed and reported provenance it had not
    verified -- a green that is worse than a skip, because a skip at least says
    it did not look.
    """
    recorded = schema_one_source()
    elsewhere = "0.0.0-not-the-recorded-release"
    assert elsewhere != recorded.version, "the stub must not accidentally be the recorded release"
    checkout = _stub_checkout(tmp_path / "span-panel-api", elsewhere)

    monkeypatch.delenv("CI", raising=False)
    with pytest.raises((pytest.fail.Exception, pytest.skip.Exception), match=elsewhere) as local:
        _source_capture(checkout, recorded.version)
    assert local.type is pytest.skip.Exception, (
        f"off CI a checkout on another release must skip, got {local.typename}. A developer's "
        f"checkout is wherever their work is, and failing them for it is what gets the check "
        f"deleted"
    )

    monkeypatch.setenv("CI", "true")
    with pytest.raises((pytest.fail.Exception, pytest.skip.Exception)) as in_ci:
        _source_capture(checkout, recorded.version)
    assert in_ci.type is pytest.fail.Exception, (
        f"under CI a checkout on another release must fail rather than {in_ci.typename.lower()}: "
        f"the workflow clones at the tag the recorded release names, so this can only mean the "
        f"derivation has come undone"
    )


def test_an_unavailable_checkout_fails_in_ci_and_skips_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard on the guard.

    The byte comparison is worth exactly as much as the thing that decides whether
    it runs, and that thing is one `if`. In `span-panel-api` it went wrong in the
    quiet direction -- the variable named a directory that did not exist, every
    provenance check skipped, and nine days of drift accumulated behind a summary
    line that read like a pass.

    So both halves are asserted, in both environments, for all three of the states
    `_checkout` distinguishes. Asserting only the CI half would leave the local
    half free to become a failure, which is the change that makes a developer
    without a sibling checkout delete the check instead of configuring it.

    `_checkout` is exercised through its public behaviour -- the exception it
    raises -- rather than by inspecting `_unconfigured`, so this keeps holding if
    the branch moves into the caller.
    """
    outcomes = (pytest.fail.Exception, pytest.skip.Exception)
    missing = "/nonexistent/span-panel-api"

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(CHECKOUT_VARIABLE, missing)
    with pytest.raises(outcomes, match="does not exist") as local:
        _checkout()
    assert local.type is pytest.skip.Exception, (
        f"off CI an unavailable checkout must skip, got {local.typename}. Failing instead is "
        f"what makes a developer without a span-panel-api checkout delete the check rather "
        f"than configure it"
    )

    monkeypatch.setenv("CI", "true")
    for value, why in (
        ("", "unset"),
        (missing, "a path that is gone"),
        (str(_REPO_ROOT), "a directory that is not a span-panel-api checkout"),
    ):
        monkeypatch.setenv(CHECKOUT_VARIABLE, value)
        with pytest.raises(outcomes) as raised:
            _checkout()
        assert raised.type is pytest.fail.Exception, (
            f"under CI, {why} must fail rather than {raised.typename.lower()}: a provenance "
            f"check that skips is one an environment can switch off, and the summary line "
            f"cannot tell the difference between that and a pass"
        )
