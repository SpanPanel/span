"""The documents say what the code does, where a test can tell.

Prose is reviewed, not tested. A few statements in the repository's documents
are facts about the code that a reader acts on, and each has gone stale at
least once, so they are held here:

- the naming rules (`developer_entity_id_and_name_handling.md`) cite functions
  by name, and name the places that may preset an `entity_id` and the functions
  whose answer the naming flag changes;
- the README quotes the naming option's labels and the config entry version a
  downgrade is refused across, and states the oldest Home Assistant the release
  installs on, which `hacs.json` enforces;
- `AGENTS.md` names the schema library a service is validated with.
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
import re

import pytest

import custom_components.span_panel as span_panel
from custom_components.span_panel.const import ENTITY_NAMING_PATTERN
from custom_components.span_panel.migrations import CURRENT_CONFIG_VERSION

REPO = Path(__file__).resolve().parent.parent
PACKAGE = Path(span_panel.__file__).resolve().parent

RULES_DOC = REPO / "developer_entity_id_and_name_handling.md"
README = REPO / "README.md"
AGENTS = REPO / "AGENTS.md"
HACS = REPO / "hacs.json"

CITED_MODULES = (
    "circuit_device_conversion",
    "circuit_number_ids",
    "entity_resolver",
    "helpers",
    "id_builder",
    "naming",
    "sensor_circuit",
    "util",
)
"""The modules the naming rules cite functions and tables from, as `module.name`.

Only modules whose name the rules use for nothing else. `entity`, `select` and
`coordinator` are modules too, and the rules also write `entity.entity_id` and
`coordinator.request_reload()` about objects of those names.
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the body of the section whose heading line starts with `heading`."""
    level = heading.split(" ", 1)[0]
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(heading))
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("#") and len(line.split(" ", 1)[0]) <= len(level)
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _list_items(section: str) -> list[str]:
    """Return each list item of `section` as one line, its wrapped lines joined."""
    items: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            items.append(line)
        elif items and line.startswith("  "):
            items[-1] = f"{items[-1]} {line.strip()}"
    return items


def entity_id_presets(root: Path) -> list[str]:
    """Return each file under `root` that assigns an entity's `entity_id`.

    An assignment to `self.entity_id` or to `_attr_entity_id`, read from the
    syntax tree so a comment or docstring that mentions one is not a finding.
    Each file is named once, relative to `root`.
    """
    found: list[str] = []
    for source in sorted(root.rglob("*.py")):
        tree = ast.parse(_read(source), filename=str(source))
        targets = [
            target
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        ]
        if any(
            (isinstance(target, ast.Attribute) and target.attr in {"entity_id", "_attr_entity_id"})
            or (isinstance(target, ast.Name) and target.id == "_attr_entity_id")
            for target in targets
        ):
            found.append(str(source.relative_to(root)))
    return found


def test_the_scan_finds_each_way_of_presetting_an_entity_id(tmp_path: Path) -> None:
    """The scan reports the assignments it exists to count, and nothing else."""
    package = tmp_path / "package"
    package.mkdir()
    (package / "at_creation.py").write_text(
        "class Sensor:\n"
        "    def __init__(self, entity_id):\n"
        "        if entity_id is not None:\n"
        "            self.entity_id = entity_id\n",
        encoding="utf-8",
    )
    (package / "by_attribute.py").write_text(
        'class Sensor:\n    _attr_entity_id = "sensor.kitchen_outlets_power"\n',
        encoding="utf-8",
    )
    (package / "reads_only.py").write_text(
        '"""A docstring saying self.entity_id = x is not an assignment."""\n'
        "\n"
        "\n"
        "def log(entity):\n"
        "    # Neither is a comment: self.entity_id = x\n"
        "    entity_id = entity.entity_id\n"
        "    return entity_id\n",
        encoding="utf-8",
    )

    assert entity_id_presets(package) == ["at_creation.py", "by_attribute.py"]


def test_the_rules_name_every_place_that_presets_an_entity_id() -> None:
    """R2 allows one preset, at creation in circuit-numbers mode, and the checklist says where.

    The checklist item is the list a reviewer checks a naming change against,
    so a preset added anywhere else fails here until the rules account for it.
    """
    checklist = _list_items(_section(_read(RULES_DOC), "## 8."))
    preset_items = [item for item in checklist if "`self.entity_id`" in item]
    assert len(preset_items) == 1, "the checklist needs one item about `self.entity_id`"

    named = sorted(set(re.findall(r"`([a-z_]+\.py)`", preset_items[0])))

    assert named == entity_id_presets(PACKAGE)


NAMING_FLAG_PARAMETER = "use_circuit_numbers"
"""The parameter a function takes the naming flag as."""


def naming_flag_functions(root: Path) -> list[str]:
    """Return each function under `root` that takes the naming flag, as `module.function`.

    A function handed the flag answers differently in the two naming modes,
    which is what the rules have to account for. Read from the syntax tree, so
    a local variable or a keyword argument of that name is not a finding.
    """
    found: list[str] = []
    for source in sorted(root.rglob("*.py")):
        tree = ast.parse(_read(source), filename=str(source))
        module = ".".join(source.relative_to(root).with_suffix("").parts)
        found.extend(
            f"{module}.{node.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(
                parameter.arg == NAMING_FLAG_PARAMETER
                for parameter in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            )
        )
    return sorted(found)


def test_the_scan_finds_each_function_that_takes_the_naming_flag(tmp_path: Path) -> None:
    """The scan reports a function the flag is a parameter of, and nothing else."""
    package = tmp_path / "package"
    package.mkdir()
    (package / "names.py").write_text(
        "def display_suffix(serial, use_circuit_numbers):\n"
        "    return serial if use_circuit_numbers else None\n"
        "\n"
        "\n"
        "async def later(*, use_circuit_numbers):\n"
        "    return use_circuit_numbers\n",
        encoding="utf-8",
    )
    (package / "callers.py").write_text(
        "def build(options, resolve):\n"
        '    use_circuit_numbers = options.get("use_circuit_numbers", False)\n'
        "    return resolve(use_circuit_numbers=use_circuit_numbers)\n",
        encoding="utf-8",
    )

    assert naming_flag_functions(package) == ["names.display_suffix", "names.later"]


def test_the_rules_name_every_function_the_naming_flag_is_passed_to() -> None:
    """The rules say what the naming flag decides, so each function it reaches is in them.

    A circuit's own device and entities are named alike in both modes, and the
    rules say so. What the flag still decides elsewhere, such as the suffix of
    a charger's device name, is stated beside that claim, function by function,
    so a new use of the flag fails here until the rules account for it.
    """
    functions = naming_flag_functions(PACKAGE)
    assert functions, "nothing takes the naming flag, so this test reads nothing"

    rules = _read(RULES_DOC)
    unnamed = [function for function in functions if f"`{function}`" not in rules]

    assert unnamed == []


def test_the_rules_cite_only_names_their_modules_have() -> None:
    """A function or table the rules cite as `module.name` exists in that module."""
    cited = set(
        re.findall(rf"`({'|'.join(CITED_MODULES)})\.([A-Za-z_][A-Za-z0-9_]*)", _read(RULES_DOC))
    )
    cited = {(module, name) for module, name in cited if name != "py"}
    assert cited, "the rules cite no function by module, so this test reads nothing"

    missing = sorted(
        f"{module}.{name}"
        for module, name in cited
        if not hasattr(importlib.import_module(f"{span_panel.__name__}.{module}"), name)
    )

    assert missing == []


def _naming_option_labels() -> list[tuple[str, str]]:
    """Return each naming option's label as the setup form shows it: its words, then its example."""
    strings = json.loads(_read(PACKAGE / "strings.json"))
    labels: list[tuple[str, str]] = []
    for label in strings["selector"][ENTITY_NAMING_PATTERN]["options"].values():
        match = re.fullmatch(r"(?P<words>.+) \(e\.g\., (?P<example>.+)\)", label)
        assert match is not None, label
        labels.append((match["words"], match["example"]))
    return labels


@pytest.mark.parametrize(("words", "example"), _naming_option_labels())
def test_the_readme_names_each_naming_option_as_setup_shows_it(words: str, example: str) -> None:
    """A user reading the README finds the choice under the words the setup form uses."""
    naming = _section(_read(README), "### Entity Naming Pattern")

    assert f"**{words}**" in naming
    assert f"`sensor.{example}`" in naming


def test_the_readme_names_the_config_entry_version_a_downgrade_is_refused_across() -> None:
    """The troubleshooting row explains the refusal by the version the entry is at now."""
    row = next(
        line
        for line in _read(README).splitlines()
        if line.startswith("| **Setup fails after downgrading the integration**")
    )

    assert f"version {CURRENT_CONFIG_VERSION}" in row


def _enforced_minimum_home_assistant() -> str:
    """Return the oldest Home Assistant HACS installs this release on, as `year.month`.

    `hacs.json` holds a full version, and the README promises a release line
    "or later", which is true only of that line's first version.
    """
    minimum = json.loads(_read(HACS))["homeassistant"]
    assert isinstance(minimum, str), minimum
    match = re.fullmatch(r"(?P<line>\d{4}\.\d{1,2})\.0", minimum)
    assert match is not None, f"hacs.json must name a release line's first version: {minimum}"
    return match["line"]


def test_the_readme_requires_the_home_assistant_version_hacs_enforces() -> None:
    """Each place the README states the minimum names the one HACS refuses to install below.

    The README is what a user reads and `hacs.json` is what stops the install,
    so one raised without the other either turns away a version the README
    allows or installs on one the README rules out.
    """
    prose = " ".join(_read(README).split())
    stated = re.findall(r"Home Assistant(?:\]\([^)]*\))? (\d{4}\.\d{1,2}) or later", prose)
    assert stated, "the README states no minimum Home Assistant, so this test reads nothing"

    assert set(stated) == {_enforced_minimum_home_assistant()}


def test_agents_names_the_schema_library_services_import() -> None:
    """The service requirements name the library `vol` is, which the code imports."""
    tree = ast.parse(_read(PACKAGE / "services.py"))
    libraries = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.asname == "vol"
    ]
    assert len(libraries) == 1, libraries

    requirements = _section(_read(AGENTS), "## Service Registration Requirements")

    assert f"`{libraries[0]}`" in requirements
