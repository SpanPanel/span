"""Simulation utilities for Span Panel config flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def get_available_simulation_configs() -> dict[str, str]:
    """Get available simulation configuration files.

    Returns:
        Dictionary mapping config keys to display names

    """
    configs = {}

    # Get the integration's simulation_configs directory
    current_file = Path(__file__)
    config_dir = current_file.parent.parent / "simulation_configs"

    if config_dir.exists():
        for yaml_file in config_dir.glob("*.yaml"):
            config_key = yaml_file.stem

            # Create user-friendly display names from filename
            display_name = config_key.replace("simulation_config_", "").replace("_", " ").title()

            configs[config_key] = display_name

    # If no configs found, provide a default
    if not configs:
        configs["simulation_config_32_circuit"] = "32-Circuit Residential Panel (Default)"

    return configs


def extract_serial_from_config(config_path: Path) -> str:
    """Extract serial number from simulation config file.

    Args:
        config_path: Path to the simulation config YAML file

    Returns:
        Serial number from the config, or default if not found

    """
    try:
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
                if isinstance(config_data, dict):
                    # Try to extract serial from various possible locations
                    if "serial_number" in config_data:
                        return str(config_data["serial_number"])
                    if "panel" in config_data and isinstance(config_data["panel"], dict):
                        if "serial_number" in config_data["panel"]:
                            return str(config_data["panel"]["serial_number"])
                    if "status" in config_data and isinstance(config_data["status"], dict):
                        if "serial_number" in config_data["status"]:
                            return str(config_data["status"]["serial_number"])
    except (FileNotFoundError, yaml.YAMLError, KeyError, ValueError):
        pass

    # Fallback to a default
    return "span-sim-001"


def get_simulation_config_path(config_key: str) -> Path:
    """Get the path to a simulation config file.

    Args:
        config_key: The config key (filename without extension)

    Returns:
        Path to the simulation config file

    """
    current_file = Path(__file__)
    config_dir = current_file.parent.parent / "simulation_configs"
    return config_dir / f"{config_key}.yaml"


def validate_yaml_config(yaml_path: Path) -> dict[str, Any]:
    """Validate and load a YAML configuration file.

    Args:
        yaml_path: Path to the YAML file

    Returns:
        Loaded YAML data as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is invalid
        ValueError: If YAML doesn't contain expected structure

    """
    if not yaml_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise TypeError("Configuration file must contain a YAML dictionary")

    return data
