"""The `span_panel_api` under test must be the one the pins name.

Every other guard in this suite reads a version. `manifest.json` pins one,
`scripts/sync-dependencies.py` holds the copies to it, and the reference payloads
the conformance tests replay are whatever the pinned wheels ship. All of that
assumes the version string identifies the code, and it does not: a checkout
sitting on unreleased work
declares the same version as the release it is ahead of, and a scratch worktree
declares the same version as the checkout it was branched from. A version is a
claim; a filesystem location is a fact.

That gap is not hypothetical here. `3cbf02a` pointed `[tool.uv.sources]` at a
scratch worktree and committed it, so every import in the suite resolved through
a checkout that had never received span-panel-api#161. The conformance tests ran
against an emitter defect that had already been fixed, for as long as it stood.
Every version check in both repositories passed, because the stale worktree
declared the same version number as the corrected code.

So this file checks the one thing no version can: **where the module actually came
from**. It reads `span_panel_api.__file__`, not metadata, because those two can
disagree -- see `Verdict.INCONSISTENT` -- and when they do it is metadata that is
wrong.

Nothing here writes down a version or a path. The pinned version is read from
`manifest.json`, which is the only file whose pins reach a user, and the expected
location is read from `[tool.uv.sources]` in `pyproject.toml`, which is what the
environment is actually built from. A guard carrying its own copy of the fact it
is guarding is the same duplicated-knowledge defect in miniature, and it fails
the day somebody bumps the real one.

Deliberate local overrides **skip** rather than fail, and only off CI. Testing
against an unreleased library is a supported workflow -- `developer.md`, "Working
against unreleased library code" -- and a guard that punishes the workflow it is
protecting gets deleted. Under `CI` there is no such thing as a legitimate
override: the workflow deletes `[tool.uv.sources]` and resolves from PyPI, so
anything but the pinned distribution in the environment's own site-packages means
the wiring has come undone. The reason a skip is not a pass is `span-panel-api`'s,
stated in its DEVELOPMENT.md -- "A skip reads in a summary line exactly like a
pass, and that is how a stale vendored capture went unnoticed for nine days".
"""

from __future__ import annotations

from enum import Enum
from importlib.metadata import Distribution, PackageNotFoundError, distribution
import json
import os
from pathlib import Path
import tomllib
from typing import NamedTuple
from urllib.parse import unquote, urlparse

import pytest
import span_panel_api

_REPO_ROOT = Path(__file__).resolve().parent.parent

DISTRIBUTION = "span-panel-api"
"""The bootstrap distribution: the one every import in this suite goes through."""

MANIFEST = _REPO_ROOT / "custom_components" / "span_panel" / "manifest.json"
PYPROJECT = _REPO_ROOT / "pyproject.toml"

WORKFLOW = "developer.md, 'Working against unreleased library code'"
"""Where the supported way to test against unreleased library work is written down."""


class Verdict(Enum):
    """What the resolved module is, relative to what the pins name."""

    AS_PINNED = "the module the pins name, at the version they pin"
    STALE = "the location the pins name, at a version they do not"
    OVERRIDDEN = "a deliberate local override: somewhere else, consistently"
    INCONSISTENT = "metadata and the imported module disagree about where the code is"


class Resolution(NamedTuple):
    """Where `span_panel_api` came from, and where it was supposed to come from.

    Four facts rather than two because two of them can lie independently.
    `installed` and `metadata_origin` both come from the installed distribution's
    metadata and describe each other; `imported` is the module Python actually
    loaded. `expected` is the declaration those are judged against.
    """

    imported: Path
    """`span_panel_api.__file__` -- the code this run will execute."""

    metadata_origin: Path
    """Where the installed distribution says its code lives: an editable target, or site-packages."""

    expected: Path
    """Where `pyproject.toml` says it should come from, or site-packages when nothing overrides."""

    installed: str
    """The version `importlib.metadata` reports."""

    pinned: str
    """The version `manifest.json` requires."""


def verdict(resolution: Resolution) -> Verdict:
    """Classify a resolution. Pure, so every outcome can be exercised directly.

    Order matters. The consistency check comes first because when metadata and
    the imported module disagree, every fact derived from metadata -- including
    the version -- describes a different copy of the library than the one about to
    run, and comparing it against anything is meaningless. That is the state a
    `PYTHONPATH` prefix produces, and it is the most dangerous of the four
    precisely because the version check it invalidates still passes.
    """
    if not resolution.imported.is_relative_to(resolution.metadata_origin):
        return Verdict.INCONSISTENT
    if not resolution.imported.is_relative_to(resolution.expected):
        return Verdict.OVERRIDDEN
    if resolution.installed != resolution.pinned:
        return Verdict.STALE
    return Verdict.AS_PINNED


def explain(outcome: Verdict, resolution: Resolution) -> str:
    """Say what is resolved and what to do about it, to someone who has not read this file."""
    if outcome is Verdict.INCONSISTENT:
        return (
            f"{DISTRIBUTION} metadata and the imported module name different directories. "
            f"The installed distribution is {resolution.installed} at "
            f"{resolution.metadata_origin}, and `span_panel_api.__file__` is "
            f"{resolution.imported}. A worktree on `PYTHONPATH` or `MYPYPATH` does this: "
            f"it has no distribution metadata of its own, so every version check reads the "
            f"installed distribution and passes while the code under test comes from "
            f"somewhere else entirely. Install the worktree instead -- "
            f"`uv pip install -e <worktree>` gives a consistent path and consistent "
            f"metadata, and `uv sync` puts it back. See {WORKFLOW}."
        )
    if outcome is Verdict.OVERRIDDEN:
        return (
            f"{DISTRIBUTION} resolves to {resolution.imported} at {resolution.installed}, "
            f"not to {resolution.expected}, which is what pyproject.toml names. That is what "
            f"a local override looks like and it is a supported one -- `uv sync` ends it. "
            f"This check cannot say anything useful about a library it was not pointed at, "
            f"so it stops here rather than reporting a pass it did not earn. See {WORKFLOW}."
        )
    return (
        f"{DISTRIBUTION} resolves to {resolution.imported}, which is where pyproject.toml "
        f"names, but the installed version is {resolution.installed} and manifest.json pins "
        f"{resolution.pinned}. The checkout is behind or ahead of the pin, and every version "
        f"check in this suite reads {resolution.installed} and agrees with itself. Run "
        f"`uv sync` to rebuild the environment from the pins, or move the checkout to the "
        f"release {resolution.pinned} names."
    )


def _pinned_version() -> str:
    """Read the `span-panel-api` pin out of `manifest.json`.

    From the manifest rather than from `pyproject.toml` because the manifest is
    the only file whose pins reach a user; `scripts/sync-dependencies.py` exists
    to hold every other declaration to it. Reading a copy would make this guard
    agree with a drifted file.
    """
    manifest: object = json.loads(MANIFEST.read_text(encoding="utf-8"))
    requirements = manifest.get("requirements") if isinstance(manifest, dict) else None
    if isinstance(requirements, list):
        for requirement in requirements:
            if isinstance(requirement, str) and requirement.startswith(f"{DISTRIBUTION}=="):
                return requirement.split("==", 1)[1].strip()
    raise AssertionError(
        f"{MANIFEST} has no exact `{DISTRIBUTION}==` requirement. That pin is what Home "
        f"Assistant installs and what this check reads; it cannot be derived from anything else."
    )


def _declared_source() -> Path | None:
    """Return the checkout `[tool.uv.sources]` points `span-panel-api` at, if it points anywhere.

    `None` is the ordinary CI answer, not an error. The workflow deletes the whole
    block before installing so that uv resolves from PyPI, then re-runs the suite
    against a file that no longer has one. Nothing is declared, so nothing is
    overridden, and site-packages is where the library is supposed to be.

    Relative values resolve against the repository root rather than the working
    directory, which is what they mean in the file uv reads -- and pytest can be
    run from anywhere.
    """
    document: object = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return None
    for key in ("tool", "uv", "sources", DISTRIBUTION):
        document = document.get(key) if isinstance(document, dict) else None
    path = document.get("path") if isinstance(document, dict) else None
    if not isinstance(path, str):
        return None
    declared = Path(path)
    return (declared if declared.is_absolute() else _REPO_ROOT / declared).resolve()


def _metadata_origin(installed: Distribution) -> Path:
    """Return where the installed distribution says its code lives.

    An editable install records its target in `direct_url.json` (PEP 610), which
    is the only place the redirection is written down as data -- the `.pth` file
    that implements it holds a `src` directory rather than the checkout. A
    non-editable install has no such record and its code is under site-packages,
    which is what `locate_file` returns.

    This is deliberately not used to decide *which* code runs. It is used to
    decide whether metadata is describing the code that runs at all.
    """
    record = installed.read_text("direct_url.json")
    if record is not None:
        direct_url: object = json.loads(record)
        if isinstance(direct_url, dict):
            url = direct_url.get("url")
            info = direct_url.get("dir_info")
            editable = isinstance(info, dict) and info.get("editable") is True
            if editable and isinstance(url, str):
                return Path(unquote(urlparse(url).path)).resolve()
    return Path(str(installed.locate_file(""))).resolve()


def resolve() -> Resolution:
    """Gather the four facts about this environment's `span_panel_api`."""
    try:
        installed = distribution(DISTRIBUTION)
    except PackageNotFoundError:
        pytest.fail(
            f"{DISTRIBUTION} is not installed, so there is nothing for this suite to test "
            f"against. Run `uv sync`."
        )

    imported = span_panel_api.__file__
    if imported is None:
        pytest.fail(
            f"span_panel_api has no `__file__`, so it was imported as a namespace package "
            f"rather than from a distribution -- there is a directory of that name on the "
            f"path with no code in it. Run `uv sync`."
        )

    metadata_origin = _metadata_origin(installed)
    return Resolution(
        imported=Path(imported).resolve(),
        metadata_origin=metadata_origin,
        expected=_declared_source() or metadata_origin,
        installed=installed.version,
        pinned=_pinned_version(),
    )


def report(resolution: Resolution) -> None:
    """Return on a clean resolution; otherwise stop the run, failing or skipping.

    `STALE` stops everywhere, CI or not: the pins and the environment disagree in
    the one place nothing downstream can see, and that is a defect wherever it
    happens. The other two are only defects in CI, where no legitimate override
    exists -- locally they are the supported workflow, reported loudly enough that
    nobody mistakes the skip for a clean run.
    """
    outcome = verdict(resolution)
    if outcome is Verdict.AS_PINNED:
        return

    reason = explain(outcome, resolution)
    if outcome is Verdict.STALE or os.environ.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


def test_the_library_under_test_is_the_one_the_pins_name() -> None:
    """The guard itself, against this environment."""
    report(resolve())


def _resolution(
    imported: str, metadata: str, expected: str, installed: str, pinned: str
) -> Resolution:
    """Build a resolution from strings, so a case reads as the situation it describes."""
    return Resolution(
        imported=Path(imported),
        metadata_origin=Path(metadata),
        expected=Path(expected),
        installed=installed,
        pinned=pinned,
    )


_CHECKOUT = "/w/span/span-panel-api"
_WORKTREE = "/w/span/span-panel-api-security"
_SITE_PACKAGES = "/w/span/.venv/lib/python3.14/site-packages"


def test_the_checkout_at_the_pinned_version_is_the_only_clean_answer() -> None:
    """Case 1: the module is where the pins point, at the version they pin."""
    resolution = _resolution(
        imported=f"{_CHECKOUT}/src/span_panel_api/__init__.py",
        metadata=_CHECKOUT,
        expected=_CHECKOUT,
        installed="3.1.0",
        pinned="3.1.0",
    )

    assert verdict(resolution) is Verdict.AS_PINNED


def test_the_checkout_at_another_version_is_the_defect_this_file_exists_for() -> None:
    """Case 2: the right location, the wrong content.

    A checkout that has not been re-synced after a pin moves, or one sitting on
    unreleased work. Nothing else in either repository can see this: the version
    the environment reports is the checkout's own, so every copy of the pin agrees
    with it, and the tests pass against a library that is not the one being
    shipped.
    """
    resolution = _resolution(
        imported=f"{_CHECKOUT}/src/span_panel_api/__init__.py",
        metadata=_CHECKOUT,
        expected=_CHECKOUT,
        installed="3.0.1",
        pinned="3.1.0",
    )

    assert verdict(resolution) is Verdict.STALE


def test_an_editable_worktree_is_an_override_rather_than_a_defect() -> None:
    """Case 3: somewhere else, but metadata says so too.

    `uv pip install -e <worktree>` rewrites the distribution's `direct_url.json`
    along with the path, so the location and the metadata move together. This is
    the workflow `developer.md` documents in place of editing `pyproject.toml`,
    and treating it as a failure is what would drive somebody back to editing the
    committed file.
    """
    resolution = _resolution(
        imported=f"{_WORKTREE}/src/span_panel_api/__init__.py",
        metadata=_WORKTREE,
        expected=_CHECKOUT,
        installed="3.2.0",
        pinned="3.1.0",
    )

    assert verdict(resolution) is Verdict.OVERRIDDEN


def test_metadata_describing_one_copy_while_another_runs_is_not_a_pass() -> None:
    """Case 4: the version check passes and means nothing.

    A worktree prefixed onto `PYTHONPATH` or `MYPYPATH` has no distribution
    metadata of its own. `importlib.metadata` therefore keeps reporting the
    installed distribution -- the right name, the right version, the right
    location -- while `span_panel_api.__file__` is inside the worktree. Every
    version-based check in this suite passes on facts about a copy that is not
    running.

    Classified before the version is compared, and before the location is judged
    against the pins, because in this state neither comparison is about the code
    that will execute.
    """
    resolution = _resolution(
        imported=f"{_WORKTREE}/src/span_panel_api/__init__.py",
        metadata=_CHECKOUT,
        expected=_CHECKOUT,
        installed="3.1.0",
        pinned="3.1.0",
    )

    assert verdict(resolution) is Verdict.INCONSISTENT


def test_site_packages_is_the_expected_location_when_nothing_overrides() -> None:
    """CI: no `[tool.uv.sources]`, so the pins name PyPI and the venv is where it lands.

    The workflow deletes the block before installing. `_declared_source` returns
    `None` there and the expected location becomes the distribution's own, which
    makes the check in CI exactly "the pinned version, out of site-packages" --
    the only correct answer when there is nothing to override with.
    """
    resolution = _resolution(
        imported=f"{_SITE_PACKAGES}/span_panel_api/__init__.py",
        metadata=_SITE_PACKAGES,
        expected=_SITE_PACKAGES,
        installed="3.1.0",
        pinned="3.1.0",
    )

    assert verdict(resolution) is Verdict.AS_PINNED


@pytest.mark.parametrize(
    "outcome",
    [Verdict.OVERRIDDEN, Verdict.INCONSISTENT],
    ids=["editable override", "metadata and module disagree"],
)
def test_an_override_fails_in_ci_and_skips_locally(
    monkeypatch: pytest.MonkeyPatch, outcome: Verdict
) -> None:
    """The guard on the guard, for the two outcomes whose severity depends on where they happen.

    Both halves are asserted, in both environments. Asserting only the CI half
    would leave the local half free to become a failure, which is what makes a
    developer delete a check rather than work around it; asserting only the local
    half would leave CI free to skip, and a skip reads in a summary line exactly
    like a pass. That is not a hypothetical -- it is how a stale vendored capture
    went unnoticed for nine days in `span-panel-api` (DEVELOPMENT.md, "A skip here
    is not a pass").
    """
    elsewhere = _WORKTREE if outcome is Verdict.OVERRIDDEN else _CHECKOUT
    resolution = _resolution(
        imported=f"{_WORKTREE}/src/span_panel_api/__init__.py",
        metadata=elsewhere,
        expected=_CHECKOUT,
        installed="3.1.0",
        pinned="3.1.0",
    )
    assert verdict(resolution) is outcome

    outcomes = (pytest.fail.Exception, pytest.skip.Exception)

    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(outcomes) as local:
        report(resolution)
    assert local.type is pytest.skip.Exception, (
        f"off CI, {outcome.value} must skip rather than {local.typename.lower()}: pointing this "
        f"suite at unreleased library work is a documented workflow, and failing it is what "
        f"sends somebody back to editing pyproject.toml"
    )

    monkeypatch.setenv("CI", "true")
    with pytest.raises(outcomes) as in_ci:
        report(resolution)
    assert in_ci.type is pytest.fail.Exception, (
        f"under CI, {outcome.value} must fail rather than {in_ci.typename.lower()}: the "
        f"workflow deletes [tool.uv.sources] and resolves from PyPI, so no legitimate "
        f"override exists there and a skip is indistinguishable from a pass"
    )


def test_every_message_names_the_paths_it_is_talking_about() -> None:
    """A guard that stops a run has to say what it found, not that it found something.

    Each of these is read by somebody who has just had a green suite stop, and the
    first question is always which library is actually loaded. The answer is the
    path, and it is not derivable from anywhere else in the output.
    """
    resolution = _resolution(
        imported=f"{_WORKTREE}/src/span_panel_api/__init__.py",
        metadata=_CHECKOUT,
        expected=_CHECKOUT,
        installed="3.0.1",
        pinned="3.1.0",
    )

    for outcome in (Verdict.STALE, Verdict.OVERRIDDEN, Verdict.INCONSISTENT):
        reason = explain(outcome, resolution)
        assert str(resolution.imported) in reason, f"{outcome.name} does not name what resolved"
        assert resolution.installed in reason, f"{outcome.name} does not name the version found"


def test_the_pinned_version_is_read_from_the_manifest_rather_than_written_here() -> None:
    """The derivation, against the real file.

    Not a tautology: it asserts the pin can still be *found*, by checking that
    what came back reconstitutes a requirement the manifest actually lists. A
    manifest that stopped using an exact `==` requirement, or renamed the
    distribution, would otherwise leave this whole file comparing against a
    version nobody declared -- which is the failure mode
    `scripts/sync-dependencies.py` was rewritten for, a matcher that quietly found
    nothing and reported success.
    """
    requirements: object = json.loads(MANIFEST.read_text(encoding="utf-8"))["requirements"]

    assert isinstance(requirements, list)
    assert f"{DISTRIBUTION}=={_pinned_version()}" in requirements
