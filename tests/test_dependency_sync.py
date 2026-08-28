"""The pin sync must fail loudly rather than quietly do nothing.

`scripts/sync-dependencies.py` holds `pyproject.toml` and `requirements_test.txt`
to the pins in `manifest.json`, which is the only file whose versions reach a
user. It runs on every commit through `prek.toml` and again in CI.

Lives here rather than beside the script in `tests/scripts/`, which `pytest.ini`
excludes with `norecursedirs`. A test that is never collected is the same failure
as a hook that never matches: green, silent, and worth nothing.

It is tested because the previous version was inert for its entire life and said
so to nobody. It matched versions with `[0-9.]+`, so `3.0.0b7` was read as
`3.0.0`, which matched nothing and rewrote nothing -- and every version this
project ships is a pre-release. A hook that matches nothing produces no changes,
which is indistinguishable from a tree that was already correct. That is the
failure these tests exist to catch, so most of them drift a file on purpose and
check that the hook notices.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "sync-dependencies.py"


def _run(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Return a miniature of this repository: a manifest and the two files it governs.

    Built rather than copied so a test cannot pass by accident of what the real
    tree happens to hold today, and so the pins can be pre-release versions --
    the case the old implementation could not see.
    """
    component = tmp_path / "custom_components" / "span_panel"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "span_panel",
                "requirements": [
                    "span-panel-api==3.0.0b7",
                    "span-panel-api-schema-0==1.0.0b5",
                    "span-panel-api-schema-1==0.1.0b6",
                ],
            },
            indent=2,
        )
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "span-panel"\n'
        "dependencies = [\n"
        '    "span-panel-api==3.0.0b7",\n'
        '    "span-panel-api-schema-0==1.0.0b5",\n'
        '    "span-panel-api-schema-1==0.1.0b6",\n'
        '    "homeassistant>=2026.8.0",\n'
        "]\n"
        "\n"
        "[tool.uv.sources]\n"
        'span-panel-api = { path = "../span-panel-api", editable = true }\n'
        "\n"
        "[tool.uv]\n"
        "# A deliberately different pin, in a table the manifest does not govern.\n"
        'constraint-dependencies = ["span-panel-api==3.0.0b3"]\n'
    )
    (tmp_path / "requirements_test.txt").write_text(
        "pytest>=9.0.3\nspan-panel-api==3.0.0b7\nspan-panel-api-schema-0==1.0.0b5\n"
        "span-panel-api-schema-1==0.1.0b6\n"
    )
    return tmp_path


def _write_script_into(repo: Path) -> None:
    """Give the miniature its own copy, since the script locates files from its own path."""
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "sync-dependencies.py").write_text(SCRIPT.read_text())


def _sync(repo: Path) -> subprocess.CompletedProcess[str]:
    _write_script_into(repo)
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "sync-dependencies.py")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_already_synced_tree_passes(repo: Path) -> None:
    result = _sync(repo)
    assert result.returncode == 0, result.stderr


def test_a_pre_release_pin_is_synced(repo: Path) -> None:
    """The exact case the previous implementation could not see.

    `3.0.0b7` was read as `3.0.0` by a `[0-9.]+` version pattern, so the
    substitution never matched and the hook reported success on a stale file.
    Every version this project has shipped is a pre-release, so this was not an
    edge case -- it was every case.
    """
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("3.0.0b7", "3.0.0b4"))

    result = _sync(repo)

    assert result.returncode == 1, "a stale pin must stop the commit"
    assert "span-panel-api==3.0.0b7" in pyproject.read_text()
    assert "3.0.0b4" not in pyproject.read_text()


def test_the_adapter_packages_are_synced_too(repo: Path) -> None:
    """`span-panel-api-schema-0` starts with `span-panel-api`, which is how it was missed.

    The old implementation entered its bootstrap branch on the prefix and then
    failed its own regex on the `-schema-0` that followed, so the adapters were
    never synced by anything.
    """
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("0.1.0b6", "0.1.0b3"))

    assert _sync(repo).returncode == 1
    assert "span-panel-api-schema-1==0.1.0b6" in pyproject.read_text()


def test_requirements_test_is_governed(repo: Path) -> None:
    """It drifted to a stale beta precisely because nothing looked at it."""
    requirements = repo / "requirements_test.txt"
    requirements.write_text(requirements.read_text().replace("3.0.0b7", "3.0.0b4"))

    assert _sync(repo).returncode == 1
    assert "span-panel-api==3.0.0b7" in requirements.read_text()


def test_only_the_project_dependencies_are_governed(repo: Path) -> None:
    """The manifest governs `[project] dependencies` and no other table.

    `pyproject.toml` names these same packages in several places: as editable
    path overrides under `[tool.uv.sources]`, in mypy's search path, and -- the
    case that makes scoping load-bearing rather than merely tidy -- as a uv
    constraint, which is a version specifier someone set deliberately and to a
    different value. A rewrite that walked the whole file would silently drag it
    to the manifest's pin and undo the constraint.
    """
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            '"span-panel-api==3.0.0b7",\n', '"span-panel-api==3.0.0b4",\n', 1
        )
    )

    assert _sync(repo).returncode == 1
    body = pyproject.read_text()

    assert '"span-panel-api==3.0.0b7",' in body, "the governed pin is synced"
    assert 'constraint-dependencies = ["span-panel-api==3.0.0b3"]' in body, (
        "the ungoverned one is not"
    )
    assert '{ path = "../span-panel-api", editable = true }' in body


def test_a_dependency_the_manifest_does_not_pin_is_untouched(repo: Path) -> None:
    """The manifest governs its own requirements and nothing else."""
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("3.0.0b7", "3.0.0b4"))

    _sync(repo)

    assert '"homeassistant>=2026.8.0"' in pyproject.read_text()


def test_an_unparseable_manifest_fails_rather_than_reporting_success(repo: Path) -> None:
    """An unreadable source of truth is not a reason to leave every copy alone."""
    (repo / "custom_components" / "span_panel" / "manifest.json").write_text("{not json")

    result = _sync(repo)

    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


def test_an_unpinned_manifest_requirement_is_an_error(repo: Path) -> None:
    """Home Assistant installs these verbatim, so a missing specifier is a bug."""
    manifest = repo / "custom_components" / "span_panel" / "manifest.json"
    manifest.write_text(json.dumps({"requirements": ["span-panel-api"]}, indent=2))

    result = _sync(repo)

    assert result.returncode == 1
    assert "no version specifier" in result.stderr


def test_the_real_repository_is_in_sync() -> None:
    """The hook runs on every commit, so this should already be true.

    Here so that a stale pin is reported by the test suite as well as by the
    hook: CI runs both, and a developer who bypasses hooks still gets told.
    """
    result = _run(REPO)
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
