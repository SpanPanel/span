"""The commit-time gate on `pyproject.toml`'s library paths must actually fire.

`scripts/check-library-path.py` runs on every commit that touches
`pyproject.toml` and rejects a `span-panel-api` path naming anything but
`../../span/span-panel-api`. It exists because `3cbf02a` pointed both blocks that
decide where the library comes from at a scratch worktree and committed it, and
nothing noticed for several commits.

Tested for the same reason `tests/test_dependency_sync.py` exists: a hook that
matches nothing produces no output, and no output is indistinguishable from a
clean tree.

Two things this file is careful about, because getting either wrong would leave a
gate that is only proven against itself.

**It drives the real entry point.** Every case runs
`scripts/check-library-path.py` as a subprocess and reads its exit status and
output, exactly as prek invokes it -- never the module's functions, and never a
reimplementation of the rule in the test. A test that reimplements the logic
proves the test's copy of the logic. `test_the_hook_prek_runs_is_the_one_tested`
closes the remaining gap by reading the entry out of `prek.toml`: a hook renamed
there and not here would otherwise go unregistered while these tests kept passing.

**The failing cases are the real files.** `tests/fixtures/pyproject_*.toml` are
byte copies of `pyproject.toml` at the two commits where this actually went
wrong, taken straight out of this repository's history and held to it by
`test_the_vendored_copies_are_the_commits_they_name`. A gate proven against a
made-up example is proven against the wrong thing: the synthetic version of a
defect is written by someone who already knows what the rule checks, so it
exercises the rule rather than the mistake. The two states nothing in history
provides -- an absent `[tool.uv.sources]` block, an `extraPaths` naming only
unrelated repositories -- are the only ones constructed here.

Lives here rather than beside the script in `tests/scripts/`, which `pytest.ini`
excludes with `norecursedirs`. A test that is never collected is the same failure
as a hook that never matches.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-library-path.py"
PYPROJECT = REPO / "pyproject.toml"
PREK = REPO / "prek.toml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

HOOK_ID = "library-path"
"""The hook's id in `prek.toml`, which is how prek is asked to run it."""

CHECKOUT = "../../span/span-panel-api"
WORKTREE = "../../span/span-panel-api-security"
"""The worktree `3cbf02a` actually named.

A sibling of the checkout, sharing every path component but the last, which is
why a prefix comparison has to be separator-aware to tell them apart. Not written
here as the definition of the defect -- the vendored copies are that -- but as
what the failure output is checked to name.
"""

BOTH_BLOCKS = "pyproject_both_blocks_redirected"
"""`3cbf02a`: `[tool.uv.sources]` and `[tool.pyright].extraPaths` both on the worktree."""

TYPE_CHECKER_ONLY = "pyproject_type_checker_path_left_behind"
"""`82a512f^`: the sources block corrected, `extraPaths` still on the worktree.

The interval between the two corrections -- several commits, during which the
repository looked fixed. A hook covering only `[tool.uv.sources]` calls this
clean, which is precisely how the second half survived as long as it did.
"""


def _run(*targets: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the hook the way prek does: the script, given filenames, judged by exit status."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(target) for target in targets)],
        capture_output=True,
        text=True,
        check=False,
    )


def _historical(name: str) -> Path:
    """Return the vendored copy of `pyproject.toml` at one historical commit."""
    return FIXTURES / f"{name}.toml"


def _recorded_commit(name: str) -> str:
    """Return the commit a vendored copy records itself as having come from."""
    return (FIXTURES / f"{name}.source").read_text(encoding="utf-8").strip()


def _synthetic(root: Path, sources: str | None, pyright: str | None) -> Path:
    """Write a miniature `pyproject.toml` for a state this repository's history does not hold.

    Deliberately not used for the failing cases. History has real instances of
    those, and a real instance is worth more than a constructed one -- see this
    module's docstring.
    """
    lines = ["[project]", 'name = "span"', ""]
    if sources is not None:
        lines += [
            "[tool.uv.sources]",
            f'span-panel-api = {{ path = "{sources}", editable = true }}',
            "",
        ]
    if pyright is not None:
        lines += [
            "[tool.pyright]",
            f'extraPaths = ["./custom_components", "{pyright}/src", "../ha-synthetic-sensors/src"]',
            "",
        ]
    written = root / "pyproject.toml"
    written.write_text("\n".join(lines), encoding="utf-8")
    return written


_HOOK_TABLE = re.compile(r"\{(?P<body>[^{}]*)\}", re.DOTALL)
"""One hook's inline table. `prek.toml` nests no braces inside one, so this is exact."""

_ENTRY = re.compile(r'^\s*entry\s*=\s*"(?P<entry>[^"]*)"', re.MULTILINE)


def _declared_entries(hook_id: str) -> list[str]:
    """Return the `entry` of every hook in `prek.toml` with this id.

    Read as text rather than through `tomllib`, because `prek.toml` is not TOML
    1.0: it writes each hook as an inline table spread over several lines, which
    the spec allows only from 1.1 and `tomllib` rejects outright. prek's own
    parser accepts it and so does `check-toml`, so the file is not wrong -- a
    strict parser is simply the wrong tool for reading it, and reformatting the
    file to suit one test would be the tail wagging the dog.
    """
    text = PREK.read_text(encoding="utf-8")
    entries: list[str] = []
    for table in _HOOK_TABLE.finditer(text):
        body = table.group("body")
        if f'id = "{hook_id}"' not in body:
            continue
        entry = _ENTRY.search(body)
        if entry is not None:
            entries.append(entry.group("entry"))
    return entries


def test_the_hook_prek_runs_is_the_one_tested() -> None:
    """`prek.toml` must invoke the script these tests invoke.

    The gap this closes is a quiet one: rename the script or the hook and every
    case below goes on passing against a file prek no longer runs. Nothing else
    would report it, because an unregistered hook produces no output either.
    """
    entries = _declared_entries(HOOK_ID)

    assert entries, f"prek.toml declares no hook with id {HOOK_ID!r}; nothing runs at commit time"
    assert len(entries) == 1, f"prek.toml declares {HOOK_ID!r} more than once: {entries}"
    assert SCRIPT.name in entries[0], (
        f"prek runs {entries[0]!r} for {HOOK_ID!r} and these tests exercise {SCRIPT.name}. "
        f"One of the two is not the gate."
    )


def test_the_committed_file_passes() -> None:
    """The real `pyproject.toml`, which is the state the hook must be silent on.

    A gate nobody can commit through is not a gate, it is an outage. This is the
    half of the proof that is easy to skip and the one that decides whether the
    hook survives its first day.
    """
    result = _run(PYPROJECT)

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_commit_that_redirected_both_blocks_is_rejected() -> None:
    """`3cbf02a` itself, byte for byte, not an approximation of it.

    All four declarations it moved are reported, rather than the first one found.
    Reporting one at a time would have made correcting this a four-commit
    conversation with the hook, and it is the last one -- `extraPaths` -- that
    nobody was looking at.
    """
    result = _run(_historical(BOTH_BLOCKS))

    assert result.returncode == 1, result.stdout or "the hook said nothing about 3cbf02a"
    for declaration in (
        f"span-panel-api = '{WORKTREE}'",
        f"span-panel-api-schema-0 = '{WORKTREE}/packages/schema-0'",
        f"span-panel-api-schema-1 = '{WORKTREE}/packages/schema-1'",
        f"extraPaths = '{WORKTREE}/src'",
    ):
        assert declaration in result.stdout, f"the failure does not report {declaration}"


def test_the_commit_that_left_the_type_checker_path_behind_is_rejected() -> None:
    """`82a512f^`: half-corrected, and still wrong.

    The sources block had been moved back to the checkout by this point and the
    type-checker path had not. The assertion that matters is the second one: the
    corrected half must not be reported, so what fails here is the half that was
    genuinely still broken rather than a hook that objects to the file in general.
    """
    result = _run(_historical(TYPE_CHECKER_ONLY))

    assert result.returncode == 1, result.stdout or "the hook said nothing about 82a512f^"
    assert f"extraPaths = '{WORKTREE}/src'" in result.stdout
    assert f"[tool.uv.sources] span-panel-api = '{WORKTREE}'" not in result.stdout, (
        "the sources block was already corrected at this commit; reporting it would mean the "
        "hook is not reading the file it was given"
    )


def test_the_failure_says_what_to_do_instead() -> None:
    """Somebody hits this precisely when they are trying to do the thing it forbids.

    The message has to carry the alternative, because a gate that only says "no"
    to a developer with a real reason to redirect the library teaches them to pass
    `--no-verify` rather than to use the venv.
    """
    result = _run(_historical(BOTH_BLOCKS))

    assert "uv pip install -e" in result.stdout, "the failure does not name the alternative"
    assert "uv sync" in result.stdout, "the failure does not say how to undo it"
    assert "developer.md" in result.stdout, "the failure does not point at the documentation"


def test_an_absent_sources_block_passes(tmp_path: Path) -> None:
    """CI deletes `[tool.uv.sources]` before installing, then re-runs these hooks.

    Constructed rather than taken from history, because no commit here has ever
    been in this state -- it exists only inside a CI job, after a `sed`. A block
    that is not there declares nothing to be wrong about, and failing on its
    absence would make the hook impossible to satisfy in the one environment that
    resolves from PyPI.
    """
    result = _run(_synthetic(tmp_path, None, CHECKOUT))

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_unrelated_path_is_left_alone(tmp_path: Path) -> None:
    """`extraPaths` carries other repositories, and they are none of this hook's business."""
    target = tmp_path / "pyproject.toml"
    target.write_text(
        '[tool.pyright]\nextraPaths = ["../ha-synthetic-sensors/src", "./custom_components"]\n',
        encoding="utf-8",
    )

    result = _run(target)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", [BOTH_BLOCKS, TYPE_CHECKER_ONLY])
def test_the_vendored_copies_are_the_commits_they_name(name: str) -> None:
    """Each copy must still be the blob its `.source` records.

    Without this, "the real historical file" is a claim in a docstring, and the
    obvious way to make a failing case pass is to edit the fixture.

    **A skip here is genuinely not the pass that a skip usually hides**, and the
    difference is worth stating because this repository's other provenance guard
    refuses exactly this exit. That one tracks a moving target -- a library
    release that can be re-cut, re-tagged, or drift -- so skipping means not
    looking at something that changes. A commit is content-addressed: the bytes at
    `3cbf02a:pyproject.toml` cannot become different bytes. The only way this
    comparison can fail is a copy that was wrong when it was vendored, and that is
    caught wherever the history is reachable, which is every developer machine and
    any CI checkout with depth. A shallow clone does not have the object to
    compare against, and asserting against an object that is not there would fail
    on the clone rather than on the fixture.
    """
    recorded = _recorded_commit(name)
    blob = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-p", f"{recorded}:pyproject.toml"],
        capture_output=True,
        check=False,
    )
    if blob.returncode != 0:
        pytest.skip(
            f"{recorded} is not reachable from this checkout (a shallow clone has no history "
            f"to compare against); the copy is verified wherever the commit is present"
        )

    assert blob.stdout == _historical(name).read_bytes(), (
        f"tests/fixtures/{name}.toml is no longer a byte copy of {recorded}:pyproject.toml, "
        f"which is what makes it evidence rather than an example. Re-vendor it with "
        f"`git show {recorded}:pyproject.toml > tests/fixtures/{name}.toml`, or correct the "
        f"commit in {name}.source if the copy was meant to come from a different one."
    )
