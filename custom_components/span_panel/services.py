"""Service registration for the Span Panel integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .const import DEFAULT_GRAPH_HORIZON, DOMAIN, VALID_GRAPH_HORIZONS
from .current_monitor import CurrentMonitor
from .graph_horizon import GraphHorizonManager
from .id_builder import build_circuit_unique_id
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
