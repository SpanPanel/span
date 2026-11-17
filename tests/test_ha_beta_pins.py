"""The beta workflow runs on the newest Home Assistant test release, or says why it cannot.

`scripts/ha_beta_pins.py pin` rewrites the `pytest-homeassistant` and
`homeassistant` pins before `.github/workflows/ha-beta.yml` locks. Its failure
that matters is a silent one: a rewrite that matched nothing runs the beta job on
the main job's versions, and the job passes having tested nothing new. So most of
these drive the script with a file or a PyPI answer it must refuse.

Imported through `pytest.ini`'s `pythonpath`, and never given the network: every
PyPI document here is built by the test. Type-checked under `mypy --strict` with
the script, by the prek hook that checks the script.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import random

import ha_beta_pins as pins
from ha_beta_pins import JSON
import pytest

REPO = Path(__file__).resolve().parent.parent

PROJECT_URL = "https://pypi.org/pypi/pytest-homeassistant/json"

AVAILABLE: JSON = {"packagetype": "bdist_wheel", "yanked": False}
YANKED: JSON = {"packagetype": "bdist_wheel", "yanked": True}

PYPROJECT = (
    "[project]\n"
    "dependencies = [\n"
    '    "homeassistant==2026.11.2",\n'
    '    "homeassistant-stubs==2026.11.2",\n'
    "]\n"
    "\n"
    "[dependency-groups]\n"
    "dev = [\n"
    "    # Never the unrelated `pytest-homeassistant` 0.1.x.\n"
    '    "pytest-homeassistant==2026.11.2",\n'
    '    "pytest-homeassistant-extra==0.4.0",\n'
    "]\n"
)
"""A miniature with neighbours whose names begin or end with the two being rewritten."""

LOCK = (
    "version = 1\n"
    'requires-python = ">=3.14.3, <3.15"\n'
    "\n"
    "[[package]]\n"
    'name = "aiohttp"\n'
    'version = "3.13.2"\n'
    "\n"
    "[[package]]\n"
    'name = "freezegun"\n'
    'version = "1.6.0rc1"\n'
    "\n"
    "[[package]]\n"
    'name = "homeassistant"\n'
    'version = "2026.12.0b0"\n'
    "\n"
    "[[package]]\n"
    'name = "orjson"\n'
    'version = "3.13.0.post1"\n'
    "\n"
    "[[package]]\n"
    'name = "pytest-homeassistant"\n'
    'version = "2026.12.0b0"\n'
    "\n"
    "[[package]]\n"
    'name = "sqlalchemy"\n'
    'version = "2.1.0.dev4"\n'
    "\n"
    "[[package]]\n"
    'name = "vendored"\n'
    'version = "1.0+local"\n'
    "\n"
    "[[package]]\n"
    'name = "span"\n'
    'source = { virtual = "." }\n'
)
"""A miniature uv lock: Core's two pre-releases, three others, a release and a post release."""


def _project(releases: Mapping[str, list[JSON]]) -> JSON:
    """Return a PyPI project document listing these releases."""
    listed: dict[str, JSON] = dict(releases)
    return {"info": {"name": "pytest-homeassistant"}, "releases": listed}


def _release(*requires: str) -> JSON:
    """Return a PyPI release document declaring these requirements."""
    declared: list[JSON] = list(requires)
    return {"info": {"requires_dist": declared}}


def _sort_key(version: str) -> pins.Version:
    """Return the version's sort key, which the test's versions all have."""
    key = pins.parse_version(version)
    assert key is not None, version
    return key


def _fetching(documents: Mapping[str, JSON]) -> Callable[[str], JSON]:
    """Return a stand-in for the PyPI fetch that answers only these URLs."""

    def fetch(url: str) -> JSON:
        return documents[url]

    return fetch


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def test_versions_order_as_pep_440_orders_them() -> None:
    ordered = [
        "0.1.3",
        "2026.11.0.dev3",
        "2026.11.0b4",
        "2026.11.0b6",
        "2026.11.0rc1",
        "2026.11.0",
        "2026.11.0.post1",
        "2026.11.2",
        "2026.12.0b0",
        "2026.12.0b1",
        "2026.12.0",
    ]
    shuffled = ordered[:]
    random.Random(2026).shuffle(shuffled)

    assert sorted(shuffled, key=_sort_key) == ordered


def test_trailing_zeros_of_the_release_do_not_count() -> None:
    assert pins.parse_version("2026.11") == pins.parse_version("2026.11.0")


@pytest.mark.parametrize("version", ["1!2026.11.0", "2026.11.0+local", "latest", ""])
def test_a_form_it_does_not_read_is_left_out_rather_than_guessed(version: str) -> None:
    assert pins.parse_version(version) is None


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [
        ("2026.12.0", False),
        ("2026.12.0.post1", False),
        ("2026.12.0b0", True),
        ("2026.12.0rc1", True),
        ("2026.12.0.dev3", True),
        ("2026.12.0.post1.dev1", True),
    ],
)
def test_a_pre_release_is_an_a_b_rc_or_dev_release(version: str, prerelease: bool) -> None:
    key = pins.parse_version(version)

    assert key is not None
    assert key.is_prerelease is prerelease


# ---------------------------------------------------------------------------
# The newest release
# ---------------------------------------------------------------------------


def test_the_newest_release_is_a_beta_once_one_is_published() -> None:
    """Above the pin, betas included; never a release with nothing to install."""
    project = _project(
        {
            "0.1.3": [AVAILABLE],
            "2026.11.0b6": [AVAILABLE],
            "2026.11.2": [AVAILABLE],
            "2026.12.0.dev20261120": [AVAILABLE],
            "2026.12.0b0": [AVAILABLE, YANKED],
            "2026.12.0b1": [YANKED, YANKED],
            "2026.12.0b2": [],
        }
    )

    available = pins.available_versions(project)

    assert "2026.12.0b1" not in available
    assert "2026.12.0b2" not in available
    assert pins.newest_version(available, "2026.11.2") == "2026.12.0b0"


def test_with_nothing_newer_the_job_runs_the_main_job_s_release() -> None:
    project = _project({"0.1.3": [AVAILABLE], "2026.11.0b6": [AVAILABLE], "2026.11.2": [AVAILABLE]})

    assert pins.newest_version(pins.available_versions(project), "2026.11.2") == "2026.11.2"


def test_the_unrelated_0_1_releases_are_never_chosen() -> None:
    """Even if every Home Assistant release were yanked, the floor keeps them out."""
    project = _project({"0.1.3": [AVAILABLE], "2026.11.2": [YANKED]})

    with pytest.raises(pins.PinError, match="no pytest-homeassistant release at or above"):
        pins.newest_version(pins.available_versions(project), "2026.11.2")


def test_a_project_document_without_releases_fails() -> None:
    with pytest.raises(pins.PinError, match="has no `releases`"):
        pins.available_versions({"info": {}})


# ---------------------------------------------------------------------------
# The Home Assistant the release pins
# ---------------------------------------------------------------------------


def test_the_core_pin_is_the_release_s_one_exact_homeassistant_pin() -> None:
    release = _release(
        "homeassistant==2026.12.0b0",
        "pytest==9.0.3",
        'aiohasupervisor==0.8.1; extra == "bluetooth"',
    )

    assert pins.core_pin(release) == "2026.12.0b0"


@pytest.mark.parametrize(
    "requires",
    [
        ("pytest==9.0.3",),
        ("homeassistant>=2026.11",),
        ('homeassistant==2026.12.0b0; extra == "full"',),
        ("homeassistant==2026.12.0b0", "homeassistant==2026.12.0b1"),
    ],
    ids=["absent", "a range", "behind a marker", "twice"],
)
def test_a_release_without_one_exact_homeassistant_pin_fails(requires: tuple[str, ...]) -> None:
    with pytest.raises(pins.PinError, match="exact homeassistant pins, not one"):
        pins.core_pin(_release(*requires))


@pytest.mark.parametrize(
    "requirement",
    ['homeassistant==2026.12.0b0"', "homeassistant==2026.12\\1", "homeassistant==<2026.12"],
    ids=["a quote", "a backslash", "an operator"],
)
def test_a_core_pin_that_is_not_a_version_fails_before_anything_is_written(
    requirement: str,
) -> None:
    """It would otherwise be written into pyproject.toml as it stands."""
    with pytest.raises(pins.PinError, match="not a version this reads"):
        pins.core_pin(_release(requirement))


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------


def test_both_pins_are_rewritten_and_nothing_else() -> None:
    rewritten = pins.rewrite_pins(
        PYPROJECT, {"pytest-homeassistant": "2026.12.0b0", "homeassistant": "2026.12.0b0"}
    )

    assert rewritten == PYPROJECT.replace(
        '"homeassistant==2026.11.2"', '"homeassistant==2026.12.0b0"'
    ).replace('"pytest-homeassistant==2026.11.2"', '"pytest-homeassistant==2026.12.0b0"')


def test_the_version_is_written_as_it_is_and_never_read_as_a_template() -> None:
    """A backslash would be a group reference in an `re.sub` template."""
    rewritten = pins.rewrite_pins(PYPROJECT, {"homeassistant": "2026.12.0b0\\1"})

    assert '"homeassistant==2026.12.0b0\\1"' in rewritten


@pytest.mark.parametrize(
    "pyproject",
    [
        PYPROJECT.replace('    "homeassistant==2026.11.2",\n', ""),
        PYPROJECT.replace(
            '    "homeassistant==2026.11.2",\n',
            '    "homeassistant==2026.11.2",\n    "homeassistant==2026.11.1",\n',
        ),
        PYPROJECT.replace('"homeassistant==2026.11.2"', '"homeassistant>=2026.11"'),
    ],
    ids=["absent", "twice", "a range"],
)
def test_a_pin_that_is_not_there_exactly_once_fails(pyproject: str) -> None:
    with pytest.raises(pins.PinError, match='"homeassistant==..." pins, not one'):
        pins.rewrite_pins(pyproject, {"homeassistant": "2026.12.0b0"})


def test_the_repository_pins_the_two_as_a_pair_the_script_can_rewrite() -> None:
    """The real file, read only: a reformatted pin fails here rather than in the beta job."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    plugin = pins.current_pin(text, pins.PLUGIN)

    assert pins.current_pin(text, pins.CORE) == plugin
    rewritten = pins.rewrite_pins(text, {pins.PLUGIN: "2099.1.0b0", pins.CORE: "2099.1.0b0"})
    changed = [
        (before, after)
        for before, after in zip(text.splitlines(), rewritten.splitlines(), strict=True)
        if before != after
    ]
    assert changed == [
        (f'    "homeassistant=={plugin}",', '    "homeassistant==2099.1.0b0",'),
        (f'    "pytest-homeassistant=={plugin}",', '    "pytest-homeassistant==2099.1.0b0",'),
    ]


# ---------------------------------------------------------------------------
# The pre-releases a lock resolved
# ---------------------------------------------------------------------------


def test_the_lock_s_pre_releases_are_listed_besides_core_s_own() -> None:
    """A post release is a release; a version in another form is listed, saying so."""
    assert pins.lock_prereleases(LOCK) == [
        "freezegun 1.6.0rc1",
        "sqlalchemy 2.1.0.dev4",
        "vendored 1.0+local (a version form this does not read)",
    ]


def test_this_repository_s_lock_can_be_read() -> None:
    """The real lock, so a change in uv's format fails here rather than in the beta job."""
    pins.lock_prereleases((REPO / "uv.lock").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The script as the workflow runs it
# ---------------------------------------------------------------------------


def test_the_script_pins_the_newest_release_and_its_homeassistant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    fetch = _fetching(
        {
            PROJECT_URL: _project({"2026.11.2": [AVAILABLE], "2026.12.0b0": [AVAILABLE]}),
            "https://pypi.org/pypi/pytest-homeassistant/2026.12.0b0/json": _release(
                "homeassistant==2026.12.0b0", "pytest==9.0.3"
            ),
        }
    )

    assert pins.main(["pin", "--pyproject", str(pyproject)], fetch=fetch) == 0

    text = pyproject.read_text(encoding="utf-8")
    assert pins.current_pin(text, "pytest-homeassistant") == "2026.12.0b0"
    assert pins.current_pin(text, "homeassistant") == "2026.12.0b0"
    assert capsys.readouterr().out == (
        "pytest-homeassistant==2026.12.0b0, homeassistant==2026.12.0b0\n"
    )


def test_the_script_says_when_it_runs_the_main_job_s_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    fetch = _fetching(
        {
            PROJECT_URL: _project({"0.1.3": [AVAILABLE], "2026.11.2": [AVAILABLE]}),
            "https://pypi.org/pypi/pytest-homeassistant/2026.11.2/json": _release(
                "homeassistant==2026.11.2"
            ),
        }
    )

    assert pins.main(["pin", "--pyproject", str(pyproject)], fetch=fetch) == 0

    assert pyproject.read_text(encoding="utf-8") == PYPROJECT
    assert capsys.readouterr().out.endswith("(the versions the main job runs)\n")


@pytest.mark.parametrize(
    ("requires", "says"),
    [
        (("pytest==9.0.3",), "exact homeassistant pins, not one"),
        (('homeassistant==2026.12.0b0"',), "not a version this reads"),
    ],
    ids=["no core pin", "a core pin that is not a version"],
)
def test_the_script_writes_nothing_when_the_release_pins_no_usable_homeassistant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], requires: tuple[str, ...], says: str
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    fetch = _fetching(
        {
            PROJECT_URL: _project({"2026.11.2": [AVAILABLE], "2026.12.0b0": [AVAILABLE]}),
            "https://pypi.org/pypi/pytest-homeassistant/2026.12.0b0/json": _release(*requires),
        }
    )

    assert pins.main(["pin", "--pyproject", str(pyproject)], fetch=fetch) == 1

    assert pyproject.read_text(encoding="utf-8") == PYPROJECT
    assert says in capsys.readouterr().err


def test_the_script_reports_the_lock_s_pre_releases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK, encoding="utf-8")

    assert pins.main(["prereleases", "--lock", str(lock)]) == 0

    assert capsys.readouterr().out == (
        "Pre-releases in the lock besides pytest-homeassistant and homeassistant: "
        "freezegun 1.6.0rc1, sqlalchemy 2.1.0.dev4, "
        "vendored 1.0+local (a version form this does not read)\n"
    )


def test_the_script_says_so_when_the_lock_has_no_other_pre_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "homeassistant"\nversion = "2026.12.0b0"\n', encoding="utf-8"
    )

    assert pins.main(["prereleases", "--lock", str(lock)]) == 0

    assert capsys.readouterr().out.endswith(": none\n")


def test_the_script_does_nothing_without_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    """So a bare run, or one asking for help, never rewrites the repository's pyproject.toml."""
    with pytest.raises(SystemExit) as exited:
        pins.main([])

    assert exited.value.code == 2
    assert "required" in capsys.readouterr().err
