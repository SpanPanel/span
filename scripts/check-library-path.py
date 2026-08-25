#!/usr/bin/env python3
"""Hold every `span-panel-api` path in `pyproject.toml` to the library checkout.

`pyproject.toml` is a committed, shared file, and two blocks in it decide where
`span_panel_api` comes from: `[tool.uv.sources]`, which every import in the suite
resolves through, and `[tool.pyright].extraPaths`, which decides what the type
checker reads. Both are written as relative paths so they mean the same thing in
the primary checkout and in every worktree beside it.

Working against unreleased library code needs those paths aimed somewhere else
for a while, and the obvious way to do that is to edit them. That is what
happened in `3cbf02a`: both blocks were pointed at a scratch worktree and
committed. It stood for several commits and a few hours of other people's work,
and while it stood a test fixture was vendored through it from a checkout that
still recorded a producer defect the library had already fixed. Every
version-based check passed -- the stale worktree declared the same version number
as the corrected code -- and only a byte comparison caught it. A version string
does not identify content; only a filesystem location does.

So the redirection does not belong in a committed file at all. It belongs in the
virtual environment, where it affects one developer's next command and nothing
else, and `developer.md` ("Working against unreleased library code") says how.
This hook is what makes that the only route: it rejects a `pyproject.toml` whose
`span-panel-api` paths name anything but the checkout.

Absence is not a failure. CI deletes the whole `[tool.uv.sources]` block before
installing, so it resolves from PyPI and re-runs these hooks against a file that
no longer has one. A block that is not there declares nothing to be wrong about.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib

CHECKOUT = "../../span/span-panel-api"
"""The library checkout, relative to this repository's root.

Relative rather than absolute because this repository is worked in through git
worktrees, and `../../span/span-panel-api` resolves to the same directory from
every one of them. An absolute path would be correct on exactly one machine.
"""

LIBRARY = "span-panel-api"
"""The name that makes a path this hook's business.

Matched as a substring rather than by parsing the path, because the failure being
caught is a *sibling* of the checkout -- `span-panel-api-security`,
`span-panel-api-p3` -- and a sibling shares every path component up to the last.
"""

WORKFLOW = "developer.md, 'Working against unreleased library code'"
"""Where the supported alternative to editing this file is written down."""


def _table(parent: dict[str, object], name: str) -> dict[str, object]:
    """Return a sub-table of a parsed TOML document, or an empty one.

    Missing and present-but-not-a-table are the same answer deliberately: this
    hook reports paths that are wrong, and `check-toml` in the same hook run
    already reports a file that does not parse. Two hooks failing on one
    malformed file says nothing the first one did not.
    """
    child = parent.get(name)
    if not isinstance(child, dict):
        return {}
    narrowed: dict[str, object] = {}
    for key, value in child.items():
        narrowed[str(key)] = value
    return narrowed


def declared_paths(document: dict[str, object]) -> list[tuple[str, str]]:
    """Return every path in the document that names the library, with where it came from.

    Both blocks, not just `[tool.uv.sources]`. They went wrong together in
    `3cbf02a` and were corrected separately -- `82a512f` moved `extraPaths` back
    six hours and several commits after the sources block, because nothing was
    looking at it. A hook that covers one of two places that hold the same fact
    leaves the slower half of the same defect in place.
    """
    tool = _table(document, "tool")
    found: list[tuple[str, str]] = []

    sources = _table(_table(tool, "uv"), "sources")
    for name, source in sorted(sources.items()):
        if not isinstance(source, dict):
            continue
        path = source.get("path")
        if isinstance(path, str) and LIBRARY in path:
            found.append((f"[tool.uv.sources] {name}", path))

    extra_paths = _table(tool, "pyright").get("extraPaths")
    if isinstance(extra_paths, list):
        for entry in extra_paths:
            if isinstance(entry, str) and LIBRARY in entry:
                found.append(("[tool.pyright] extraPaths", entry))

    return found


def is_within_checkout(path: str) -> bool:
    """Whether a declared path is the checkout itself or something inside it.

    The trailing separator on the prefix is the whole check.
    `../../span/span-panel-api-security` starts with `../../span/span-panel-api`
    as a string and is a different directory, which is exactly the mistake being
    caught; `../../span/span-panel-api/src` does not survive that comparison by
    accident.
    """
    return path == CHECKOUT or path.startswith(f"{CHECKOUT}/")


def offences(path: Path) -> list[str]:
    """Return one line per path that names the library and is not inside the checkout."""
    document: dict[str, object] = dict(tomllib.loads(path.read_text(encoding="utf-8")))
    return [
        f"{path}: {where} = {declared!r}"
        for where, declared in declared_paths(document)
        if not is_within_checkout(declared)
    ]


def main(arguments: list[str]) -> int:
    """Check each file named on the command line; default to this repository's own."""
    repository = Path(__file__).resolve().parent.parent
    targets = [Path(argument) for argument in arguments] or [repository / "pyproject.toml"]

    found: list[str] = []
    for target in targets:
        if target.is_file():
            found.extend(offences(target))

    if not found:
        return 0

    print("A span-panel-api path names something other than the library checkout:")
    for offence in found:
        print(f"  {offence}")
    print(
        f"\nEvery path here must be {CHECKOUT!r} or a directory inside it. To test against "
        f"unreleased library work, put the worktree in the virtual environment instead of in "
        f"this file:\n"
        f"\n    uv pip install -e ../../span/span-panel-api-<worktree>\n"
        f"\nThat redirects your next command and nothing else, and `uv sync` puts it back. "
        f"See {WORKFLOW}.\n"
        f"\nThis file is shared: a worktree named here redirects every import in the suite, "
        f"for everyone, for as long as it goes unnoticed -- and a stale checkout declares the "
        f"same version as the current one, so nothing that compares versions can see it."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
