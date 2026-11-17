#!/usr/bin/env python3
"""Pin `pyproject.toml` to the newest Home Assistant test release, betas included.

The HA beta workflow (`.github/workflows/ha-beta.yml`) runs the suite on the
newest `pytest-homeassistant` release and the `homeassistant` that release pins,
so a change in a Core beta is seen about a week before it ships. Each release of
the plugin pins exactly one `homeassistant`, of its own version, so the two pins
move together and this rewrites both.

**The newest release** is the highest version on PyPI that is no older than the
plugin version `pyproject.toml` pins now and has at least one file that is not
yanked. Pre-releases count: they are what the workflow is for. The floor is the
current pin rather than a constant, so it rises with the main job's, and it is
what keeps out the unrelated `pytest-homeassistant` 0.1.x releases another
author published under the same name in 2020.

Standard library only, because it runs before the project's environment exists.

It fails rather than doing nothing when a pin it rewrites is not exactly one
`"name==version"` string in the file, or when the release declares no exact
`homeassistant` pin. A rewrite that matched nothing would run the beta job on
the main job's versions and report a pass that tested nothing new.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
import json
from pathlib import Path
import re
import sys
from typing import NamedTuple
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"

PLUGIN = "pytest-homeassistant"
"""Home Assistant's test plugin, released in lockstep with Core."""

CORE = "homeassistant"

PYPI = "https://pypi.org/pypi"

_TIMEOUT_S = 30

_VERSION = re.compile(
    r"^(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?P<pre_phase>a|b|rc)(?P<pre_number>\d+))?"
    r"(?:\.post(?P<post>\d+))?"
    r"(?:\.dev(?P<dev>\d+))?$"
)
"""A version as PyPI's JSON spells it, normalized: no epoch and no local part.

Home Assistant publishes `2026.11.2` and `2026.12.0b0`; the post and dev parts
are here so such a version is ordered correctly rather than skipped.
"""

_PRE_PHASES = {"a": 0, "b": 1, "rc": 2}


class PinError(Exception):
    """The inputs cannot produce a correct pair of pins."""


class Version(NamedTuple):
    """A version's PEP 440 sort key, for the forms `_VERSION` reads."""

    release: tuple[int, ...]
    pre: tuple[int, ...]
    post: tuple[int, ...]
    dev: tuple[int, ...]


def parse_version(text: str) -> Version | None:
    """Return the version's sort key, or None for a form this does not read.

    Ordered as PEP 440 orders them: a dev release before the pre-releases of
    the same release, those before the release, and a post release after it.
    Trailing zeros of the release do not count, so `2026.11` equals `2026.11.0`.
    """
    if (match := _VERSION.match(text)) is None:
        return None
    release = tuple(int(part) for part in match["release"].split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    pre: tuple[int, ...]
    if match["pre_phase"] is not None:
        pre = (_PRE_PHASES[match["pre_phase"]], int(match["pre_number"]))
    elif match["post"] is None and match["dev"] is not None:
        pre = (-1,)
    else:
        pre = (len(_PRE_PHASES),)
    post = (-1,) if match["post"] is None else (int(match["post"]),)
    dev = (1,) if match["dev"] is None else (0, int(match["dev"]))
    return Version(release, pre, post, dev)


def available_versions(project: object) -> list[str]:
    """Return the versions in a PyPI project document with a file that is not yanked."""
    releases = _field(project, "releases", "the project document")
    if not isinstance(releases, dict):
        raise PinError("the project document's `releases` is not a mapping")
    available: list[str] = []
    for version, files in releases.items():
        if not isinstance(version, str) or not isinstance(files, list):
            raise PinError(f"release {version!r} is not a list of files")
        if any(isinstance(file, dict) and file.get("yanked") is not True for file in files):
            available.append(version)
    return available


def newest_version(versions: Iterable[str], floor: str) -> str:
    """Return the highest version no older than `floor`, pre-releases included."""
    if (floor_key := parse_version(floor)) is None:
        raise PinError(f"the pinned {PLUGIN} version {floor!r} is not one this reads")
    candidates = [
        (key, version)
        for version in versions
        if (key := parse_version(version)) is not None and key >= floor_key
    ]
    if not candidates:
        raise PinError(f"PyPI lists no {PLUGIN} release at or above {floor}")
    return max(candidates)[1]


def core_pin(release: object) -> str:
    """Return the `homeassistant` version a plugin release document pins exactly."""
    info = _field(release, "info", "the release document")
    requires = _field(info, "requires_dist", "the release's `info`")
    if not isinstance(requires, list):
        raise PinError("the release's `requires_dist` is not a list")
    pinned: list[str] = [
        match["version"]
        for requirement in requires
        if isinstance(requirement, str)
        and (match := _exact_pin(CORE).fullmatch(requirement.strip())) is not None
    ]
    if len(pinned) != 1:
        raise PinError(
            f"the release declares {len(pinned)} exact {CORE} pins, not one: {requires!r}"
        )
    return pinned[0]


def current_pin(pyproject: str, name: str) -> str:
    """Return the version `pyproject.toml` pins `name` to, as its one `"name==version"`."""
    found: list[str] = _quoted_pin(name).findall(pyproject)
    if len(found) != 1:
        raise PinError(f'pyproject.toml has {len(found)} "{name}==..." pins, not one')
    return found[0]


def rewrite_pins(pyproject: str, pins: Mapping[str, str]) -> str:
    """Return `pyproject.toml` with each named pin set to its version.

    Each name must appear exactly once as a quoted `"name==version"`. The match
    is anchored on the opening quote, so `homeassistant` never matches inside
    `"pytest-homeassistant==..."`.
    """
    for name, version in pins.items():
        current_pin(pyproject, name)
        pyproject = _quoted_pin(name).sub(version, pyproject)
    return pyproject


def fetch_json(url: str) -> object:
    """Return the JSON document at a URL."""
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=_TIMEOUT_S) as response:
        document: object = json.load(response)
    return document


def main(argv: Sequence[str] | None = None, *, fetch: Callable[[str], object] = fetch_json) -> int:
    """Rewrite both pins to the newest plugin release and report what was chosen."""
    parser = argparse.ArgumentParser(
        prog="ha-beta-pins.py",
        description=(
            f"Rewrite the {PLUGIN} and {CORE} pins in pyproject.toml to the newest "
            f"{PLUGIN} release, pre-releases included, and the {CORE} it pins."
        ),
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=PYPROJECT,
        help="the file to rewrite (default: this repository's pyproject.toml)",
    )
    pyproject_path: Path = parser.parse_args(argv).pyproject
    try:
        text = pyproject_path.read_text(encoding="utf-8")
        floor = current_pin(text, PLUGIN)
        plugin = newest_version(available_versions(fetch(f"{PYPI}/{PLUGIN}/json")), floor)
        core = core_pin(fetch(f"{PYPI}/{PLUGIN}/{plugin}/json"))
        pyproject_path.write_text(
            rewrite_pins(text, {PLUGIN: plugin, CORE: core}), encoding="utf-8"
        )
    except PinError as err:
        sys.stderr.write(f"ha-beta-pins: {err}\n")
        return 1
    same = " (the versions the main job runs)" if plugin == floor else ""
    sys.stdout.write(f"{PLUGIN}=={plugin}, {CORE}=={core}{same}\n")
    return 0


def _field(document: object, key: str, what: str) -> object:
    """Return one key of a JSON object, or fail naming what was malformed."""
    if not isinstance(document, dict) or key not in document:
        raise PinError(f"{what} has no `{key}`")
    value: object = document[key]
    return value


def _exact_pin(name: str) -> re.Pattern[str]:
    """Match a requirement that pins `name` exactly and carries no marker."""
    return re.compile(rf"{re.escape(name)}\s*==\s*(?P<version>[^\s;,()]+)")


def _quoted_pin(name: str) -> re.Pattern[str]:
    """Match the version in `"name==version"`, the form a pyproject dependency takes."""
    return re.compile(rf'(?<="{re.escape(name)}==)[^"]+(?=")')


if __name__ == "__main__":
    sys.exit(main())
