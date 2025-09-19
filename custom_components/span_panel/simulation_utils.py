"""Simulation utilities for SPAN Panel integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Union

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
import yaml

from .const import (
    CONF_SIMULATION_CONFIG,
    COORDINATOR,
    DOMAIN,
)
from .options import (
    INVERTER_LEG1,
    INVERTER_LEG2,
)
from .simulation_generator import SimulationYamlGenerator

_LOGGER = logging.getLogger(__name__)


def infer_template_for(name: str, tabs: list[int]) -> str:
    """Infer circuit template based on circuit name and tab configuration.
    
    Args:
        name: Circuit name to analyze
        tabs: List of tab numbers for this circuit
        
    Returns:
        Template string identifier for the circuit type
    """
    lname = str(name).lower()
    if any(k in lname for k in ["light", "lights"]):
        return "lighting"
    if "kitchen" in lname and "outlet" in lname:
        return "kitchen_outlets"
    if any(
        k in lname
        for k in ["hvac", "furnace", "air conditioner", "ac", "heat pump"]
    ):
        return "hvac"
    if any(k in lname for k in ["fridge", "refrigerator", "wine fridge"]):
        return "refrigerator"
    if any(k in lname for k in ["ev", "charger"]):
        return "ev_charger"
    if any(k in lname for k in ["pool", "spa", "fountain"]):
        return "pool_equipment"
    if any(k in lname for k in ["internet", "router", "network", "modem"]):
        return "always_on"
    # Heuristics: 240V multi-tab loads as major appliances
    if len(tabs) >= 2:
        return "major_appliance"
    # Fallbacks
    if "outlet" in lname:
        return "outlets"
    return "major_appliance"


async def clone_panel_to_simulation(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    user_input: dict[str, Any] | None = None,
) -> Union[ConfigFlowResult, tuple[Path, dict[str, str]]]:
    """Clone the live panel into a simulation YAML stored in simulation_configs.
    
    Args:
        hass: Home Assistant instance
        config_entry: Configuration entry for the SPAN panel
        user_input: User input from the config flow form
        
    Returns:
        ConfigFlowResult indicating success or failure
    """
    errors: dict[str, str] = {}

    # Resolve coordinator (live)
    coordinator_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    coordinator = coordinator_data.get(COORDINATOR)
    if coordinator is None:
        from homeassistant.config_entries import ConfigFlowResult
        return ConfigFlowResult(type="abort", reason="coordinator_unavailable")

    # Compute default filename
    device_name = config_entry.data.get("device_name", config_entry.title)
    safe_device = slugify(device_name) if isinstance(device_name, str) else "span_panel"

    # We no longer need to compute num_tabs upfront since filename is device-based

    config_dir = Path(__file__).parent / "simulation_configs"
    base_name = f"simulation_config_{safe_device}.yaml"
    dest_path = config_dir / base_name

    # Suffix if exists: _2, _3, ...
    suffix_index = 1
    if await hass.async_add_executor_job(dest_path.exists):
        suffix_index = 2
        while True:
            candidate = (
                config_dir
                / f"simulation_config_{safe_device}_{suffix_index}.yaml"
            )
            if not await hass.async_add_executor_job(candidate.exists):
                dest_path = candidate
                break
            suffix_index += 1

    if user_input is not None:
        try:
            # Use a separate generator to build YAML purely from live data
            # Pass solar leg selections from options if present
            leg1_opt = config_entry.options.get(INVERTER_LEG1, 0)
            leg2_opt = config_entry.options.get(INVERTER_LEG2, 0)
            generator = SimulationYamlGenerator(
                hass=hass,
                coordinator=coordinator,
                solar_leg1=int(leg1_opt) if leg1_opt else None,
                solar_leg2=int(leg2_opt) if leg2_opt else None,
            )
            snapshot_yaml, num_tabs = await generator.build_yaml_from_live_panel()

            # snapshot_yaml and num_tabs returned by generator
            snapshot_yaml["panel_config"]["serial_number"] = f"{safe_device}_simulation" + (
                "" if suffix_index == 1 else f"_{suffix_index}"
            )

            # Use the same filename pattern (device name based, not tab count)
            # The dest_path is already correctly set above

            # Ensure directory exists and write file
            await hass.async_add_executor_job(
                lambda: dest_path.parent.mkdir(parents=True, exist_ok=True)
            )

            def _write_yaml() -> None:
                with dest_path.open("w", encoding="utf-8") as f:
                    yaml.safe_dump(snapshot_yaml, f, sort_keys=False)

            await hass.async_add_executor_job(_write_yaml)
            _LOGGER.info("Cloned live panel to simulation YAML at %s", dest_path)

            # Update config entry to point to the new simulation config
            try:
                new_data = dict(config_entry.data)
                new_data[CONF_SIMULATION_CONFIG] = dest_path.stem
                hass.config_entries.async_update_entry(config_entry, data=new_data)
                _LOGGER.debug("Set CONF_SIMULATION_CONFIG to %s", dest_path.stem)
            except Exception as update_err:
                _LOGGER.warning(
                    "Failed to set CONF_SIMULATION_CONFIG to %s: %s",
                    dest_path.stem,
                    update_err,
                )

            from homeassistant.config_entries import ConfigFlowResult
            return ConfigFlowResult(
                type="create_entry",
                title="",
                data={},
                description=f"Cloned panel to {dest_path.name} in simulation_configs",
            )

        except Exception as e:
            _LOGGER.error("Clone to simulation failed: %s", e)
            errors["base"] = f"Clone failed: {e}"

    # Return the destination path for the form
    return dest_path, errors
