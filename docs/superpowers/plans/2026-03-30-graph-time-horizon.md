# Graph Time Horizon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable time horizons to circuit graphs with recorder-backed full history, global defaults, and per-circuit overrides persisted in HA
Storage.

**Architecture:** New `GraphHorizonManager` backend class (mirroring `CurrentMonitor`) with HA Storage persistence, service calls, and a read endpoint. Frontend
gains a `GraphSettingsCache`, expanded Settings tab with circuit table, side panel graph section, and adaptive history loading per circuit horizon.
Synchronization via `graph-settings-changed` custom events.

**Tech Stack:** Python (HA integration), vanilla JS (Lovelace card), HA Storage API, HA WebSocket/service calls, ECharts via `ha-chart-base`.

**Spec:** `docs/superpowers/specs/2026-03-30-graph-time-horizon-design.md`

---

## Tasks

### Task 1: Backend — GraphHorizonManager class

**Files:**

- Create: `custom_components/span_panel/graph_horizon.py`
- Create: `tests/test_graph_horizon.py`
- Modify: `custom_components/span_panel/const.py:81` (after `EVENT_CURRENT_ALERT`)

- [ ] **Step 1: Add constants to `const.py`**

Add after line 81 (`EVENT_CURRENT_ALERT = "span_panel_current_alert"`):

```python
# Graph time horizon configuration
VALID_GRAPH_HORIZONS: Final[tuple[str, ...]] = ("5m", "1h", "1d", "1M")
DEFAULT_GRAPH_HORIZON = "5m"
```

- [ ] **Step 2: Write failing tests for GraphHorizonManager**

Create `tests/test_graph_horizon.py`:

```python
"""Tests for the GraphHorizonManager class."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.span_panel.const import (
    DEFAULT_GRAPH_HORIZON,
    VALID_GRAPH_HORIZONS,
)
from custom_components.span_panel.graph_horizon import GraphHorizonManager


def _make_hass():
    """Create a minimal mock hass object."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda c: c)
    return hass


def _make_manager(hass=None, entry_id="test_entry"):
    """Create a GraphHorizonManager with mocked hass and entry."""
    if hass is None:
        hass = _make_hass()
    entry = MagicMock()
    entry.entry_id = entry_id
    return GraphHorizonManager(hass, entry)


class TestGlobalHorizon:
    """Tests for global horizon get/set."""

    def test_default_global_horizon(self):
        manager = _make_manager()
        assert manager.get_global_horizon() == DEFAULT_GRAPH_HORIZON

    def test_set_global_horizon(self):
        manager = _make_manager()
        manager.set_global_horizon("1h")
        assert manager.get_global_horizon() == "1h"

    def test_set_invalid_horizon_raises(self):
        manager = _make_manager()
        with pytest.raises(ValueError, match="Invalid graph horizon"):
            manager.set_global_horizon("2h")

    def test_set_global_prunes_matching_overrides(self):
        """When global changes to match an override, the override is removed."""
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1h")
        assert manager.get_effective_horizon("circuit_1") == "1h"
        manager.set_global_horizon("1h")
        # Override should be pruned since it now matches global
        assert "circuit_1" not in manager._circuit_overrides


class TestCircuitOverrides:
    """Tests for per-circuit horizon overrides."""

    def test_effective_horizon_returns_global_when_no_override(self):
        manager = _make_manager()
        assert manager.get_effective_horizon("circuit_1") == DEFAULT_GRAPH_HORIZON

    def test_set_circuit_override(self):
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1d")
        assert manager.get_effective_horizon("circuit_1") == "1d"

    def test_set_circuit_invalid_horizon_raises(self):
        manager = _make_manager()
        with pytest.raises(ValueError, match="Invalid graph horizon"):
            manager.set_circuit_horizon("circuit_1", "bad")

    def test_set_circuit_matching_global_removes_override(self):
        """Setting a circuit to the global value removes the override."""
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1h")
        assert "circuit_1" in manager._circuit_overrides
        manager.set_circuit_horizon("circuit_1", DEFAULT_GRAPH_HORIZON)
        assert "circuit_1" not in manager._circuit_overrides

    def test_clear_circuit_override(self):
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1d")
        manager.clear_circuit_horizon("circuit_1")
        assert manager.get_effective_horizon("circuit_1") == DEFAULT_GRAPH_HORIZON

    def test_clear_nonexistent_override_is_noop(self):
        manager = _make_manager()
        manager.clear_circuit_horizon("nonexistent")  # should not raise


class TestGetAllSettings:
    """Tests for get_all_settings output."""

    def test_returns_global_and_empty_circuits(self):
        manager = _make_manager()
        settings = manager.get_all_settings()
        assert settings["global_horizon"] == DEFAULT_GRAPH_HORIZON
        assert settings["circuits"] == {}

    def test_returns_overrides_with_has_override_flag(self):
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1M")
        settings = manager.get_all_settings()
        assert settings["circuits"]["circuit_1"] == {
            "horizon": "1M",
            "has_override": True,
        }


class TestStoragePersistence:
    """Tests for async_load and async_save."""

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self):
        hass = _make_hass()
        manager = _make_manager(hass)
        manager.set_global_horizon("1d")
        manager.set_circuit_horizon("c1", "1M")
        manager.set_circuit_horizon("c2", "1h")

        # Capture what was saved
        saved_data = {}

        async def fake_save(data):
            saved_data.update(data)

        manager._store = MagicMock()
        manager._store.async_save = AsyncMock(side_effect=fake_save)
        await manager.async_save()

        assert saved_data["global_horizon"] == "1d"
        assert saved_data["circuit_overrides"] == {"c1": "1M", "c2": "1h"}

        # Load into a fresh manager
        manager2 = _make_manager(hass)
        manager2._store = MagicMock()
        manager2._store.async_load = AsyncMock(return_value=saved_data)
        await manager2.async_load()

        assert manager2.get_global_horizon() == "1d"
        assert manager2.get_effective_horizon("c1") == "1M"
        assert manager2.get_effective_horizon("c2") == "1h"

    @pytest.mark.asyncio
    async def test_load_handles_no_existing_data(self):
        hass = _make_hass()
        manager = _make_manager(hass)
        manager._store = MagicMock()
        manager._store.async_load = AsyncMock(return_value=None)
        await manager.async_load()
        assert manager.get_global_horizon() == DEFAULT_GRAPH_HORIZON
        assert manager._circuit_overrides == {}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_graph_horizon.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.span_panel.graph_horizon'`

- [ ] **Step 4: Implement GraphHorizonManager**

Create `custom_components/span_panel/graph_horizon.py`:

```python
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
        self._hass = hass
        self._entry = entry
        self._global_horizon: str = DEFAULT_GRAPH_HORIZON
        self._circuit_overrides: dict[str, str] = {}
        self._store: Store = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )

    # --- Public API ---

    def get_global_horizon(self) -> str:
        """Return the current global default horizon."""
        return self._global_horizon

    def set_global_horizon(self, horizon: str) -> None:
        """Set the global default horizon and prune matching overrides."""
        _validate_horizon(horizon)
        self._global_horizon = horizon
        # Remove overrides that now match the new global
        self._circuit_overrides = {
            cid: h
            for cid, h in self._circuit_overrides.items()
            if h != horizon
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

    def get_all_settings(self) -> dict[str, Any]:
        """Return full state for frontend consumption."""
        circuits: dict[str, dict[str, Any]] = {}
        for circuit_id, horizon in self._circuit_overrides.items():
            circuits[circuit_id] = {
                "horizon": horizon,
                "has_override": True,
            }
        return {
            "global_horizon": self._global_horizon,
            "circuits": circuits,
        }

    # --- Storage ---

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

    async def async_save(self) -> None:
        """Persist settings to HA Storage."""
        await self._store.async_save(
            {
                "global_horizon": self._global_horizon,
                "circuit_overrides": self._circuit_overrides,
            }
        )


def _validate_horizon(horizon: str) -> None:
    """Raise ValueError if horizon is not a valid preset."""
    if horizon not in VALID_GRAPH_HORIZONS:
        msg = f"Invalid graph horizon '{horizon}'. Must be one of {VALID_GRAPH_HORIZONS}"
        raise ValueError(msg)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_graph_horizon.py -v`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/bflood/projects/HA/span
git add custom_components/span_panel/const.py custom_components/span_panel/graph_horizon.py tests/test_graph_horizon.py
git commit -m "feat: add GraphHorizonManager with storage persistence and tests"
```

---

### Task 2: Backend — Service calls and read endpoint

**Files:**

- Modify: `custom_components/span_panel/__init__.py:630-788` (add new registration function)
- Modify: `custom_components/span_panel/__init__.py:348-357` (instantiate manager)
- Modify: `custom_components/span_panel/__init__.py:404-413` (unload)
- Modify: `custom_components/span_panel/services.yaml` (add service definitions)
- Create: `tests/test_graph_horizon_services.py`

- [ ] **Step 1: Write failing tests for service integration**

Create `tests/test_graph_horizon_services.py`:

```python
"""Tests for graph horizon service call handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.span_panel.const import DEFAULT_GRAPH_HORIZON


def _make_hass():
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda c: c)
    return hass


def _make_runtime_data(hass, entry_id="test_entry"):
    """Create mock runtime data with a GraphHorizonManager."""
    from custom_components.span_panel.graph_horizon import GraphHorizonManager

    entry = MagicMock()
    entry.entry_id = entry_id

    manager = GraphHorizonManager(hass, entry)
    # Prevent actual storage writes
    manager._store = MagicMock()
    manager._store.async_save = AsyncMock()
    manager._store.async_load = AsyncMock(return_value=None)

    coordinator = MagicMock()
    coordinator.graph_horizon_manager = manager

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator

    return runtime_data, manager


class TestSetGraphTimeHorizon:
    """Tests for the set_graph_time_horizon service."""

    @pytest.mark.asyncio
    async def test_set_global_horizon(self):
        from custom_components.span_panel.graph_horizon import GraphHorizonManager

        hass = _make_hass()
        runtime_data, manager = _make_runtime_data(hass)

        manager.set_global_horizon("1h")
        assert manager.get_global_horizon() == "1h"

    @pytest.mark.asyncio
    async def test_set_invalid_horizon_raises(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        with pytest.raises(ValueError):
            manager.set_global_horizon("invalid")


class TestSetCircuitGraphHorizon:
    """Tests for the set_circuit_graph_horizon service."""

    @pytest.mark.asyncio
    async def test_set_circuit_override(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        manager.set_circuit_horizon("c1", "1d")
        assert manager.get_effective_horizon("c1") == "1d"

    @pytest.mark.asyncio
    async def test_clear_circuit_override(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        manager.set_circuit_horizon("c1", "1d")
        manager.clear_circuit_horizon("c1")
        assert manager.get_effective_horizon("c1") == DEFAULT_GRAPH_HORIZON


class TestGetGraphSettings:
    """Tests for the get_graph_settings service."""

    @pytest.mark.asyncio
    async def test_returns_settings(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        manager.set_circuit_horizon("c1", "1M")
        result = manager.get_all_settings()

        assert result["global_horizon"] == DEFAULT_GRAPH_HORIZON
        assert result["circuits"]["c1"]["horizon"] == "1M"
        assert result["circuits"]["c1"]["has_override"] is True
```

- [ ] **Step 2: Run tests to verify they pass** (these test the manager directly, ensuring it works before wiring services)

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/test_graph_horizon_services.py -v`

Expected: All PASS.

- [ ] **Step 3: Add service definitions to `services.yaml`**

Append to end of `custom_components/span_panel/services.yaml`:

```yaml
set_graph_time_horizon:
  fields:
    horizon:
      required: true
      selector:
        select:
          options:
            - "5m"
            - "1h"
            - "1d"
            - "1M"

set_circuit_graph_horizon:
  fields:
    circuit_id:
      required: true
      selector:
        text:
    horizon:
      required: true
      selector:
        select:
          options:
            - "5m"
            - "1h"
            - "1d"
            - "1M"

clear_circuit_graph_horizon:
  fields:
    circuit_id:
      required: true
      selector:
        text:

get_graph_settings:
```

- [ ] **Step 4: Wire service handlers and manager instantiation in `__init__.py`**

Add import at the top of `__init__.py` alongside the `CurrentMonitor` import:

```python
from .graph_horizon import GraphHorizonManager
```

In `async_setup_entry()`, after the CurrentMonitor block (after line 357), add GraphHorizonManager instantiation:

```python
            graph_horizon = GraphHorizonManager(hass, entry)
            await graph_horizon.async_load()
            coordinator.graph_horizon_manager = graph_horizon
```

In `async_setup()` (around line 155 where `_async_register_monitoring_services` is called), add:

```python
    _async_register_graph_horizon_services(hass)
```

Add the new registration function after `_async_register_monitoring_services()` (after line 788):

```python
def _async_register_graph_horizon_services(hass: HomeAssistant) -> None:
    """Register graph time horizon services."""

    def _get_horizon_manager(
        call: ServiceCall,
    ) -> GraphHorizonManager:
        """Find the GraphHorizonManager for the given entry."""
        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue
            if entry_id is None or entry.entry_id == entry_id:
                mgr = getattr(entry.runtime_data.coordinator, "graph_horizon_manager", None)
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

    async def async_handle_get_graph_settings(
        call: ServiceCall,
    ) -> ServiceResponse:
        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or not isinstance(
                entry.runtime_data, SpanPanelRuntimeData
            ):
                continue
            if entry_id is None or entry.entry_id == entry_id:
                mgr = getattr(entry.runtime_data.coordinator, "graph_horizon_manager", None)
                if mgr is not None:
                    return cast(ServiceResponse, mgr.get_all_settings())
        return cast(ServiceResponse, {"global_horizon": "5m", "circuits": {}})

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
        "get_graph_settings",
        async_handle_get_graph_settings,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
        supports_response=SupportsResponse.ONLY,
    )
```

Also add the import for `VALID_GRAPH_HORIZONS` to the const imports block in `__init__.py`:

```python
from .const import (
    ...
    VALID_GRAPH_HORIZONS,
)
```

- [ ] **Step 5: Run all backend tests**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -q`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/bflood/projects/HA/span
git add custom_components/span_panel/__init__.py custom_components/span_panel/services.yaml tests/test_graph_horizon_services.py
git commit -m "feat: register graph horizon service calls and read endpoint"
```

---

### Task 3: Frontend — Constants and GraphSettingsCache

**Files:**

- Modify: `src/constants.js:11` (after `LIVE_SAMPLE_INTERVAL_MS`)
- Create: `src/core/graph-settings.js`
- Modify: `src/i18n.js:65-67` (after settings keys, add horizon keys)

- [ ] **Step 1: Add GRAPH_HORIZONS to constants.js**

Add after line 11 (`export const LIVE_SAMPLE_INTERVAL_MS = 1000;`):

```javascript
// ── Graph time horizon presets ─────────────────────────────────────────────

export const DEFAULT_GRAPH_HORIZON = "5m";

export const GRAPH_HORIZONS = {
  "5m": { ms: 5 * 60 * 1000, refreshMs: 1000, useRealtime: true },
  "1h": { ms: 60 * 60 * 1000, refreshMs: 30000, useRealtime: false },
  "1d": { ms: 24 * 60 * 60 * 1000, refreshMs: 60000, useRealtime: false },
  "1M": { ms: 30 * 24 * 60 * 60 * 1000, refreshMs: 60000, useRealtime: false },
};
```

- [ ] **Step 2: Add i18n keys**

In `src/i18n.js`, after the existing settings keys (line 67, after `"settings.open_link"`), add:

```javascript
    // Graph time horizon
    "horizon.5m": "5 Minutes",
    "horizon.1h": "1 Hour",
    "horizon.1d": "1 Day",
    "horizon.1M": "1 Month",
    "settings.graph_horizon_heading": "Graph Time Horizon",
    "settings.graph_horizon_description": "Default time window for all circuit graphs. Individual circuits can override this in their settings panel.",
    "settings.global_default": "Global Default",
    "settings.default_scale": "Default Scale",
    "settings.circuit_graph_scales": "Circuit Graph Scales",
    "settings.col.circuit": "Circuit",
    "settings.col.scale": "Scale",
    "sidepanel.graph_horizon": "Graph Time Horizon",
    "sidepanel.graph_horizon_failed": "Graph horizon update failed:",
    "sidepanel.clear_graph_horizon_failed": "Clear graph horizon failed:",
```

Add matching keys to the other language blocks (`es`, `fr`, `ja`, `pt`) — use the English values as placeholders since the frontend falls back to English for
missing keys. For each language block, add the same keys after their respective `settings.open_link` line.

- [ ] **Step 3: Create GraphSettingsCache**

Create `src/core/graph-settings.js`:

```javascript
// src/core/graph-settings.js
import { INTEGRATION_DOMAIN, DEFAULT_GRAPH_HORIZON } from "../constants.js";

const GRAPH_SETTINGS_POLL_INTERVAL_MS = 30_000;

/**
 * Caches graph horizon settings fetched via the get_graph_settings service.
 * Re-fetches at most every 30 seconds unless invalidated.
 */
export class GraphSettingsCache {
  constructor() {
    this._settings = null;
    this._lastFetch = 0;
    this._fetching = false;
  }

  /**
   * Fetch graph settings, returning cached data if recent.
   * @param {object} hass - Home Assistant instance
   * @param {string} [configEntryId] - Optional config entry ID
   * @returns {Promise<object|null>} Graph settings or null
   */
  async fetch(hass, configEntryId) {
    const now = Date.now();
    if (this._fetching) return this._settings;
    if (this._settings && now - this._lastFetch < GRAPH_SETTINGS_POLL_INTERVAL_MS) {
      return this._settings;
    }

    this._fetching = true;
    try {
      const serviceData = {};
      if (configEntryId) serviceData.config_entry_id = configEntryId;
      const resp = await hass.callWS({
        type: "call_service",
        domain: INTEGRATION_DOMAIN,
        service: "get_graph_settings",
        service_data: serviceData,
        return_response: true,
      });
      this._settings = resp?.response || null;
      this._lastFetch = now;
    } catch {
      this._settings = null;
    } finally {
      this._fetching = false;
    }
    return this._settings;
  }

  /** Force the next fetch() call to re-query the backend. */
  invalidate() {
    this._lastFetch = 0;
  }

  /** @returns {object|null} Last fetched settings */
  get settings() {
    return this._settings;
  }

  /** Clear cached settings (e.g., on config change). */
  clear() {
    this._settings = null;
    this._lastFetch = 0;
  }
}

/**
 * Get the effective horizon for a circuit.
 * @param {object|null} settings - Full graph settings from get_graph_settings
 * @param {string} circuitId - Circuit identifier
 * @returns {string} Horizon key (e.g., "5m", "1h")
 */
export function getEffectiveHorizon(settings, circuitId) {
  if (!settings) return DEFAULT_GRAPH_HORIZON;
  const override = settings.circuits?.[circuitId];
  if (override?.has_override) return override.horizon;
  return settings.global_horizon || DEFAULT_GRAPH_HORIZON;
}
```

- [ ] **Step 4: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/constants.js src/core/graph-settings.js src/i18n.js
git commit -m "feat: add GRAPH_HORIZONS constants, GraphSettingsCache, and i18n keys"
```

---

### Task 4: Frontend — Settings tab expansion

**Files:**

- Modify: `src/panel/tab-settings.js` (full rewrite)
- Modify: `src/panel/span-panel.js:225-229` (pass hass and topology to settings tab)

- [ ] **Step 1: Update span-panel.js to pass hass and topology to SettingsTab**

In `src/panel/span-panel.js`, change the settings case in `_renderTab()` (lines 225-230) from:

```javascript
      case "settings": {
        container.innerHTML = "";
        const selectedDevice = this._panels.find(p => p.id === this._selectedPanelId);
        const configEntryId = selectedDevice?.config_entries?.[0] || null;
        this._settingsTab.render(container, configEntryId);
        break;
      }
```

to:

```javascript
      case "settings": {
        container.innerHTML = "";
        const selectedDevice = this._panels.find(p => p.id === this._selectedPanelId);
        const configEntryId = selectedDevice?.config_entries?.[0] || null;
        await this._settingsTab.render(container, this._hass, configEntryId);
        break;
      }
```

- [ ] **Step 2: Rewrite tab-settings.js**

Replace `src/panel/tab-settings.js` with:

```javascript
import { INTEGRATION_DOMAIN, GRAPH_HORIZONS, DEFAULT_GRAPH_HORIZON } from "../constants.js";
import { escapeHtml } from "../helpers/sanitize.js";
import { t } from "../i18n.js";

const CELL_SELECT_STYLE = `
  background:var(--secondary-background-color,#333);
  border:1px solid var(--divider-color);
  color:var(--primary-text-color);
  border-radius:3px;padding:4px 8px;font-size:0.85em;
`;

function horizonOptions(selectedKey) {
  return Object.keys(GRAPH_HORIZONS)
    .map(key => `<option value="${key}" ${key === selectedKey ? "selected" : ""}>${t(`horizon.${key}`)}</option>`)
    .join("");
}

export class SettingsTab {
  constructor() {
    this._debounceTimer = null;
    this._configEntryId = null;
  }

  async render(container, hass, configEntryId) {
    if (configEntryId !== undefined) this._configEntryId = configEntryId;

    // Fetch graph settings
    let graphSettings = null;
    try {
      const serviceData = {};
      if (this._configEntryId) serviceData.config_entry_id = this._configEntryId;
      const resp = await hass.callWS({
        type: "call_service",
        domain: INTEGRATION_DOMAIN,
        service: "get_graph_settings",
        service_data: serviceData,
        return_response: true,
      });
      graphSettings = resp?.response || null;
    } catch {
      graphSettings = null;
    }

    // Fetch topology for circuit names
    let topology = null;
    try {
      topology = await hass.callWS({
        type: "span_panel/panel_topology",
        device_id: null,
      });
    } catch {
      // topology unavailable — circuit table won't render
    }

    const globalHorizon = graphSettings?.global_horizon || DEFAULT_GRAPH_HORIZON;
    const circuitOverrides = graphSettings?.circuits || {};

    // Build circuit rows sorted by name
    const circuits = [];
    if (topology?.circuits) {
      for (const [uuid, circuit] of Object.entries(topology.circuits)) {
        const name = circuit.name || uuid;
        const override = circuitOverrides[uuid];
        const effectiveHorizon = override?.has_override ? override.horizon : globalHorizon;
        const hasOverride = override?.has_override === true;
        circuits.push({ uuid, name, effectiveHorizon, hasOverride });
      }
    }
    circuits.sort((a, b) => a.name.localeCompare(b.name));

    const circuitRows = circuits
      .map(c => {
        const eid = escapeHtml(c.uuid);
        return `
          <tr style="border-bottom:1px solid var(--divider-color,#333);">
            <td style="padding:8px;">
              <span>${escapeHtml(c.name)}</span>
            </td>
            <td style="padding:8px;">
              <select class="horizon-select" data-circuit="${eid}" style="${CELL_SELECT_STYLE}">
                ${horizonOptions(c.effectiveHorizon)}
              </select>
            </td>
            <td style="padding:8px;">
              ${
                c.hasOverride
                  ? `<button class="reset-btn" data-circuit="${eid}"
                       style="background:none;border:1px solid var(--divider-color);color:var(--primary-text-color);border-radius:4px;padding:3px 6px;cursor:pointer;font-size:0.75em;">
                    ${t("monitoring.reset")}
                  </button>`
                  : ""
              }
            </td>
          </tr>
        `;
      })
      .join("");

    const href = this._configEntryId
      ? `/config/integrations/integration/span_panel#config_entry=${this._configEntryId}`
      : "/config/integrations/integration/span_panel";

    container.innerHTML = `
      <div style="padding:16px;">
        <h2 style="margin-top:0;">${t("settings.heading")}</h2>
        <p style="color:var(--secondary-text-color);margin-bottom:16px;">
          ${t("settings.description")}
        </p>
        <a href="${href}"
           style="color:var(--primary-color);text-decoration:none;">
          ${t("settings.open_link")} &rarr;
        </a>

        <hr style="border:none;border-top:1px solid var(--divider-color);margin:20px 0;">

        <h2 style="margin-top:0;">${t("settings.graph_horizon_heading")}</h2>

        <div style="margin-bottom:20px;padding:14px;background:var(--secondary-background-color,#252530);border-radius:8px;">
          <h3 style="margin:0 0 12px;font-size:0.95em;">${t("settings.global_default")}</h3>
          <div style="display:flex;align-items:center;gap:12px;">
            <span style="font-size:0.85em;color:var(--secondary-text-color);min-width:100px;">${t("settings.default_scale")}</span>
            <select id="global-horizon" style="${CELL_SELECT_STYLE}">
              ${horizonOptions(globalHorizon)}
            </select>
          </div>
          <div style="font-size:0.75em;color:var(--secondary-text-color);margin-top:8px;">
            ${t("settings.graph_horizon_description")}
          </div>
        </div>

        ${
          circuits.length > 0
            ? `
          <h3 style="font-size:0.95em;">${t("settings.circuit_graph_scales")}</h3>
          <table style="width:100%;border-collapse:collapse;">
            <thead>
              <tr style="text-align:left;border-bottom:1px solid var(--divider-color);">
                <th style="padding:6px 8px;">${t("settings.col.circuit")}</th>
                <th style="padding:6px 8px;">${t("settings.col.scale")}</th>
                <th style="padding:6px 8px;"></th>
              </tr>
            </thead>
            <tbody>
              ${circuitRows}
            </tbody>
          </table>
        `
            : ""
        }

        <div id="settings-status" style="font-size:0.8em;color:var(--secondary-text-color);margin-top:8px;min-height:1.2em;"></div>
      </div>
    `;

    this._bindGlobalHorizon(container, hass);
    this._bindCircuitHorizons(container, hass);
    this._bindResetButtons(container, hass);
  }

  _serviceData(data) {
    if (this._configEntryId) data.config_entry_id = this._configEntryId;
    return data;
  }

  _bindGlobalHorizon(container, hass) {
    const select = container.querySelector("#global-horizon");
    if (!select) return;

    select.addEventListener("change", async () => {
      try {
        await hass.callWS({
          type: "call_service",
          domain: INTEGRATION_DOMAIN,
          service: "set_graph_time_horizon",
          service_data: this._serviceData({ horizon: select.value }),
        });
        container.dispatchEvent(new CustomEvent("graph-settings-changed", { bubbles: true, composed: true }));
        await this.render(container, hass);
      } catch (err) {
        const status = container.querySelector("#settings-status");
        if (status) {
          status.textContent = `${t("error.prefix")} ${err.message || t("error.failed_save")}`;
          status.style.color = "var(--error-color, #f44336)";
        }
      }
    });
  }

  _bindCircuitHorizons(container, hass) {
    const timers = new Map();
    for (const select of container.querySelectorAll(".horizon-select")) {
      select.addEventListener("change", () => {
        const circuitId = select.dataset.circuit;
        const key = `horizon-${circuitId}`;
        clearTimeout(timers.get(key));
        timers.set(
          key,
          setTimeout(async () => {
            try {
              await hass.callWS({
                type: "call_service",
                domain: INTEGRATION_DOMAIN,
                service: "set_circuit_graph_horizon",
                service_data: this._serviceData({ circuit_id: circuitId, horizon: select.value }),
              });
              container.dispatchEvent(new CustomEvent("graph-settings-changed", { bubbles: true, composed: true }));
              await this.render(container, hass);
            } catch (err) {
              select.style.borderColor = "var(--error-color, #f44336)";
            }
          }, 500)
        );
      });
    }
  }

  _bindResetButtons(container, hass) {
    for (const btn of container.querySelectorAll(".reset-btn")) {
      btn.addEventListener("click", async () => {
        const circuitId = btn.dataset.circuit;
        try {
          await hass.callWS({
            type: "call_service",
            domain: INTEGRATION_DOMAIN,
            service: "clear_circuit_graph_horizon",
            service_data: this._serviceData({ circuit_id: circuitId }),
          });
          container.dispatchEvent(new CustomEvent("graph-settings-changed", { bubbles: true, composed: true }));
          await this.render(container, hass);
        } catch (err) {
          const status = container.querySelector("#settings-status");
          if (status) {
            status.textContent = `${t("error.prefix")} ${err.message || t("error.failed")}`;
            status.style.color = "var(--error-color, #f44336)";
          }
        }
      });
    }
  }
}
```

- [ ] **Step 3: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/panel/tab-settings.js src/panel/span-panel.js
git commit -m "feat: expand Settings tab with global + per-circuit graph horizon controls"
```

---

### Task 5: Frontend — Side panel graph horizon section

**Files:**

- Modify: `src/core/side-panel.js:276-294` (add graph horizon section in `_renderCircuitMode`)
- Modify: `src/panel/tab-dashboard.js:156-164` (pass graph horizon info when opening side panel)

- [ ] **Step 1: Add `_renderGraphHorizonSection` to side-panel.js**

In `src/core/side-panel.js`, add the import at the top (after existing imports):

```javascript
import { GRAPH_HORIZONS, DEFAULT_GRAPH_HORIZON } from "../constants.js";
```

In `_renderCircuitMode()` (around line 293), add a call between `_renderSheddingSection` and `_renderMonitoringSection`:

```javascript
this._renderGraphHorizonSection(body, cfg);
```

Add the new method after `_renderSheddingSection()` (after line 390):

```javascript
  // ── Graph horizon section ──────────────────────────────────────────

  _renderGraphHorizonSection(body, cfg) {
    const section = document.createElement("div");
    section.className = "section";

    const sectionLabel = document.createElement("div");
    sectionLabel.className = "section-label";
    sectionLabel.textContent = t("sidepanel.graph_horizon");
    section.appendChild(sectionLabel);

    const graphInfo = cfg.graphHorizonInfo;
    const hasOverride = graphInfo?.has_override === true;
    const currentHorizon = graphInfo?.horizon || DEFAULT_GRAPH_HORIZON;

    // Global / Custom radio
    const radioGroup = document.createElement("div");
    radioGroup.className = "radio-group";
    radioGroup.innerHTML = `
      <label><input type="radio" name="graph-mode" value="global" ${!hasOverride ? "checked" : ""} /> ${t("sidepanel.global")}</label>
      <label><input type="radio" name="graph-mode" value="custom" ${hasOverride ? "checked" : ""} /> ${t("sidepanel.custom")}</label>
    `;
    section.appendChild(radioGroup);

    // Horizon dropdown
    const selectWrap = document.createElement("div");
    selectWrap.dataset.role = "graph-horizon-fields";
    selectWrap.style.display = hasOverride ? "block" : "none";

    const row = document.createElement("div");
    row.className = "field-row";

    const label = document.createElement("span");
    label.className = "field-label";
    label.textContent = t("settings.default_scale");

    const selectEl = document.createElement("select");
    selectEl.dataset.role = "graph-horizon-select";
    for (const key of Object.keys(GRAPH_HORIZONS)) {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = t(`horizon.${key}`);
      if (key === currentHorizon) opt.selected = true;
      selectEl.appendChild(opt);
    }

    row.appendChild(label);
    row.appendChild(selectEl);
    selectWrap.appendChild(row);
    section.appendChild(selectWrap);

    // Event: radio change
    const radios = radioGroup.querySelectorAll('input[type="radio"]');
    for (const radio of radios) {
      radio.addEventListener("change", () => {
        const isCustom = radio.value === "custom" && radio.checked;
        selectWrap.style.display = isCustom ? "block" : "none";
        if (!isCustom && radio.checked) {
          const circuitId = cfg.uuid;
          this._callDomainService("clear_circuit_graph_horizon", { circuit_id: circuitId })
            .then(() => {
              this.dispatchEvent(new CustomEvent("graph-settings-changed", { bubbles: true, composed: true }));
            })
            .catch(err => this._showError(`${t("sidepanel.clear_graph_horizon_failed")} ${err.message ?? err}`));
        }
      });
    }

    // Event: dropdown change
    selectEl.addEventListener("change", () => {
      this._debounce("graph-horizon", DEBOUNCE_MS, () => {
        const circuitId = cfg.uuid;
        this._callDomainService("set_circuit_graph_horizon", {
          circuit_id: circuitId,
          horizon: selectEl.value,
        })
          .then(() => {
            this.dispatchEvent(new CustomEvent("graph-settings-changed", { bubbles: true, composed: true }));
          })
          .catch(err => this._showError(`${t("sidepanel.graph_horizon_failed")} ${err.message ?? err}`));
      });
    });

    body.appendChild(section);
  }
```

- [ ] **Step 2: Pass graph horizon info when opening side panel in tab-dashboard.js**

In `src/panel/tab-dashboard.js`, add import at the top:

```javascript
import { GraphSettingsCache, getEffectiveHorizon } from "../core/graph-settings.js";
```

Add `_graphSettingsCache` to the constructor (after `_monitoringCache`):

```javascript
this._graphSettingsCache = new GraphSettingsCache();
```

In `render()`, after `await this._monitoringCache.fetch(hass);` (line 40), add:

```javascript
await this._graphSettingsCache.fetch(hass);
```

In `_bindGearClicks()`, after fetching monitoring info (line 158), add graph horizon info lookup and pass it to `sidePanel.open()`:

Change lines 157-164 from:

```javascript
await this._monitoringCache.fetch(this._hass);
const monitoringInfo = this._monitoringCache?.status?.circuits?.[circuit.entities?.power] || null;

sidePanel.open({
  ...circuit,
  uuid,
  monitoringInfo,
});
```

to:

```javascript
await this._monitoringCache.fetch(this._hass);
const monitoringInfo = this._monitoringCache?.status?.circuits?.[circuit.entities?.power] || null;

await this._graphSettingsCache.fetch(this._hass);
const graphSettings = this._graphSettingsCache.settings;
const graphHorizonInfo = graphSettings?.circuits?.[uuid]
  ? graphSettings.circuits[uuid]
  : { horizon: graphSettings?.global_horizon || "5m", has_override: false };

sidePanel.open({
  ...circuit,
  uuid,
  monitoringInfo,
  graphHorizonInfo,
});
```

In the `side-panel-closed` event handler (line 71-73), also invalidate graph settings:

```javascript
container.addEventListener("side-panel-closed", () => {
  this._monitoringCache.invalidate();
  this._graphSettingsCache.invalidate();
});
```

Add a listener for `graph-settings-changed` to invalidate the cache:

```javascript
container.addEventListener("graph-settings-changed", () => {
  this._graphSettingsCache.invalidate();
});
```

- [ ] **Step 3: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/core/side-panel.js src/panel/tab-dashboard.js
git commit -m "feat: add graph horizon section to side panel with cache sync"
```

---

### Task 6: Frontend — Adaptive history loading with per-circuit horizons

**Files:**

- Modify: `src/helpers/history.js` (add horizon-aware duration function)
- Modify: `src/core/history-loader.js` (accept horizon map, group by horizon)
- Modify: `src/panel/tab-dashboard.js` (build horizon map, adaptive polling)

- [ ] **Step 1: Add `getHorizonDurationMs` to helpers/history.js**

Add to `src/helpers/history.js` after the existing imports:

```javascript
import { GRAPH_HORIZONS, DEFAULT_GRAPH_HORIZON } from "../constants.js";
```

Add a new function after `getHistoryDurationMs`:

```javascript
/**
 * Get duration in ms for a horizon key.
 * @param {string} horizonKey - e.g. "5m", "1h", "1d", "1M"
 * @returns {number} Duration in milliseconds
 */
export function getHorizonDurationMs(horizonKey) {
  const h = GRAPH_HORIZONS[horizonKey];
  return h ? h.ms : GRAPH_HORIZONS[DEFAULT_GRAPH_HORIZON].ms;
}
```

- [ ] **Step 2: Modify history-loader.js to accept per-circuit horizon map**

Change the signature and implementation of `loadHistory` in `src/core/history-loader.js`.

Replace the existing `loadHistory` export (lines 121-147) with:

```javascript
/**
 * Load historical power data from HA recorder into the powerHistory Map.
 * Supports per-circuit horizons by grouping circuits by their effective duration.
 *
 * @param {object} hass
 * @param {object} topology
 * @param {object} config - card config (fallback for duration)
 * @param {Map<string, {time: number, value: number}[]>} powerHistory - mutated in place
 * @param {Map<string, string>} [horizonMap] - optional uuid → horizon key map
 */
export async function loadHistory(hass, topology, config, powerHistory, horizonMap) {
  if (!topology || !hass) return;

  // Group circuits by effective duration
  const groups = new Map(); // durationMs → { entityIds: [], uuidByEntity: Map }

  for (const [uuid, circuit] of Object.entries(topology.circuits)) {
    const eid = getCircuitChartEntity(circuit, config);
    if (!eid) continue;

    let durationMs;
    if (horizonMap && horizonMap.has(uuid)) {
      durationMs = getHorizonDurationMs(horizonMap.get(uuid));
    } else {
      durationMs = getHistoryDurationMs(config);
    }

    if (!groups.has(durationMs)) {
      groups.set(durationMs, { entityIds: [], uuidByEntity: new Map() });
    }
    const group = groups.get(durationMs);
    group.entityIds.push(eid);
    group.uuidByEntity.set(eid, uuid);
  }

  // Add sub-device entities to the default duration group
  const defaultDurationMs = getHistoryDurationMs(config);
  if (!groups.has(defaultDurationMs)) {
    groups.set(defaultDurationMs, { entityIds: [], uuidByEntity: new Map() });
  }
  _collectSubDeviceEntityIdsInto(topology, groups.get(defaultDurationMs).entityIds, groups.get(defaultDurationMs).uuidByEntity);

  // Load each group
  const promises = [];
  for (const [durationMs, group] of groups) {
    if (group.entityIds.length === 0) continue;
    const useStatistics = durationMs > 2 * 60 * 60 * 1000;
    if (useStatistics) {
      promises.push(loadStatisticsHistory(hass, group.entityIds, group.uuidByEntity, durationMs, powerHistory));
    } else {
      promises.push(loadRawHistory(hass, group.entityIds, group.uuidByEntity, durationMs, powerHistory));
    }
  }
  await Promise.all(promises);
}
```

Add the import for `getHorizonDurationMs` at the top:

```javascript
import { getHistoryDurationMs, getMaxHistoryPoints, getMinGapMs, deduplicateAndTrim, getHorizonDurationMs } from "../helpers/history.js";
```

- [ ] **Step 3: Update tab-dashboard.js for adaptive polling**

In `src/panel/tab-dashboard.js`, add imports:

```javascript
import { GRAPH_HORIZONS, DEFAULT_GRAPH_HORIZON } from "../constants.js";
import { getHorizonDurationMs } from "../helpers/history.js";
```

Modify `render()` to build a horizon map and pass it to `loadHistory`:

After `await this._graphSettingsCache.fetch(hass);` add:

```javascript
// Build per-circuit horizon map
this._horizonMap = new Map();
const graphSettings = this._graphSettingsCache.settings;
if (topo?.circuits) {
  for (const uuid of Object.keys(topo.circuits)) {
    const override = graphSettings?.circuits?.[uuid];
    const horizon = override?.has_override ? override.horizon : graphSettings?.global_horizon || DEFAULT_GRAPH_HORIZON;
    this._horizonMap.set(uuid, horizon);
  }
}
```

Change the `loadHistory` call (line 76) from:

```javascript
await loadHistory(hass, topo, config, this._powerHistory);
```

to:

```javascript
await loadHistory(hass, topo, config, this._powerHistory, this._horizonMap);
```

Modify `_recordSamples()` to only record live samples for circuits on the "5m" horizon:

After `const uuid` is determined in the loop (line 101), add a check:

```javascript
      const horizon = this._horizonMap?.get(uuid) || DEFAULT_GRAPH_HORIZON;
      if (!GRAPH_HORIZONS[horizon]?.useRealtime) continue;
```

Add a periodic recorder refresh for non-realtime circuits. In `render()`, after setting up the live update interval (line 86-90), add:

```javascript
// Periodic recorder refresh for non-realtime horizons
this._recorderRefreshInterval = setInterval(async () => {
  if (!this._topology || !this._hass) return;
  const nonRealtimeMap = new Map();
  for (const [uuid, horizon] of this._horizonMap) {
    if (!GRAPH_HORIZONS[horizon]?.useRealtime) {
      nonRealtimeMap.set(uuid, horizon);
    }
  }
  if (nonRealtimeMap.size === 0) return;
  // Clear and reload history for non-realtime circuits
  for (const uuid of nonRealtimeMap.keys()) {
    this._powerHistory.delete(uuid);
  }
  try {
    await loadHistory(this._hass, this._topology, this._config, this._powerHistory, nonRealtimeMap);
    updateCircuitDOM(container, this._hass, topo, this._config, this._powerHistory);
  } catch {
    // Recorder data will refresh on next interval
  }
}, 30000);
```

Update `stop()` to clear the recorder refresh interval:

```javascript
  stop() {
    if (this._updateInterval) {
      clearInterval(this._updateInterval);
      this._updateInterval = null;
    }
    if (this._recorderRefreshInterval) {
      clearInterval(this._recorderRefreshInterval);
      this._recorderRefreshInterval = null;
    }
  }
```

Also listen for `graph-settings-changed` to reload history:

```javascript
container.addEventListener("graph-settings-changed", async () => {
  this._graphSettingsCache.invalidate();
  await this._graphSettingsCache.fetch(this._hass);

  // Rebuild horizon map
  const newSettings = this._graphSettingsCache.settings;
  if (topo?.circuits) {
    for (const uuid of Object.keys(topo.circuits)) {
      const override = newSettings?.circuits?.[uuid];
      const horizon = override?.has_override ? override.horizon : newSettings?.global_horizon || DEFAULT_GRAPH_HORIZON;
      this._horizonMap.set(uuid, horizon);
    }
  }

  // Reload all history with new horizons
  this._powerHistory.clear();
  try {
    await loadHistory(this._hass, topo, this._config, this._powerHistory, this._horizonMap);
  } catch {
    // Will populate on next refresh
  }
  updateCircuitDOM(container, this._hass, topo, this._config, this._powerHistory);
  updateSubDeviceDOM(container, this._hass, topo, this._config, this._powerHistory);
});
```

- [ ] **Step 4: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/helpers/history.js src/core/history-loader.js src/panel/tab-dashboard.js
git commit -m "feat: adaptive history loading with per-circuit horizons and recorder refresh"
```

---

### Task 7: Frontend — Chart X-axis scaling for different horizons

**Files:**

- Modify: `src/chart/chart-options.js` (adjust x-axis range for horizon)
- Modify: `src/core/dom-updater.js` (pass horizon info to chart builder)

- [ ] **Step 1: Check current chart-options.js and dom-updater.js**

Read `src/chart/chart-options.js` and `src/core/dom-updater.js` to understand how the x-axis min/max are set and how duration is passed through.

- [ ] **Step 2: Update chart-options.js to accept dynamic duration**

The chart currently derives its x-axis range from a fixed `durationMs` parameter. Ensure that when a longer horizon is active, the x-axis spans the full horizon
window. The `buildChartOptions` function should already accept `durationMs` — verify it uses it for `xAxis.min` as `Date.now() - durationMs`. If it does, no
changes needed here since the per-circuit duration will flow through from `dom-updater.js`.

If `dom-updater.js` hardcodes the duration from config, update it to accept a `horizonMap` parameter and look up per-circuit durations. The key change is: where
`getHistoryDurationMs(config)` is called for chart rendering, it should check `horizonMap.get(uuid)` first.

- [ ] **Step 3: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/chart/chart-options.js src/core/dom-updater.js
git commit -m "feat: scale chart x-axis per circuit horizon"
```

---

### Task 8: Frontend — Synchronization between Settings tab and side panel

**Files:**

- Modify: `src/panel/span-panel.js` (wire `graph-settings-changed` event for cross-tab sync)

- [ ] **Step 1: Add event listener in span-panel.js**

In `_renderTab()` or after `_bindTabNavigation()`, add a listener on the shadow root for `graph-settings-changed`:

```javascript
this.shadowRoot.addEventListener("graph-settings-changed", () => {
  // If settings tab is active, re-render to pick up changes from side panel
  if (this._activeTab === "settings") {
    this._renderTab();
  }
});
```

This ensures that when the side panel dispatches `graph-settings-changed` (which bubbles through composed shadow DOM), the Settings tab re-renders with fresh
data.

- [ ] **Step 2: Verify synchronization works end-to-end**

The sync flow should be:

1. Side panel changes horizon → service call → dispatches `graph-settings-changed`
2. `tab-dashboard.js` hears it → invalidates cache → reloads history
3. `span-panel.js` hears it → if Settings tab active, re-renders it
4. Settings tab changes horizon → service call → dispatches `graph-settings-changed` → re-renders self
5. `tab-dashboard.js` hears it → invalidates cache → reloads history

- [ ] **Step 3: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/panel/span-panel.js
git commit -m "feat: synchronize graph settings between Settings tab and side panel"
```

---

### Task 9: Frontend — Build and verify

**Files:**

- Modify: `src/card/span-panel-card.js` (same changes as tab-dashboard.js for standalone card mode)

- [ ] **Step 1: Apply same graph horizon support to span-panel-card.js**

The standalone card (`span-panel-card.js`) needs the same changes as `tab-dashboard.js`: import `GraphSettingsCache`, build horizon map, pass to `loadHistory`,
adaptive polling. Mirror the changes from Task 6 for the card's `_updateData()` and `set hass()` methods.

- [ ] **Step 2: Build the frontend**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Build succeeds with no errors.

- [ ] **Step 3: Copy dist to integration**

Copy the built files to the integration's `www/` directory:

```bash
cp -r /Users/bflood/projects/HA/cards/span-card/dist/* /Users/bflood/projects/HA/span/custom_components/span_panel/www/
```

- [ ] **Step 4: Run all backend tests**

Run: `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -q`

Expected: All tests PASS.

- [ ] **Step 5: Commit both repos**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add -A
git commit -m "feat: complete graph time horizon frontend support"

cd /Users/bflood/projects/HA/span
git add custom_components/span_panel/www/
git commit -m "feat: update frontend dist with graph time horizon feature"
```

---

### Task 10: Integration testing and verification

- [ ] **Step 1: Verify backend services work**

Start HA with the integration loaded. In Developer Tools > Services, test:

- Call `span_panel.set_graph_time_horizon` with `horizon: "1h"` — should succeed
- Call `span_panel.get_graph_settings` — should return `{ "global_horizon": "1h", "circuits": {} }`
- Call `span_panel.set_circuit_graph_horizon` with a circuit ID and `horizon: "1d"` — should succeed
- Call `span_panel.get_graph_settings` — should show the circuit override
- Call `span_panel.clear_circuit_graph_horizon` with the circuit ID — override removed
- Restart HA — settings should persist

- [ ] **Step 2: Verify Settings tab UI**

Navigate to the Span Panel dashboard, Settings tab:

- Global dropdown shows current horizon
- Circuit table lists all circuits with inline dropdowns
- Changing global updates all non-overridden circuits
- Setting a circuit to a custom value shows Reset button
- Clicking Reset removes the override

- [ ] **Step 3: Verify side panel UI**

Click gear icon on a circuit:

- Graph Time Horizon section appears between Shedding and Monitoring
- Global/Custom radio works
- Switching to Custom enables dropdown
- Changing dropdown calls service and dispatches event
- Switching back to Global calls clear service

- [ ] **Step 4: Verify chart behavior**

- Set a circuit to 1 hour — chart should immediately show 1 hour of recorder data
- Set to 1 day — chart shows full day from recorder statistics
- Set back to 5 minutes — chart shows realtime with 1s polling
- Warning overlay lines visible on amperage charts at all horizons

- [ ] **Step 5: Verify synchronization**

- Change a circuit's horizon in the side panel → switch to Settings tab → should show the override
- Change a circuit's horizon in the Settings tab → open that circuit's side panel → should show Custom with correct value
- Change global in Settings tab → all non-overridden circuits' charts update immediately
