"""The Span Panel integration."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os

import ha_synthetic_sensors
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify
import voluptuous as vol

# Import config flow to ensure it's registered
from . import config_flow  # noqa: F401  # type: ignore[misc]
from .const import (
    CONF_SIMULATION_CONFIG,
    CONF_SIMULATION_OFFLINE_MINUTES,
    CONF_SIMULATION_START_TIME,
    CONF_USE_SSL,
    COORDINATOR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NAME,
    SENSOR_SET,
    STORAGE_MANAGER,
)
from .coordinator import SpanPanelCoordinator
from .migration import (
    migrate_config_entry_to_synthetic_sensors,
)

# Handle solar options changes before reload (battery is now native sensor)
from .options import (
    ENERGY_REPORTING_GRACE_PERIOD,
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
    Options,
)
from .span_panel import SpanPanel
from .span_panel_api import SpanPanelAuthError
from .synthetic_sensors import (
    async_export_synthetic_config_service,
    cleanup_synthetic_sensors,
)
from .synthetic_solar import (
    handle_solar_options_change,
)
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

# Test that module loads
_LOGGER.debug("SPAN PANEL MODULE LOADED! Version: %s", CURRENT_CONFIG_VERSION)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry for synthetic sensor YAML generation."""

    if config_entry.version < CURRENT_CONFIG_VERSION:
        _LOGGER.debug(
            "Migrating config entry %s from version %s to %s for synthetic sensor setup",
            config_entry.entry_id,
            config_entry.version,
            CURRENT_CONFIG_VERSION,
        )

        # Migrate this config entry to synthetic sensors
        success = await migrate_config_entry_to_synthetic_sensors(hass, config_entry)

        if not success:
            _LOGGER.error("Failed to migrate config entry %s", config_entry.entry_id)
            return False

        # Set the post-migration stable flag to prevent future entity renames
        updated_options = dict(config_entry.options)
        updated_options["POST_MIGRATION_STABLE"] = True

        # Update config entry version and options using HA API
        hass.config_entries.async_update_entry(
            config_entry,
            data=config_entry.data,
            options=updated_options,
            title=config_entry.title,
            version=CURRENT_CONFIG_VERSION,
        )
        _LOGGER.debug(
            "Successfully migrated config entry %s to version %s and set POST_MIGRATION_STABLE flag",
            config_entry.entry_id,
            CURRENT_CONFIG_VERSION,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""
    _LOGGER.debug("SETUP ENTRY CALLED! Entry ID: %s, Version: %s", entry.entry_id, entry.version)

    # Configure ha-synthetic-sensors logging to match this integration's level
    try:
        # Use the same logging level as this integration
        integration_level = _LOGGER.getEffectiveLevel()
        ha_synthetic_sensors.configure_logging(integration_level)

        _LOGGER.debug(
            "Synthetic sensors logging configured to level %s",
            logging.getLevelName(integration_level),
        )

    except Exception as e:
        _LOGGER.warning("Failed to configure ha-synthetic-sensors logging: %s", e)

    config = entry.data
    host = config[CONF_HOST]
    name = "SpanPanel"

    _LOGGER.debug("DEBUG: Starting SPAN Panel integration setup for host: %s", host)

    use_ssl_value = config.get(CONF_USE_SSL, False)

    # Get scan interval from options with a default, with coercion and clamp
    raw_scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds)
    try:
        scan_interval = int(float(raw_scan_interval))
    except (TypeError, ValueError):
        scan_interval = int(DEFAULT_SCAN_INTERVAL.total_seconds())

    if scan_interval < 5:
        _LOGGER.debug(
            "Configured scan interval %s is below minimum; clamping to 5 seconds",
            scan_interval,
        )
        scan_interval = 5

    if str(raw_scan_interval) != str(scan_interval):
        _LOGGER.debug(
            "Coerced scan interval option from raw=%s to %s seconds",
            raw_scan_interval,
            scan_interval,
        )

    # Log at INFO so it is visible without debug logging
    _LOGGER.info(
        "Span Panel startup: using scan interval=%s seconds (raw option=%s)",
        scan_interval,
        raw_scan_interval,
    )

    # Determine simulation config path if in simulation mode
    simulation_mode = config.get("simulation_mode", False)
    simulation_config_path = None
    simulation_start_time = None
    simulation_offline_minutes = entry.options.get(CONF_SIMULATION_OFFLINE_MINUTES, 0)

    if simulation_mode:
        # Get the selected simulation config from config entry, default to 32-circuit
        selected_config = config.get(CONF_SIMULATION_CONFIG, "simulation_config_32_circuit")

        current_dir = os.path.dirname(__file__)
        simulation_config_path = os.path.join(
            current_dir, "simulation_configs", f"{selected_config}.yaml"
        )
        _LOGGER.debug(
            "Using simulation config: %s (selected: %s)", simulation_config_path, selected_config
        )

        # Get simulation start time from config entry data or options
        simulation_start_time_str = config.get(CONF_SIMULATION_START_TIME)
        if not simulation_start_time_str:
            # Try to get from options (for existing integrations)
            simulation_start_time_str = entry.options.get(CONF_SIMULATION_START_TIME)

        if simulation_start_time_str:
            try:
                simulation_start_time = datetime.fromisoformat(simulation_start_time_str)
                _LOGGER.debug("Using simulation start time: %s", simulation_start_time)
            except (ValueError, TypeError) as e:
                _LOGGER.warning(
                    "Invalid simulation start time format '%s': %s", simulation_start_time_str, e
                )
                simulation_start_time = None
    else:
        # Ensure no simulation parameters are used for live panels
        _LOGGER.debug("Simulation mode disabled - using live panel connection")

    try:
        span_panel = SpanPanel(
            host=config[CONF_HOST],
            access_token=config[CONF_ACCESS_TOKEN],
            options=Options(entry),
            use_ssl=use_ssl_value,
            scan_interval=scan_interval,
            simulation_mode=simulation_mode,
            simulation_config_path=simulation_config_path,
            simulation_start_time=simulation_start_time,
            simulation_offline_minutes=simulation_offline_minutes,
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
            try:
                auth_test_success = await span_panel.api.ping_with_auth()
                if not auth_test_success:
                    _LOGGER.error("Authenticated API test failed during setup")
                    raise ConnectionError("Failed to authenticate with SPAN Panel")
                _LOGGER.debug("Successfully tested authenticated connection")
            except SpanPanelAuthError as e:
                _LOGGER.error("Authentication error during setup: %s", e)
                _LOGGER.error(
                    "The stored access token may be invalid. Please reconfigure the integration with a new token."
                )
                raise ConnectionError(
                    f"Authentication failed: {e}. Please reconfigure with a new access token."
                ) from e
        else:
            auth_test_success = True

        _LOGGER.debug("Successfully set up and tested SPAN Panel API client")

        coordinator = SpanPanelCoordinator(hass, span_panel, entry)
        _LOGGER.debug("DEBUG: Created coordinator: %s", coordinator)

        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug(
            "DEBUG: Initial data refresh completed - coordinator data: %s",
            type(coordinator.data).__name__ if coordinator.data else "None",
        )

        entry.async_on_unload(entry.add_update_listener(update_listener))

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            COORDINATOR: coordinator,
            NAME: name,
        }

        # Generate default device name based on existing devices
        serial_number = span_panel.status.serial_number

        # Determine if this is a simulator
        is_simulator = any(
            [
                "sim" in serial_number.lower(),
                serial_number.lower().startswith("myserial"),
                serial_number.lower().startswith("span-sim"),
                entry.data.get(CONF_SIMULATION_CONFIG) is not None,
            ]
        )

        # Create smart default name
        base_name = "SPAN Simulator" if is_simulator else "SPAN Panel"
        _LOGGER.debug(
            "DEVICE_NAME_DEBUG: Base name selected: %s (is_simulator: %s)", base_name, is_simulator
        )

        # Check existing config entries to avoid conflicts
        existing_entries = hass.config_entries.async_entries(DOMAIN)
        existing_titles = {
            e.title
            for e in existing_entries
            if e.title and e.title != serial_number and e.entry_id != entry.entry_id
        }

        # Find unique name
        smart_device_name = base_name
        counter = 2
        while smart_device_name in existing_titles:
            smart_device_name = f"{base_name} {counter}"
            counter += 1

        # Update config entry title if it's currently the serial number
        if entry.title == serial_number:
            hass.config_entries.async_update_entry(entry, title=smart_device_name)

        # PHASE 1 Ensure device is registered BEFORE synthetic sensors are created
        await ensure_device_registered(hass, entry, span_panel, smart_device_name)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Register export synthetic config service
        if not hass.services.has_service(DOMAIN, "export_synthetic_config"):
            export_service_schema = vol.Schema(
                {
                    vol.Required("directory"): str,
                    vol.Required("sensor_set_id"): str,
                }
            )

            hass.services.async_register(
                DOMAIN,
                "export_synthetic_config",
                async_export_synthetic_config_service,
                schema=export_service_schema,
            )

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
    _LOGGER.debug("Unloading SPAN Panel integration")

    # Clean up synthetic sensor coordinator
    try:
        await cleanup_synthetic_sensors(entry)
    except Exception as e:
        _LOGGER.error("Error cleaning up synthetic sensors: %s", e)

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
        # Clean up any remaining orphaned entities for this config entry
        try:
            entity_registry = er.async_get(hass)
            orphaned_entities = [
                entity_id
                for entity_id, entity_entry in entity_registry.entities.items()
                if entity_entry.config_entry_id == entry.entry_id
                and entity_entry.platform == DOMAIN
            ]

            if orphaned_entities:
                _LOGGER.debug(
                    "Cleaning up %d orphaned entities during unload: %s",
                    len(orphaned_entities),
                    orphaned_entities,
                )
                for entity_id in orphaned_entities:
                    entity_registry.async_remove(entity_id)
                _LOGGER.info(
                    "Cleaned up %d orphaned entities during unload", len(orphaned_entities)
                )
        except Exception as e:
            _LOGGER.warning("Failed to cleanup orphaned entities during unload: %s", e)

        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.debug("Successfully unloaded SPAN Panel integration")
    else:
        _LOGGER.error("Failed to unload some platforms")

    return bool(unload_ok)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    _LOGGER.info("=== SPAN PANEL UPDATE LISTENER CALLED ===")
    _LOGGER.info("Configuration options changed, reloading SPAN Panel integration")
    _LOGGER.info("Update listener called with options: %s", entry.options)
    _LOGGER.info("Update listener called for entry_id: %s, title: %s", entry.entry_id, entry.title)
    try:
        # Check if Home Assistant is shutting down
        if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.not_running):
            _LOGGER.debug("Home Assistant is shutting down, skipping update listener")
            return

        # Get the coordinator from hass data
        coordinator_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        coordinator = coordinator_data.get(COORDINATOR)
        storage_manager = coordinator_data.get(STORAGE_MANAGER)
        sensor_set = coordinator_data.get(SENSOR_SET)

        _LOGGER.debug(
            "Update listener - coordinator: %s, storage_manager: %s, sensor_set: %s",
            coordinator is not None,
            storage_manager is not None,
            sensor_set is not None,
        )

        if coordinator and sensor_set:
            # Handle simulation options change (offline minutes, start time)
            simulation_offline_minutes = entry.options.get(CONF_SIMULATION_OFFLINE_MINUTES, 0)

            # Update simulation parameters in the existing API instance
            if hasattr(coordinator, "span_panel") and coordinator.span_panel:
                span_panel = coordinator.span_panel
                if hasattr(span_panel, "api") and span_panel.api:
                    _LOGGER.info(
                        "Found existing SpanPanel API instance, updating simulation parameters"
                    )

                    # Update simulation offline mode - this should start the offline timer from "now"
                    # The simulation_start_time is separate and used for the simulated time of day
                    span_panel.api.set_simulation_offline_mode(simulation_offline_minutes)
                else:
                    _LOGGER.warning("SpanPanel API instance not found in coordinator")
            else:
                _LOGGER.warning("SpanPanel instance not found in coordinator")

            # Handle solar options change
            solar_enabled = entry.options.get(INVERTER_ENABLE, False)
            # Coerce legs to integers to handle legacy string-stored options
            leg1_raw = entry.options.get(INVERTER_LEG1, 0)
            leg2_raw = entry.options.get(INVERTER_LEG2, 0)
            try:
                leg1 = int(leg1_raw)
            except (TypeError, ValueError):
                leg1 = 0
            try:
                leg2 = int(leg2_raw)
            except (TypeError, ValueError):
                leg2 = 0

            _LOGGER.debug(
                "Solar options change - enabled: %s, leg1: %s, leg2: %s", solar_enabled, leg1, leg2
            )
            _LOGGER.debug("Entry options: %s", entry.options)

            # Check if we're still running before handling solar options
            if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.not_running):
                _LOGGER.debug("Home Assistant is shutting down, skipping solar options change")
                return

            # Get device name from config entry
            device_name = entry.data.get("device_name", entry.title)
            result = await handle_solar_options_change(
                hass, entry, coordinator, sensor_set, solar_enabled, leg1, leg2, device_name
            )
            if not result:
                _LOGGER.warning("Solar options change failed")

            # Handle grace period global variable update
            old_grace_period = getattr(coordinator, "_last_grace_period", 15)
            new_grace_period = entry.options.get(ENERGY_REPORTING_GRACE_PERIOD, 15)

            if old_grace_period != new_grace_period:
                _LOGGER.info(
                    "Updating global grace period from %s to %s minutes",
                    old_grace_period,
                    new_grace_period,
                )
                try:
                    await sensor_set.async_set_global_variable(
                        "energy_grace_period_minutes", new_grace_period
                    )
                    # Store the new value for future comparisons
                    coordinator._last_grace_period = new_grace_period
                except Exception as e:
                    _LOGGER.error("Failed to update global grace period: %s", e)

            # Precision options are only set during initial setup

            # Battery is now handled as a native sensor during reload
            # No need for separate battery options handling

            # Start/refresh performance instrumentation immediately if enabled

        else:
            _LOGGER.warning("No coordinator or storage manager found for options change handling")

        # Check again if Home Assistant is shutting down before reload
        if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.not_running):
            _LOGGER.debug("Home Assistant is shutting down, skipping reload")
            return

        # Only reload if solar configuration changed, not for precision-only changes
        if _requires_full_reload(entry):
            await hass.config_entries.async_reload(entry.entry_id)
            _LOGGER.debug("Successfully reloaded SPAN Panel integration")
        else:
            _LOGGER.debug("No full reload needed - changes applied in place")
    except asyncio.CancelledError:
        _LOGGER.debug("Update listener was cancelled during shutdown")
        # Re-raise the CancelledError to properly handle the cancellation
        raise
    except Exception as e:
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", e, exc_info=True)


def _requires_full_reload(entry: ConfigEntry) -> bool:
    """Determine if a full integration reload is required based on option changes.

    Precision changes are handled in-place by handle_precision_options_change(),
    simulation options are handled in-place by update_listener(),
    while solar and other configuration changes require a full reload.

    Args:
        entry: The config entry with current options

    Returns:
        True if full reload is required, False if changes can be applied in-place

    """
    # Check if only simulation options changed
    simulation_options = {CONF_SIMULATION_OFFLINE_MINUTES, CONF_SIMULATION_START_TIME}
    current_options = set(entry.options.keys())

    _LOGGER.info(
        "_requires_full_reload check: current_options=%s, simulation_options=%s",
        current_options,
        simulation_options,
    )

    # If only simulation options are present, no reload needed
    if current_options.issubset(simulation_options):
        _LOGGER.info("Only simulation options changed - no reload needed")
        return False

    _LOGGER.info("Other options present - reload needed")

    # Since we don't track previous option values, we use a conservative approach:
    # - Precision changes are handled in-place (no reload needed)
    # - Simulation options are handled in-place (no reload needed)
    # - Solar configuration changes need reload
    # - Other changes (battery, naming, etc.) need reload

    # For now, reload for solar and other configuration changes.
    # The precision and simulation changes are applied in-place before this check.
    return True


async def ensure_device_registered(
    hass: HomeAssistant, entry: ConfigEntry, span_panel: SpanPanel, device_name: str
) -> None:
    """Register or reconcile the Home Assistant Device before creating sensors.

    Why this is necessary
    - Synthetic sensors must attach to a specific HA Device. Ensuring the device
      exists first prevents sensors from being created without a proper parent,
      which can lead to mis-grouping in the UI and harder migrations later.
    - Real panels: the device identifier is the hardware serial, which is stable.
      If the user renames the device, we may update the display name, but no
      reassignment is needed because the underlying identifier does not change.
    - Simulators: the device identifier is derived from the chosen device name
      (slug). If the simulator name changes (e.g., becomes unique like "SPAN Simulator 2"),
      the target identifier can change, and a new device may be created. In those
      cases, reassignment moves existing entities to the correct simulator device so
      grouping remains consistent. For fresh simulator entries, the device is
      registered before entities are created, so reassignment is a no-op; it mainly
      matters after a rename that changes the identifier.
    - Device renames: when a simulator device name changes, the identifier (slug) can
      change, creating a new device entry. Reassignment moves entities from the old
      device to the new one so grouping remains consistent. For fresh simulator
      entries, the device is registered before entities are created, so reassignment
      is a no-op.
    - Entity IDs are preserved via registry lookup by unique_id. Renaming the device
      does not retroactively rename existing entity_ids; only newly created entities
      use the current device name (when USE_DEVICE_PREFIX is enabled) for ID construction.
    - The device name is later used in several places (for example, as an
      entity_id prefix when USE_DEVICE_PREFIX is enabled and as a fallback for
      simulator device identifiers). Establishing the final device early ensures
      consistent naming and attachment.

    Behavior
    - If a device with the expected identifier exists, update its display name to
      the smart device name when appropriate; otherwise create a new device using
      ``panel_to_device_info`` with ``device_name``.
    - For simulator entries, move existing entities of this config entry to the
      target device if their current device_id differs, to maintain grouping and
      prevent collisions.

    Args:
        hass: Home Assistant instance
        entry: Config entry for this SPAN device/simulator
        span_panel: Connected SPAN panel API wrapper
        device_name: Smart device name to use for registry and naming

    """

    device_registry = dr.async_get(hass)

    _LOGGER.debug("DEVICE_NAME_DEBUG: Received device_name parameter: %s", device_name)

    # Check if device already exists
    serial_number = span_panel.status.serial_number
    # Use per-entry identifier for simulators to avoid collisions
    is_simulator = bool(entry.data.get("simulation_mode", False))
    desired_identifier = (
        slugify(device_name)
        if is_simulator and isinstance(device_name, str) and device_name
        else serial_number
    )
    existing_device = device_registry.async_get_device(identifiers={(DOMAIN, desired_identifier)})

    if existing_device:
        _LOGGER.debug("DEVICE_NAME_DEBUG: Found existing device: %s", existing_device.name)
        # If device exists but has wrong name (serial number), update it
        if existing_device.name == serial_number:
            _LOGGER.debug(
                "DEVICE_NAME_DEBUG: Updating device name from '%s' to '%s'",
                existing_device.name,
                device_name,
            )
            device_registry.async_update_device(existing_device.id, name=device_name)
        else:
            _LOGGER.debug(
                "DEVICE_NAME_DEBUG: Device already has correct name: %s", existing_device.name
            )
        target_device = existing_device
    else:
        _LOGGER.debug("DEVICE_NAME_DEBUG: No existing device found, creating new one")
        # Use the provided smart device name
        device_info = panel_to_device_info(span_panel, device_name)

        _LOGGER.debug("DEVICE_NAME_DEBUG: Generated device_info: %s", device_info)

        # Register device with smart default name - HA will use this as the suggested name in UI
        device = device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
        target_device = device

    # For simulators: after a device rename that changes the identifier, move this entry's
    # entities to the new target device. Fresh simulator entries register the device first
    # and won't need reassignment (no-op in that case).
    try:
        if is_simulator and target_device is not None:
            entity_registry = er.async_get(hass)
            entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
            for ent in entries:
                if ent.device_id != target_device.id:
                    _LOGGER.debug(
                        "DEVICE_REASSIGN: Moving entity %s from device %s to %s",
                        ent.entity_id,
                        ent.device_id,
                        target_device.id,
                    )
                    entity_registry.async_update_entity(ent.entity_id, device_id=target_device.id)
    except Exception as mig_err:
        _LOGGER.warning(
            "DEVICE_REASSIGN: Failed to reassign entities to target device: %s", mig_err
        )
