"""Nothing in the integration reads the `friendly_name` state attribute.

Home Assistant plans to retire `friendly_name`, so the integration takes names
from the entity and device registries instead. Two guards keep a read from
coming back:

- a scan of the package's Python sources for the `"friendly_name"` string
  literal, the key such a read would use;
- Ruff's banned-API list in `pyproject.toml`, which rejects each name
  `homeassistant.const` gives the same key: `ATTR_FRIENDLY_NAME`,
  `EntityStateAttribute.FRIENDLY_NAME` (the one Core itself reads the attribute
  with) and `CONF_FRIENDLY_NAME`.

Two reads escape both guards and stay review items:

- one through `State.name`, Core's own accessor for the attribute;
- one through a constant imported from a module other than
  `homeassistant.const`, since Ruff matches the name a constant is imported
  under and any module that imports one of these also offers it.

The scan covers `*.py` only. The bundled card under `frontend/dist` is built
from the card's repository, whose own lint bans the attribute there.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

import custom_components.span_panel as span_panel

REPO = Path(__file__).resolve().parent.parent
PACKAGE = Path(span_panel.__file__).resolve().parent

FRIENDLY_NAME = "friendly_name"


def friendly_name_literals(root: Path) -> list[str]:
    """Return `file:line` for every `"friendly_name"` string literal under `root`.

    The syntax tree is read, not the text, so a comment or docstring that
    mentions the attribute is not a finding, and neither is a longer string
    such as the stored naming option `"friendly_names"`.
    """
    found: list[str] = []
    for source in sorted(root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        found.extend(
            f"{source.relative_to(root)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == FRIENDLY_NAME
        )
    return found


def test_package_has_no_friendly_name_literal() -> None:
    assert friendly_name_literals(PACKAGE) == []


def test_the_scan_finds_each_friendly_name_literal(tmp_path: Path) -> None:
    """The scan reports the reads it exists to stop, and nothing else."""
    package = tmp_path / "package"
    nested = package / "nested"
    nested.mkdir(parents=True)
    (package / "reads.py").write_text(
        '"""A docstring that mentions friendly_name is not a read."""\n'
        "\n"
        "# Neither is a comment that mentions friendly_name.\n"
        'NAMING_OPTION = "friendly_names"\n'
        "\n"
        "\n"
        "def name_of(state):\n"
        '    return state.attributes.get("friendly_name")\n',
        encoding="utf-8",
    )
    (nested / "subscripts.py").write_text(
        "def name_of(state):\n    return state.attributes['friendly_name']\n",
        encoding="utf-8",
    )

    assert friendly_name_literals(package) == ["nested/subscripts.py:2", "reads.py:8"]


def banned_api_findings(source: str) -> list[str]:
    """Return Ruff's banned-API findings for `source` as a file of the package.

    Ruff runs with the repository's own configuration, so the answer is what CI
    gives for the same lines in `custom_components/span_panel`.
    """
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--output-format=concise",
            "--stdin-filename",
            str(PACKAGE / "reads_the_attribute.py"),
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )

    # 0 is clean and 1 is findings; anything else means Ruff itself failed.
    assert checked.returncode in (0, 1), checked.stderr
    return [line for line in checked.stdout.splitlines() if "TID251" in line]


@pytest.mark.parametrize(
    ("source", "banned"),
    [
        pytest.param(
            "from homeassistant.const import ATTR_FRIENDLY_NAME\n",
            "homeassistant.const.ATTR_FRIENDLY_NAME",
            id="attr_constant",
        ),
        pytest.param(
            "from homeassistant.const import EntityStateAttribute\n"
            "\n"
            "\n"
            "def name_of(state):\n"
            "    return state.attributes.get(EntityStateAttribute.FRIENDLY_NAME)\n",
            "homeassistant.const.EntityStateAttribute.FRIENDLY_NAME",
            id="state_attribute_enum",
        ),
        pytest.param(
            "from homeassistant import const\n"
            "\n"
            "\n"
            "def name_of(state):\n"
            "    return state.attributes[const.EntityStateAttribute.FRIENDLY_NAME]\n",
            "homeassistant.const.EntityStateAttribute.FRIENDLY_NAME",
            id="state_attribute_enum_through_the_module",
        ),
        pytest.param(
            "from homeassistant.const import CONF_FRIENDLY_NAME\n",
            "homeassistant.const.CONF_FRIENDLY_NAME",
            id="conf_constant",
        ),
    ],
)
def test_ruff_bans_the_friendly_name_constant(source: str, banned: str) -> None:
    """The repository's Ruff configuration rejects each Core name for the key."""
    findings = banned_api_findings(source)

    assert len(findings) == 1, findings
    assert f"`{banned}` is banned" in findings[0]


def test_ruff_allows_the_other_shared_state_attributes() -> None:
    """The ban is on the one member, not on Core's enum of shared attributes."""
    findings = banned_api_findings(
        "from homeassistant.const import EntityStateAttribute\n"
        "\n"
        "\n"
        "def device_class_of(state):\n"
        "    return state.attributes.get(EntityStateAttribute.DEVICE_CLASS)\n"
    )

    assert findings == []
