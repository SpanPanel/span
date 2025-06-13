"""The Span Panel integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant

from .const import COORDINATOR, DEFAULT_SCAN_INTERVAL, DOMAIN, NAME, CONF_USE_SSL
from .coordinator import SpanPanelCoordinator
from .entity_summary import log_entity_summary
from .options import Options
from .span_panel import SpanPanel

# Import config flow to ensure it's registered
from . import config_flow  # noqa: F401

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""
    config = entry.data
    host = config[CONF_HOST]
    name = "SpanPanel"

    _LOGGER.info("Setting up SPAN Panel integration for host: %s", host)

    use_ssl_value = config.get(CONF_USE_SSL, False)

    try:
        span_panel = SpanPanel(
            host=config[CONF_HOST],
            access_token=config[CONF_ACCESS_TOKEN],
            options=Options(entry),
            use_ssl=use_ssl_value,
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

        # Get scan interval from options with a default
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds
        )
        _LOGGER.debug("Using scan interval: %s seconds", scan_interval)

        coordinator = SpanPanelCoordinator(
            hass, span_panel, name, update_interval=scan_interval, config_entry=entry
        )
        _LOGGER.debug("Created coordinator: %s", coordinator)

        _LOGGER.debug("Performing initial data refresh")
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug(
            "Initial data refresh completed - coordinator data: %s",
            type(coordinator.data).__name__ if coordinator.data else "None",
        )

        entry.async_on_unload(entry.add_update_listener(update_listener))

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            COORDINATOR: coordinator,
            NAME: name,
        }

        _LOGGER.debug("Setting up platforms: %s", PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Platform setup completed")

        # Debug logging of entity summary
        _LOGGER.debug("Logging entity summary")
        log_entity_summary(coordinator, entry)

        _LOGGER.info("Successfully set up SPAN Panel integration")
        return True

    except Exception as e:
        _LOGGER.error("Failed to setup SPAN Panel integration: %s", e, exc_info=True)
        # Clean up on failure
        try:
            if "span_panel" in locals():
                await span_panel.close()
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
                if hasattr(span_panel, "close") and callable(span_panel.close):
                    await span_panel.close()
                    _LOGGER.debug("Successfully closed SpanPanel API client")
            except TypeError as e:
                # Handle non-awaitable objects gracefully (e.g., in tests)
                _LOGGER.debug(
                    "API close method is not awaitable, skipping cleanup: %s", e
                )
            except Exception as e:
                _LOGGER.error("Error during API cleanup: %s", e)
    else:
        _LOGGER.warning("No coordinator data found for entry %s", entry.entry_id)

    _LOGGER.debug("Unloading platforms: %s", PLATFORMS)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info("Successfully unloaded SPAN Panel integration")
    else:
        _LOGGER.error("Failed to unload some platforms")

    return bool(unload_ok)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    _LOGGER.info("Configuration options changed, reloading SPAN Panel integration")
    try:
        await hass.config_entries.async_reload(entry.entry_id)
        _LOGGER.info("Successfully reloaded SPAN Panel integration")
    except Exception as e:
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", e, exc_info=True)
