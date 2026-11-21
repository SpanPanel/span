"""Graph time horizon manager for SPAN Panel circuit charts.

Manages global default and per-circuit override horizons with HA Storage
persistence.  Mirrors the CurrentMonitor storage pattern.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, TypedDict

from homeassistant.helpers.storage import Store

from .const import DEFAULT_GRAPH_HORIZON, VALID_GRAPH_HORIZONS
from .device_id_migration import rekeyed

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "span_panel_graph_horizon"


class HorizonOverride(TypedDict):
    """One circuit's or sub-device's own horizon, as the card reads it."""

    horizon: str
    has_override: bool


class GraphHorizonSettings(TypedDict):
    """Every horizon the card's charts follow: the global one, and each override by what it is for."""

    global_horizon: str
    circuits: dict[str, HorizonOverride]
    sub_devices: dict[str, HorizonOverride]


class StoredGraphHorizons(TypedDict):
    """One panel's graph horizons, as `GraphHorizonManager` saves them.

    Circuit horizons are keyed by circuit id, sub-device horizons by device
    registry id. A compile-time claim only; `_read_stored` is what the disk is
    held to.
    """

    global_horizon: str
    circuit_overrides: dict[str, str]
    subdevice_overrides: dict[str, str]


class GraphHorizonManager:
    """Manages graph time horizon settings with per-circuit overrides."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the GraphHorizonManager."""
        self._hass = hass
        self._entry = entry
        self._global_horizon: str = DEFAULT_GRAPH_HORIZON
        self._circuit_overrides: dict[str, str] = {}
        self._subdevice_overrides: dict[str, str] = {}
        self._store = _store_for(hass, entry)

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

    def get_all_settings(self) -> GraphHorizonSettings:
        """Return full state for frontend consumption."""
        return {
            "global_horizon": self._global_horizon,
            "circuits": {
                circuit_id: {"horizon": horizon, "has_override": True}
                for circuit_id, horizon in self._circuit_overrides.items()
            },
            "sub_devices": {
                subdevice_id: {"horizon": horizon, "has_override": True}
                for subdevice_id, horizon in self._subdevice_overrides.items()
            },
        }

    async def async_load(self) -> None:
        """Load settings from HA Storage."""
        data = _read_stored(await self._store.async_load())
        if data is None:
            return
        self._global_horizon = data["global_horizon"]
        self._circuit_overrides = data["circuit_overrides"]
        self._subdevice_overrides = data["subdevice_overrides"]

    async def async_save(self) -> None:
        """Persist settings to HA Storage."""
        await self._store.async_save(
            StoredGraphHorizons(
                global_horizon=self._global_horizon,
                circuit_overrides=self._circuit_overrides,
                subdevice_overrides=self._subdevice_overrides,
            )
        )


def _store_for(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredGraphHorizons]:
    """Return the store holding one panel's graph horizons."""
    return Store(hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}")


async def async_forget_graph_horizons(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the graph horizons when the config entry is removed.

    Keyed by the entry's id, so no entry would ever load them again: left
    behind, the file is one nothing owns.
    """
    await _store_for(hass, entry).async_remove()


def _read_stored(stored: object) -> StoredGraphHorizons | None:
    """Read the graph horizons off disk, keeping only valid horizons; None for nothing to read.

    The one reader of the store, for the manager's load and the device-id
    re-key alike. None for nothing stored, or anything but an object. A global
    horizon that is not a valid preset reads as the default, and an override
    that is not one, or is not keyed by a string, is dropped: the chart then
    uses the global horizon, as it would had the override never been set.
    """
    if not isinstance(stored, dict):
        return None
    global_horizon = stored.get("global_horizon")
    return StoredGraphHorizons(
        global_horizon=(
            global_horizon
            if isinstance(global_horizon, str) and global_horizon in VALID_GRAPH_HORIZONS
            else DEFAULT_GRAPH_HORIZON
        ),
        circuit_overrides=_read_horizons(stored.get("circuit_overrides")),
        subdevice_overrides=_read_horizons(stored.get("subdevice_overrides")),
    )


def _read_horizons(value: object) -> dict[str, str]:
    """Read one map of overrides: each valid horizon under a string key."""
    if not isinstance(value, dict):
        return {}
    return {
        key: horizon
        for key, horizon in value.items()
        if isinstance(key, str) and isinstance(horizon, str) and horizon in VALID_GRAPH_HORIZONS
    }


async def async_rekey_circuit_ids(
    hass: HomeAssistant, entry: ConfigEntry, mapping: Mapping[str, str]
) -> int:
    """Move the stored circuit horizons from old circuit ids to new ones, on disk.

    Run by the device-id migration before the manager loads the store. The
    sub-device horizons are keyed by device registry id, which the migration
    keeps, so they stay as they are. A value already under the new id wins
    (`device_id_migration.rekeyed`); every other part of the store is written
    back as the manager reads it (`_read_stored`). Written only when something
    moved. Returns how many horizons moved.
    """
    store = _store_for(hass, entry)
    data = _read_stored(await store.async_load())
    if data is None:
        return 0
    rekeyed_overrides, moved = rekeyed(data["circuit_overrides"], mapping)
    if moved:
        data["circuit_overrides"] = rekeyed_overrides
        await store.async_save(data)
    return moved


def _validate_horizon(horizon: str) -> None:
    """Raise ValueError if horizon is not a valid preset."""
    if horizon not in VALID_GRAPH_HORIZONS:
        msg = f"Invalid graph horizon '{horizon}'. Must be one of {VALID_GRAPH_HORIZONS}"
        raise ValueError(msg)
