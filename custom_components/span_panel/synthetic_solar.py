"""Solar synthetic sensor generation for SPAN Panel integration.

This module handles the generation of solar synthetic sensors using formula-based
calculations that reference native HA unmapped circuit entities.
"""

from __future__ import annotations

import logging
from typing import Any

from ha_synthetic_sensors import (
    SensorManager,
    rebind_backing_entities,
)
from ha_synthetic_sensors.config_types import GlobalSettingsDict
from ha_synthetic_sensors.sensor_set import SensorSet
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, USE_DEVICE_PREFIX
from .coordinator import SpanPanelCoordinator
from .helpers import (
    NEW_SENSOR,
    build_binary_sensor_unique_id_for_entry,
    construct_120v_synthetic_entity_id,
    construct_240v_synthetic_entity_id,
    construct_panel_entity_id,
    construct_syn_calc_entity_id,
    construct_synthetic_unique_id_for_entry,
    construct_tabs_attribute,
    construct_voltage_attribute,
    get_device_identifier_for_entry,
    get_unmapped_circuit_entity_id,
)
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .synthetic_sensors import _synthetic_coordinators
from .synthetic_utils import combine_yaml_templates, fill_template, load_template

_LOGGER = logging.getLogger(__name__)

# Solar sensor definitions - these reference native HA entities via formulas
SOLAR_SENSOR_DEFINITIONS = [
    {
        "template": "solar_current_power.yaml.txt",
        "sensor_type": "power",
        "description": "Current solar power production",
    },
    {
        "template": "solar_produced_energy.yaml.txt",
        "sensor_type": "energy_produced",
        "description": "Total solar energy produced",
    },
    {
        "template": "solar_consumed_energy.yaml.txt",
        "sensor_type": "energy_consumed",
        "description": "Total solar energy consumed",
    },
    {
        "template": "solar_net_energy.yaml.txt",
        "sensor_type": "net_energy",
        "description": "Solar net energy (consumed - produced)",
    },
]


def _extract_leg_numbers(leg1_circuit: str, leg2_circuit: str) -> tuple[int, int]:
    """Extract circuit numbers from leg circuit IDs.

    Args:
        leg1_circuit: Circuit ID for leg 1 (e.g., "unmapped_tab_15")
        leg2_circuit: Circuit ID for leg 2 (e.g., "unmapped_tab_16")

    Returns:
        Tuple of (leg1_number, leg2_number)

    """
    leg1_number = (
        int(leg1_circuit.replace("unmapped_tab_", ""))
        if leg1_circuit.startswith("unmapped_tab_")
        else 0
    )
    leg2_number = (
        int(leg2_circuit.replace("unmapped_tab_", ""))
        if leg2_circuit and leg2_circuit.startswith("unmapped_tab_")
        else 0
    )
    return leg1_number, leg2_number


def _get_template_attributes(leg1_number: int, leg2_number: int) -> tuple[str, int]:
    """Generate tabs and voltage attributes for solar sensors.

    Args:
        leg1_number: Circuit number for leg 1
        leg2_number: Circuit number for leg 2

    Returns:
        Tuple of (tabs_attribute, voltage_attribute)

    """
    if leg1_number > 0 and leg2_number > 0:
        # Create a synthetic circuit object with both tab numbers for attribute generation
        synthetic_circuit = SpanPanelCircuit(
            circuit_id="solar_synthetic",
            name="Solar Synthetic",
            relay_state="CLOSED",
            instant_power=0.0,
            instant_power_update_time=0,
            produced_energy=0.0,
            consumed_energy=0.0,
            energy_accum_update_time=0,
            priority="NORMAL",
            is_user_controllable=False,
            is_sheddable=False,
            is_never_backup=False,
            tabs=[leg1_number, leg2_number],
        )
        tabs_attribute_full = construct_tabs_attribute(synthetic_circuit)
        voltage_attribute = construct_voltage_attribute(synthetic_circuit)

        tabs_attribute = tabs_attribute_full if tabs_attribute_full else ""
        voltage_attribute = voltage_attribute if voltage_attribute is not None else 0
    else:
        tabs_attribute = ""
        voltage_attribute = 0

    return tabs_attribute, voltage_attribute


def _generate_sensor_entity_id(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    sensor_type: str,
    leg1_number: int,
    leg2_number: int,
    migration_mode: bool = False,
    hass: HomeAssistant | None = None,
) -> str | None:
    """Generate entity ID for a solar sensor.

    Args:
        coordinator: The SPAN Panel coordinator
        span_panel: The SPAN Panel data
        sensor_type: Type of sensor (e.g., "power", "energy_produced")
        leg1_number: Circuit number for leg 1
        leg2_number: Circuit number for leg 2
        migration_mode: Whether we're in migration mode
        hass: Home Assistant instance (required for migration mode)

    Returns:
        Entity ID string or None if generation fails

    """
    # In migration mode, look up existing entity_id from registry
    if migration_mode and hass:
        # Generate the unique_id that solar sensors should have
        device_name = coordinator.config_entry.data.get(
            "device_name", coordinator.config_entry.title
        )
        unique_id = construct_synthetic_unique_id_for_entry(
            coordinator, span_panel, f"solar_{sensor_type}", device_name
        )

        entity_registry = er.async_get(hass)
        existing_entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if existing_entity_id:
            # Use existing entity ID as-is - no sanitization needed during migration
            _LOGGER.debug(
                "MIGRATION: Using existing solar entity %s for unique_id %s",
                existing_entity_id,
                unique_id,
            )
            return existing_entity_id

        # FATAL ERROR: Migration mode but migrated key not found in registry
        raise ValueError(
            f"MIGRATION ERROR: Expected solar unique_id '{unique_id}' not found in registry. "
            f"This indicates migration failed for solar sensor {sensor_type}."
        )

    # Normal mode: generate new entity_id
    device_name = coordinator.config_entry.data.get("device_name", coordinator.config_entry.title)
    if leg1_number > 0 and leg2_number > 0:
        # Two tabs - use 240V synthetic helper
        return construct_240v_synthetic_entity_id(
            coordinator,
            span_panel,
            "sensor",
            sensor_type,
            friendly_name="Solar",
            unique_id=construct_synthetic_unique_id_for_entry(
                coordinator, span_panel, f"solar_{sensor_type}", device_name
            ),
            migration_mode=migration_mode,
            tab1=leg1_number,
            tab2=leg2_number,
        )

    # Single tab - use 120V synthetic helper
    active_tab = leg1_number if leg1_number > 0 else leg2_number
    return construct_120v_synthetic_entity_id(
        coordinator,
        span_panel,
        "sensor",
        sensor_type,
        friendly_name="Solar",
        unique_id=construct_synthetic_unique_id_for_entry(
            coordinator, span_panel, f"solar_{sensor_type}", device_name
        ),
        migration_mode=migration_mode,
        tab=active_tab,
    )


async def _process_sensor_template(
    hass: HomeAssistant,
    sensor_def: dict[str, Any],
    template_vars: dict[str, Any],
    entity_id: str | None,
) -> dict[str, Any] | None:
    """Process a sensor template and return the configuration.

    Args:
        hass: Home Assistant instance
        sensor_def: Sensor definition dictionary
        template_vars: Template variables
        entity_id: Entity ID for the sensor

    Returns:
        Sensor configuration dictionary or None if processing fails

    """
    if not entity_id:
        return None

    # Validate that required entity variables are present and valid
    required_vars = []
    if "power" in sensor_def["sensor_type"]:
        required_vars = ["leg1_power_entity", "leg2_power_entity"]
    elif "produced" in sensor_def["sensor_type"]:
        required_vars = ["leg1_produced_entity", "leg2_produced_entity"]
    elif "consumed" in sensor_def["sensor_type"]:
        required_vars = ["leg1_consumed_entity", "leg2_consumed_entity"]
    elif "net_energy" in sensor_def["sensor_type"]:
        required_vars = ["net_consumed_entity_id", "net_produced_entity_id"]

    # Check if any required variables are missing or empty
    for var in required_vars:
        if not template_vars.get(var) or template_vars[var] == "":
            _LOGGER.error(
                "Missing or empty required variable '%s' for sensor template %s. Variables: %s",
                var,
                sensor_def["template"],
                template_vars,
            )
            return None

    # Add entity_id to template variables
    sensor_template_vars = template_vars.copy()
    sensor_template_vars["entity_id"] = entity_id

    # Convert template variables to strings, but preserve numeric attributes as unquoted
    string_template_vars = {}
    for key, value in sensor_template_vars.items():
        if key == "voltage_attribute" and isinstance(value, int | float):
            # Keep voltage as unquoted number for YAML
            string_template_vars[key] = str(value)
        elif isinstance(value, int | float):
            # Other numeric literals as strings
            string_template_vars[key] = str(value)
        elif value is not None:
            string_template_vars[key] = str(value)
        else:
            string_template_vars[key] = ""

    _LOGGER.debug(
        "Solar template variables for %s: %r", sensor_def["template"], string_template_vars
    )

    try:
        template_files = [sensor_def["template"]]
        combined_result = await combine_yaml_templates(hass, template_files, string_template_vars)
        _LOGGER.debug(
            "Template processing result for %s: %r", sensor_def["template"], combined_result
        )
    except Exception as template_error:
        _LOGGER.error(
            "Template processing failed for %s: %s",
            sensor_def["template"],
            template_error,
            exc_info=True,
        )
        return None

    if (
        not combined_result
        or not isinstance(combined_result, dict)
        or "sensor_configs" not in combined_result
    ):
        _LOGGER.error(
            "No sensors found in template %s. Combined result: %r",
            sensor_def["template"],
            combined_result,
        )
        return None

    # Extract the sensor configuration
    template_sensors = combined_result["sensor_configs"]
    if not template_sensors:
        _LOGGER.error(
            "Empty sensors in template %s. Template sensors: %r",
            sensor_def["template"],
            template_sensors,
        )
        return None

    # Get the first (and should be only) sensor from the template
    sensor_key = list(template_sensors.keys())[0]
    sensor_config = template_sensors[sensor_key].copy()

    _LOGGER.debug("Raw sensor config from template %s: %r", sensor_def["template"], sensor_config)

    # Create the final sensor configuration
    return {
        "entity_id": sensor_config.get("entity_id", entity_id),
        "name": sensor_config.get("name", ""),
        "formula": sensor_config.get("formula", ""),
        "variables": sensor_config.get("variables", {}),
        "attributes": sensor_config.get("attributes", {}),
        "metadata": sensor_config.get("metadata", {}),
    }


async def generate_solar_sensors_with_entity_ids(
    coordinator: SpanPanelCoordinator,
    span_panel: SpanPanel,
    leg1_entity_id: str,
    leg2_entity_id: str,
    device_name: str,
    migration_mode: bool = False,
    hass: HomeAssistant | None = None,
) -> dict[str, Any]:
    """Generate solar sensor configurations using YAML templates with direct entity IDs.

    Args:
        coordinator: The SPAN Panel coordinator
        span_panel: The SPAN Panel data
        leg1_entity_id: Entity ID for leg 1 (e.g., "sensor.span_panel_unmapped_tab_30_power")
        leg2_entity_id: Entity ID for leg 2 (e.g., "sensor.span_panel_unmapped_tab_32_power")
        device_name: The name of the device to use for sensor generation
        migration_mode: Whether we're in migration mode
        hass: Home Assistant instance (required for migration mode)

    Returns:
        Dictionary of sensor configurations

    """
    sensor_configs = {}

    # Extract circuit numbers from entity IDs for template attributes
    leg1_number = int(leg1_entity_id.split("_")[-2]) if "_" in leg1_entity_id else 0
    leg2_number = int(leg2_entity_id.split("_")[-2]) if "_" in leg2_entity_id else 0

    # If no valid tabs are configured, don't generate any solar sensors
    if leg1_number == 0 and leg2_number == 0:
        _LOGGER.debug("No valid solar tabs configured, skipping solar sensor generation")
        return {}

    # Create leg entities directly from the provided entity IDs
    leg_entities = {
        "leg1_power_entity": leg1_entity_id,
        "leg1_produced_entity": leg1_entity_id.replace("_power", "_energy_produced"),
        "leg1_consumed_entity": leg1_entity_id.replace("_power", "_energy_consumed"),
        "leg2_power_entity": leg2_entity_id,
        "leg2_produced_entity": leg2_entity_id.replace("_power", "_energy_produced"),
        "leg2_consumed_entity": leg2_entity_id.replace("_power", "_energy_consumed"),
    }

    _LOGGER.debug("Using direct entity IDs: %s", leg_entities)

    # Get template attributes
    tabs_attribute, voltage_attribute = _get_template_attributes(leg1_number, leg2_number)

    # Construct panel status entity ID
    use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)

    # panel_status is a new sensor, so always use migration_mode=False when looking up its entity ID
    panel_status_entity_id = construct_panel_entity_id(
        coordinator,
        span_panel,
        "binary_sensor",
        "panel_status",
        device_name,
        unique_id=NEW_SENSOR
        if migration_mode
        else build_binary_sensor_unique_id_for_entry(
            coordinator, span_panel, "panel_status", device_name
        ),
        migration_mode=migration_mode,
        use_device_prefix=use_device_prefix,
    )

    # Template variables for solar sensors
    template_vars = {
        "device_identifier": get_device_identifier_for_entry(coordinator, span_panel, device_name),
        "energy_grace_period_minutes": str(
            coordinator.config_entry.options.get("energy_reporting_grace_period", 15)
        ),
        "power_display_precision": str(
            coordinator.config_entry.options.get("power_display_precision", 0)
        ),
        "energy_display_precision": str(
            coordinator.config_entry.options.get("energy_display_precision", 2)
        ),
        "panel_status_entity_id": panel_status_entity_id
        or construct_panel_entity_id(
            coordinator,
            span_panel,
            "binary_sensor",
            "panel_status",
            device_name,
            unique_id=NEW_SENSOR,
            migration_mode=False,
            use_device_prefix=use_device_prefix,
        ),
        "leg1_circuit": f"unmapped_tab_{leg1_number}",
        "leg2_circuit": f"unmapped_tab_{leg2_number}",
        "tabs_attribute": tabs_attribute,
        "voltage_attribute": str(voltage_attribute),
        # Map entity IDs to the variable names expected by the templates
        "leg1_produced": leg_entities.get("leg1_produced_entity", ""),
        "leg2_produced": leg_entities.get("leg2_produced_entity", ""),
        "leg1_consumed": leg_entities.get("leg1_consumed_entity", ""),
        "leg2_consumed": leg_entities.get("leg2_consumed_entity", ""),
        "leg1_power": leg_entities.get("leg1_power_entity", ""),
        "leg2_power": leg_entities.get("leg2_power_entity", ""),
        # Net energy placeholders for solar net energy template (will be updated after generation)
        "net_consumed_entity_id": "",
        "net_produced_entity_id": "",
        # Also include the original entity IDs for backward compatibility
        **leg_entities,
    }

    _LOGGER.debug("Complete template_vars for solar sensors: %s", template_vars)

    # Track consumed/produced entity_ids to build Net Energy after both exist
    solar_consumed_entity_id: str | None = None
    solar_produced_entity_id: str | None = None

    # Generate non-net energy sensors first
    for sensor_def in SOLAR_SENSOR_DEFINITIONS:
        # Skip net energy sensor for now - process it after consumed/produced are available
        if sensor_def["sensor_type"] == "net_energy":
            continue
        try:
            # Generate entity ID for this sensor
            entity_id = _generate_sensor_entity_id(
                coordinator,
                span_panel,
                sensor_def["sensor_type"],
                leg1_number,
                leg2_number,
                migration_mode,
                hass,
            )

            unique_id = construct_synthetic_unique_id_for_entry(
                coordinator, span_panel, f"solar_{sensor_def['sensor_type']}", device_name
            )

            # Process the sensor template
            _LOGGER.debug("Processing template %s for sensor %s", sensor_def["template"], unique_id)
            # Add sensor_key to template vars for this specific sensor
            sensor_template_vars = template_vars.copy()
            sensor_template_vars["sensor_key"] = unique_id
            if hass is None:
                _LOGGER.error("Home Assistant instance is None, cannot process sensor template")
                continue
            final_config = await _process_sensor_template(
                hass, sensor_def, sensor_template_vars, entity_id
            )
            if final_config:
                sensor_configs[unique_id] = final_config
                _LOGGER.debug("Successfully generated solar sensor: %s -> %s", unique_id, entity_id)
                _LOGGER.debug(
                    "Final config has formula: %s, variables: %s",
                    bool(final_config.get("formula")),
                    bool(final_config.get("variables")),
                )

                # Record consumed/produced entity_ids for Net Energy construction
                if sensor_def["sensor_type"] == "energy_consumed":
                    solar_consumed_entity_id = entity_id
                elif sensor_def["sensor_type"] == "energy_produced":
                    solar_produced_entity_id = entity_id
            else:
                _LOGGER.error("Template processing returned None for %s", sensor_def["template"])

        except Exception as e:
            _LOGGER.error(
                "Error generating solar sensor from template %s: %s",
                sensor_def["template"],
                e,
                exc_info=True,
            )

    # Now process the net energy sensor with the consumed/produced entity IDs available
    if solar_consumed_entity_id and solar_produced_entity_id:
        # Update template variables with consumed/produced entity IDs
        template_vars["net_consumed_entity_id"] = solar_consumed_entity_id
        template_vars["net_produced_entity_id"] = solar_produced_entity_id
        # Find the net energy sensor definition
        net_energy_sensor_def = next(
            (
                sensor_def
                for sensor_def in SOLAR_SENSOR_DEFINITIONS
                if sensor_def["sensor_type"] == "net_energy"
            ),
            None,
        )
        if net_energy_sensor_def:
            try:
                # Generate entity ID for net energy sensor
                entity_id = _generate_sensor_entity_id(
                    coordinator,
                    span_panel,
                    net_energy_sensor_def["sensor_type"],
                    leg1_number,
                    leg2_number,
                    migration_mode,
                    hass,
                )

                unique_id = construct_synthetic_unique_id_for_entry(
                    coordinator,
                    span_panel,
                    f"solar_{net_energy_sensor_def['sensor_type']}",
                    device_name,
                )

                # Process the net energy sensor template
                _LOGGER.debug(
                    "Processing net energy template %s for sensor %s",
                    net_energy_sensor_def["template"],
                    unique_id,
                )
                sensor_template_vars = template_vars.copy()
                sensor_template_vars["sensor_key"] = unique_id
                if hass is not None:
                    final_config = await _process_sensor_template(
                        hass, net_energy_sensor_def, sensor_template_vars, entity_id
                    )
                    if final_config:
                        sensor_configs[unique_id] = final_config
                        _LOGGER.debug(
                            "Successfully generated solar net energy sensor: %s -> %s",
                            unique_id,
                            entity_id,
                        )
                    else:
                        _LOGGER.error(
                            "Template processing returned None for %s",
                            net_energy_sensor_def["template"],
                        )
                else:
                    _LOGGER.error(
                        "Home Assistant instance is None, cannot process net energy sensor template"
                    )

            except Exception as e:
                _LOGGER.error(
                    "Error generating solar net energy sensor from template %s: %s",
                    net_energy_sensor_def["template"],
                    e,
                    exc_info=True,
                )
        else:
            _LOGGER.error("Net energy sensor definition not found in SOLAR_SENSOR_DEFINITIONS")
    else:
        _LOGGER.warning(
            "Cannot generate solar net energy sensor: missing consumed (%s) or produced (%s) entity IDs",
            solar_consumed_entity_id,
            solar_produced_entity_id,
        )

    _LOGGER.debug("Generated %d solar sensor configurations", len(sensor_configs))

    return sensor_configs


async def handle_solar_sensor_crud(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: SpanPanelCoordinator,
    sensor_set: SensorSet,
    enable_solar: bool,
    leg1_circuit: int,
    leg2_circuit: int,
    device_name: str | None = None,
    migration_mode: bool = False,
) -> bool:
    """Handle solar sensor CRUD operations.

    Args:
        hass: Home Assistant instance
        config_entry: The config entry
        coordinator: SPAN Panel coordinator
        sensor_set: The cached SensorSet instance for this device
        enable_solar: Whether solar sensors should be enabled
        leg1_circuit: Circuit number for leg 1
        leg2_circuit: Circuit number for leg 2
        device_name: The device name to use for entity ID construction
        migration_mode: Whether we're in migration mode

    Returns:
        True if successful, False otherwise

    """
    try:
        span_panel = coordinator.data

        # Verify circuits exist by trying to get their entity IDs
        leg1_entity_id = get_unmapped_circuit_entity_id(
            span_panel, leg1_circuit, "power", device_name
        )
        leg2_entity_id = get_unmapped_circuit_entity_id(
            span_panel, leg2_circuit, "power", device_name
        )

        if not leg1_entity_id:
            _LOGGER.error("Circuit tab %d not found in panel circuits", leg1_circuit)
            return False
        if not leg2_entity_id:
            _LOGGER.error("Circuit tab %d not found in panel circuits", leg2_circuit)
            return False

        _LOGGER.debug("Found solar entity IDs: %s and %s", leg1_entity_id, leg2_entity_id)

        # Generate other entity IDs from the verified power entity IDs
        leg1_produced_entity = leg1_entity_id.replace("_power", "_energy_produced")
        leg1_consumed_entity = leg1_entity_id.replace("_power", "_energy_consumed")
        leg2_produced_entity = leg2_entity_id.replace("_power", "_energy_produced")
        leg2_consumed_entity = leg2_entity_id.replace("_power", "_energy_consumed")

        # Create the leg entities dictionary directly
        leg_entities = {
            "leg1_power_entity": leg1_entity_id,
            "leg1_produced_entity": leg1_produced_entity,
            "leg1_consumed_entity": leg1_consumed_entity,
            "leg2_power_entity": leg2_entity_id,
            "leg2_produced_entity": leg2_produced_entity,
            "leg2_consumed_entity": leg2_consumed_entity,
        }

        # Define the solar sensor templates
        solar_templates = [
            "solar_current_power",
            "solar_produced_energy",
            "solar_consumed_energy",
            "solar_net_energy",
        ]

        # Track solar entity IDs for net energy sensor
        solar_consumed_entity_id: str | None = None
        solar_produced_entity_id: str | None = None

        # Set up global settings for solar sensors
        global_settings: GlobalSettingsDict = {"variables": {"energy_grace_period_minutes": "30"}}
        await sensor_set.async_set_global_settings(global_settings)
        _LOGGER.debug("Set global settings for solar sensors: %s", global_settings)

        _LOGGER.info("SOLAR_DEBUG: Starting solar sensor addition")
        for template_name in solar_templates:
            try:
                # Generate unique ID for this solar sensor using the same helper as other sensors
                sensor_name = f"solar_{template_name.split('_', 1)[1]}"
                sensor_unique_id = construct_synthetic_unique_id_for_entry(
                    coordinator, span_panel, sensor_name, device_name
                )

                # Generate entity_id with migration mode support
                sensor_type = template_name.split("_", 1)[
                    1
                ]  # "current_power", "produced_energy", etc.
                if migration_mode:
                    # Look up existing entity_id from registry
                    entity_registry = er.async_get(hass)
                    existing_entity_id = entity_registry.async_get_entity_id(
                        "sensor", DOMAIN, sensor_unique_id
                    )
                    if existing_entity_id:
                        # Use existing entity ID as-is - no sanitization needed during migration
                        solar_entity_id = existing_entity_id
                        _LOGGER.debug(
                            "MIGRATION: Using existing solar entity %s for unique_id %s",
                            existing_entity_id,
                            sensor_unique_id,
                        )
                    elif sensor_type == "net_energy":
                        # Special case: net energy sensor is new during migration
                        # During migration: check if solar was configured by looking for consumed/produced sensors
                        # Check if solar was configured by looking for consumed energy sensor
                        consumed_exists = entity_registry.async_get_entity_id(
                            "sensor",
                            DOMAIN,
                            construct_synthetic_unique_id_for_entry(
                                coordinator, span_panel, "solar_consumed_energy", device_name
                            ),
                        )
                        produced_exists = entity_registry.async_get_entity_id(
                            "sensor",
                            DOMAIN,
                            construct_synthetic_unique_id_for_entry(
                                coordinator, span_panel, "solar_produced_energy", device_name
                            ),
                        )

                        if consumed_exists or produced_exists:
                            # Use proper entity ID construction with device prefix
                            use_device_prefix = coordinator.config_entry.options.get(
                                USE_DEVICE_PREFIX, True
                            )
                            solar_entity_id = (
                                construct_syn_calc_entity_id(
                                    coordinator,
                                    span_panel,
                                    "sensor",
                                    f"solar_{sensor_type}",
                                    device_name or "",
                                    unique_id=sensor_unique_id,
                                    migration_mode=migration_mode,
                                    use_device_prefix=use_device_prefix,
                                )
                                or f"sensor.solar_{sensor_type}"
                            )
                            _LOGGER.debug(
                                "MIGRATION: Creating new net energy sensor %s (solar was configured)",
                                solar_entity_id,
                            )
                    else:
                        # FATAL ERROR: Migration mode but migrated key not found in registry
                        raise ValueError(
                            f"MIGRATION ERROR: Expected solar unique_id '{sensor_unique_id}' not found in registry. "
                            f"This indicates migration failed for solar sensor {sensor_type}."
                        )
                else:
                    # Normal mode: generate new entity_id with device prefix
                    use_device_prefix = coordinator.config_entry.options.get(
                        USE_DEVICE_PREFIX, True
                    )
                    solar_entity_id = (
                        construct_panel_entity_id(
                            coordinator,
                            span_panel,
                            "sensor",
                            f"solar_{sensor_type}",
                            device_name or "",
                            unique_id=sensor_unique_id,
                            migration_mode=migration_mode,
                            use_device_prefix=use_device_prefix,
                        )
                        or f"sensor.solar_{sensor_type}"
                    )

                # Record entity IDs for net energy sensor
                if sensor_type == "produced_energy":
                    solar_produced_entity_id = solar_entity_id
                elif sensor_type == "consumed_energy":
                    solar_consumed_entity_id = solar_entity_id

                # Load the template as a string
                template_content = await load_template(hass, template_name)

                # Get proper voltage attribute using helper function
                tabs_attribute, voltage_attribute = _get_template_attributes(
                    leg1_circuit, leg2_circuit
                )

                # Construct panel status entity ID
                # panel_status is a new sensor, so always use migration_mode=False when looking up its entity ID
                use_device_prefix = coordinator.config_entry.options.get(USE_DEVICE_PREFIX, True)
                panel_status_entity_id = construct_panel_entity_id(
                    coordinator,
                    span_panel,
                    "binary_sensor",
                    "panel_status",
                    device_name or "",
                    unique_id=NEW_SENSOR
                    if migration_mode
                    else build_binary_sensor_unique_id_for_entry(
                        coordinator, span_panel, "panel_status", device_name or ""
                    ),
                    migration_mode=migration_mode,
                    use_device_prefix=use_device_prefix,
                )

                # Prepare template variables
                panel_status_id = panel_status_entity_id or construct_panel_entity_id(
                    coordinator,
                    span_panel,
                    "binary_sensor",
                    "panel_status",
                    device_name or "",
                    unique_id=NEW_SENSOR,
                    migration_mode=False,
                    use_device_prefix=use_device_prefix,
                )

                template_vars = {
                    "sensor_key": sensor_unique_id,
                    "entity_id": solar_entity_id,
                    "panel_status_entity_id": panel_status_id or "",
                    # Provide entity IDs for the formulas in the templates
                    # Power
                    "leg1_power_entity": leg_entities["leg1_power_entity"],
                    "leg2_power_entity": leg_entities["leg2_power_entity"],
                    # Produced energy
                    "leg1_produced_entity": leg_entities["leg1_produced_entity"],
                    "leg2_produced_entity": leg_entities["leg2_produced_entity"],
                    # Consumed energy
                    "leg1_consumed_entity": leg_entities["leg1_consumed_entity"],
                    "leg2_consumed_entity": leg_entities["leg2_consumed_entity"],
                    "tabs_attribute": tabs_attribute,
                    "voltage_attribute": str(voltage_attribute),
                    "power_display_precision": "0",
                    "energy_display_precision": "2",
                }

                # Add net energy specific variables if this is the net energy sensor
                if template_name == "solar_net_energy":
                    template_vars.update(
                        {
                            "net_consumed_entity_id": str(solar_consumed_entity_id)
                            if solar_consumed_entity_id
                            else "",
                            "net_produced_entity_id": str(solar_produced_entity_id)
                            if solar_produced_entity_id
                            else "",
                        }
                    )

                # Fill the template with the actual values
                filled_template = fill_template(template_content, template_vars)

                # Add the filled template directly using YAML CRUD
                await sensor_set.async_add_sensor_from_yaml(filled_template)
                _LOGGER.debug("Added solar sensor via YAML CRUD: %s", template_name)

            except Exception as e:
                _LOGGER.error("Error adding solar sensor %s: %s", template_name, e)

        _LOGGER.debug("Added %d solar sensors via YAML CRUD", len(solar_templates))

        # YAML CRUD operations are self-contained and automatically update the sensor manager
        # No additional configuration reload is needed - the sensors are already active
        _LOGGER.debug("Solar sensors added via YAML CRUD - no additional reload needed")

        return True

    except Exception as e:
        _LOGGER.error("Error handling solar sensor CRUD: %s", e, exc_info=True)
        return False


async def handle_solar_options_change(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: SpanPanelCoordinator,
    sensor_set: SensorSet,  # SensorSet parameter - cached from storage
    enable_solar: bool,
    leg1_circuit: int,
    leg2_circuit: int,
    device_name: str | None = None,
    migration_mode: bool = False,
) -> bool:
    """Handle solar options change by performing CRUD operations on SensorSet.

    Args:
        hass: Home Assistant instance
        config_entry: The config entry
        coordinator: SPAN Panel coordinator
        sensor_set: The cached SensorSet instance for this device
        enable_solar: Whether solar sensors should be enabled
        leg1_circuit: Circuit number for leg 1
        leg2_circuit: Circuit number for leg 2
        device_name: The device name to use for entity ID construction
        migration_mode: Whether we're in migration mode

    Returns:
        True if successful, False otherwise

    """
    try:
        _LOGGER.debug(
            "handle_solar_options_change called with: enable_solar=%s, leg1_circuit=%s, leg2_circuit=%s",
            enable_solar,
            leg1_circuit,
            leg2_circuit,
        )

        span_panel = coordinator.data

        # Debug: Show available circuits in panel
        _LOGGER.debug("Available circuits in panel:")
        for circuit_id, circuit_data in span_panel.circuits.items():
            _LOGGER.debug("  Circuit ID %s: %s", circuit_id, circuit_data.name)
            _LOGGER.debug(
                "    Circuit attributes: %s",
                [attr for attr in dir(circuit_data) if not attr.startswith("_")],
            )
            break  # Just show one circuit's attributes

        # Use the cached SensorSet instance directly
        if not sensor_set.exists:
            _LOGGER.error("Sensor set does not exist for solar options change")
            return False

        # Check if solar sensors already exist and match current configuration
        expected_solar_ids = construct_expected_solar_sensor_ids(
            coordinator, span_panel, device_name
        )
        existing_sensors = sensor_set.list_sensors()
        existing_solar_ids = [
            s.unique_id for s in existing_sensors if s.unique_id in expected_solar_ids
        ]

        # In migration mode, only add solar sensors if they don't exist
        if migration_mode:
            if existing_solar_ids:
                _LOGGER.debug(
                    "Migration mode: Solar sensors already exist (%d found), skipping creation",
                    len(existing_solar_ids),
                )
                return True
        else:
            # Normal mode - check if configuration actually changed
            solar_should_exist = enable_solar and leg1_circuit > 0 and leg2_circuit > 0
            solar_currently_exists = len(existing_solar_ids) > 0

            if solar_should_exist == solar_currently_exists:
                # No change needed - solar state matches desired state
                if solar_should_exist:
                    _LOGGER.debug(
                        "Solar sensors already exist with correct configuration, no changes needed"
                    )
                else:
                    _LOGGER.debug("Solar sensors correctly disabled, no changes needed")
                return True

            # Configuration changed - remove existing solar sensors if any
            if existing_solar_ids:
                for solar_id in existing_solar_ids:
                    await sensor_set.async_remove_sensor(solar_id)
                    _LOGGER.debug("Removed existing solar sensor: %s", solar_id)

        if enable_solar and leg1_circuit > 0 and leg2_circuit > 0:
            # Handle solar sensor creation
            success = await handle_solar_sensor_crud(
                hass,
                config_entry,
                coordinator,
                sensor_set,
                enable_solar=True,
                leg1_circuit=leg1_circuit,
                leg2_circuit=leg2_circuit,
                device_name=device_name,
                migration_mode=migration_mode,
            )
            if not success:
                _LOGGER.error("Failed to add solar sensors")
                return False
        else:
            _LOGGER.debug(
                "Solar sensors disabled or invalid legs - removed %d sensors",
                len(existing_solar_ids),
            )

        # After CRUD, re-register backing entities and trigger an initial update so the new
        # solar sensors evaluate immediately. Reuse the existing change notifier.
        try:
            synthetic_coord = _synthetic_coordinators.get(config_entry.entry_id)
            if synthetic_coord is not None:
                # Get the active sensor_manager from the integration cache (set during setup)
                data = hass.data.setdefault(DOMAIN, {}).setdefault(config_entry.entry_id, {})
                sensor_manager = data.get("sensor_manager")
                if isinstance(sensor_manager, SensorManager):
                    backing_ids = set(synthetic_coord.backing_entity_metadata.keys())
                    # Re-bind full set with the existing notifier (if set)
                    change_notifier = synthetic_coord.change_notifier

                    rebind_backing_entities(
                        sensor_manager,
                        backing_ids,
                        change_notifier,
                        trigger_initial_update=True,
                        logger=_LOGGER,
                    )
        except Exception as e:
            _LOGGER.warning("Post-CRUD solar rebind/update failed: %s", e)

        # Storage is automatically saved by SensorSet operations
        return True

    except Exception as e:
        _LOGGER.error("Error handling solar options change: %s", e)
        return False


def get_solar_data_value(
    sensor_key: str, span_panel: SpanPanel, sensor_map: dict[str, Any]
) -> float:
    """Get solar data value for a sensor key.

    Args:
        sensor_key: The sensor key to get data for
        span_panel: The span panel instance
        sensor_map: Mapping of sensor keys to values

    Returns:
        The solar data value (currently always returns 0.0)

    """
    return 0.0


def get_stored_solar_sensor_ids_from_set(sensor_set: SensorSet) -> list[str]:
    """Extract solar sensor IDs from a SensorSet.

    Args:
        sensor_set: The SensorSet to search for solar sensors

    Returns:
        List of unique IDs for solar sensors found in the set

    """
    try:
        sensors = sensor_set.list_sensors()
        solar_sensor_ids = []

        for sensor in sensors:
            # Check by name patterns
            if "solar" in sensor.unique_id.lower():
                solar_sensor_ids.append(sensor.unique_id)
                continue

            # Check by formula patterns (solar sensors often reference leg1/leg2)
            for formula in sensor.formulas:
                if hasattr(formula, "variables"):
                    variables = formula.variables
                    if isinstance(variables, dict):
                        # Look for leg1/leg2 patterns in variables
                        if any("leg1" in str(v) or "leg2" in str(v) for v in variables.values()):
                            solar_sensor_ids.append(sensor.unique_id)
                            break

            # Check by entity ID patterns (circuit_XX_YY patterns for 240V solar)
            if sensor.entity_id and "_" in sensor.entity_id and "circuit_" in sensor.entity_id:
                parts = sensor.entity_id.split("_")
                if len(parts) >= 4 and parts[-2].isdigit() and parts[-3].isdigit():
                    solar_sensor_ids.append(sensor.unique_id)

        return solar_sensor_ids
    except Exception as e:
        _LOGGER.warning("Error extracting solar sensor IDs from set: %s", e)
        return []


def construct_expected_solar_sensor_ids(
    coordinator: SpanPanelCoordinator, span_panel: SpanPanel, device_name: str | None = None
) -> list[str]:
    """Construct the expected solar sensor unique IDs directly."""
    solar_sensor_names = [
        "solar_current_power",
        "solar_produced_energy",
        "solar_consumed_energy",
        "solar_net_energy",
    ]
    return [
        construct_synthetic_unique_id_for_entry(coordinator, span_panel, name, device_name)
        for name in solar_sensor_names
    ]
