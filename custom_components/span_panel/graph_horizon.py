"""Graph time horizon manager for SPAN Panel circuit charts.

Manages global default and per-circuit override horizons with HA Storage
persistence.  Mirrors the CurrentMonitor storage pattern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import DEFAULT_GRAPH_HORIZON, VALID_GRAPH_HORIZONS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "span_panel_graph_horizon"


class GraphHorizonManager:
    """Manages graph time horizon settings with per-circuit overrides."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the GraphHorizonManager."""
        self._hass = hass
        self._entry = entry
        self._global_horizon: str = DEFAULT_GRAPH_HORIZON
        self._circuit_overrides: dict[str, str] = {}
        self._subdevice_overrides: dict[str, str] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )

    def get_global_horizon(self) -> str:
        """Return the current global default horizon."""
        return self._global_horizon

    def set_global_horizon(self, horizon: str) -> None:
        """Set the global default horizon and prune matching overrides."""
        _validate_horizon(horizon)
        self._global_horizon = horizon
        self._circuit_overrides = {
            cid: h for cid, h in self._circuit_overrides.items() if h != horizon
        }
        self._subdevice_overrides = {
            sid: h for sid, h in self._subdevice_overrides.items() if h != horizon
        }
        self._hass.async_create_task(self.async_save())

    def get_effective_horizon(self, circuit_id: str) -> str:
        """Return the override horizon for a circuit, or the global default."""
        return self._circuit_overrides.get(circuit_id, self._global_horizon)

    def set_circuit_horizon(self, circuit_id: str, horizon: str) -> None:
        """Set a per-circuit horizon override."""
        _validate_horizon(horizon)
        if horizon == self._global_horizon:
            self._circuit_overrides.pop(circuit_id, None)
        else:
            self._circuit_overrides[circuit_id] = horizon
        self._hass.async_create_task(self.async_save())

    def clear_circuit_horizon(self, circuit_id: str) -> None:
        """Remove a per-circuit override, reverting to global."""
        self._circuit_overrides.pop(circuit_id, None)
        self._hass.async_create_task(self.async_save())

    def get_effective_subdevice_horizon(self, subdevice_id: str) -> str:
        """Return the override horizon for a sub-device, or the global default."""
        return self._subdevice_overrides.get(subdevice_id, self._global_horizon)

    def set_subdevice_horizon(self, subdevice_id: str, horizon: str) -> None:
        """Set a per-sub-device horizon override."""
        _validate_horizon(horizon)
        if horizon == self._global_horizon:
            self._subdevice_overrides.pop(subdevice_id, None)
        else:
            self._subdevice_overrides[subdevice_id] = horizon
        self._hass.async_create_task(self.async_save())

    def clear_subdevice_horizon(self, subdevice_id: str) -> None:
        """Remove a per-sub-device override, reverting to global."""
        self._subdevice_overrides.pop(subdevice_id, None)
        self._hass.async_create_task(self.async_save())

    def get_all_settings(self) -> dict[str, Any]:
        """Return full state for frontend consumption."""
        circuits: dict[str, dict[str, Any]] = {}
        for circuit_id, horizon in self._circuit_overrides.items():
            circuits[circuit_id] = {
                "horizon": horizon,
                "has_override": True,
            }
        sub_devices: dict[str, dict[str, Any]] = {}
        for subdevice_id, horizon in self._subdevice_overrides.items():
            sub_devices[subdevice_id] = {
                "horizon": horizon,
                "has_override": True,
            }
        return {
            "global_horizon": self._global_horizon,
            "circuits": circuits,
            "sub_devices": sub_devices,
        }

    async def async_load(self) -> None:
        """Load settings from HA Storage."""
        data = await self._store.async_load()
        if data is None:
            return
        stored_horizon = data.get("global_horizon", DEFAULT_GRAPH_HORIZON)
        if stored_horizon in VALID_GRAPH_HORIZONS:
            self._global_horizon = stored_horizon
        self._circuit_overrides = {
            cid: h
            for cid, h in data.get("circuit_overrides", {}).items()
            if h in VALID_GRAPH_HORIZONS
        }
        self._subdevice_overrides = {
            sid: h
            for sid, h in data.get("subdevice_overrides", {}).items()
            if h in VALID_GRAPH_HORIZONS
        }

    async def async_save(self) -> None:
        """Persist settings to HA Storage."""
        await self._store.async_save(
            {
                "global_horizon": self._global_horizon,
                "circuit_overrides": self._circuit_overrides,
                "subdevice_overrides": self._subdevice_overrides,
            }
        )


def _validate_horizon(horizon: str) -> None:
    """Raise ValueError if horizon is not a valid preset."""
    if horizon not in VALID_GRAPH_HORIZONS:
        msg = f"Invalid graph horizon '{horizon}'. Must be one of {VALID_GRAPH_HORIZONS}"
        raise ValueError(msg)
