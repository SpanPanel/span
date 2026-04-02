"""Sync and validate translation files against strings.json.

Generates translations/en.json from strings.json and validates that all
other translation files are complete (no missing keys) and contain no
orphaned keys that don't exist in strings.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"
STRINGS_PATH = COMPONENT_DIR / "strings.json"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"
EN_PATH = TRANSLATIONS_DIR / "en.json"


def collect_leaf_keys(obj: dict | str, prefix: str = "") -> set[str]:
    """Recursively collect dot-delimited paths to leaf (non-dict) values."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                keys.update(collect_leaf_keys(value, full))
            else:
                keys.add(full)
    return keys


def collect_all_keys(obj: dict | str, prefix: str = "") -> set[str]:
    """Recursively collect all dot-delimited key paths from a nested dict."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            full = f"{prefix}.{key}" if prefix else key
            keys.add(full)
            keys.update(collect_all_keys(value, full))
    return keys


def find_orphaned_keys(source_keys: set[str], translation: dict) -> list[str]:
    """Return keys present in the translation but absent from the source."""
    translation_keys = collect_all_keys(translation)
    return sorted(translation_keys - source_keys)


def find_missing_keys(source_leaf_keys: set[str], translation: dict) -> list[str]:
    """Return leaf keys present in the source but absent from the translation."""
    translation_leaf_keys = collect_leaf_keys(translation)
    return sorted(source_leaf_keys - translation_leaf_keys)


def sync_en(source: dict) -> bool:
    """Write strings.json content to translations/en.json. Return True if changed."""
    TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    new_content = json.dumps(source, indent=2, ensure_ascii=False) + "\n"

    if EN_PATH.exists():
        existing = EN_PATH.read_text(encoding="utf-8")
        if existing == new_content:
            return False

    EN_PATH.write_text(new_content, encoding="utf-8")
    return True


def validate_translations(
    source_all_keys: set[str], source_leaf_keys: set[str]
) -> list[str]:
    """Validate all non-en translation files. Return list of error messages."""
    errors: list[str] = []

    for lang_file in sorted(TRANSLATIONS_DIR.glob("*.json")):
        if lang_file.name == "en.json":
            continue

        lang = lang_file.stem
        try:
            translation = json.loads(lang_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{lang}: invalid JSON — {exc}")
            continue

        orphaned = find_orphaned_keys(source_all_keys, translation)
        if orphaned:
            errors.append(
                f"{lang}: {len(orphaned)} orphaned key(s) not in strings.json:\n"
                + "\n".join(f"  - {k}" for k in orphaned)
            )

        missing = find_missing_keys(source_leaf_keys, translation)
        if missing:
            errors.append(
                f"{lang}: {len(missing)} missing key(s) from strings.json:\n"
                + "\n".join(f"  - {k}" for k in missing)
            )

    return errors


def main() -> int:
    if not STRINGS_PATH.exists():
        print(f"ERROR: {STRINGS_PATH} not found", file=sys.stderr)
        return 1

    source = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    source_all_keys = collect_all_keys(source)
    source_leaf_keys = collect_leaf_keys(source)

    changed = sync_en(source)
    if changed:
        print(f"Updated {EN_PATH.relative_to(Path.cwd())}")

    errors = validate_translations(source_all_keys, source_leaf_keys)
    if errors:
        print("Translation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    print("Translations OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
