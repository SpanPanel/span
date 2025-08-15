"""Shared utilities and types for synthetic sensor system.

This module contains shared types and utility functions used by both
synthetic_sensors.py and synthetic_panel_circuits.py to avoid circular imports.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
import yaml

_LOGGER = logging.getLogger(__name__)


class BackingEntity(TypedDict):
    """Structure for backing entity data used by ha-synthetic-sensors."""

    entity_id: str
    value: float | int | str | None
    data_path: str


class CombinedYamlResult(TypedDict):
    """Result of combining header template with sensor templates."""

    global_settings: dict[str, Any]
    sensor_configs: dict[str, Any]
    filled_template: str  # Add the filled template string


async def load_template(hass: HomeAssistant, template_name: str) -> str:
    """Load a YAML template from the yaml_templates directory as text.

    Args:
        hass: Home Assistant instance
        template_name: Name of the template file (with or without .yaml.txt extension)

    Returns:
        Template content as string

    Raises:
        FileNotFoundError: If template file doesn't exist

    """
    template_dir = Path(__file__).parent / "yaml_templates"

    # Add .yaml.txt extension only if not already present
    if not template_name.endswith(".yaml.txt"):
        template_name = f"{template_name}.yaml.txt"

    template_path = template_dir / template_name

    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    content: str = await hass.async_add_executor_job(template_path.read_text)
    return content


async def combine_yaml_templates(
    hass: HomeAssistant, sensor_template_names: list[str], placeholders: dict[str, str]
) -> CombinedYamlResult:
    """Combine header template with sensor templates and extract global settings.

    This function:
    1. Loads the sensor_set_header.yaml.txt template
    2. Loads and concatenates the specified sensor templates
    3. Fills all placeholders in the combined template
    4. Parses the combined YAML to extract global settings and sensor configs

    Args:
        hass: Home Assistant instance
        sensor_template_names: List of sensor template names to combine
        placeholders: Dictionary of placeholder values to fill in templates

    Returns:
        CombinedYamlResult with global_settings and sensor_configs

    Raises:
        FileNotFoundError: If any template file doesn't exist
        yaml.YAMLError: If YAML parsing fails

    """
    # Load header template first
    header_template = await load_template(hass, "sensor_set_header")

    # Load all sensor templates
    sensor_templates = []
    for template_name in sensor_template_names:
        template = await load_template(hass, template_name)
        sensor_templates.append(template)

    # Combine header with sensor templates
    # Header comes first, then each sensor template
    combined_template = header_template
    for sensor_template in sensor_templates:
        # Add newline separator and append sensor template
        combined_template += "\n\n" + sensor_template

    if combined_template is None:
        _LOGGER.error("Combined template is None!")
        return {"global_settings": {}, "sensor_configs": {}}

    # Fill all placeholders in the combined template
    filled_template = fill_template(combined_template, placeholders)

    # Parse the combined YAML
    try:
        parsed_yaml = yaml.safe_load(filled_template)
    except yaml.YAMLError as e:
        _LOGGER.error("Failed to parse combined YAML: %s", e)
        _LOGGER.error("Combined template content:\n%s", filled_template)
        raise

    # Extract global settings
    global_settings = parsed_yaml.get("global_settings", {}) if parsed_yaml else {}
    if global_settings is None:
        global_settings = {}

    # Extract sensor configs from the sensors section
    sensor_configs = parsed_yaml.get("sensors", {}) if parsed_yaml else {}
    if sensor_configs is None:
        sensor_configs = {}

    return {
        "global_settings": global_settings,
        "sensor_configs": sensor_configs,
        "filled_template": filled_template,
    }


def fill_template(template: str, replacements: dict[str, str]) -> str:
    """Fill template placeholders with actual values.

    Args:
        template: Template string with {{placeholder}} markers
        replacements: Dictionary mapping placeholder names to replacement values

    Returns:
        Template with all placeholders replaced

    """
    result = template
    for placeholder, replacement in replacements.items():
        old_placeholder = f"{{{{{placeholder}}}}}"
        result = result.replace(old_placeholder, replacement)
    return result
