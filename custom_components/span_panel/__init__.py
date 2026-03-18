"""The Span Panel integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import cast

from homeassistant.components.persistent_notification import async_create as pn_create
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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from span_panel_api import (
    SpanMqttClient,
    SpanPanelSnapshot,
    detect_api_version,
)
from span_panel_api.exceptions import (
    SpanPanelAuthError,
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
    CONF_HTTP_PORT,
    DEFAULT_SNAPSHOT_INTERVAL,
    DOMAIN,
)
from .coordinator import SpanPanelCoordinator
from .helpers import build_circuit_unique_id
from .migration import migrate_config_entry_sensors
from .options import SNAPSHOT_UPDATE_INTERVAL
from .util import snapshot_to_device_info
from .websocket import async_register_commands


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

# Config entry version — bumped to 6 for simulation removal
CURRENT_CONFIG_VERSION = 6

# Map internal device_type values to external manifest format
_DEVICE_TYPE_MAP: dict[str, str] = {"bess": "battery"}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Span Panel integration (domain-level, called once)."""
    _async_register_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry through successive versions."""

    if config_entry.version >= CURRENT_CONFIG_VERSION:
        return True

    # --- Gate: verify panel firmware before any schema changes ---
    # For entries from the v1 integration era (pre-v3 schema), probe the panel
    # to confirm it is running v2 firmware.  If the panel is still on v1
    # firmware, the v2 integration cannot operate it.  By returning False
    # *before* touching the schema we leave the config entry at its original
    # version so the user can safely roll back to the prior integration.
    if config_entry.version < 3 and not config_entry.data.get("simulation_mode", False):
        host = config_entry.data.get(CONF_HOST)
        if not host:
            _LOGGER.error(
                "Config entry %s has no host — cannot verify panel firmware",
                config_entry.entry_id,
            )
            return False

        try:
            detection = await detect_api_version(host)
        except Exception as err:
            _LOGGER.error(
                "Could not reach panel at %s to verify firmware version: %s. "
                "Migration deferred until the panel is reachable.",
                host,
                err,
            )
            pn_create(
                hass,
                f"Could not reach your SPAN Panel at **{host}** to verify its "
                "firmware version. The integration will not load until the panel "
                "is reachable. Please ensure the panel is online, then reload "
                "the integration.",
                title="SPAN Panel: Panel Unreachable During Migration",
                notification_id=f"span_panel_unreachable_{config_entry.entry_id}",
            )
            return False

        if detection.api_version != "v2":
            _LOGGER.error(
                "Panel at %s is running %s firmware. "
                "This version of the integration requires v2 firmware. "
                "Please upgrade your panel firmware, then reload the integration. "
                "You can also roll back to the previous integration version.",
                host,
                detection.api_version,
            )
            pn_create(
                hass,
                f"Your SPAN Panel at **{host}** is running **{detection.api_version}** "
                "firmware which is not compatible with this version of the "
                "integration. Please either:\n\n"
                "1. Upgrade the panel firmware to v2, then reload the integration.\n"
                "2. Roll back to the previous integration version.",
                title="SPAN Panel: Firmware Upgrade Required",
                notification_id=f"span_panel_v1_firmware_{config_entry.entry_id}",
            )
            return False

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

    # --- v4 → v5: remove wwanLink binary sensor ---
    if config_entry.version < 5:
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)

        removed = 0
        for entity in entities:
            # Remove wwanLink binary sensor (replaced by vendor_cloud regular sensor)
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

    # --- v5 → v6: reject simulation entries ---
    if config_entry.version < 6:
        if config_entry.data.get(CONF_API_VERSION) == "simulation" or config_entry.data.get(
            "simulation_mode", False
        ):
            pn_create(
                hass,
                "This SPAN Panel config entry was a **built-in simulator** which "
                "has been removed in this version. Please remove this entry and "
                "use the standalone SPAN simulator instead.",
                title="SPAN Panel: Simulation Entry Removed",
                notification_id=f"span_simulation_removed_{config_entry.entry_id}",
            )
            _LOGGER.warning(
                "Config entry %s is a simulation entry — rejecting migration",
                config_entry.entry_id,
            )
            return False

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
                raise ConfigEntryAuthFailed(
                    f"v2 panel is missing MQTT credentials ({', '.join(missing)}). "
                    "Please reauthenticate to provide a passphrase."
                )

            host = config[CONF_HOST]
            serial_number = entry.unique_id
            if not serial_number:
                raise ConfigEntryNotReady("Config entry has no unique_id (serial number)")

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

        else:
            raise ConfigEntryError(f"Unknown api_version: {api_version}")

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

        return True

    except Exception:
        if coordinator is not None:
            await coordinator.async_shutdown()
        raise


async def async_unload_entry(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading SPAN Panel integration")

    if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
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
    except Exception as e:
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", e, exc_info=True)


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
        """Export circuit manifest for all configured SPAN panels."""
        if not hass.config_entries.async_loaded_entries(DOMAIN):
            raise ServiceValidationError(
                "No SPAN panel config entries are loaded. "
                "Add and configure a SPAN panel before calling this service."
            )

        entity_reg = er.async_get(hass)
        panels = []

        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue

            snapshot = entry.runtime_data.coordinator.data
            if snapshot is None:
                continue

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
        supports_response=SupportsResponse.ONLY,
    )
