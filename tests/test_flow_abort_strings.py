"""Every reason a flow of this integration aborts with has a string, in every language.

Home Assistant shows an abort through the string its reason names, and a reason
with none reaches the user as its bare key. Nothing at run time notices: the
flow aborts as it should, and only the message is missing. So the reasons are
read from the source of every module in the integration -- every
`async_abort`, and every Core helper that aborts on the flow's behalf -- and
each is looked up where Home Assistant will look for it. That place depends on
the kind of flow, so each abort is placed by the Home Assistant flow its class
descends from:

- a config flow reads `config.abort`;
- an options flow reads `options.abort`;
- a repair flow reads `issues.<key>.fix_flow.abort`, for each issue it fixes.
  Which issues those are is asked of `repairs.async_create_fix_flow` itself,
  with an id built the way the issue's own module builds it.

An abort that names another translation domain is left out, and so is one Core
words itself: `already_in_progress`, and the default of `async_update_and_abort`,
both come from the `homeassistant` domain.

Two ways of aborting would go round the scan, so neither may appear at all:
`AbortFlow`, which can be raised from any helper a flow calls, and
`_async_abort_entries_match`, whose reason the scan does not read. The checks
are monotone, so the reasons the flows are known to have are pinned too, or a
scan that found nothing would pass.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Final

from homeassistant.core import HomeAssistant
import pytest

from custom_components.span_panel import repairs
from custom_components.span_panel.const import DOMAIN

_PACKAGE: Final = "custom_components.span_panel"
_COMPONENT: Final = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"

_LANGUAGE_FILES: Final[dict[str, Path]] = {
    "en": _COMPONENT / "strings.json",
    "es": _COMPONENT / "translations" / "es.json",
    "fr": _COMPONENT / "translations" / "fr.json",
    "ja": _COMPONENT / "translations" / "ja.json",
    "pt": _COMPONENT / "translations" / "pt.json",
}
"""English is read from `strings.json`, the source `translations/en.json` is generated from."""

_FLOW_KINDS: Final[dict[str, str]] = {
    "ConfigFlow": "config",
    "OptionsFlow": "options",
    "OptionsFlowWithReload": "options",
    "RepairsFlow": "repairs",
    "ConfirmRepairFlow": "repairs",
}
"""Home Assistant's flow bases, by the kind of flow they make, which decides where its strings live."""

_FORBIDDEN: Final = frozenset({"AbortFlow", "_async_abort_entries_match"})

_EXPLICIT_REASON: Final = frozenset(
    {"async_abort", "async_update_and_abort", "async_update_reload_and_abort"}
)
"""Calls that abort with the `reason` they are given.

The two `update` ones default to a string of Core's own, so only a `reason`
passed to them is this integration's to word.
"""

_DEFAULT_REASON: Final[dict[str, tuple[str, str]]] = {
    "_abort_if_unique_id_configured": ("error", "already_configured"),
    "_abort_if_unique_id_mismatch": ("reason", "unique_id_mismatch"),
}
"""Core's helpers that abort on the flow's behalf: the keyword naming the reason, and its default.

Their defaults are looked up in this integration's own strings.
"""

_PROBE_ENTRY_ID: Final = "entry-probe"


@dataclass(frozen=True, slots=True)
class _Source:
    """One module of the integration, parsed."""

    name: str
    path: Path
    tree: ast.Module

    def module(self) -> ModuleType:
        return importlib.import_module(self.name)

    def where(self, node: ast.AST) -> str:
        return f"{self.path.name}:{getattr(node, 'lineno', '?')}"


@dataclass(frozen=True, slots=True)
class _Abort:
    """One reason one flow class aborts with."""

    flow_class: str
    reason: str
    where: str


def _sources() -> tuple[_Source, ...]:
    sources: list[_Source] = []
    for path in sorted(_COMPONENT.rglob("*.py")):
        parts = path.relative_to(_COMPONENT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        sources.append(
            _Source(
                ".".join((_PACKAGE, *parts)),
                path,
                ast.parse(path.read_text(encoding="utf-8")),
            )
        )
    return tuple(sources)


_SOURCES: Final = _sources()


def _base_name(base: ast.expr) -> str | None:
    if isinstance(base, ast.Subscript):
        base = base.value
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


type _ClassKey = tuple[str, str]
"""A class the integration defines: its module, and its name."""


def _class_bases() -> dict[_ClassKey, tuple[str, ...]]:
    """Every class the integration defines, with the names of its bases."""
    return {
        (source.name, node.name): tuple(
            name for base in node.bases if (name := _base_name(base)) is not None
        )
        for source in _SOURCES
        for node in ast.walk(source.tree)
        if isinstance(node, ast.ClassDef)
    }


_BASES: Final = _class_bases()


def _defining(name: str, module: str) -> list[_ClassKey]:
    """The integration's classes a base named `name` can be, from `module`: its own first."""
    if (module, name) in _BASES:
        return [(module, name)]
    return [key for key in _BASES if key[1] == name]


def _flow_kind(key: _ClassKey, seen: frozenset[_ClassKey] = frozenset()) -> str | None:
    """The kind of Home Assistant flow a class descends from, through the integration's own classes."""
    for base in _BASES.get(key, ()):
        if base in _FLOW_KINDS:
            return _FLOW_KINDS[base]
        for parent in _defining(base, key[0]):
            if parent not in seen and (kind := _flow_kind(parent, seen | {key})) is not None:
                return kind
    return None


def _calls(tree: ast.Module) -> Iterator[tuple[str | None, ast.Call]]:
    """Every call in `tree`, with the innermost class it is made in."""

    def visit(node: ast.AST, owner: str | None) -> Iterator[tuple[str | None, ast.Call]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                yield owner, child
            yield from visit(child, owner)

    yield from visit(tree, None)


def _text(source: _Source, node: ast.expr) -> str:
    """The string `node` spells, or the module-level constant it names."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        value = getattr(source.module(), node.id)
        assert isinstance(value, str), f"{source.where(node)} {node.id} is not a string"
        return value
    raise AssertionError(f"{source.where(node)} names a reason this test cannot read: {ast.unparse(node)}")


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}


def _reason(source: _Source, call: ast.Call) -> str | None:
    """The reason `call` aborts with in this integration's domain, or None if it does not."""
    if not isinstance(call.func, ast.Attribute):
        return None
    name = call.func.attr
    keywords = _keywords(call)
    if "translation_domain" in keywords and _text(source, keywords["translation_domain"]) != DOMAIN:
        return None
    if name in _EXPLICIT_REASON:
        if "reason" in keywords:
            return _text(source, keywords["reason"])
        # A flow's `async_abort` takes its reason by keyword only. Called with a
        # positional argument it is the flow manager's, ending another flow by id.
        assert name != "async_abort" or call.args, f"{source.where(call)} aborts with no reason"
        return None
    if name in _DEFAULT_REASON:
        keyword, default = _DEFAULT_REASON[name]
        return _text(source, keywords[keyword]) if keyword in keywords else default
    return None


def _scan() -> tuple[dict[str, list[_Abort]], list[str]]:
    """Every abort by the kind of flow it is in, and every abort that is in no flow at all."""
    by_kind: dict[str, list[_Abort]] = {}
    unplaced: list[str] = []
    for source in _SOURCES:
        for owner, call in _calls(source.tree):
            reason = _reason(source, call)
            if reason is None:
                continue
            kind = None if owner is None else _flow_kind((source.name, owner))
            if owner is None or kind is None:
                unplaced.append(f"{reason} ({source.where(call)}, in {owner or 'no class'})")
                continue
            by_kind.setdefault(kind, []).append(_Abort(owner, reason, source.where(call)))
    return by_kind, unplaced


_ABORTS, _UNPLACED = _scan()


def _fixable_issues() -> list[tuple[str, str]]:
    """Every issue the integration raises as fixable: its translation key, and an id for it.

    Every issue is raised through `entry_issues.async_create_entry_issue`, which
    a test in `test_entry_issues` holds the integration to, so those calls are
    the ones read. The id is built by the function the raising module builds
    it with, for a made-up entry, so it reaches `async_create_fix_flow` as a
    real one would.
    """
    issues: list[tuple[str, str]] = []
    for source in _SOURCES:
        for _, call in _calls(source.tree):
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "async_create_entry_issue":
                continue
            keywords = _keywords(call)
            fixable = keywords.get("is_fixable")
            if not (isinstance(fixable, ast.Constant) and fixable.value is True):
                continue
            key = keywords.get("translation_key")
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
                f"{source.where(call)} raises a fixable issue with a key this test cannot read"
            )
            id_expr = call.args[2] if len(call.args) > 2 else keywords.get("issue_id")
            assert (
                isinstance(id_expr, ast.Call)
                and isinstance(id_expr.func, ast.Name)
                and len(id_expr.args) == 1
            ), f"{source.where(call)} builds its issue id in a way this test cannot repeat"
            build = getattr(source.module(), id_expr.func.id)
            issue_id = build(_PROBE_ENTRY_ID)
            assert isinstance(issue_id, str)
            issues.append((key.value, issue_id))
    return issues


async def _issues_by_fix_flow(hass: HomeAssistant) -> dict[str, set[str]]:
    """Each repair flow class, with the translation keys of the issues it fixes."""
    fixes: dict[str, set[str]] = {}
    for key, issue_id in _fixable_issues():
        flow = await repairs.async_create_fix_flow(hass, issue_id, None)
        fixes.setdefault(type(flow).__name__, set()).add(key)
    return fixes


def _strings(language: str) -> dict[str, object]:
    strings = json.loads(_LANGUAGE_FILES[language].read_text(encoding="utf-8"))
    assert isinstance(strings, dict)
    return strings


def _section(node: object, *path: str) -> dict[str, object]:
    """The object at `path` under `node`, or an empty one where the file has none."""
    for key in path:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _reasons(kind: str) -> set[str]:
    return {abort.reason for abort in _ABORTS.get(kind, [])}


def _names(node: ast.AST) -> list[str]:
    """The names `node` uses or imports."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Import | ast.ImportFrom):
        return [alias.name.rsplit(".", 1)[-1] for alias in node.names]
    return []


def _forbidden_uses(source: _Source) -> list[str]:
    """Every use or import of a name in `_FORBIDDEN` in `source`."""
    return [
        f"{source.where(node)} {name}"
        for node in ast.walk(source.tree)
        for name in _names(node)
        if name in _FORBIDDEN
    ]


def test_no_flow_aborts_where_this_scan_cannot_follow() -> None:
    """Neither `AbortFlow` nor `_async_abort_entries_match`, and no abort outside a flow class."""
    forbidden = sorted(use for source in _SOURCES for use in _forbidden_uses(source))
    assert not forbidden, f"aborts the scan cannot read: {forbidden}"
    assert not _UNPLACED, f"aborts in no flow this test can place: {_UNPLACED}"


def test_the_scan_finds_the_aborts_the_flows_are_known_to_have() -> None:
    """The checks below pass for any subset, the empty one included."""
    assert {
        "already_configured",
        "not_ipv4_address",
        "not_span_panel",
        "panel_firmware_flat",
        "reauth_failed",
        "reauth_successful",
        "unique_id_mismatch",
    } <= _reasons("config")
    assert {"broker_port_unreachable", "ca_leaf_mismatch", "ca_unreadable", "entry_gone"} <= (
        _reasons("repairs")
    )


@pytest.mark.parametrize("language", sorted(_LANGUAGE_FILES))
@pytest.mark.parametrize("kind", ["config", "options"])
def test_every_config_and_options_flow_abort_has_a_string(kind: str, language: str) -> None:
    aborts = _section(_strings(language), kind, "abort")
    missing = sorted(
        f"{abort.reason} ({abort.where})"
        for abort in _ABORTS.get(kind, [])
        if not aborts.get(abort.reason)
    )
    assert not missing, f"{language} has no {kind}.abort string for {missing}"


async def test_the_fix_flows_are_found_for_the_issues_known_to_be_fixable(
    hass: HomeAssistant,
) -> None:
    """The repair check below passes for any subset too."""
    fixes = await _issues_by_fix_flow(hass)
    assert {"panel_ca_changed", "panel_ca_unusable"} <= fixes.get("PanelCAChangedRepairFlow", set())


@pytest.mark.parametrize("language", sorted(_LANGUAGE_FILES))
async def test_every_repair_flow_abort_has_a_string_under_each_issue_it_fixes(
    hass: HomeAssistant, language: str
) -> None:
    """Under each fixed issue's own `fix_flow`, which is where a repair flow's strings live."""
    issues = _section(_strings(language), "issues")
    fixes = await _issues_by_fix_flow(hass)
    missing = sorted(
        f"issues.{key}.fix_flow.abort.{abort.reason} ({abort.where})"
        for abort in _ABORTS.get("repairs", [])
        for key in fixes.get(abort.flow_class, set())
        if not _section(issues, key, "fix_flow", "abort").get(abort.reason)
    )
    assert not missing, f"{language} is missing {missing}"
    unfixed = sorted({abort.flow_class for abort in _ABORTS.get("repairs", [])} - fixes.keys())
    assert not unfixed, f"repair flows no fixable issue reaches: {unfixed}"
