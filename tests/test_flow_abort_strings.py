"""Every reason a flow of this integration aborts with has a string, in every language.

Home Assistant shows an abort through the string its reason names, and a reason
with none reaches the user as its bare key. Nothing at run time notices: the
flow aborts as it should, and only the message is missing. So the reasons are
read from the source -- every `async_abort`, and every Core helper that aborts
on the flow's behalf -- and each is looked up where Home Assistant will look.

An abort that names another translation domain is left out, and so is one Core
words itself: `already_in_progress`, and the default of `async_update_and_abort`,
both come from the `homeassistant` domain. The check is monotone, so the reasons
the source is known to hold are pinned too, or a scan that found nothing would
pass.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import ModuleType

import pytest

from custom_components.span_panel import config_flow, repairs
from custom_components.span_panel.const import DOMAIN

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"

_LANGUAGE_FILES: dict[str, Path] = {
    "en": _COMPONENT / "strings.json",
    "es": _COMPONENT / "translations" / "es.json",
    "fr": _COMPONENT / "translations" / "fr.json",
    "ja": _COMPONENT / "translations" / "ja.json",
    "pt": _COMPONENT / "translations" / "pt.json",
}
"""English is read from `strings.json`, the source `translations/en.json` is generated from."""

_EXPLICIT_REASON = frozenset({"async_abort", "async_update_and_abort", "async_update_reload_and_abort"})
"""Calls that abort with the `reason` they are given.

The two `update` ones default to a string of Core's own, so only a `reason`
passed to them is this integration's to word.
"""

_DEFAULT_REASON: dict[str, tuple[str, str]] = {
    "_abort_if_unique_id_configured": ("error", "already_configured"),
    "_abort_if_unique_id_mismatch": ("reason", "unique_id_mismatch"),
}
"""Core's helpers that abort on the flow's behalf: the keyword naming the reason, and its default.

Their defaults are looked up in this integration's own strings.
"""


def _text(module: ModuleType, node: ast.expr) -> str:
    """The string `node` spells, or the module-level constant it names."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        value = getattr(module, node.id)
        assert isinstance(value, str), f"{module.__name__}:{node.lineno} {node.id} is not a string"
        return value
    raise AssertionError(
        f"{module.__name__}:{node.lineno} aborts with a reason this test cannot read: "
        f"{ast.unparse(node)}"
    )


def _reason(module: ModuleType, call: ast.Call) -> str | None:
    """The reason `call` aborts with in this integration's domain, or None if it does not."""
    if not isinstance(call.func, ast.Attribute):
        return None
    name = call.func.attr
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
    if "translation_domain" in keywords and _text(module, keywords["translation_domain"]) != DOMAIN:
        return None
    if name in _EXPLICIT_REASON:
        if "reason" in keywords:
            return _text(module, keywords["reason"])
        # A flow's `async_abort` takes its reason by keyword only. Called with a
        # positional argument it is the flow manager's, ending another flow by id.
        assert name != "async_abort" or call.args, (
            f"{module.__name__}:{call.lineno} aborts with no reason"
        )
        return None
    if name in _DEFAULT_REASON:
        keyword, default = _DEFAULT_REASON[name]
        return _text(module, keywords[keyword]) if keyword in keywords else default
    return None


def _abort_reasons(module: ModuleType) -> dict[str, int]:
    """Every reason `module` aborts a flow with, and the first line that does."""
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    reasons: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (reason := _reason(module, node)) is not None:
            reasons[reason] = min(node.lineno, reasons.get(reason, node.lineno))
    return reasons


def _fixable_issue_keys() -> set[str]:
    """The translation key of every issue the integration raises as fixable."""
    keys: set[str] = set()
    for path in sorted(_COMPONENT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            fixable = keywords.get("is_fixable")
            key = keywords.get("translation_key")
            if (
                isinstance(fixable, ast.Constant)
                and fixable.value is True
                and isinstance(key, ast.Constant)
                and isinstance(key.value, str)
            ):
                keys.add(key.value)
    return keys


def _strings(language: str) -> dict[str, object]:
    strings = json.loads(_LANGUAGE_FILES[language].read_text(encoding="utf-8"))
    assert isinstance(strings, dict)
    return strings


def _section(node: object, *path: str) -> dict[str, object]:
    """The object at `path` under `node`, or an empty one where the file has none."""
    for key in path:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


_CONFIG_FLOW_REASONS = _abort_reasons(config_flow)
_REPAIR_FLOW_REASONS = _abort_reasons(repairs)
_FIXABLE_ISSUES = _fixable_issue_keys()


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
    } <= _CONFIG_FLOW_REASONS.keys()
    assert {"broker_port_unreachable", "ca_leaf_mismatch", "ca_unreadable", "entry_gone"} <= (
        _REPAIR_FLOW_REASONS.keys()
    )
    assert {"panel_ca_changed", "panel_ca_unusable"} <= _FIXABLE_ISSUES


@pytest.mark.parametrize("language", sorted(_LANGUAGE_FILES))
def test_every_config_flow_abort_has_a_string(language: str) -> None:
    aborts = _section(_strings(language), "config", "abort")
    missing = sorted(
        f"{reason} (config_flow.py:{line})"
        for reason, line in _CONFIG_FLOW_REASONS.items()
        if not aborts.get(reason)
    )
    assert not missing, f"{language} has no config.abort string for {missing}"


@pytest.mark.parametrize("language", sorted(_LANGUAGE_FILES))
def test_every_repair_flow_abort_has_a_string_under_every_fixable_issue(language: str) -> None:
    """Under each fixable issue's own `fix_flow`, which is where a repair flow's strings live.

    `repairs.async_create_fix_flow` hands every fixable issue the same flow, so
    each issue needs every reason that flow can abort with.
    """
    issues = _section(_strings(language), "issues")
    missing = sorted(
        f"issues.{issue}.fix_flow.abort.{reason} (repairs.py:{line})"
        for issue in _FIXABLE_ISSUES
        for reason, line in _REPAIR_FLOW_REASONS.items()
        if not _section(issues, issue, "fix_flow", "abort").get(reason)
    )
    assert not missing, f"{language} is missing {missing}"
