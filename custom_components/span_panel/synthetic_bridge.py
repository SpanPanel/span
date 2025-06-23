"""Bridge to ha-synthetic-sensors for SPAN Panel synthetic sensors."""

import logging
import os
from pathlib import Path
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import yaml

_LOGGER = logging.getLogger(__name__)


class SyntheticSensorsBridge:
    """Generic bridge to manage ha-synthetic-sensors YAML configuration files."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        config_dir: str | None = None,
        device_serial: str | None = None,
    ):
        """Initialize the synthetic sensors bridge.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for the integration
            config_dir: Optional directory to store config files. If None, uses integration directory.
            device_serial: Optional device serial number for unique ID generation

        """
        self._hass = hass
        self._config_entry = config_entry
        self._device_serial = device_serial

        # Use provided config_dir or default to integration directory
        if config_dir is not None:
            # Use provided directory
            self._config_file = Path(config_dir) / "span-ha-synthetic.yaml"
        else:
            # Default: place YAML file in the integration directory so it gets cleaned up
            # when the integration is deleted
            hass_config_dir = hass.config.config_dir
            if not hass_config_dir:
                raise ValueError("Home Assistant config directory is not available")
            integration_dir = Path(hass_config_dir) / "custom_components" / "span_panel"
            self._config_file = integration_dir / "span-ha-synthetic.yaml"

    @property
    def config_file_path(self) -> Path:
        """Get the path to the synthetic sensors config file."""
        return self._config_file

    def get_unique_id_prefix(self) -> str:
        """Get the appropriate unique ID prefix for SPAN synthetic sensors.

        This generates a prefix that matches the v1.0.10 format for compatibility,
        using the device serial number when available.

        Returns:
            Unique ID prefix (e.g., "span_ABC123_synthetic")

        """
        if not self._device_serial:
            # Fallback to integration-based prefix if no device serial
            return f"span_{self._config_entry.entry_id}_synthetic"

        return f"span_{self._device_serial}_synthetic"

    async def remove_config(self) -> None:
        """Remove the configuration file."""
        try:
            await self._hass.async_add_executor_job(self._remove_config_file)
            _LOGGER.info("Removed synthetic sensors config: %s", self._config_file)
        except Exception as e:
            _LOGGER.error("Failed to remove synthetic sensors config: %s", e)

    async def validate_config(self) -> bool:
        """Validate the generated YAML configuration."""
        try:
            config = await self._hass.async_add_executor_job(self._read_config_file)
            if config is None:
                return False

            # Basic validation
            if "version" not in config or "sensors" not in config:
                return False

            _LOGGER.debug("Synthetic sensors config validation passed")
            return True

        except Exception as e:
            _LOGGER.error("Synthetic sensors config validation failed: %s", e)
            return False

    def _write_config_file(self, yaml_content: str) -> None:
        """Write YAML content to config file (blocking, for executor)."""
        # Ensure the directory exists
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_file, "w", encoding="utf-8") as file:
            file.write(yaml_content)

    def _remove_config_file(self) -> None:
        """Remove config file (blocking, for executor)."""
        if self._config_file.exists():
            os.remove(self._config_file)

    def _read_config_file(self) -> dict[str, Any] | None:
        """Read and parse config file (blocking, for executor)."""
        if not self._config_file.exists():
            return None

        try:
            with open(self._config_file, encoding="utf-8") as file:
                content = yaml.safe_load(file)
                # Return None for any non-dict content (including None, empty, malformed)
                return cast(dict[str, Any], content) if isinstance(content, dict) else None
        except (OSError, yaml.YAMLError):
            return None
