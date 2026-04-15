"""Service registration for the Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import voluptuous as vol

from .const import DEFAULT_GRAPH_HORIZON, DOMAIN, VALID_GRAPH_HORIZONS
from .current_monitor import CurrentMonitor
from .frontend import FavoriteKind, async_get_favorites, async_set_favorite
from .graph_horizon import GraphHorizonManager
from .id_builder import build_circuit_unique_id, extract_circuit_uuid_from_unique_id
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from . import SpanPanelRuntimeData

_LOGGER = logging.getLogger(__name__)

# Map internal device_type values to external manifest format
_DEVICE_TYPE_MAP: dict[str, str] = {"bess": "battery"}


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain-level services (called once per HA instance)."""

    async def async_handle_export_manifest(
        _call: ServiceCall,
    ) -> ServiceResponse:
        """Export circuit topology manifest for all configured SPAN panels."""
        from . import SpanPanelRuntimeData  # pylint: disable=import-outside-toplevel

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
            vol.Optional(COOLDOWN_DURATION_M): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("monitoring_enabled"): bool,
            vol.Optional("config_entry_id"): str,
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
            vol.Optional(COOLDOWN_DURATION_M): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("monitoring_enabled"): bool,
            vol.Optional("config_entry_id"): str,
        }
    )


def _build_clear_circuit_threshold_schema() -> vol.Schema:
    """Build schema for clear_circuit_threshold service."""
    return vol.Schema(
        {
            vol.Required("circuit_id"): str,
            vol.Optional("config_entry_id"): str,
        }
    )


def _build_clear_mains_threshold_schema() -> vol.Schema:
    """Build schema for clear_mains_threshold service."""
    return vol.Schema(
        {
            vol.Required("leg"): str,
            vol.Optional("config_entry_id"): str,
        }
    )


def _build_set_global_monitoring_schema() -> vol.Schema:
    """Build schema for set_global_monitoring service."""
    return vol.Schema(
        {
            vol.Optional("enabled"): bool,
            vol.Optional("continuous_threshold_pct"): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional("spike_threshold_pct"): vol.All(int, vol.Range(min=1, max=200)),
            vol.Optional("window_duration_m"): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("cooldown_duration_m"): vol.All(int, vol.Range(min=1, max=180)),
            vol.Optional("notify_targets"): str,
            vol.Optional("notification_title_template"): str,
            vol.Optional("notification_message_template"): str,
            vol.Optional("notification_priority"): vol.In(
                ["default", "passive", "active", "time-sensitive", "critical"]
            ),
            vol.Optional("config_entry_id"): str,
        }
    )


def _async_register_monitoring_services(hass: HomeAssistant) -> None:
    """Register current monitoring services."""

    def _get_runtime_data(
        config_entry_id: str | None = None,
    ) -> tuple[SpanPanelRuntimeData, ConfigEntry] | None:
        """Find SPAN panel runtime data and entry.

        When config_entry_id is provided, returns that specific entry.
        Otherwise falls back to the first loaded entry.
        """
        from . import SpanPanelRuntimeData  # pylint: disable=import-outside-toplevel

        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue
            if config_entry_id is None or entry.entry_id == config_entry_id:
                return entry.runtime_data, entry
        return None

    def _get_monitor(
        call: ServiceCall,
        config_entry_id: str | None = None,
    ) -> CurrentMonitor:
        """Find the CurrentMonitor for the given entry."""
        entry_id = config_entry_id or call.data.get("config_entry_id")
        result = _get_runtime_data(entry_id)
        if result is not None:
            runtime_data, _entry = result
            if runtime_data.coordinator.current_monitor is not None:
                return runtime_data.coordinator.current_monitor
        raise ServiceValidationError(
            "No SPAN panel with current monitoring enabled.",
            translation_domain=DOMAIN,
            translation_key="monitoring_not_enabled",
        )

    async def _get_or_create_monitor(
        config_entry_id: str | None = None,
    ) -> CurrentMonitor:
        """Find or bootstrap a CurrentMonitor for the specified panel."""
        result = _get_runtime_data(config_entry_id)
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
        # Seed the monitor with the coordinator's latest snapshot so that
        # get_monitoring_status returns circuits immediately (before the
        # next coordinator poll cycle).
        snapshot = runtime_data.coordinator.data
        if snapshot is not None:
            monitor.process_snapshot(snapshot)
        return monitor

    async def async_handle_set_circuit_threshold(call: ServiceCall) -> None:
        monitor = _get_monitor(call)
        data = dict(call.data)
        entity_id = data.pop("circuit_id")
        data.pop("config_entry_id", None)
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
        data.pop("config_entry_id", None)
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
        entry_id = call.data.get("config_entry_id")
        result = _get_runtime_data(entry_id)
        if result is None:
            return cast(ServiceResponse, {"enabled": False})
        runtime_data, _entry = result
        monitor = runtime_data.coordinator.current_monitor
        if monitor is None:
            return cast(ServiceResponse, {"enabled": False})
        status = monitor.get_monitoring_status()
        status["enabled"] = True
        status["global_settings"] = monitor.get_global_settings()
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
        data = dict(call.data)
        enabled = data.pop("enabled", None)
        entry_id = data.pop("config_entry_id", None)

        if enabled is False:
            # Disable monitoring: stop the monitor and mark storage as disabled
            result = _get_runtime_data(entry_id)
            if result is not None:
                runtime_data, entry = result
                monitor = runtime_data.coordinator.current_monitor
                if monitor is not None:
                    monitor.async_stop()
                    await monitor.async_save_disabled()
                    runtime_data.coordinator.current_monitor = None
            return

        monitor = await _get_or_create_monitor(entry_id)
        if data:
            monitor.set_global_settings(data)

    hass.services.async_register(
        DOMAIN,
        "get_monitoring_status",
        async_handle_get_monitoring_status,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "set_global_monitoring",
        async_handle_set_global_monitoring,
        schema=_build_set_global_monitoring_schema(),
    )

    async def async_handle_test_notification(call: ServiceCall) -> None:
        from .alert_dispatcher import dispatch_test_alert  # pylint: disable=import-outside-toplevel

        entry_id = call.data.get("config_entry_id")
        monitor = await _get_or_create_monitor(entry_id)
        settings = monitor.get_global_settings()
        dispatch_test_alert(hass, settings)

    hass.services.async_register(
        DOMAIN,
        "test_notification",
        async_handle_test_notification,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
    )


def _async_register_graph_horizon_services(hass: HomeAssistant) -> None:
    """Register graph time horizon services."""

    def _get_horizon_manager(
        call: ServiceCall,
    ) -> GraphHorizonManager:
        """Find the GraphHorizonManager for the given entry."""
        from . import SpanPanelRuntimeData  # pylint: disable=import-outside-toplevel

        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue
            if entry_id is None or entry.entry_id == entry_id:
                mgr = entry.runtime_data.coordinator.graph_horizon_manager
                if mgr is not None:
                    return mgr
        raise ServiceValidationError(
            "No SPAN panel with graph horizon manager found.",
            translation_domain=DOMAIN,
            translation_key="graph_horizon_not_available",
        )

    async def async_handle_set_graph_time_horizon(call: ServiceCall) -> None:
        manager = _get_horizon_manager(call)
        horizon = call.data["horizon"]
        manager.set_global_horizon(horizon)

    async def async_handle_set_circuit_graph_horizon(call: ServiceCall) -> None:
        manager = _get_horizon_manager(call)
        circuit_id = call.data["circuit_id"]
        horizon = call.data["horizon"]
        manager.set_circuit_horizon(circuit_id, horizon)

    async def async_handle_clear_circuit_graph_horizon(call: ServiceCall) -> None:
        manager = _get_horizon_manager(call)
        circuit_id = call.data["circuit_id"]
        manager.clear_circuit_horizon(circuit_id)

    async def async_handle_set_subdevice_graph_horizon(call: ServiceCall) -> None:
        manager = _get_horizon_manager(call)
        subdevice_id = call.data["subdevice_id"]
        horizon = call.data["horizon"]
        manager.set_subdevice_horizon(subdevice_id, horizon)

    async def async_handle_clear_subdevice_graph_horizon(call: ServiceCall) -> None:
        manager = _get_horizon_manager(call)
        subdevice_id = call.data["subdevice_id"]
        manager.clear_subdevice_horizon(subdevice_id)

    async def async_handle_get_graph_settings(
        call: ServiceCall,
    ) -> ServiceResponse:
        from . import SpanPanelRuntimeData  # pylint: disable=import-outside-toplevel

        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue
            if entry_id is None or entry.entry_id == entry_id:
                mgr = entry.runtime_data.coordinator.graph_horizon_manager
                if mgr is not None:
                    return cast(ServiceResponse, mgr.get_all_settings())
        return cast(ServiceResponse, {"global_horizon": DEFAULT_GRAPH_HORIZON, "circuits": {}})

    hass.services.async_register(
        DOMAIN,
        "set_graph_time_horizon",
        async_handle_set_graph_time_horizon,
        schema=vol.Schema(
            {
                vol.Required("horizon"): vol.In(VALID_GRAPH_HORIZONS),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_circuit_graph_horizon",
        async_handle_set_circuit_graph_horizon,
        schema=vol.Schema(
            {
                vol.Required("circuit_id"): str,
                vol.Required("horizon"): vol.In(VALID_GRAPH_HORIZONS),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "clear_circuit_graph_horizon",
        async_handle_clear_circuit_graph_horizon,
        schema=vol.Schema(
            {
                vol.Required("circuit_id"): str,
                vol.Optional("config_entry_id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_subdevice_graph_horizon",
        async_handle_set_subdevice_graph_horizon,
        schema=vol.Schema(
            {
                vol.Required("subdevice_id"): str,
                vol.Required("horizon"): vol.In(VALID_GRAPH_HORIZONS),
                vol.Optional("config_entry_id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "clear_subdevice_graph_horizon",
        async_handle_clear_subdevice_graph_horizon,
        schema=vol.Schema(
            {
                vol.Required("subdevice_id"): str,
                vol.Optional("config_entry_id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "get_graph_settings",
        async_handle_get_graph_settings,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
        supports_response=SupportsResponse.ONLY,
    )


def _async_register_favorites_services(hass: HomeAssistant) -> None:
    """Register cross-panel favorites services (domain-level).

    The public API takes ``entity_id`` — any sensor on a SPAN circuit or
    sub-device — and resolves it server-side to the internal
    ``(panel_device_id, kind, target_id)`` tuple used in storage. Circuit
    UUIDs and HA device IDs are not part of the user-visible surface.
    """

    def _resolve_entity_to_favorite_target(entity_id: str) -> tuple[str, FavoriteKind, str]:
        """Return ``(panel_device_id, kind, target_id)`` for a SPAN entity.

        ``kind`` is ``"circuits"`` or ``"sub_devices"``. For circuits,
        ``target_id`` is the panel-local circuit uuid (extracted from the
        entity's unique_id). For sub-devices, ``target_id`` is the HA
        device id of the BESS/EVSE; the panel id walks up via ``via_device_id``.

        Failure paths use distinct translation keys so users see the
        actual reason their pick was rejected.
        """
        entity_reg = er.async_get(hass)
        entry = entity_reg.async_get(entity_id)
        if entry is None or entry.platform != DOMAIN:
            raise ServiceValidationError(
                f"Entity {entity_id} is not a SPAN Panel entity.",
                translation_domain=DOMAIN,
                translation_key="favorite_not_span_entity",
                translation_placeholders={"entity_id": entity_id},
            )

        if entry.device_id is None:
            raise ServiceValidationError(
                f"Entity {entity_id} is not attached to a device.",
                translation_domain=DOMAIN,
                translation_key="favorite_no_device",
                translation_placeholders={"entity_id": entity_id},
            )

        device_registry = dr.async_get(hass)
        device_entry = device_registry.async_get(entry.device_id)
        if device_entry is None or not any(
            domain == DOMAIN for domain, _ in device_entry.identifiers
        ):
            raise ServiceValidationError(
                f"Entity {entity_id} does not belong to a SPAN Panel device.",
                translation_domain=DOMAIN,
                translation_key="favorite_not_span_entity",
                translation_placeholders={"entity_id": entity_id},
            )

        # Sub-device branch: resolve the parent main panel and store the
        # sub-device id directly. Sub-devices register with via_device_id;
        # main panels never do, so via_device_id presence is a reliable
        # discriminator (BESS / EVSE today).
        if device_entry.via_device_id is not None:
            parent_id = device_entry.via_device_id
            parent = device_registry.async_get(parent_id)
            if parent is None or not any(domain == DOMAIN for domain, _ in parent.identifiers):
                raise ServiceValidationError(
                    f"Sub-device {entity_id} has no SPAN Panel parent.",
                    translation_domain=DOMAIN,
                    translation_key="favorite_subdevice_no_span_parent",
                    translation_placeholders={"entity_id": entity_id},
                )
            return parent.id, "sub_devices", device_entry.id

        # Main-panel branch: extract circuit uuid from unique_id.
        # Format: ``span_{serial}_{circuit_uuid}_{suffix}``.
        if not entry.unique_id:
            raise ServiceValidationError(
                f"Entity {entity_id} has no unique id to resolve.",
                translation_domain=DOMAIN,
                translation_key="favorite_no_unique_id",
                translation_placeholders={"entity_id": entity_id},
            )
        circuit_uuid = extract_circuit_uuid_from_unique_id(entry.unique_id)
        if circuit_uuid is None:
            raise ServiceValidationError(
                f"Could not derive a favorite target from entity {entity_id}. "
                "Pick a circuit sensor (current/power) or a sub-device sensor.",
                translation_domain=DOMAIN,
                translation_key="favorite_no_circuit_uuid",
                translation_placeholders={"entity_id": entity_id},
            )

        return device_entry.id, "circuits", circuit_uuid

    async def async_handle_get_favorites(_call: ServiceCall) -> ServiceResponse:
        favorites = await async_get_favorites(hass)
        return cast(ServiceResponse, {"favorites": favorites})

    async def async_handle_add_favorite(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data["entity_id"]
        panel_device_id, kind, target_id = _resolve_entity_to_favorite_target(entity_id)
        favorites = await async_set_favorite(hass, panel_device_id, kind, target_id, True)
        return cast(ServiceResponse, {"favorites": favorites})

    async def async_handle_remove_favorite(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data["entity_id"]
        panel_device_id, kind, target_id = _resolve_entity_to_favorite_target(entity_id)
        favorites = await async_set_favorite(hass, panel_device_id, kind, target_id, False)
        return cast(ServiceResponse, {"favorites": favorites})

    _favorite_mutation_schema = vol.Schema({vol.Required("entity_id"): str})

    hass.services.async_register(
        DOMAIN,
        "get_favorites",
        async_handle_get_favorites,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "add_favorite",
        async_handle_add_favorite,
        schema=_favorite_mutation_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "remove_favorite",
        async_handle_remove_favorite,
        schema=_favorite_mutation_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )
