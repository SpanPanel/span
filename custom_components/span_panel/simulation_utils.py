"""Clone panel utilities for SPAN Panel integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
import yaml

from .simulation_generator import SimulationYamlGenerator

if TYPE_CHECKING:
    from .coordinator import SpanPanelCoordinator

_LOGGER = logging.getLogger(__name__)


async def clone_panel_to_simulation(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    user_input: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, str]]:
    """Clone the live panel into a simulation YAML for the standalone simulator.

    Args:
        hass: Home Assistant instance
        config_entry: Configuration entry for the SPAN panel
        user_input: User input from the config flow form

    Returns:
        Tuple of (destination_path, errors_dict)

    """
    errors: dict[str, str] = {}

    # Compute default filename first
    device_name = config_entry.data.get("device_name", config_entry.title)
    safe_device = slugify(device_name) if isinstance(device_name, str) else "span_panel"

    config_dir = Path(hass.config.config_dir) / "span_panel" / "exports"
    base_name = f"simulation_config_{safe_device}.yaml"
    dest_path = config_dir / base_name

    # Resolve coordinator from runtime_data
    coordinator: SpanPanelCoordinator | None = None
    if hasattr(config_entry, "runtime_data") and config_entry.runtime_data is not None:
        coordinator = config_entry.runtime_data.coordinator
    if coordinator is None:
        errors["base"] = "coordinator_unavailable"
        return dest_path, errors

    # Suffix if exists: _2, _3, ...
    suffix_index = 1
    if await hass.async_add_executor_job(dest_path.exists):
        suffix_index = 2
        while True:
            candidate = config_dir / f"simulation_config_{safe_device}_{suffix_index}.yaml"
            if not await hass.async_add_executor_job(candidate.exists):
                dest_path = candidate
                break
            suffix_index += 1

    if user_input is not None:
        try:
            # Use a separate generator to build YAML purely from live data
            generator = SimulationYamlGenerator(
                hass=hass,
                coordinator=coordinator,
            )
            snapshot_yaml, num_tabs = await generator.build_yaml_from_live_panel()

            # snapshot_yaml and num_tabs returned by generator
            snapshot_yaml["panel_config"]["serial_number"] = f"{safe_device}_simulation" + (
                "" if suffix_index == 1 else f"_{suffix_index}"
            )

            # Ensure directory exists and write file
            await hass.async_add_executor_job(
                lambda: dest_path.parent.mkdir(parents=True, exist_ok=True)
            )

            def _write_yaml() -> None:
                with dest_path.open("w", encoding="utf-8") as f:
                    yaml.safe_dump(snapshot_yaml, f, sort_keys=False)

            await hass.async_add_executor_job(_write_yaml)
            _LOGGER.info("Cloned live panel to simulation YAML at %s", dest_path)

            # Return success with no errors
            return dest_path, {}

        except Exception as e:
            _LOGGER.error("Clone to simulation failed: %s", e)
            errors["base"] = f"Clone failed: {e}"

    # Return the destination path for the form
    return dest_path, errors
