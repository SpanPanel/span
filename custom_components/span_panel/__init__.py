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
from homeassistant.helpers import device_registry as dr

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
