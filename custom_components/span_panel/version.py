"""Version for span."""

import os
import json
from typing import Any, cast


def get_version() -> str:
    """Return the version from manifest.json."""
    manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest: dict[str, Any] = json.load(f)
    return cast(str, manifest["version"])


__version__ = get_version()
