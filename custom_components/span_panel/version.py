"""Version for span."""

import json
import os
from typing import Any, cast

from homeassistant.core import HomeAssistant


def get_version() -> str:
    """Return the version from manifest.json."""
    manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest: dict[str, Any] = json.load(f)
    return cast(str, manifest["version"])


async def async_get_version(hass: HomeAssistant) -> str:
    """Return the version from manifest.json using async file operations."""
    manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")

    def _read_manifest() -> dict[str, Any]:
        with open(manifest_path, encoding="utf-8") as f:
            return cast(dict[str, Any], json.load(f))

    manifest = await hass.async_add_executor_job(_read_manifest)
    return cast(str, manifest["version"])


__version__ = get_version()
