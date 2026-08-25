"""The commit-time gate on `pyproject.toml`'s library paths must actually fire.

`scripts/check-library-path.py` runs on every commit that touches
`pyproject.toml` and rejects a `span-panel-api` path naming anything but
`../../span/span-panel-api`. It exists because `3cbf02a` committed a scratch
worktree into both blocks that decide where the library comes from, and nothing
noticed for several commits.

Tested for the same reason `tests/test_dependency_sync.py` exists: a hook that
matches nothing produces no output, and no output is indistinguishable from a
clean tree. Most of what follows hands the hook a file that is wrong on purpose
and checks that it says so.

Lives here rather than beside the script in `tests/scripts/`, which `pytest.ini`
excludes with `norecursedirs`. A test that is never collected is the same failure
as a hook that never matches.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-library-path.py"
PYPROJECT = REPO / "pyproject.toml"

CHECKOUT = "../../span/span-panel-api"
WORKTREE = "../../span/span-panel-api-security"
"""The worktree `3cbf02a` actually named. A sibling of the checkout, sharing every
path component but the last, which is why a prefix comparison has to be
separator-aware to see the difference."""


def _run(*targets: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(target) for target in targets)],
        capture_output=True,
        text=True,
        check=False,
    )


def _pyproject(root: Path, sources: str | None, pyright: str | None) -> Path:
    """Write a miniature `pyproject.toml` declaring the library at the given roots.

    Built rather than copied so a case cannot pass by accident of what the real
    tree happens to hold today, and so `None` can express a block that is absent
    -- which is the shape CI installs from.
    """
    lines = ['[project]', 'name = "span"', ""]
    if sources is not None:
        lines += [
            "[tool.uv.sources]",
            f'span-panel-api = {{ path = "{sources}", editable = true }}',
            f'span-panel-api-schema-0 = {{ path = "{sources}/packages/schema-0", editable = true }}',
            f'span-panel-api-schema-1 = {{ path = "{sources}/packages/schema-1", editable = true }}',
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


def test_the_committed_file_passes() -> None:
    """The real `pyproject.toml`, which is the state the hook must be silent on.

    A gate nobody can commit through is not a gate, it is an outage. This is the
    half of the proof that is easy to skip and the one that decides whether the
    hook survives its first day.
    """
    result = _run(PYPROJECT)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("sources", "pyright"),
    [(WORKTREE, WORKTREE), (WORKTREE, CHECKOUT), (CHECKOUT, WORKTREE)],
    ids=["both blocks", "the sources block alone", "the type-checker path alone"],
)
def test_a_worktree_in_either_block_is_rejected(
    tmp_path: Path, sources: str, pyright: str
) -> None:
    """`3cbf02a` in miniature, and each half of it alone.

    Both blocks are checked because both were wrong and they were corrected
    separately: `82a512f` moved `extraPaths` back several commits and hours after
    the sources block, because nothing was watching it. The third case here is
    that interval -- a file half-corrected, which is exactly the state a hook
    covering only `[tool.uv.sources]` would call clean.
    """
    result = _run(_pyproject(tmp_path, sources, pyright))

    assert result.returncode == 1, result.stdout or "the hook said nothing"
    assert WORKTREE in result.stdout, "the failure does not name the offending path"


def test_the_checkout_in_both_blocks_passes(tmp_path: Path) -> None:
    """The corrected state, including the sub-paths the adapters and pyright use."""
    result = _run(_pyproject(tmp_path, CHECKOUT, CHECKOUT))

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_absent_sources_block_passes(tmp_path: Path) -> None:
    """CI deletes `[tool.uv.sources]` before installing, then re-runs these hooks.

    A block that is not there declares nothing to be wrong about, and failing on
    its absence would make the hook impossible to satisfy in the one environment
    that resolves from PyPI.
    """
    result = _run(_pyproject(tmp_path, None, CHECKOUT))

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_failure_says_what_to_do_instead(tmp_path: Path) -> None:
    """Somebody hits this precisely when they are trying to do the thing it forbids.

    The message has to carry the alternative, because a gate that only says "no"
    to a developer with a real reason to redirect the library teaches them to
    pass `--no-verify` rather than to use the venv.
    """
    result = _run(_pyproject(tmp_path, WORKTREE, CHECKOUT))

    assert "uv pip install -e" in result.stdout, "the failure does not name the alternative"
    assert "uv sync" in result.stdout, "the failure does not say how to undo it"
    assert "developer.md" in result.stdout, "the failure does not point at the documentation"


def test_an_unrelated_path_is_left_alone(tmp_path: Path) -> None:
    """`extraPaths` carries other repositories, and they are none of this hook's business."""
    target = tmp_path / "pyproject.toml"
    target.write_text(
        '[tool.pyright]\nextraPaths = ["../ha-synthetic-sensors/src", "./custom_components"]\n',
        encoding="utf-8",
    )

    result = _run(target)

    assert result.returncode == 0, result.stdout + result.stderr
