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
    """Get HomeAssistant's dependency pins from poetry show."""
    try:
        result = subprocess.run(
            ["poetry", "show", "homeassistant", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        ha_info = json.loads(result.stdout)
        return {dep["name"]: dep["version"] for dep in ha_info.get("dependencies", [])}
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        print(f"Error getting HA dependencies: {e}")
        return {}


def update_pyproject_constraints(ha_deps):
    """Update pyproject.toml with compatible constraints."""
    pyproject_path = Path("pyproject.toml")

    if not pyproject_path.exists():
        print("pyproject.toml not found")
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

        # Update constraints for deps we care about
        deps = (
            pyproject.setdefault("tool", {}).setdefault("poetry", {}).setdefault("dependencies", {})
        )

        updated = []
        for dep_name, version in ha_deps.items():
            if dep_name in exact_match_deps and dep_name in deps:
                old_constraint = deps[dep_name]
                new_constraint = f"^{version}"  # Allow patch updates
                if old_constraint != new_constraint:
                    deps[dep_name] = new_constraint
                    updated.append(f"{dep_name}: {old_constraint} -> {new_constraint}")

        if updated:
            with open(pyproject_path, "w") as f:
                toml.dump(pyproject, f)
            print("Updated constraints:")
            for update in updated:
                print(f"  {update}")
            return True
        else:
            print("No updates needed")
            return False

    except Exception as e:
        print(f"Error updating pyproject.toml: {e}")
        return False


def main():
    print("Fetching HomeAssistant dependency pins...")
    ha_deps = get_ha_dependencies()

    if not ha_deps:
        print("Could not fetch HA dependencies")
        sys.exit(1)

    print(f"Found {len(ha_deps)} HA dependencies")

    if update_pyproject_constraints(ha_deps):
        print("\nRun 'poetry lock --no-update' to update the lock file")


if __name__ == "__main__":
    main()
