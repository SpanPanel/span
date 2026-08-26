#!/usr/bin/env python3
"""Refresh the vendored schema-1 capture and the release it records, in one action.

`tests/fixtures/schema_one_tree.json` is a byte copy of a payload that lives in
`span-panel-api`, and `tests/fixtures/schema_one_tree.source` records which
release it was copied from. Two files, two facts, and **that is the whole reason
this script exists**: until now they were updated by two different actions -- a
copy and an edit -- so doing one and not the other was a single missed keystroke,
and the result was a capture whose claim about itself was wrong. Performing both
from one command makes them incapable of disagreeing, which is a stronger
guarantee than any amount of care with a two-step README.

It is not hypothetical. The capture was once refreshed from a checkout that sat
behind the recorded release, producing a copy that was faithful to *a* release
and mislabelled as another; it recorded a producer defect that upstream had
already fixed, and every conformance test kept passing against it. So this script
refuses to copy from a checkout whose schema-1 release is not the one installed,
and refuses again unless that checkout is standing on the release's *tag* --
because the stale worktree declared the right version while sitting on the wrong
commit, and only a tag can be checked. Copying from unreleased work is still
possible, with `--allow-unreleased`, because it is occasionally correct; it just
has to be said out loud. Making the wrong refresh hard is more of the point than
making the right one convenient.

**The capture is deliberately not copied at test time.** Doing that would undo
both things vendoring bought. It would put the cross-repo dependency back --
every test run needing a `span-panel-api` checkout, which is exactly what
committing the bytes removed -- and it would make the byte comparison in
`tests/test_fixture_provenance.py` vacuous, since a copy compared against the
thing it was just copied from can only ever pass. Committed bytes that are
*allowed to disagree with their source* are what let that comparison find the
stale checkout. A refresh is a deliberate act, taken here and reviewed in a diff.

Usage, from the repository root:

    uv run python scripts/refresh-vendored-capture.py

`SPAN_PANEL_API_DIR` names the checkout; see `.env.example`. A correct run when
nothing has moved upstream is a no-op and says so.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO))

from tests.adapter_fixtures import (  # noqa: E402
    CHECKOUT_VARIABLE,
    SCHEMA_ONE_DISTRIBUTION,
    SCHEMA_ONE_RELEASE_DECLARATION,
    SCHEMA_ONE_SOURCE_PATHS,
    SCHEMA_ONE_TREE,
    SCHEMA_ONE_TREE_SOURCE,
)

DOTENV = REPO / ".env"

_VERSION = re.compile(r'^version = "([^"]+)"', re.MULTILINE)


class RefreshError(Exception):
    """Something about the environment or the checkout makes a refresh unsafe."""


def load_dotenv() -> None:
    """Populate the environment from `.env`, without overriding what is set.

    Read directly rather than through python-dotenv, for the reason
    `tests/conftest.py` gives: parsing a handful of `export KEY=value` lines does
    not justify a dependency. This is a second small implementation rather than a
    shared one because `conftest.py` must stay free of heavy imports at
    collection time, and anything this script could import it from is not.
    """
    if not DOTENV.is_file():
        return
    for raw in DOTENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def installed_release() -> str:
    """Return the release of the adapter this environment actually has.

    Read rather than accepted as an argument, because a version typed on a
    command line is a third place for the claim to be wrong. The pin has one
    home in `manifest.json`, reaches the environment through `pyproject.toml`,
    and this is that same value read back out.
    """
    try:
        return version(SCHEMA_ONE_DISTRIBUTION)
    except PackageNotFoundError as exc:
        raise RefreshError(
            f"{SCHEMA_ONE_DISTRIBUTION} is not installed, so there is no release to "
            f"record. Run this through the project environment: "
            f"`uv run python scripts/{Path(__file__).name}`."
        ) from exc


def checkout() -> Path:
    """Resolve the `span-panel-api` checkout named by the environment.

    A relative value is resolved against the repository root rather than the
    working directory, because that is what `.env.example`'s `../span-panel-api`
    means and this can be run from anywhere.
    """
    configured = os.environ.get(CHECKOUT_VARIABLE)
    if not configured:
        raise RefreshError(
            f"{CHECKOUT_VARIABLE} is not set. Point it at a span-panel-api checkout -- "
            f"copy `.env.example` to `.env` and edit it, or export it for this run. "
            f"There is no default: a refresh has to name the checkout it trusts."
        )
    path = Path(configured)
    if not path.is_absolute():
        path = REPO / path
    if not path.is_dir():
        raise RefreshError(
            f"{CHECKOUT_VARIABLE}={configured} does not exist. Point it at a "
            f"span-panel-api checkout."
        )
    if not (path / SCHEMA_ONE_RELEASE_DECLARATION).is_file():
        raise RefreshError(
            f"{CHECKOUT_VARIABLE}={configured} has no {SCHEMA_ONE_RELEASE_DECLARATION}, "
            f"so it is not a span-panel-api checkout (or not a complete one). Re-clone it."
        )
    return path


def declared_release(source: Path) -> str:
    """Read the release a checkout declares, from the package it would build."""
    declaration = (source / SCHEMA_ONE_RELEASE_DECLARATION).read_text(encoding="utf-8")
    found = _VERSION.search(declaration)
    if found is None:
        raise RefreshError(
            f"{source / SCHEMA_ONE_RELEASE_DECLARATION} declares no version, so there is "
            f"no way to tell which release its capture belongs to."
        )
    return found.group(1)


def _git(source: Path, *arguments: str) -> str | None:
    """Run a read-only git command in the checkout, or None if git cannot answer.

    None covers a checkout that is not a git repository at all -- an unpacked
    sdist, a copied directory -- which is a legitimate way to have the files and
    simply means the position check cannot be made.
    """
    try:
        finished = subprocess.run(  # noqa: S603
            ["git", "-C", str(source), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.strip()


def require_position_at_the_release(source: Path, release: str, allow_unreleased: bool) -> None:
    """Refuse a checkout that is not sitting on the release it claims.

    **This is the check that would have caught the refresh that went wrong, and
    the version comparison above is not.** The stale worktree the capture came
    from *declared* the installed release while sitting on unreleased work behind
    `main`, so the declaration and the pin agreed while the bytes were a release
    old. A version string in a working tree is a statement of intent; a tag is a
    fact, and only the tag can be checked.

    Three outcomes, because they call for different actions:

    * Positioned at the tag -- nothing to say, this is the ordinary case.
    * Positioned elsewhere while the tag exists -- refuse. A release is available
      and there is no reason to copy from anything else.
    * Tag absent -- refuse unless `--allow-unreleased` was passed. The release is
      not cut, so unreleased work is the only thing there is to copy from, and
      that is sometimes the right call: it is how this capture was corrected when
      the fix was on `main` and 1.1.0 was not yet tagged. But it is a decision, not
      a detail, and it must be made deliberately. A warning on stderr is not good
      enough -- this repository already learned that a message which does not stop
      anything reads exactly like success.
    """
    tag = f"schema-1-v{release}"
    tagged = _git(source, "rev-list", "-n", "1", tag)

    if tagged is None:
        if not allow_unreleased:
            raise RefreshError(
                f"{source} declares {release}, but {tag} does not exist there (or it is "
                f"not a git checkout), so these bytes are unreleased work that nothing "
                f"can verify against a release. That is occasionally the right thing to "
                f"vendor -- it is how this capture was corrected while the fix was only "
                f"on `main` -- but it has to be deliberate. Fetch the tag if you expected "
                f"one (`git -C {source} fetch --tags`), or re-run with --allow-unreleased "
                f"and say in the commit message why."
            )
        print(
            f"warning: {tag} does not exist in {source}, so this vendors unreleased "
            f"work. Nothing can verify these bytes against a release until {tag} is "
            f"cut; refresh again once it is.",
            file=sys.stderr,
        )
        return

    head = _git(source, "rev-parse", "HEAD")
    if head is not None and head != tagged:
        raise RefreshError(
            f"{source} is on {head[:12]}, not on {tag} ({tagged[:12]}), even though "
            f"{tag} exists. Copy from the release, not from a working tree that happens "
            f"to declare it -- that difference is exactly how this capture went wrong "
            f"before. `git -C {source} checkout {tag}` and run this again."
        )


def capture_in(source: Path, release: str) -> Path:
    """Locate the capture inside a checkout, at whichever of the two paths holds it."""
    for relative in SCHEMA_ONE_SOURCE_PATHS:
        candidate = source / relative
        if candidate.is_file():
            return candidate
    locations = " nor ".join(str(relative) for relative in SCHEMA_ONE_SOURCE_PATHS)
    raise RefreshError(
        f"{source} is {SCHEMA_ONE_DISTRIBUTION} {release} but holds the capture at "
        f"neither {locations}. Either the checkout is incomplete, or that release "
        f"moved the file again and SCHEMA_ONE_SOURCE_PATHS needs the new location."
    )


def refresh(allow_unreleased: bool) -> int:
    """Copy the capture and write the release it came from. Return an exit code."""
    load_dotenv()

    installed = installed_release()
    source = checkout()
    declared = declared_release(source)

    if declared != installed:
        raise RefreshError(
            f"{CHECKOUT_VARIABLE} names a checkout on {SCHEMA_ONE_DISTRIBUTION} "
            f"{declared}, and this environment has {installed} installed. Refreshing "
            f"from it would vendor bytes from one release and record another -- which "
            f"is how the capture came to carry a producer defect that upstream had "
            f"already fixed. Move the checkout to {installed} (its tag is "
            f"schema-1-v{installed}), or change the pin in manifest.json and re-sync."
        )

    require_position_at_the_release(source, installed, allow_unreleased)
    capture = capture_in(source, declared)

    # `shutil.copyfile`, never a json.load/json.dump round trip. Re-serialising is
    # what turns a copy into a reformatting: it rewrites indentation, key order and
    # float spelling, and the byte comparison in tests/test_fixture_provenance.py
    # exists precisely to reject the result.
    unchanged = SCHEMA_ONE_TREE.is_file() and SCHEMA_ONE_TREE.read_bytes() == capture.read_bytes()
    shutil.copyfile(capture, SCHEMA_ONE_TREE)

    record = f"{SCHEMA_ONE_DISTRIBUTION}=={installed}\n"
    record_unchanged = (
        SCHEMA_ONE_TREE_SOURCE.is_file()
        and SCHEMA_ONE_TREE_SOURCE.read_text(encoding="utf-8") == record
    )
    SCHEMA_ONE_TREE_SOURCE.write_text(record, encoding="utf-8")

    print(f"source:   {capture}")
    print(f"release:  {SCHEMA_ONE_DISTRIBUTION}=={installed}")
    if unchanged and record_unchanged:
        print("Already current; nothing changed.")
        return 0
    print(f"updated:  {SCHEMA_ONE_TREE.relative_to(REPO)}")
    print(f"updated:  {SCHEMA_ONE_TREE_SOURCE.relative_to(REPO)}")
    print(
        "Review the diff before committing, and commit both files together -- "
        "they are one fact in two files."
    )
    return 0


ALLOW_UNRELEASED = "--allow-unreleased"


def main(argv: list[str]) -> int:
    """Run the refresh, reporting a bad environment as prose rather than a traceback.

    Arguments are checked by hand rather than with argparse: there is one flag,
    and an unrecognised one has to be an error instead of being ignored -- a
    misspelled `--allow-unreleased` that silently did nothing would turn the
    deliberate decision back into an accident.
    """
    unknown = [argument for argument in argv if argument != ALLOW_UNRELEASED]
    if unknown:
        print(
            f"refresh-vendored-capture: unrecognised argument(s) {' '.join(unknown)}. "
            f"The only option is {ALLOW_UNRELEASED}.",
            file=sys.stderr,
        )
        return 2
    try:
        return refresh(allow_unreleased=ALLOW_UNRELEASED in argv)
    except RefreshError as exc:
        print(f"refresh-vendored-capture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
