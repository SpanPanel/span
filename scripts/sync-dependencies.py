#!/usr/bin/env python3
"""Synchronize dependency versions from manifest.json to CI workflow.

This script reads the dependency versions from custom_components/span_panel/manifest.json
and updates the corresponding sed commands in .github/workflows/ci.yml to match.

Used as a pre-commit hook to ensure CI workflow stays in sync with manifest versions.
"""

import json
import re
import sys
from pathlib import Path


def get_manifest_versions():
    """Extract dependency versions from manifest.json."""
    manifest_path = Path("custom_components/span_panel/manifest.json")

    if not manifest_path.exists():
        print(f"Error: {manifest_path} not found")
        return None

    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        requirements = manifest.get('requirements', [])
        versions = {}

        for req in requirements:
            if req.startswith('span-panel-api'):
                # Extract version from span-panel-api~=1.1.0
                match = re.search(r'span-panel-api[~=]+([0-9.]+)', req)
                if match:
                    versions['span-panel-api'] = match.group(1)
            elif req.startswith('ha-synthetic-sensors'):
                # Extract version from ha-synthetic-sensors~=1.0.8
                match = re.search(r'ha-synthetic-sensors[~=]+([0-9.]+)', req)
                if match:
                    versions['ha-synthetic-sensors'] = match.group(1)

        return versions

    except Exception as e:
        print(f"Error reading manifest.json: {e}")
        return None


def update_ci_workflow(versions):
    """Update the CI workflow with the specified versions."""
    ci_path = Path(".github/workflows/ci.yml")

    if not ci_path.exists():
        print(f"Error: {ci_path} not found")
        return False

    try:
        with open(ci_path, 'r') as f:
            content = f.read()

        original_content = content

        # Update span-panel-api version
        if 'span-panel-api' in versions:
            span_version = versions['span-panel-api']
            content = re.sub(
                r'span-panel-api = "\^[0-9.]+"',
                f'span-panel-api = "^{span_version}"',
                content
            )

        # Update ha-synthetic-sensors version
        if 'ha-synthetic-sensors' in versions:
            ha_version = versions['ha-synthetic-sensors']
            content = re.sub(
                r'ha-synthetic-sensors = "\^[0-9.]+"',
                f'ha-synthetic-sensors = "^{ha_version}"',
                content
            )

        # Check if changes were made
        if content != original_content:
            with open(ci_path, 'w') as f:
                f.write(content)
            return True

        return False

    except Exception as e:
        print(f"Error updating CI workflow: {e}")
        return False


def main():
    """Main function."""
    print("Checking dependency version sync between manifest.json and CI workflow...")

    # Get versions from manifest
    versions = get_manifest_versions()
    if not versions:
        print("Failed to read versions from manifest.json")
        sys.exit(1)

    print(f"Manifest versions: {versions}")

    # Update CI workflow
    changes_made = update_ci_workflow(versions)

    if changes_made:
        print("✅ CI workflow updated to match manifest.json versions")
        print("Please review the changes and commit them.")
        sys.exit(1)  # Exit with error to fail pre-commit
    else:
        print("✅ CI workflow already in sync with manifest.json")
        sys.exit(0)


if __name__ == "__main__":
    main()
