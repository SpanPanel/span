#!/usr/bin/env python3
"""Sync dependency versions with HomeAssistant's pins.
Run this periodically to keep your pyproject.toml aligned with HA.
"""

import json
from pathlib import Path
import subprocess
import sys

import toml


def get_ha_dependencies():
    """Get HomeAssistant's dependency pins from uv pip show."""
    try:
        result = subprocess.run(
            ["uv", "pip", "show", "homeassistant", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        ha_info = json.loads(result.stdout)
        return {dep["name"]: dep["version"] for dep in ha_info.get("dependencies", [])}
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return {}


def update_pyproject_constraints(ha_deps):
    """Update pyproject.toml with compatible constraints."""
    pyproject_path = Path("pyproject.toml")

    if not pyproject_path.exists():
        return False

    try:
        with open(pyproject_path) as f:
            pyproject = toml.load(f)

        # Dependencies that should match HA exactly
        exact_match_deps = {
            "httpx",
            "voluptuous",
            "aiohttp",
            "yarl",
            "typing-extensions",
            "requests",
            "cryptography",
            "pyjwt",
            "pyyaml",
        }

        deps = pyproject.get("project", {}).get("dependencies", [])

        updated = []
        new_deps = []
        for dep_str in deps:
            dep_name = dep_str.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("!=")[0].strip()
            if dep_name.lower() in exact_match_deps and dep_name.lower() in {d.lower() for d in ha_deps}:
                version = ha_deps.get(dep_name) or ha_deps.get(dep_name.lower())
                if version:
                    new_constraint = f"{dep_name}>={version}"
                    if dep_str != new_constraint:
                        updated.append(f"{dep_name}: {dep_str} -> {new_constraint}")
                        new_deps.append(new_constraint)
                        continue
            new_deps.append(dep_str)

        if updated:
            pyproject["project"]["dependencies"] = new_deps
            with open(pyproject_path, "w") as f:
                toml.dump(pyproject, f)
            return True
        else:
            return False

    except Exception:
        return False


def main():
    ha_deps = get_ha_dependencies()

    if not ha_deps:
        sys.exit(1)


    if update_pyproject_constraints(ha_deps):
        pass


if __name__ == "__main__":
    main()
