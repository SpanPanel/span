# Graph Time Horizon — Design Spec

## Summary

Add configurable time horizons to circuit graphs, combining HA recorder (historical) data with realtime polling. Graphs display the full history for the
selected scale immediately on render. Time horizons can be set globally (default: 5 minutes) and overridden per-circuit, with settings persisted in HA Storage.

## Requirements

1. **Immediate full history**: When a graph renders or the horizon changes, the chart must immediately populate the complete time window from recorder data. No
   "empty chart that fills in" behavior.
2. **Four preset scales**: 5 minutes, 1 hour, 1 day, 1 month.
3. **Global default**: Configurable in the Settings tab, defaults to 5 minutes.
4. **Per-circuit overrides**: Configurable in both the Settings tab (circuit table) and the side panel (gear icon).
5. **Persistent storage**: All settings survive restarts, stored in HA Storage (same pattern as CurrentMonitor).
6. **Settings synchronization**: Changes made in the side panel must be reflected in the Settings tab and vice versa. The graph settings cache must be
   invalidated on any mutation, and visible UI components must re-render with current data.
7. **Adaptive data strategy**: 5-minute scale uses recorder backfill + 1s realtime polling. Longer horizons use recorder data only with periodic refresh.
8. **Warning overlays**: Breaker rating lines (80% yellow dashed, 100% red solid) always visible on amperage charts regardless of time horizon.

## Architecture

### Backend — `GraphHorizonManager`

New class in `custom_components/span_panel/graph_horizon.py`, mirroring `CurrentMonitor`'s storage pattern.

**State:**

- `_global_horizon: str` — one of `"5m"`, `"1h"`, `"1d"`, `"1M"`. Default: `"5m"`.
- `_circuit_overrides: dict[str, str]` — circuit_id → horizon. Only populated when different from global.
- `_store: Store` — HA Storage persistence with versioned key.

**Public API:**

- `get_global_horizon() -> str` — returns current global default.
- `set_global_horizon(horizon: str) -> None` — validates and sets global default. Prunes any circuit overrides that now match the new global (same
  redundancy-removal pattern as `CurrentMonitor._is_redundant_override`).
- `get_effective_horizon(circuit_id: str) -> str` — returns override if set, otherwise global.
- `set_circuit_horizon(circuit_id: str, horizon: str) -> None` — sets per-circuit override. If the value matches global, removes it instead (redundancy check).
- `clear_circuit_horizon(circuit_id: str) -> None` — removes per-circuit override, reverts to global.
- `get_all_settings() -> dict` — returns `{ "global_horizon": str, "circuits": { circuit_id: { "horizon": str, "has_override": bool } } }` for frontend
  consumption.
- `async_load() -> None` — loads from HA Storage on startup.
- `async_save() -> None` — persists to HA Storage after mutations.

**Storage format:**

```python
STORAGE_VERSION = 1
STORAGE_KEY = f"span_panel_graph_horizon.{entry.entry_id}"
# Stored data:
{
    "global_horizon": "5m",
    "circuit_overrides": {
        "circuit_uuid_1": "1h",
        "circuit_uuid_2": "1d"
    }
}
```

**Valid horizons (constant):**

```python
VALID_GRAPH_HORIZONS = ("5m", "1h", "1d", "1M")
```

### Service Calls

Three new service calls registered in `__init__.py`:

| Service                       | Parameters                                                 | Description                 |
| ----------------------------- | ---------------------------------------------------------- | --------------------------- |
| `set_graph_time_horizon`      | `horizon: str`, `config_entry_id?: str`                    | Set global default horizon  |
| `set_circuit_graph_horizon`   | `circuit_id: str`, `horizon: str`, `config_entry_id?: str` | Set per-circuit override    |
| `clear_circuit_graph_horizon` | `circuit_id: str`, `config_entry_id?: str`                 | Remove per-circuit override |

All validate `horizon` against `VALID_GRAPH_HORIZONS` and raise `vol.Invalid` / `ServiceValidationError` for invalid values.

A read endpoint is also needed: service call `get_graph_settings` (with `return_response: true`), matching the `get_monitoring_status` pattern. The service
returns the output of `get_all_settings()`.

### Integration Setup Changes (`__init__.py`)

- Instantiate `GraphHorizonManager` in `async_setup_entry()`, alongside `CurrentMonitor`.
- Call `await graph_horizon_manager.async_load()` to restore persisted settings.
- Store reference on coordinator: `coordinator.graph_horizon_manager = graph_horizon_manager`.
- Register the three service calls with appropriate schemas.

### Frontend — Settings Tab (`panel/tab-settings.js`)

Major expansion from current minimal implementation. New structure:

1. **Existing content**: Integration settings link (unchanged).
2. **Divider**.
3. **Graph Time Horizon heading**.
4. **Global Default section**: Background panel with a dropdown for the four presets. Changing the dropdown triggers a debounced service call to
   `set_graph_time_horizon`, then re-renders the tab.
5. **Circuit Graph Scales table**:
   - Columns: Circuit name, Scale (dropdown), Reset button.
   - All circuits listed, sorted alphabetically by name.
   - Each row has an inline dropdown. Changing it triggers a debounced service call to `set_circuit_graph_horizon`.
   - Reset button appears only for circuits with overrides (`has_override: true`). Clicking it calls `clear_circuit_graph_horizon` and re-renders.
   - No CUSTOM badge — the presence of the Reset button is the visual indicator.

**Data fetching**: On render, calls `get_graph_settings` service to get global + per-circuit state. Needs circuit names, which come from the topology data
already available via `span_panel/panel_topology` websocket command (or passed in from the parent component).

### Frontend — Side Panel (`core/side-panel.js`)

New `_renderGraphHorizonSection(body, cfg)` method, called in `_renderCircuitMode()` between `_renderSheddingSection()` and `_renderMonitoringSection()`.

**UI structure:**

- Section label: "Graph Time Horizon"
- Global/Custom radio (same pattern as monitoring)
- Dropdown with four presets, disabled when "Global" is selected
- When "Global" selected and user had a custom override: calls `clear_circuit_graph_horizon`
- When "Custom" selected and dropdown changes: calls `set_circuit_graph_horizon` with debounce

**Data source**: The side panel `open(config)` call must include the circuit's current graph horizon and whether it has an override. This data comes from the
graph settings cache (see Synchronization below).

### Frontend — Graph Settings Cache (`core/graph-settings.js`)

New file, mirroring `MonitoringStatusCache`:

```javascript
export class GraphSettingsCache {
  constructor() {
    this._settings = null;
    this._lastFetch = 0;
    this._fetching = false;
  }

  async fetch(hass) {
    /* same pattern as MonitoringStatusCache.fetch */
  }
  invalidate() {
    this._lastFetch = 0;
  }
  get settings() {
    return this._settings;
  }
  clear() {
    this._settings = null;
    this._lastFetch = 0;
  }
}
```

Poll interval: 30s (same as monitoring).

### Frontend — History Loader Changes (`core/history-loader.js`)

`loadHistory()` currently takes a single `config` object and derives one duration for all circuits. This must change to support per-circuit horizons.

**New signature concept:**

```javascript
export async function loadHistory(hass, topology, horizonMap, powerHistory)
```

Where `horizonMap` is `Map<circuitUuid, horizonKey>` (e.g., `"5m"`, `"1h"`). Each circuit's history is loaded with its effective duration. The function groups
circuits by horizon to minimize WebSocket calls (one `history_during_period` or `statistics_during_period` call per unique horizon, not per circuit).

### Frontend — Card Changes (`card/span-panel-card.js`)

1. **Init**: Instantiate `GraphSettingsCache`, fetch on discovery completion.
2. **Build horizon map**: Before loading history, build `Map<uuid, horizonKey>` from graph settings cache (effective horizon per circuit).
3. **Adaptive polling**:
   - For circuits on `"5m"` horizon: continue 1s realtime polling (current behavior).
   - For circuits on longer horizons: skip realtime sample recording. Instead, periodically re-fetch recorder data (30s for 1h, 60s for 1d/1M).
4. **Horizon change handling**: When graph settings change (detected via cache invalidation + re-fetch), clear affected circuits' history and reload from
   recorder.
5. **Immediate population**: On first render and on horizon change, `loadHistory()` must complete before the chart renders, ensuring the full time window is
   visible immediately.

### Frontend — Constants (`constants.js`)

```javascript
export const GRAPH_HORIZONS = {
  "5m": { ms: 5 * 60 * 1000, label: () => t("horizon.5m"), refreshMs: 1000, useRealtime: true },
  "1h": { ms: 60 * 60 * 1000, label: () => t("horizon.1h"), refreshMs: 30000, useRealtime: false },
  "1d": { ms: 24 * 60 * 60 * 1000, label: () => t("horizon.1d"), refreshMs: 60000, useRealtime: false },
  "1M": { ms: 30 * 24 * 60 * 60 * 1000, label: () => t("horizon.1M"), refreshMs: 60000, useRealtime: false },
};
```

### Frontend — i18n (`i18n.js`)

New keys for all supported languages:

- `horizon.5m`, `horizon.1h`, `horizon.1d`, `horizon.1M` — preset labels
- `settings.graph_horizon_heading` — section heading
- `settings.graph_horizon_description` — help text
- `settings.global_default` — sub-heading
- `settings.circuit_graph_scales` — table heading
- `sidepanel.graph_horizon` — side panel section label
- `monitoring.reset` is reused for the Reset button

## Synchronization

This is a critical design concern. Changes to graph horizon settings can originate from two places: the Settings tab circuit table and the side panel. Both must
stay in sync.

**Mechanism:**

1. **Shared cache**: Both the Settings tab and the card/side panel use the same `GraphSettingsCache` instance (owned by the parent card/panel component and
   passed down).
2. **Mutation → invalidate → re-render**:
   - When the **Settings tab** changes a horizon (global or per-circuit), it: calls the service → invalidates the cache → re-renders itself → dispatches a
     `graph-settings-changed` custom event.
   - When the **side panel** changes a horizon, it: calls the service → invalidates the cache → dispatches a `graph-settings-changed` custom event.
   - The **parent component** listens for `graph-settings-changed` and: re-fetches the cache → updates the active tab if it's Settings → triggers history reload
     for affected circuits.
3. **No stale reads**: The cache's `invalidate()` forces the next `fetch()` to query the backend, regardless of the 30s poll interval. Any UI component that
   renders graph settings must call `fetch()` before rendering.
4. **Chart re-render on change**: When a circuit's effective horizon changes, its history buffer is cleared and reloaded from the recorder for the new time
   window. The chart must not render until the new history is loaded (immediate full population requirement).

**Event flow example — user changes circuit horizon in side panel:**

```text
Side panel: service call → cache.invalidate() → dispatch "graph-settings-changed"
Parent: hears event → cache.fetch() → update charts for affected circuit → if Settings tab visible, re-render it
```

**Event flow example — user changes global default in Settings tab:**

```text
Settings tab: service call → cache.invalidate() → re-render self → dispatch "graph-settings-changed"
Parent: hears event → cache.fetch() → update charts for ALL circuits without overrides
```

## Data Loading Strategy

| Horizon | Recorder API                        | Period    | Realtime Buffer                                           |
| ------- | ----------------------------------- | --------- | --------------------------------------------------------- |
| 5m      | `history/history_during_period`     | Raw       | Yes, 1s polling fills gap where recorder hasn't committed |
| 1h      | `history/history_during_period`     | Raw       | No — recorder refresh every 30s                           |
| 1d      | `recorder/statistics_during_period` | `5minute` | No — recorder refresh every 60s                           |
| 1M      | `recorder/statistics_during_period` | `hour`    | No — recorder refresh every 60s                           |

The existing `loadHistory()` already switches between raw history and statistics at the 2-hour boundary. This logic is preserved — the threshold naturally puts
5m and 1h on raw history, and 1d/1M on statistics.

## Card Editor Compatibility

The existing card editor fields (`history_days`, `history_hours`, `history_minutes`) remain functional as a fallback for standalone card usage outside the HA
panel dashboard. When graph settings are available from the backend (i.e., `GraphHorizonManager` is active), the backend settings take precedence. When running
as a standalone card without the panel topology, the card editor fields are used as before.

## Files Changed

### Backend (new)

- `custom_components/span_panel/graph_horizon.py` — GraphHorizonManager class

### Backend (modified)

- `custom_components/span_panel/__init__.py` — instantiation, service registration
- `custom_components/span_panel/services.yaml` — service definitions
- `custom_components/span_panel/const.py` — valid horizon constants

### Frontend (new)

- `src/core/graph-settings.js` — GraphSettingsCache class

### Frontend (modified)

- `src/panel/tab-settings.js` — global dropdown + circuit table
- `src/core/side-panel.js` — graph horizon section
- `src/core/history-loader.js` — per-circuit duration, grouped API calls
- `src/card/span-panel-card.js` — cache, adaptive polling, horizon change handling
- `src/panel/span-panel.js` — cache instance, event wiring (if using panel mode)
- `src/constants.js` — GRAPH_HORIZONS map
- `src/helpers/history.js` — adapt duration helpers for horizon keys
- `src/i18n.js` — new translation keys

## Testing

### Backend

- Unit tests for `GraphHorizonManager`: get/set global, get/set/clear circuit overrides, redundancy removal, storage load/save round-trip, validation of invalid
  horizons.
- Service call tests: verify correct delegation to manager, error on invalid input.

### Frontend

- `GraphSettingsCache`: fetch, invalidation, clear behavior.
- Settings tab rendering: correct circuit list, dropdown values, reset button visibility.
- Side panel: Global/Custom radio behavior, service calls on change.
- History loader: correct API selection per horizon, grouping by horizon.
- Synchronization: side panel change reflected in Settings tab, and vice versa.
