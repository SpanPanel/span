"""The Span Panel integration."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify
from span_panel_api import (
    DynamicSimulationEngine,
    SpanMqttClient,
    SpanPanelSnapshot,
)
from span_panel_api.exceptions import (
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)
from span_panel_api.mqtt.models import MqttClientConfig

# Import config flow to ensure it's registered
from . import config_flow  # noqa: F401  # type: ignore[misc]
from .const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_SIMULATION_CONFIG,
    CONF_SIMULATION_OFFLINE_MINUTES,
    CONF_SIMULATION_START_TIME,
    COORDINATOR,
    DOMAIN,
    NAME,
)
from .coordinator import SpanPanelCoordinator
from .migration import migrate_config_entry_sensors
from .services import (
    async_setup_cleanup_energy_spikes_service,
    async_setup_main_meter_monitoring,
    async_setup_undo_stats_adjustments_service,
)
from .util import snapshot_to_device_info

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)

# Config entry version — bumped to 4 for v2 data path + solar migration
CURRENT_CONFIG_VERSION = 4


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry through successive versions."""

    if config_entry.version >= CURRENT_CONFIG_VERSION:
        return True

    _LOGGER.debug(
        "Migrating config entry %s from version %s to %s",
        config_entry.entry_id,
        config_entry.version,
        CURRENT_CONFIG_VERSION,
    )

    # --- v1 → v2: unique_id normalisation (existing logic) ---
    if config_entry.version < 2:
        migration_success = await migrate_config_entry_sensors(hass, config_entry)
        if not migration_success:
            _LOGGER.warning(
                "Migration v1→v2 failed for config entry %s",
                config_entry.entry_id,
            )
            return False

        hass.config_entries.async_update_entry(
            config_entry,
            data=config_entry.data,
            options=config_entry.options,
            title=config_entry.title,
            version=2,
        )
        _LOGGER.debug("Migrated config entry %s to version 2", config_entry.entry_id)

    # --- v2 → v3: add api_version field ---
    if config_entry.version < 3:
        updated_data = dict(config_entry.data)

        if updated_data.get("simulation_mode", False):
            updated_data[CONF_API_VERSION] = "simulation"
        else:
            updated_data[CONF_API_VERSION] = "v1"

        hass.config_entries.async_update_entry(
            config_entry,
            data=updated_data,
            options=config_entry.options,
            title=config_entry.title,
            version=3,
        )
        _LOGGER.debug("Migrated config entry %s to version 3", config_entry.entry_id)

    # --- v3 → v4: solar migration flag + remove solar/retry options ---
    if config_entry.version < 4:
        updated_options = dict(config_entry.options)
        updated_data = dict(config_entry.data)

        # Check if user had solar configured under v1 options layout
        solar_was_enabled = updated_options.pop("enable_solar_circuit", False)
        updated_options.pop("leg1", None)
        updated_options.pop("leg2", None)

        if solar_was_enabled:
            # PV circuit UUID is only known at runtime (from MQTT data),
            # so defer entity registry update to first coordinator refresh.
            updated_data["solar_migration_pending"] = True
            _LOGGER.info(
                "Solar was configured — setting solar_migration_pending flag "
                "for runtime entity registry migration"
            )

        # Remove v1 REST retry options (no longer applicable)
        for key in ("api_retries", "api_retry_timeout", "api_retry_backoff_multiplier"):
            updated_options.pop(key, None)

        hass.config_entries.async_update_entry(
            config_entry,
            data=updated_data,
            options=updated_options,
            version=4,
        )
        _LOGGER.debug("Migrated config entry %s to version 4", config_entry.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""
    _LOGGER.debug("Setting up entry %s (version %s)", entry.entry_id, entry.version)

    config = entry.data
    api_version = config.get(CONF_API_VERSION, "v1")

    # v1 entries: block until firmware upgrade
    if api_version == "v1":
        raise ConfigEntryNotReady(
            "This panel has v1 firmware which is no longer supported. "
            "Please upgrade to v2 firmware and reconfigure the integration."
        )

    coordinator: SpanPanelCoordinator | None = None

    try:
        # --- v2 MQTT entries ---
        if api_version == "v2":
            required_keys = (
                CONF_EBUS_BROKER_HOST,
                CONF_EBUS_BROKER_USERNAME,
                CONF_EBUS_BROKER_PASSWORD,
                CONF_EBUS_BROKER_PORT,
            )
            missing = [k for k in required_keys if not config.get(k)]
            if missing:
                raise ConfigEntryNotReady(
                    f"v2 panel is missing MQTT credentials ({', '.join(missing)}). "
                    "Please reconfigure the integration to provide a passphrase."
                )

            host = config[CONF_HOST]
            serial_number = entry.unique_id
            if not serial_number:
                raise ConfigEntryNotReady("Config entry has no unique_id (serial number)")

            broker_config = MqttClientConfig(
                broker_host=config[CONF_EBUS_BROKER_HOST],
                username=config[CONF_EBUS_BROKER_USERNAME],
                password=config[CONF_EBUS_BROKER_PASSWORD],
                mqtts_port=int(config[CONF_EBUS_BROKER_PORT]),
            )

            client = SpanMqttClient(host, serial_number, broker_config)
            try:
                await client.connect()
            except (SpanPanelConnectionError, SpanPanelTimeoutError) as err:
                await client.close()
                raise ConfigEntryNotReady(f"Failed to connect to SPAN panel: {err}") from err

            coordinator = SpanPanelCoordinator(hass, client, entry, is_streaming=True)
            await coordinator.async_config_entry_first_refresh()
            await coordinator.async_setup_streaming()

        # --- Simulation entries ---
        elif api_version == "simulation":
            selected_config = config.get(CONF_SIMULATION_CONFIG, "simulation_config_32_circuit")
            current_dir = os.path.dirname(__file__)
            config_path = Path(current_dir) / "simulation_configs" / f"{selected_config}.yaml"

            serial_number = entry.unique_id or f"SPAN-SIM-{entry.entry_id[:8]}"

            engine = DynamicSimulationEngine(
                serial_number=serial_number,
                config_path=config_path,
            )
            await engine.initialize_async()

            # Apply simulation start time override if configured
            simulation_start_time_str = config.get(CONF_SIMULATION_START_TIME) or entry.options.get(
                CONF_SIMULATION_START_TIME
            )
            if simulation_start_time_str:
                try:
                    datetime.fromisoformat(simulation_start_time_str)
                    engine.override_simulation_start_time(simulation_start_time_str)
                    _LOGGER.debug("Using simulation start time: %s", simulation_start_time_str)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Invalid simulation start time '%s': %s",
                        simulation_start_time_str,
                        e,
                    )

            coordinator = SpanPanelCoordinator(hass, engine, entry, is_streaming=False)
            await coordinator.async_config_entry_first_refresh()

            # Apply simulation offline mode if configured
            simulation_offline_minutes = entry.options.get(CONF_SIMULATION_OFFLINE_MINUTES, 0)
            if simulation_offline_minutes > 0:
                coordinator.set_simulation_offline_mode(simulation_offline_minutes)

        else:
            raise ConfigEntryNotReady(f"Unknown api_version: {api_version}")

        # --- Common setup for all transport modes ---

        entry.async_on_unload(entry.add_update_listener(update_listener))

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            COORDINATOR: coordinator,
            NAME: "SpanPanel",
        }

        snapshot: SpanPanelSnapshot = coordinator.data
        serial_number = snapshot.serial_number

        is_simulator = api_version == "simulation"

        # Create smart default name
        base_name = "SPAN Simulator" if is_simulator else "SPAN Panel"

        # Check existing config entries to avoid conflicts
        existing_entries = hass.config_entries.async_entries(DOMAIN)
        existing_titles = {
            e.title
            for e in existing_entries
            if e.title and e.title != serial_number and e.entry_id != entry.entry_id
        }

        smart_device_name = base_name
        counter = 2
        while smart_device_name in existing_titles:
            smart_device_name = f"{base_name} {counter}"
            counter += 1

        # Update config entry title if it's currently the serial number
        if entry.title == serial_number:
            hass.config_entries.async_update_entry(entry, title=smart_device_name)

        await ensure_device_registered(hass, entry, snapshot, smart_device_name)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Register services
        await async_setup_cleanup_energy_spikes_service(hass)
        await async_setup_undo_stats_adjustments_service(hass)

        # Set up main meter monitoring for firmware reset detection
        unsub_main_meter = await async_setup_main_meter_monitoring(hass, entry.entry_id)
        if unsub_main_meter:
            entry.async_on_unload(unsub_main_meter)

        return True

    except Exception:
        if coordinator is not None:
            await coordinator.async_shutdown()
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading SPAN Panel integration")

    coordinator_data = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator_data and COORDINATOR in coordinator_data:
        coordinator: SpanPanelCoordinator = coordinator_data[COORDINATOR]
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return bool(unload_ok)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry and clean up all associated data."""
    _LOGGER.debug("Removing SPAN Panel integration entry: %s", entry.entry_id)

    if entry.state.recoverable:
        await async_unload_entry(hass, entry)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates."""
    _LOGGER.debug("Configuration options changed for entry: %s", entry.entry_id)

    try:
        if hass.state is not CoreState.running:
            return

        coordinator_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        coordinator: SpanPanelCoordinator | None = coordinator_data.get(COORDINATOR)

        if coordinator:
            # Update simulation offline mode if this is a simulation entry
            api_version = entry.data.get(CONF_API_VERSION)
            if api_version == "simulation":
                simulation_offline_minutes = entry.options.get(CONF_SIMULATION_OFFLINE_MINUTES, 0)
                _LOGGER.info(
                    "Update listener: processing simulation_offline_minutes = %s",
                    simulation_offline_minutes,
                )
                coordinator.set_simulation_offline_mode(simulation_offline_minutes)

        if hass.state is not CoreState.running:
            return

        if _requires_full_reload(entry):
            await hass.config_entries.async_reload(entry.entry_id)
            _LOGGER.debug("Successfully reloaded SPAN Panel integration")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", e, exc_info=True)


def _requires_full_reload(entry: ConfigEntry) -> bool:
    """Determine if a full integration reload is required.

    Simulation-only option changes (offline minutes, start time) are applied
    in-place via the coordinator and do not require a reload.
    """
    has_simulation_flag = entry.options.get("_simulation_only_change", False)
    if has_simulation_flag:
        _LOGGER.debug("Simulation-only change detected - no reload needed")
        return False

    return True


async def ensure_device_registered(
    hass: HomeAssistant,
    entry: ConfigEntry,
    snapshot: SpanPanelSnapshot,
    device_name: str,
) -> None:
    """Register or reconcile the HA Device before creating sensors.

    Ensures the device exists in the device registry with proper naming and
    identifiers. For simulators, moves existing entities to the correct device
    if the identifier changed due to a name change.
    """
    device_registry = dr.async_get(hass)

    serial_number = snapshot.serial_number
    is_simulator = entry.data.get(CONF_API_VERSION) == "simulation"
    host = entry.data.get(CONF_HOST)

    desired_identifier = slugify(device_name) if is_simulator and device_name else serial_number
    existing_device = device_registry.async_get_device(identifiers={(DOMAIN, desired_identifier)})

    if existing_device:
        if existing_device.name == serial_number:
            device_registry.async_update_device(existing_device.id, name=device_name)
        target_device = existing_device
    else:
        device_info = snapshot_to_device_info(snapshot, device_name, is_simulator, host)
        device = device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
        target_device = device

    # For simulators: move entities to the target device if their current device differs
    try:
        if is_simulator:
            entity_registry = er.async_get(hass)
            entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
            for ent in entries:
                if ent.device_id != target_device.id:
                    _LOGGER.debug(
                        "Moving entity %s from device %s to %s",
                        ent.entity_id,
                        ent.device_id,
                        target_device.id,
                    )
                    entity_registry.async_update_entity(ent.entity_id, device_id=target_device.id)
    except (KeyError, ValueError, AttributeError) as err:
        _LOGGER.warning("Failed to reassign entities to target device: %s", err)
