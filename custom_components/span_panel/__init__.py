"""The Span Panel integration."""

from __future__ import annotations

import logging

# Lightweight import for logging configuration only
import ha_synthetic_sensors
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

# Import config flow to ensure it's registered
from . import config_flow  # noqa: F401  # type: ignore[misc]
from .const import (
    CONF_USE_SSL,
    COORDINATOR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NAME,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_circuit_unique_id,
    build_panel_unique_id,
    build_synthetic_unique_id,
)
from .span_panel import SpanPanel
from .span_panel_api import Options
from .span_sensor_manager import SpanSensorManager
from .util import panel_to_device_info

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)

# Config entry version for unique ID consistency migration
CURRENT_CONFIG_VERSION = 2


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry for unique ID consistency."""
    _LOGGER.debug("Checking config entry version: %s", config_entry.version)

    if config_entry.version < CURRENT_CONFIG_VERSION:
        _LOGGER.info(
            "Migrating config entry from version %s to %s for unique ID consistency",
            config_entry.version,
            CURRENT_CONFIG_VERSION,
        )

        # Perform unique ID migration
        await migrate_unique_ids_for_consistency(hass, config_entry)

        # Update config entry version
        config_entry.version = CURRENT_CONFIG_VERSION
        _LOGGER.info("Successfully migrated config entry to version %s", CURRENT_CONFIG_VERSION)

    return True


async def migrate_unique_ids_for_consistency(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Migrate existing unique IDs to consistent pattern."""

    entity_registry = er.async_get(hass)

    # Get all entities for this config entry
    entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

    migration_count = 0
    for entity in entities:
        old_unique_id = entity.unique_id

        # Convert old inconsistent patterns to new consistent pattern
        new_unique_id = convert_to_consistent_unique_id_pattern(old_unique_id)

        if new_unique_id != old_unique_id:
            _LOGGER.info("Migrating unique ID: %s -> %s", old_unique_id, new_unique_id)

            # Update entity with new unique ID (Home Assistant handles previous_unique_id automatically)
            entity_registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)
            migration_count += 1

    _LOGGER.info("Migrated %d unique IDs to consistent pattern", migration_count)


def convert_to_consistent_unique_id_pattern(old_unique_id: str) -> str:
    """Convert old unique ID patterns to consistent format using pure helper functions.

    This function uses the same pure build functions that generate new unique IDs to ensure
    consistency. It extracts components from old unique IDs and reconstructs them using
    current generation logic as the single source of truth.
    """
    # Handle legacy synthetic patterns - convert to new format
    if "_synthetic_" in old_unique_id:
        # Legacy: span_abc123_synthetic_15_16_solar_inverter_instant_power
        # Extract serial and sensor name, let helper build the new format
        parts = old_unique_id.split("_synthetic_")
        if len(parts) == 2:
            serial = parts[0].replace("span_", "")
            remainder = parts[1]  # 15_16_solar_inverter_instant_power

            # Skip circuit numbers, get sensor name
            remainder_parts = remainder.split("_")
            if len(remainder_parts) >= 3:
                sensor_name = "_".join(remainder_parts[2:])  # solar_inverter_instant_power

                # Fix known legacy suffix inconsistencies
                if sensor_name.endswith("_instant_power"):
                    sensor_name = sensor_name.replace("_instant_power", "_power")

                # Let the helper build the correct unique ID
                return build_synthetic_unique_id(serial, sensor_name)

    # For other patterns, extract components and use pure build functions
    if old_unique_id.startswith("span_"):
        parts = old_unique_id.split("_")
        if len(parts) >= 3:
            serial = parts[1]  # abc123

            # Check if it's a circuit pattern
            if len(parts) >= 5 and parts[2] == "circuit":
                # Pattern: span_serial_circuit_uuid_suffix
                circuit_id = f"circuit_{parts[3]}"  # circuit_uuid
                old_suffix = parts[4]  # Just the suffix part

                # Use pure build function to generate what it should be
                return build_circuit_unique_id(serial, circuit_id, old_suffix)

            elif len(parts) >= 4 and len(parts[2]) == 32:
                # Pattern: span_serial_uuid_suffix (no circuit_ prefix)
                circuit_id = parts[2]  # uuid
                old_suffix = parts[3]  # Just the suffix part

                # Use pure build function to generate what it should be
                return build_circuit_unique_id(serial, circuit_id, old_suffix)

            else:
                # Panel pattern: span_serial_description_key
                description_key = "_".join(parts[2:])

                # Use pure build function to generate what it should be
                return build_panel_unique_id(serial, description_key)

    # If we can't parse it, return unchanged
    return old_unique_id


async def ensure_device_registered(
    hass: HomeAssistant, entry: ConfigEntry, span_panel: SpanPanel
) -> None:
    """Ensure SPAN device is registered in device registry before synthetic sensor creation."""
    device_registry = dr.async_get(hass)
    device_info = panel_to_device_info(span_panel)

    # Register device if it doesn't exist
    device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
    _LOGGER.debug(
        "DEVICE_REGISTRATION: Ensured device is registered for serial %s",
        span_panel.status.serial_number,
    )


async def setup_synthetic_sensors(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SpanPanelCoordinator
) -> None:
    """Set up synthetic sensors using proper submodule import pattern."""

    try:
        # Create SpanSensorManager for YAML generation
        unified_manager = SpanSensorManager(hass, entry)

        # Generate YAML configuration
        _LOGGER.debug("SYNTHETIC_SETUP_DEBUG: Generating YAML configuration")
        config_generated = await unified_manager.generate_unified_config(
            coordinator, coordinator.data
        )
        if not config_generated:
            _LOGGER.error("Failed to generate synthetic sensor YAML configuration")
            return
        _LOGGER.debug("SYNTHETIC_SETUP_DEBUG: Successfully generated YAML configuration")

        # Get YAML file path
        yaml_path_str = await unified_manager.get_config_file_path()
        if not yaml_path_str:
            _LOGGER.error("YAML file path not available for synthetic sensors")
            return

        # Get all backing entities that we can provide data for
        registered_entities = await unified_manager.get_registered_entity_ids(coordinator.data)
        _LOGGER.debug(
            "SYNTHETIC_SETUP_DEBUG: Got %d registered backing entities", len(registered_entities)
        )

        # Store the sensor manager and backing entities in hass.data for sensor.py to use
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}
        if entry.entry_id not in hass.data[DOMAIN]:
            hass.data[DOMAIN][entry.entry_id] = {}

        hass.data[DOMAIN][entry.entry_id]["synthetic_manager"] = unified_manager
        hass.data[DOMAIN][entry.entry_id]["backing_entities"] = registered_entities
        hass.data[DOMAIN][entry.entry_id]["yaml_path"] = yaml_path_str

        _LOGGER.info(
            "SYNTHETIC_SETUP_DEBUG: Synthetic sensors setup completed successfully using submodule imports"
        )

    except Exception as e:
        _LOGGER.error("Failed to set up synthetic sensors: %s", e, exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""

    # Configure ha-synthetic-sensors logging to match this integration's level
    try:
        # Use the same logging level as this integration
        integration_level = _LOGGER.getEffectiveLevel()
        ha_synthetic_sensors.configure_logging(integration_level)

        # Test that logging is working - this will output test messages
        if hasattr(ha_synthetic_sensors, "test_logging"):
            ha_synthetic_sensors.test_logging()

        # Check the configuration
        logging_info = ha_synthetic_sensors.get_logging_info()
        _LOGGER.debug(
            "Synthetic sensors logging configured to level %s: %s",
            logging.getLevelName(integration_level),
            logging_info,
        )
    except Exception as e:
        _LOGGER.warning("Failed to configure ha-synthetic-sensors logging: %s", e)

    config = entry.data
    host = config[CONF_HOST]
    name = "SpanPanel"

    _LOGGER.error("DEBUG: Starting SPAN Panel integration setup for host: %s", host)

    use_ssl_value = config.get(CONF_USE_SSL, False)

    # Get scan interval from options with a default
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds)
    _LOGGER.debug("Using scan interval: %s seconds", scan_interval)

    try:
        span_panel = SpanPanel(
            host=config[CONF_HOST],
            access_token=config[CONF_ACCESS_TOKEN],
            options=Options(entry),
            use_ssl=use_ssl_value,
            scan_interval=scan_interval,
        )

        _LOGGER.debug("Created SpanPanel instance: %s", span_panel)

        # Initialize the API client using Long-Lived Pattern
        await span_panel.api.setup()

        # Verify the connection is working by doing a test call
        _LOGGER.debug("Testing API connection...")
        test_success = await span_panel.api.ping()
        if not test_success:
            _LOGGER.error("API ping test failed during setup")
            raise ConnectionError("Failed to establish connection to SPAN Panel")

        # If we have a token, also test authenticated endpoints
        if span_panel.api.access_token:
            _LOGGER.debug("Testing authenticated API connection...")
            auth_test_success = await span_panel.api.ping_with_auth()
            if not auth_test_success:
                _LOGGER.error("Authenticated API test failed during setup")
                raise ConnectionError("Failed to authenticate with SPAN Panel")
            _LOGGER.debug("Successfully tested authenticated connection")

        _LOGGER.debug("Successfully set up and tested SPAN Panel API client")

        coordinator = SpanPanelCoordinator(
            hass, span_panel, name, update_interval=scan_interval, config_entry=entry
        )
        _LOGGER.error("DEBUG: Created coordinator: %s", coordinator)

        await coordinator.async_config_entry_first_refresh()
        _LOGGER.error(
            "DEBUG: Initial data refresh completed - coordinator data: %s",
            type(coordinator.data).__name__ if coordinator.data else "None",
        )

        entry.async_on_unload(entry.add_update_listener(update_listener))

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            COORDINATOR: coordinator,
            NAME: name,
        }

        # PHASE 1 FIX: Ensure device is registered BEFORE synthetic sensors are created
        await ensure_device_registered(hass, entry, span_panel)

        # Set up synthetic sensors with full YAML generation and registration BEFORE platforms
        try:
            await setup_synthetic_sensors(hass, entry, coordinator)
            _LOGGER.debug("Successfully set up synthetic sensors")
        except Exception as e:
            _LOGGER.error("Failed to set up synthetic sensors: %s", e, exc_info=True)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        return True

    except Exception as e:
        _LOGGER.error("Failed to setup SPAN Panel integration: %s", e, exc_info=True)
        # Clean up on failure
        try:
            if "span_panel" in locals():
                span_panel_instance = locals().get("span_panel")
                if isinstance(span_panel_instance, SpanPanel):
                    await span_panel_instance.close()
        except Exception as cleanup_error:
            _LOGGER.debug("Error during cleanup: %s", cleanup_error)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading SPAN Panel integration")

    # Get the coordinator and clean up resources
    coordinator_data = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator_data and COORDINATOR in coordinator_data:
        coordinator = coordinator_data[COORDINATOR]
        # Clean up the API client resources
        if hasattr(coordinator, "span_panel_api") and coordinator.span_panel_api:
            span_panel = coordinator.span_panel_api
            try:
                # SpanPanel has a close method that properly cleans up the API client
                if isinstance(span_panel, SpanPanel):
                    await span_panel.close()
                    _LOGGER.debug("Successfully closed SpanPanel API client")
            except TypeError as e:
                # Handle non-awaitable objects gracefully
                _LOGGER.debug("API close method is not awaitable, skipping cleanup: %s", e)
            except Exception as e:
                _LOGGER.error("Error during API cleanup: %s", e)
    else:
        _LOGGER.warning("No coordinator data found for entry %s", entry.entry_id)

    _LOGGER.debug("Unloading platforms: %s", PLATFORMS)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.debug("Successfully unloaded SPAN Panel integration")
    else:
        _LOGGER.error("Failed to unload some platforms")

    return bool(unload_ok)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    _LOGGER.info("Configuration options changed, reloading SPAN Panel integration")
    try:
        await hass.config_entries.async_reload(entry.entry_id)
        _LOGGER.debug("Successfully reloaded SPAN Panel integration")
    except Exception as e:
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", e, exc_info=True)
