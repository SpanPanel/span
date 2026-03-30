"""The Span Panel integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from typing import cast

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import (
    CoreState,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.typing import ConfigType
from span_panel_api import SpanMqttClient, SpanPanelSnapshot
from span_panel_api.exceptions import (
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)
from span_panel_api.mqtt.models import MqttClientConfig
import voluptuous as vol

# Import config flow to ensure it's registered
from . import config_flow  # noqa: F401  # type: ignore[misc]
from .const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DEFAULT_SNAPSHOT_INTERVAL,
    DOMAIN,
    ENABLE_CURRENT_MONITORING,
)
from .coordinator import SpanPanelCoordinator
from .current_monitor import CurrentMonitor
from .helpers import build_circuit_unique_id
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    SNAPSHOT_UPDATE_INTERVAL,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from .util import snapshot_to_device_info
from .websocket import async_register_commands

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class SpanPanelRuntimeData:
    """Runtime data for a Span Panel config entry."""

    coordinator: SpanPanelCoordinator


type SpanPanelConfigEntry = ConfigEntry[SpanPanelRuntimeData]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)

# Must match the storage version produced by the latest supported entry format.
CURRENT_CONFIG_VERSION = 6

# Map internal device_type values to external manifest format
_DEVICE_TYPE_MAP: dict[str, str] = {"bess": "battery"}

PANEL_URL = "/span_panel_frontend"
PANEL_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Span Panel integration (domain-level, called once)."""
    _async_register_services(hass)
    _async_register_monitoring_services(hass)

    # Register sidebar panel serving the frontend JS bundle
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_URL, PANEL_FRONTEND_DIR, cache_headers=True)]
    )
    await async_register_panel(
        hass,
        webcomponent_name="span-panel",
        frontend_url_path="span-panel",
        sidebar_title="Span Panel",
        sidebar_icon="mdi:lightning-bolt",
        module_url=f"{PANEL_URL}/span-panel.js",
        require_admin=False,
        config={},
    )

    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: SpanPanelConfigEntry) -> bool:
    """Migrate config entry through successive versions.

    Supports upgrades from v1.3.1+ (config version 2) through to the
    current version 6. Each step mutates only the fields relevant to
    that version boundary.
    """
    if config_entry.version >= CURRENT_CONFIG_VERSION:
        return True

    _LOGGER.debug(
        "Migrating config entry %s from version %s to %s",
        config_entry.entry_id,
        config_entry.version,
        CURRENT_CONFIG_VERSION,
    )

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

    # --- v3 → v4: remove legacy solar/retry options ---
    if config_entry.version < 4:
        updated_options = dict(config_entry.options)
        updated_data = dict(config_entry.data)

        # Remove v1 solar options (no longer applicable)
        updated_options.pop("enable_solar_circuit", None)
        updated_options.pop("leg1", None)
        updated_options.pop("leg2", None)

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

    # --- v4 → v5: remove wwanLink binary sensor ---
    if config_entry.version < 5:
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

        removed = 0
        for entity in entities:
            if entity.domain == "binary_sensor" and entity.unique_id.endswith("_wwanLink"):
                entity_registry.async_remove(entity.entity_id)
                _LOGGER.info("Removed deprecated wwanLink binary sensor: %s", entity.entity_id)
                removed += 1

        if removed:
            _LOGGER.info("v4→v5 migration: removed %d deprecated entities", removed)

        hass.config_entries.async_update_entry(
            config_entry,
            version=5,
        )
        _LOGGER.debug("Migrated config entry %s to version 5", config_entry.entry_id)

    # --- v5 → v6: bump version ---
    if config_entry.version < 6:
        if config_entry.data.get(CONF_API_VERSION) == "simulation" or config_entry.data.get(
            "simulation_mode", False
        ):
            _LOGGER.warning(
                "Config entry '%s' is a built-in simulation entry which is no "
                "longer supported. Please remove it manually from Settings > "
                "Devices & Services",
                config_entry.title,
            )

        hass.config_entries.async_update_entry(
            config_entry,
            version=6,
        )
        _LOGGER.debug("Migrated config entry %s to version 6", config_entry.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""
    _LOGGER.debug("Setting up entry %s (version %s)", entry.entry_id, entry.version)

    # Register WebSocket commands once per HA instance
    domain_data: dict[str, bool] = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("websocket_registered"):
        domain_data["websocket_registered"] = True
        async_register_commands(hass)

    config = entry.data
    api_version = config.get(CONF_API_VERSION, "v1")

    # v1 entries: trigger reauthentication so user can provide v2 credentials
    if api_version == "v1":
        raise ConfigEntryAuthFailed(
            "This panel requires reauthentication. "
            "Please reauthenticate with your panel passphrase or proximity."
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
                raise ConfigEntryAuthFailed(  # noqa: TRY301
                    f"v2 panel is missing MQTT credentials ({', '.join(missing)}). "
                    "Please reauthenticate to provide a passphrase."
                )

            host = config[CONF_HOST]
            serial_number = entry.unique_id
            if not serial_number:
                raise ConfigEntryNotReady(  # noqa: TRY301
                    "Config entry has no unique_id (serial number)"
                )

            # The MQTT broker runs on the panel itself. The panel advertises
            # its own mDNS hostname (.local) as ebusBrokerHost, but mDNS
            # does not resolve across VLAN boundaries. Use the user-configured
            # panel host (IP or FQDN) which is known reachable.
            advertised_broker = config[CONF_EBUS_BROKER_HOST]
            if advertised_broker != host:
                _LOGGER.debug(
                    "Panel advertised broker host '%s' differs from configured "
                    "host '%s'; using configured host for MQTT connection",
                    advertised_broker,
                    host,
                )

            broker_config = MqttClientConfig(
                broker_host=host,
                username=config[CONF_EBUS_BROKER_USERNAME],
                password=config[CONF_EBUS_BROKER_PASSWORD],
                mqtts_port=int(config[CONF_EBUS_BROKER_PORT]),
            )

            panel_http_port = int(config.get(CONF_HTTP_PORT, 80))

            snapshot_interval = entry.options.get(
                SNAPSHOT_UPDATE_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL
            )
            client = SpanMqttClient(
                host,
                serial_number,
                broker_config,
                snapshot_interval=snapshot_interval,
                panel_http_port=panel_http_port,
            )
            try:
                await client.connect()
            except SpanPanelAuthError as err:
                await client.close()
                raise ConfigEntryAuthFailed(f"MQTT authentication failed: {err}") from err
            except (SpanPanelConnectionError, SpanPanelTimeoutError) as err:
                await client.close()
                raise ConfigEntryNotReady(f"Failed to connect to SPAN panel: {err}") from err

            coordinator = SpanPanelCoordinator(hass, client, entry)
            await coordinator.async_config_entry_first_refresh()
            await coordinator.async_setup_streaming()

            if entry.options.get(
                ENABLE_CURRENT_MONITORING, False
            ) or await CurrentMonitor.async_is_enabled(hass, entry):
                monitor = CurrentMonitor(hass, entry)
                await monitor.async_start()
                coordinator.current_monitor = monitor

        else:
            raise ConfigEntryError(  # noqa: TRY301
                f"Unknown api_version: {api_version}"
            )

        # --- Common setup for all transport modes ---

        entry.async_on_unload(entry.add_update_listener(update_listener))

        entry.runtime_data = SpanPanelRuntimeData(coordinator=coordinator)

        snapshot: SpanPanelSnapshot = coordinator.data
        serial_number = snapshot.serial_number

        base_name = "SPAN Panel"

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
    except Exception:
        if coordinator is not None:
            await coordinator.async_shutdown()
        raise
    else:
        return True


async def async_unload_entry(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading SPAN Panel integration")

    if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
        if entry.runtime_data.coordinator.current_monitor is not None:
            entry.runtime_data.coordinator.current_monitor.async_stop()
        await entry.runtime_data.coordinator.async_shutdown()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow manual removal of a device (e.g., stale EVSE sub-device).

    The main panel device cannot be removed — only sub-devices (like EVSE
    chargers) that are no longer present can be removed by the user.
    """
    if not hasattr(config_entry, "runtime_data") or config_entry.runtime_data is None:
        return True

    coordinator = config_entry.runtime_data.coordinator
    snapshot = coordinator.data

    # Identify the main panel device identifier
    panel_identifier = snapshot.serial_number

    # Prevent removal of the main panel device
    for identifier in device_entry.identifiers:
        if identifier == (DOMAIN, panel_identifier):
            return False

    return True


async def update_listener(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> None:
    """Handle options updates."""
    _LOGGER.debug("Configuration options changed for entry: %s", entry.entry_id)

    try:
        if hass.state is not CoreState.running:
            return

        await hass.config_entries.async_reload(entry.entry_id)
        _LOGGER.debug("Successfully reloaded SPAN Panel integration")

    except asyncio.CancelledError:
        raise
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", err)


async def ensure_device_registered(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
    snapshot: SpanPanelSnapshot,
    device_name: str,
) -> None:
    """Register or reconcile the HA Device before creating sensors.

    Ensures the device exists in the device registry with proper naming and
    identifiers.
    """
    device_registry = dr.async_get(hass)

    serial_number = snapshot.serial_number
    host = entry.data.get(CONF_HOST)

    existing_device = device_registry.async_get_device(identifiers={(DOMAIN, serial_number)})

    if existing_device:
        if existing_device.name == serial_number:
            device_registry.async_update_device(existing_device.id, name=device_name)
    else:
        device_info = snapshot_to_device_info(snapshot, device_name, host=host)
        device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain-level services (called once per HA instance)."""

    async def async_handle_export_manifest(
        _call: ServiceCall,
    ) -> ServiceResponse:
        """Export circuit topology manifest for all configured SPAN panels."""
        if not hass.config_entries.async_loaded_entries(DOMAIN):
            raise ServiceValidationError(
                "No SPAN panel configuration entries are loaded. "
                "Add and configure a SPAN panel before calling this service.",
                translation_domain=DOMAIN,
                translation_key="export_manifest_no_entries",
            )

        entity_reg = er.async_get(hass)
        panels = []

        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue

            snapshot = entry.runtime_data.coordinator.data
            serial = snapshot.serial_number
            circuits = []

            for circuit_id, circuit in snapshot.circuits.items():
                if circuit_id.startswith("unmapped_tab_"):
                    continue

                tabs = getattr(circuit, "tabs", None)
                if not tabs:
                    continue

                unique_id = build_circuit_unique_id(serial, circuit_id, "instantPowerW")
                entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
                if entity_id is None:
                    continue

                raw_type = getattr(circuit, "device_type", "circuit")

                circuits.append(
                    {
                        "entity_id": entity_id,
                        "template": f"clone_{min(tabs)}",
                        "device_type": _DEVICE_TYPE_MAP.get(raw_type, raw_type),
                        "tabs": list(tabs),
                    }
                )

            if circuits:
                panels.append(
                    {
                        "serial": serial,
                        "host": entry.data[CONF_HOST],
                        "circuits": circuits,
                    }
                )

        return cast(ServiceResponse, {"panels": panels})

    hass.services.async_register(
        DOMAIN,
        "export_circuit_manifest",
        async_handle_export_manifest,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )


def _build_set_circuit_threshold_schema() -> vol.Schema:
    """Build schema for set_circuit_threshold service."""
    return vol.Schema(
        {
            vol.Required("circuit_id"): str,
            vol.Optional(CONTINUOUS_THRESHOLD_PCT): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional(SPIKE_THRESHOLD_PCT): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional(WINDOW_DURATION_M): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("monitoring_enabled"): bool,
        }
    )


def _build_set_mains_threshold_schema() -> vol.Schema:
    """Build schema for set_mains_threshold service."""
    return vol.Schema(
        {
            vol.Required("leg"): str,
            vol.Optional(CONTINUOUS_THRESHOLD_PCT): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional(SPIKE_THRESHOLD_PCT): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional(WINDOW_DURATION_M): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("monitoring_enabled"): bool,
        }
    )


def _build_clear_circuit_threshold_schema() -> vol.Schema:
    """Build schema for clear_circuit_threshold service."""
    return vol.Schema({vol.Required("circuit_id"): str})


def _build_clear_mains_threshold_schema() -> vol.Schema:
    """Build schema for clear_mains_threshold service."""
    return vol.Schema({vol.Required("leg"): str})


def _build_set_global_monitoring_schema() -> vol.Schema:
    """Build schema for set_global_monitoring service."""
    return vol.Schema(
        {
            vol.Optional("continuous_threshold_pct"): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional("spike_threshold_pct"): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional("window_duration_m"): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("cooldown_duration_m"): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("notify_targets"): str,
            vol.Optional("enable_persistent_notifications"): bool,
            vol.Optional("enable_event_bus"): bool,
        }
    )


def _async_register_monitoring_services(hass: HomeAssistant) -> None:
    """Register current monitoring services."""

    def _get_runtime_data() -> tuple[SpanPanelRuntimeData, ConfigEntry] | None:
        """Find the first loaded SPAN panel runtime data and entry."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if hasattr(entry, "runtime_data") and isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                return entry.runtime_data, entry
        return None

    def _get_monitor(call: ServiceCall) -> CurrentMonitor:
        """Find the CurrentMonitor for the calling entry."""
        result = _get_runtime_data()
        if result is not None:
            runtime_data, _entry = result
            if runtime_data.coordinator.current_monitor is not None:
                return runtime_data.coordinator.current_monitor
        raise ServiceValidationError(
            "No SPAN panel with current monitoring enabled.",
            translation_domain=DOMAIN,
            translation_key="monitoring_not_enabled",
        )

    async def _get_or_create_monitor() -> CurrentMonitor:
        """Find or bootstrap a CurrentMonitor for the first loaded panel."""
        result = _get_runtime_data()
        if result is None:
            raise ServiceValidationError(
                "No SPAN panel integration loaded.",
                translation_domain=DOMAIN,
                translation_key="monitoring_not_enabled",
            )
        runtime_data, entry = result
        if runtime_data.coordinator.current_monitor is not None:
            return runtime_data.coordinator.current_monitor
        monitor = CurrentMonitor(hass, entry)
        await monitor.async_start()
        runtime_data.coordinator.current_monitor = monitor
        return monitor

    async def async_handle_set_circuit_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        data = dict(call.data)
        entity_id = data.pop("circuit_id")
        circuit_id = monitor.resolve_entity_to_circuit_id(entity_id)
        monitor.set_circuit_override(circuit_id, data)

    async def async_handle_clear_circuit_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        entity_id = call.data["circuit_id"]
        circuit_id = monitor.resolve_entity_to_circuit_id(entity_id)
        monitor.clear_circuit_override(circuit_id)

    async def async_handle_set_mains_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        data = dict(call.data)
        entity_id = data.pop("leg")
        leg = monitor.resolve_entity_to_mains_leg(entity_id)
        monitor.set_mains_override(leg, data)

    async def async_handle_clear_mains_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        entity_id = call.data["leg"]
        leg = monitor.resolve_entity_to_mains_leg(entity_id)
        monitor.clear_mains_override(leg)

    async def async_handle_get_monitoring_status(
        call: ServiceCall,
    ) -> ServiceResponse:
        result = _get_runtime_data()
        if result is None:
            return cast(ServiceResponse, {"enabled": False})
        runtime_data, _entry = result
        monitor = runtime_data.coordinator.current_monitor
        if monitor is None:
            return cast(ServiceResponse, {"enabled": False})
        status = monitor.get_monitoring_status()
        status["enabled"] = True
        return cast(ServiceResponse, status)

    hass.services.async_register(
        DOMAIN,
        "set_circuit_threshold",
        async_handle_set_circuit_threshold,
        schema=_build_set_circuit_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN,
        "clear_circuit_threshold",
        async_handle_clear_circuit_threshold,
        schema=_build_clear_circuit_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN,
        "set_mains_threshold",
        async_handle_set_mains_threshold,
        schema=_build_set_mains_threshold_schema(),
    )
    hass.services.async_register(
        DOMAIN,
        "clear_mains_threshold",
        async_handle_clear_mains_threshold,
        schema=_build_clear_mains_threshold_schema(),
    )

    async def async_handle_set_global_monitoring(call: ServiceCall) -> None:
        monitor = await _get_or_create_monitor()
        monitor.set_global_settings(dict(call.data))

    hass.services.async_register(
        DOMAIN,
        "get_monitoring_status",
        async_handle_get_monitoring_status,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "set_global_monitoring",
        async_handle_set_global_monitoring,
        schema=_build_set_global_monitoring_schema(),
    )
