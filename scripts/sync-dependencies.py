#!/usr/bin/env python3
"""Synchronize dependency versions from manifest.json to pyproject.toml.

This script reads the dependency versions from custom_components/span_panel/manifest.json
and updates the corresponding dependencies in pyproject.toml to match.

Used as a pre-commit hook to ensure pyproject.toml stays in sync with manifest versions.
"""

import json
from pathlib import Path
import re
import sys


def get_manifest_versions():
    """Extract dependency versions from manifest.json."""
    manifest_path = Path("custom_components/span_panel/manifest.json")

    if not manifest_path.exists():
        return None

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)

        requirements = manifest.get("requirements", [])
        versions = {}

        for req in requirements:
            if req.startswith("span-panel-api"):
                # Extract full specifier (e.g. ==2.3.0, >=2.0.0, ~=1.1.0)
                match = re.search(r"span-panel-api([>~=!]+[0-9.]+)", req)
                if match:
                    versions["span-panel-api"] = match.group(1)
            elif req.startswith("ha-synthetic-sensors"):
                # Extract full specifier (e.g. >=1.0.8, ~=1.0.8)
                match = re.search(r"ha-synthetic-sensors([>~=!]+[0-9.]+)", req)
                if match:
                    versions["ha-synthetic-sensors"] = match.group(1)

        return versions

    except Exception:
        return None


def update_pyproject_dependencies(versions):
    """Update pyproject.toml dependencies with manifest versions."""
    pyproject_path = Path("pyproject.toml")

    if not pyproject_path.exists():
        return False

    try:
        with open(pyproject_path) as f:
            content = f.read()

        original_content = content

        # Update span-panel-api version in [project] dependencies
        if "span-panel-api" in versions:
            span_spec = versions["span-panel-api"]
            content = re.sub(
                r'"span-panel-api[><=~!]+[0-9.]+"',
                f'"span-panel-api{span_spec}"',
                content,
            )

        # Update ha-synthetic-sensors version in [project] dependencies
        if "ha-synthetic-sensors" in versions:
            ha_spec = versions["ha-synthetic-sensors"]
            content = re.sub(
                r'"ha-synthetic-sensors[><=~!]+[0-9.]+"',
                f'"ha-synthetic-sensors{ha_spec}"',
                content,
            )

        if content != original_content:
            with open(pyproject_path, "w") as f:
                f.write(content)
            return True

        return False

    except Exception:
        return False


def main():
    """Main function."""
    versions = get_manifest_versions()
    if not versions:
        sys.exit(1)

    changes_made = update_pyproject_dependencies(versions)

    if changes_made:
        sys.exit(1)  # Exit with error to fail pre-commit
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
