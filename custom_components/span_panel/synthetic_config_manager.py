"""Generic configuration manager for synthetic sensors with CRUD operations."""

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from homeassistant.core import HomeAssistant
import yaml

_LOGGER = logging.getLogger(__name__)


class SyntheticConfigManager:
    """Centralized manager for synthetic sensor configurations across all devices/panels."""

    _instances: dict[str, "SyntheticConfigManager"] = {}
    _lock = asyncio.Lock()

    def __init__(
        self,
        hass: HomeAssistant,
        config_filename: str = "synthetic_sensors.yaml",
        config_dir: str | None = None,
    ):
        """Initialize the centralized config manager.

        Args:
            hass: Home Assistant instance
            config_filename: Name of the config file to manage
            config_dir: Optional custom directory for config file (for tests)

        """
        self._hass = hass

        if config_dir is not None:
            # Use custom directory (for tests)
            self._config_file = Path(config_dir) / config_filename
        else:
            # Use default HA config directory
            self._config_file = (
                Path(hass.config.config_dir) / "custom_components" / "span_panel" / config_filename
            )

        self._file_lock = asyncio.Lock()

    @classmethod
    async def get_instance(
        cls, hass: HomeAssistant, config_filename: str = "synthetic_sensors.yaml"
    ) -> "SyntheticConfigManager":
        """Get singleton instance of the config manager for a specific config file."""
        async with cls._lock:
            if config_filename not in cls._instances:
                cls._instances[config_filename] = cls(hass, config_filename)
            return cls._instances[config_filename]

    async def create_sensor(
        self, device_id: str, sensor_key: str, sensor_config: dict[str, Any]
    ) -> None:
        """Create a new sensor entry for a specific device.

        Args:
            device_id: Device identifier (e.g., panel serial number)
            sensor_key: Sensor key (e.g., "solar_inverter_instant_power")
            sensor_config: Sensor configuration dictionary

        """
        async with self._file_lock:
            config = await self._read_config()

            # Ensure sensors section exists
            if "sensors" not in config:
                config["sensors"] = {}

            # Add device_identifier to the sensor config
            sensor_config_with_device = sensor_config.copy()
            sensor_config_with_device["device_identifier"] = f"span_panel_{device_id}"

            # Use the original sensor key (no device scoping)
            config["sensors"][sensor_key] = sensor_config_with_device

            await self._write_config(config)
            _LOGGER.debug("Created sensor %s for device %s", sensor_key, device_id)

    async def read_sensor(self, device_id: str, sensor_key: str) -> dict[str, Any] | None:
        """Read a sensor configuration for a specific device.

        Args:
            device_id: Device identifier
            sensor_key: Sensor key

        Returns:
            Sensor configuration or None if not found

        """
        async with self._file_lock:
            config = await self._read_config()
            sensor_config = config.get("sensors", {}).get(sensor_key)
            if sensor_config is None:
                return None

            # Check if this sensor belongs to the requested device
            expected_device_id = f"span_panel_{device_id}"
            if sensor_config.get("device_identifier") != expected_device_id:
                return None

            return cast(dict[str, Any], sensor_config) if isinstance(sensor_config, dict) else None

    async def update_sensor(
        self, device_id: str, sensor_key: str, sensor_config: dict[str, Any]
    ) -> bool:
        """Update an existing sensor configuration for a specific device.

        Args:
            device_id: Device identifier
            sensor_key: Sensor key
            sensor_config: Updated sensor configuration

        Returns:
            True if sensor was updated, False if not found

        """
        async with self._file_lock:
            config = await self._read_config()

            if "sensors" not in config or sensor_key not in config["sensors"]:
                return False

            # Check if this sensor belongs to the requested device
            existing_sensor = config["sensors"][sensor_key]
            expected_device_id = f"span_panel_{device_id}"
            if existing_sensor.get("device_identifier") != expected_device_id:
                return False

            # Add device_identifier to the updated config
            sensor_config_with_device = sensor_config.copy()
            sensor_config_with_device["device_identifier"] = expected_device_id

            config["sensors"][sensor_key] = sensor_config_with_device
            await self._write_config(config)
            _LOGGER.debug("Updated sensor %s for device %s", sensor_key, device_id)
            return True

    async def delete_sensor(self, device_id: str, sensor_key: str) -> bool:
        """Delete a sensor configuration for a specific device.

        Args:
            device_id: Device identifier
            sensor_key: Sensor key

        Returns:
            True if sensor was deleted, False if not found

        """
        async with self._file_lock:
            config = await self._read_config()

            if "sensors" not in config or sensor_key not in config["sensors"]:
                return False

            # Check if this sensor belongs to the requested device
            existing_sensor = config["sensors"][sensor_key]
            expected_device_id = f"span_panel_{device_id}"
            if existing_sensor.get("device_identifier") != expected_device_id:
                return False

            del config["sensors"][sensor_key]
            await self._write_config(config)
            _LOGGER.debug("Deleted sensor %s for device %s", sensor_key, device_id)
            return True

    async def delete_all_device_sensors(self, device_id: str) -> int:
        """Delete all sensors for a specific device.

        Args:
            device_id: Device identifier

        Returns:
            Number of sensors deleted

        """
        expected_device_id = f"span_panel_{device_id}"

        async with self._file_lock:
            config = await self._read_config()

            if "sensors" not in config:
                return 0

            # Find all sensors for this device
            sensors_to_delete = [
                key
                for key, sensor_config in config["sensors"].items()
                if sensor_config.get("device_identifier") == expected_device_id
            ]

            # Delete them
            for key in sensors_to_delete:
                del config["sensors"][key]

            # If no sensors remain, remove the file entirely
            if not config["sensors"]:
                if self._config_file.exists():
                    await self._hass.async_add_executor_job(self._config_file.unlink)
                    _LOGGER.debug("Removed empty config file %s", self._config_file)
            else:
                await self._write_config(config)

            _LOGGER.info("Deleted %d sensors for device %s", len(sensors_to_delete), device_id)
            return len(sensors_to_delete)

    async def list_device_sensors(self, device_id: str) -> dict[str, dict[str, Any]]:
        """List all sensors for a specific device.

        Args:
            device_id: Device identifier

        Returns:
            Dictionary of sensor_key -> sensor_config for this device

        """
        expected_device_id = f"span_panel_{device_id}"

        async with self._file_lock:
            config = await self._read_config()

            if "sensors" not in config:
                return {}

            # Find all sensors for this device
            device_sensors = {}
            for sensor_key, sensor_config in config["sensors"].items():
                if sensor_config.get("device_identifier") == expected_device_id:
                    device_sensors[sensor_key] = sensor_config

            return device_sensors

    async def get_config_file_path(self) -> Path:
        """Get the path to the configuration file."""
        return self._config_file

    async def config_file_exists(self) -> bool:
        """Check if the configuration file exists."""
        return self._config_file.exists()

    async def read_config(self) -> dict[str, Any]:
        """Read the configuration file (public method)."""
        return await self._read_config()

    async def _read_config(self) -> dict[str, Any]:
        """Read the configuration file."""
        if not self._config_file.exists():
            return {"version": "1.0", "sensors": {}}

        try:

            def _read_yaml() -> dict[str, Any]:
                with open(self._config_file, encoding="utf-8") as f:
                    result = yaml.safe_load(f)
                    return (
                        cast(dict[str, Any], result)
                        if isinstance(result, dict)
                        else {"version": "1.0", "sensors": {}}
                    )

            return await self._hass.async_add_executor_job(_read_yaml)
        except (yaml.YAMLError, OSError) as e:
            _LOGGER.error("Failed to read config file %s: %s", self._config_file, e)
            return {"version": "1.0", "sensors": {}}

    async def _write_config(self, config: dict[str, Any]) -> None:
        """Write the configuration file."""
        try:

            def _write_yaml() -> None:
                # Ensure directory exists
                self._config_file.parent.mkdir(parents=True, exist_ok=True)

                with open(self._config_file, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            await self._hass.async_add_executor_job(_write_yaml)
        except OSError as e:
            _LOGGER.error("Failed to write config file %s: %s", self._config_file, e)
            raise
