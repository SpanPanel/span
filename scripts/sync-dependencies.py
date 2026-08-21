#!/usr/bin/env python3
"""Hold every declaration of a library pin to the one in manifest.json.

Home Assistant installs what `custom_components/span_panel/manifest.json`
requires, so that file is the only one whose pins reach a user. Every other
place the same library is named -- `pyproject.toml` for local development,
`requirements_test.txt` for a bare pip setup -- is a copy, and a copy that drifts
is worse than no copy: the tests pass against one version while the integration
ships another.

Run as a pre-commit hook. Rewrites the copies and exits non-zero when it changed
something, so the commit stops and the corrected files are re-staged.

**Why this was rewritten.** The previous version matched versions with `[0-9.]+`
and so stopped at the first letter: `3.0.0b7` was seen as `3.0.0`, which matched
nothing and silently rewrote nothing. Every version this project has ever shipped
is a pre-release, so the hook has been inert for its whole life while reporting
success on every commit. It also only ever looked for the bootstrap package --
`span-panel-api-schema-0` starts with `span-panel-api`, so it entered the branch
and then failed its own regex -- and it never knew about `requirements_test.txt`
at all, which is how that file came to pin `b4` against a manifest requiring `b7`.

The lesson is in the shape rather than the regex: nothing parsed, so nothing
could report that it had not matched. This version parses the requirement, looks
the name up, and verifies the result, so a miss is an error rather than a
no-change.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "custom_components" / "span_panel" / "manifest.json"
PYPROJECT = REPO / "pyproject.toml"
REQUIREMENTS_TEST = REPO / "requirements_test.txt"

# One requirement: a PEP 508 name, then a specifier that runs to the end. The
# version half is deliberately unconstrained -- `3.0.0b7`, `1.0.0rc1`, `2.6.4`,
# `1.0.0.post1` and `>=1,<2` all have to survive it, and enumerating version
# grammar is what broke the last one.
REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?P<spec>[<>=!~].*)$"
)


class SyncError(Exception):
    """Something is wrong with the inputs, as opposed to merely out of date."""


def manifest_pins() -> dict[str, str]:
    """Return the manifest's requirements, keyed by package name.

    Raises rather than returning empty on a bad manifest. An unreadable source of
    truth is not a reason to leave every copy alone and report success.
    """
    if not MANIFEST.is_file():
        raise SyncError(f"no manifest at {MANIFEST.relative_to(REPO)}")
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SyncError(f"{MANIFEST.relative_to(REPO)} is not valid JSON: {exc}") from exc

    pins: dict[str, str] = {}
    for requirement in manifest.get("requirements", []):
        match = REQUIREMENT.match(str(requirement).strip())
        if match is None:
            raise SyncError(
                f"manifest requirement {requirement!r} has no version specifier; "
                "Home Assistant installs these verbatim, so an unpinned one is a bug "
                "rather than something to copy"
            )
        pins[match["name"]] = match.group(0)
    if not pins:
        raise SyncError(f"{MANIFEST.relative_to(REPO)} declares no requirements")
    return pins


def _project_dependencies_span(text: str) -> tuple[int, int]:
    """Return the character span of `[project]`'s `dependencies = [...]` array.

    Scoped rather than global on purpose. `pyproject.toml` names these same
    packages twice more -- as editable path overrides under `[tool.uv.sources]`,
    and inside mypy's search path -- and rewriting either would replace a
    filesystem path with a version specifier.
    """
    project = re.search(r"^\[project\]\s*$", text, re.MULTILINE)
    if project is None:
        raise SyncError("pyproject.toml has no [project] table")
    next_table = re.search(r"^\[", text[project.end() :], re.MULTILINE)
    end_of_project = project.end() + (
        next_table.start() if next_table else len(text) - project.end()
    )

    array = re.search(
        r"^dependencies\s*=\s*\[", text[project.start() : end_of_project], re.MULTILINE
    )
    if array is None:
        raise SyncError("[project] declares no dependencies array")
    start = project.start() + array.end()
    closing = text.find("]", start)
    if closing == -1:
        raise SyncError("[project] dependencies array is never closed")
    return start, closing


def sync_pyproject(pins: dict[str, str]) -> list[str]:
    """Rewrite pinned requirements inside `[project] dependencies`. Return the changes."""
    text = PYPROJECT.read_text(encoding="utf-8")
    start, end = _project_dependencies_span(text)
    changes: list[str] = []

    def replace(match: re.Match[str]) -> str:
        requirement = REQUIREMENT.match(match["req"])
        if requirement is None or requirement["name"] not in pins:
            return match.group(0)
        wanted = pins[requirement["name"]]
        if match["req"] == wanted:
            return match.group(0)
        changes.append(f"pyproject.toml: {match['req']} -> {wanted}")
        return f'"{wanted}"'

    body = re.sub(r'"(?P<req>[^"]+)"', replace, text[start:end])
    if changes:
        PYPROJECT.write_text(text[:start] + body + text[end:], encoding="utf-8")
    return changes


def sync_requirements_test(pins: dict[str, str]) -> list[str]:
    """Rewrite pinned requirements in `requirements_test.txt`. Return the changes."""
    if not REQUIREMENTS_TEST.is_file():
        return []
    lines = REQUIREMENTS_TEST.read_text(encoding="utf-8").splitlines()
    changes: list[str] = []

    for index, line in enumerate(lines):
        requirement = REQUIREMENT.match(line.strip())
        if requirement is None or requirement["name"] not in pins:
            continue
        wanted = pins[requirement["name"]]
        if line.strip() != wanted:
            changes.append(f"requirements_test.txt: {line.strip()} -> {wanted}")
            lines[index] = wanted

    if changes:
        REQUIREMENTS_TEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changes


def verify(pins: dict[str, str]) -> None:
    """Read the rewritten files back and confirm they say what was intended.

    The point of the whole rewrite. A regex that matches nothing produces no
    changes, which is indistinguishable from a file that was already correct --
    that is exactly how the previous version stayed silently broken. Checking the
    result against the manifest turns a miss into a failure.
    """
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    for requirement in declared:
        match = REQUIREMENT.match(str(requirement).strip())
        if match and match["name"] in pins and match.group(0) != pins[match["name"]]:
            raise SyncError(
                f"pyproject.toml still declares {requirement!r} after syncing; "
                f"the manifest pins {pins[match['name']]!r}"
            )

    if REQUIREMENTS_TEST.is_file():
        for line in REQUIREMENTS_TEST.read_text(encoding="utf-8").splitlines():
            match = REQUIREMENT.match(line.strip())
            if match and match["name"] in pins and match.group(0) != pins[match["name"]]:
                raise SyncError(
                    f"requirements_test.txt still declares {line.strip()!r} after syncing; "
                    f"the manifest pins {pins[match['name']]!r}"
                )


def main() -> int:
    """Sync, verify, and fail the commit if anything moved."""
    try:
        pins = manifest_pins()
        changes = sync_pyproject(pins) + sync_requirements_test(pins)
        verify(pins)
    except SyncError as exc:
        print(f"sync-dependencies: {exc}", file=sys.stderr)
        return 1

    if not changes:
        return 0
    for change in changes:
        print(f"sync-dependencies: {change}")
    print("sync-dependencies: files updated to match the manifest; re-stage and commit again")
    return 1


if __name__ == "__main__":
    sys.exit(main())
