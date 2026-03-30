# span-card Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the span-card to support monitoring indicators, shedding icons, A/W toggle, gear icons with side-panel config, and produce a second build
output for the integration panel.

**Architecture:** Refactor the 787-line `SpanPanelCard` class by extracting shared rendering modules into `src/core/`. Build a new integration panel entry point
in `src/panel/` that reuses these core modules. Rollup produces two bundles: the existing Lovelace card and a new full-page panel JS.

**Tech Stack:** Vanilla JavaScript, Web Components (HTMLElement), Rollup, HA WebSocket API, HA service calls

**Repo:** `/Users/bflood/projects/HA/cards/span-card` (branch: `integration-panel`)

**Build:** `npm run build` (rollup)

**Lint:** `npx eslint src/` and `npx prettier --check src/`

---

## File Structure

### Extracted core modules (from existing card code)

| File                              | Responsibility                                                                                        | Extracted from                             |
| --------------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `src/core/grid-renderer.js`       | `buildGridHTML()`, `renderCircuitSlot()`, `renderEmptySlot()` — pure functions returning HTML strings | `span-panel-card.js:551-785`               |
| `src/core/sub-device-renderer.js` | `buildSubDevicesHTML()`, `buildSubEntityHTML()`, `buildSubDeviceChartsHTML()`                         | `span-panel-card.js:614-717`               |
| `src/core/header-renderer.js`     | `buildHeaderHTML()` — panel header with stats, gear icon, A/W toggle                                  | New, replaces inline header in `_render()` |
| `src/core/dom-updater.js`         | `updateCircuitDOM()`, `updateHeaderDOM()`, `updateSubDeviceDOM()` — incremental DOM patches           | `span-panel-card.js:307-434`               |
| `src/core/history-loader.js`      | `loadHistory()`, `loadStatisticsHistory()`, `loadRawHistory()`, `collectSubDeviceEntityIds()`         | `span-panel-card.js:144-262`               |
| `src/core/side-panel.js`          | `SidePanelElement` — web component for circuit/panel config                                           | New                                        |
| `src/core/monitoring-status.js`   | `fetchMonitoringStatus()`, `getCircuitAlertState()`, `hasCustomOverrides()`                           | New                                        |

### New files

| File                               | Responsibility                                                |
| ---------------------------------- | ------------------------------------------------------------- |
| `src/core/constants-monitoring.js` | Monitoring-specific constants (thresholds, colors, icons)     |
| `src/panel/index.js`               | Integration panel entry point, registers `span-panel` element |
| `src/panel/span-panel.js`          | `SpanPanelElement` — full-page shell with tab router          |
| `src/panel/tab-dashboard.js`       | Panel tab — wraps core grid + header                          |
| `src/panel/tab-monitoring.js`      | Monitoring tab — global settings + overrides table            |
| `src/panel/tab-settings.js`        | Settings tab — general integration config                     |

### Modified files

| File                          | Changes                                                                      |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `src/card/span-panel-card.js` | Slim down to ~200 lines — delegate to core modules                           |
| `src/card/card-styles.js`     | Add styles for shedding icons, monitoring indicators, gear icons, side panel |
| `src/constants.js`            | Add shedding priority constants, monitoring color thresholds                 |
| `src/helpers/format.js`       | Add `formatAmps()` helper                                                    |
| `rollup.config.mjs`           | Add second output for panel bundle                                           |

---

### Task 1: Extract grid renderer to core module

**Files:**

- Create: `src/core/grid-renderer.js`
- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Create `src/core/grid-renderer.js`**

Extract `_buildGridHTML`, `_renderCircuitSlot`, and `_renderEmptySlot` as standalone functions. They are already pure (take data, return HTML strings) so this
is a mechanical move. Each function gains explicit parameters instead of reading `this._hass` and `this._config`.

```js
// src/core/grid-renderer.js
import { escapeHtml } from "../helpers/sanitize.js";
import { formatPowerSigned, formatPowerUnit } from "../helpers/format.js";
import { tabToRow, tabToCol, classifyDualTab } from "../helpers/layout.js";
import { getChartMetric } from "../helpers/chart.js";
import { DEVICE_TYPE_PV, RELAY_STATE_CLOSED } from "../constants.js";

export function buildGridHTML(topology, totalRows, durationMs, hass, config) {
  const tabMap = new Map();
  const occupiedTabs = new Set();

  for (const [uuid, circuit] of Object.entries(topology.circuits)) {
    const tabs = circuit.tabs;
    if (!tabs || tabs.length === 0) continue;
    const primaryTab = Math.min(...tabs);
    const layout = tabs.length === 1 ? "single" : classifyDualTab(tabs);
    tabMap.set(primaryTab, { uuid, circuit, layout });
    for (const t of tabs) occupiedTabs.add(t);
  }

  const rowsToSkipLeft = new Set();
  const rowsToSkipRight = new Set();

  for (const [primaryTab, entry] of tabMap) {
    if (entry.layout === "col-span") {
      const tabs = entry.circuit.tabs;
      const secondaryTab = Math.max(...tabs);
      const secondaryRow = tabToRow(secondaryTab);
      const col = tabToCol(primaryTab);
      if (col === 0) rowsToSkipLeft.add(secondaryRow);
      else rowsToSkipRight.add(secondaryRow);
    }
  }

  let gridHTML = "";
  for (let row = 1; row <= totalRows; row++) {
    const leftTab = row * 2 - 1;
    const rightTab = row * 2;
    const leftEntry = tabMap.get(leftTab);
    const rightEntry = tabMap.get(rightTab);

    gridHTML += `<div class="tab-label tab-left" style="grid-row: ${row}; grid-column: 1;">${leftTab}</div>`;

    if (leftEntry && leftEntry.layout === "row-span") {
      gridHTML += renderCircuitSlot(leftEntry.uuid, leftEntry.circuit, row, "2 / 4", "row-span", durationMs, hass, config);
      gridHTML += `<div class="tab-label tab-right" style="grid-row: ${row}; grid-column: 4;">${rightTab}</div>`;
      continue;
    }

    if (!rowsToSkipLeft.has(row)) {
      if (leftEntry && (leftEntry.layout === "col-span" || leftEntry.layout === "single")) {
        gridHTML += renderCircuitSlot(leftEntry.uuid, leftEntry.circuit, row, "2", leftEntry.layout, durationMs, hass, config);
      } else if (!occupiedTabs.has(leftTab)) {
        gridHTML += renderEmptySlot(row, "2");
      }
    }

    if (!rowsToSkipRight.has(row)) {
      if (rightEntry && (rightEntry.layout === "col-span" || rightEntry.layout === "single")) {
        gridHTML += renderCircuitSlot(rightEntry.uuid, rightEntry.circuit, row, "3", rightEntry.layout, durationMs, hass, config);
      } else if (!occupiedTabs.has(rightTab)) {
        gridHTML += renderEmptySlot(row, "3");
      }
    }

    gridHTML += `<div class="tab-label tab-right" style="grid-row: ${row}; grid-column: 4;">${rightTab}</div>`;
  }
  return gridHTML;
}

export function renderCircuitSlot(uuid, circuit, row, col, layout, _durationMs, hass, config) {
  const entityId = circuit.entities?.power;
  const state = entityId ? hass.states[entityId] : null;
  const powerW = state ? parseFloat(state.state) || 0 : 0;
  const isProducer = circuit.device_type === DEVICE_TYPE_PV || powerW < 0;

  const switchEntityId = circuit.entities?.switch;
  const switchState = switchEntityId ? hass.states[switchEntityId] : null;
  const isOn = switchState ? switchState.state === "on" : (state?.attributes?.relay_state || circuit.relay_state) === RELAY_STATE_CLOSED;

  const breakerAmps = circuit.breaker_rating_a;
  const breakerLabel = breakerAmps ? `${Math.round(breakerAmps)}A` : "";
  const name = escapeHtml(circuit.name || "Unknown");

  const chartMetric = getChartMetric(config);
  const showCurrent = chartMetric.entityRole === "current";
  let valueHTML;
  if (showCurrent) {
    const currentEid = circuit.entities?.current;
    const currentState = currentEid ? hass.states[currentEid] : null;
    const amps = currentState ? parseFloat(currentState.state) || 0 : 0;
    valueHTML = `<strong>${chartMetric.format(amps)}</strong><span class="power-unit">A</span>`;
  } else {
    valueHTML = `<strong>${formatPowerSigned(powerW)}</strong><span class="power-unit">${formatPowerUnit(powerW)}</span>`;
  }

  const rowSpan = layout === "col-span" ? `${row} / span 2` : `${row}`;
  const layoutClass = layout === "row-span" ? "circuit-row-span" : layout === "col-span" ? "circuit-col-span" : "";

  return `
    <div class="circuit-slot ${isOn ? "" : "circuit-off"} ${isProducer ? "circuit-producer" : ""} ${layoutClass}"
         style="grid-row: ${rowSpan}; grid-column: ${col};"
         data-uuid="${escapeHtml(uuid)}">
      <div class="circuit-header">
        <div class="circuit-info">
          ${breakerLabel ? `<span class="breaker-badge">${breakerLabel}</span>` : ""}
          <span class="circuit-name">${name}</span>
        </div>
        <div class="circuit-controls">
          <span class="power-value">
            ${valueHTML}
          </span>
          ${
            circuit.is_user_controllable !== false && circuit.entities?.switch
              ? `
            <div class="toggle-pill ${isOn ? "toggle-on" : "toggle-off"}">
              <span class="toggle-label">${isOn ? "On" : "Off"}</span>
              <span class="toggle-knob"></span>
            </div>
          `
              : ""
          }
        </div>
      </div>
      <div class="chart-container"></div>
    </div>
  `;
}

export function renderEmptySlot(row, col) {
  return `
    <div class="circuit-slot circuit-empty" style="grid-row: ${row}; grid-column: ${col};">
      <span class="empty-label">&mdash;</span>
    </div>
  `;
}
```

- [ ] **Step 2: Update `span-panel-card.js` to import from core**

Replace the three extracted methods with imports. Remove the method bodies and delegate:

```js
// At top of span-panel-card.js, add:
import { buildGridHTML, renderCircuitSlot, renderEmptySlot } from "../core/grid-renderer.js";

// In _render(), replace:
//   const gridHTML = this._buildGridHTML(topo, totalRows, durationMs);
// with:
const gridHTML = buildGridHTML(topo, totalRows, durationMs, hass, this._config);

// Delete methods: _buildGridHTML, _renderCircuitSlot, _renderEmptySlot
```

- [ ] **Step 3: Verify build**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Build succeeds, `dist/span-panel-card.js` produced without errors.

- [ ] **Step 4: Lint check**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npx eslint src/ && npx prettier --check src/`

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/core/grid-renderer.js src/card/span-panel-card.js
git commit -m "refactor: extract grid renderer to core module"
```

---

### Task 2: Extract sub-device renderer to core module

**Files:**

- Create: `src/core/sub-device-renderer.js`
- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Create `src/core/sub-device-renderer.js`**

Extract `_buildSubDevicesHTML`, `_buildSubEntityHTML`, and `_buildSubDeviceChartsHTML` as standalone functions.

```js
// src/core/sub-device-renderer.js
import { escapeHtml } from "../helpers/sanitize.js";
import { formatPowerSigned, formatPowerUnit } from "../helpers/format.js";
import { findSubDevicePowerEntity, findBatteryLevelEntity, findBatterySoeEntity, findBatteryCapacityEntity } from "../helpers/entity-finder.js";
import { DEVICE_TYPE_PV, SUB_DEVICE_TYPE_BESS, SUB_DEVICE_TYPE_EVSE, SUB_DEVICE_KEY_PREFIX } from "../constants.js";

export function buildSubDevicesHTML(topology, hass, config, _durationMs) {
  const showBattery = config.show_battery !== false;
  const showEvse = config.show_evse !== false;
  let subDevHTML = "";

  if (!topology.sub_devices) return subDevHTML;

  for (const [devId, sub] of Object.entries(topology.sub_devices)) {
    if (sub.type === SUB_DEVICE_TYPE_BESS && !showBattery) continue;
    if (sub.type === SUB_DEVICE_TYPE_EVSE && !showEvse) continue;

    const label = sub.type === SUB_DEVICE_TYPE_EVSE ? "EV Charger" : sub.type === SUB_DEVICE_TYPE_BESS ? "Battery" : "Sub-device";
    const powerEid = findSubDevicePowerEntity(sub);
    const powerState = powerEid ? hass.states[powerEid] : null;
    const powerW = powerState ? parseFloat(powerState.state) || 0 : 0;

    const isBess = sub.type === SUB_DEVICE_TYPE_BESS;
    const battLevelEid = isBess ? findBatteryLevelEntity(sub) : null;
    const battSoeEid = isBess ? findBatterySoeEntity(sub) : null;
    const battCapEid = isBess ? findBatteryCapacityEntity(sub) : null;

    const hideEids = new Set([powerEid, battLevelEid, battSoeEid, battCapEid].filter(Boolean));
    const entHTML = buildSubEntityHTML(sub, hass, config, hideEids);
    const chartsHTML = buildSubDeviceChartsHTML(devId, sub, isBess, powerEid, battLevelEid, battSoeEid);

    subDevHTML += `
      <div class="sub-device ${isBess ? "sub-device-bess" : ""}" data-subdev="${escapeHtml(devId)}">
        <div class="sub-device-header">
          <span class="sub-device-type">${escapeHtml(label)}</span>
          <span class="sub-device-name">${escapeHtml(sub.name || "")}</span>
          ${powerEid
            ? `<span class="sub-power-value"><strong>${formatPowerSigned(powerW)}</strong>
               <span class="power-unit">${formatPowerUnit(powerW)}</span></span>`
            : ""}
        </div>
        ${chartsHTML}
        ${entHTML}
      </div>
    `;
  }
  return subDevHTML;
}

export function buildSubEntityHTML(sub, hass, config, hideEids) {
  const visibleEnts = config.visible_sub_entities || {};
  let entHTML = "";
  if (!sub.entities) return entHTML;

  for (const [entityId, info] of Object.entries(sub.entities)) {
    if (hideEids.has(entityId)) continue;
    if (visibleEnts[entityId] !== true) continue;
    const state = hass.states[entityId];
    if (!state) continue;
    let name = info.original_name || state.attributes.friendly_name || entityId;
    const devName = sub.name || "";
    if (name.startsWith(devName + " ")) name = name.slice(devName.length + 1);
    let displayValue;
    if (hass.formatEntityState) {
      displayValue = hass.formatEntityState(state);
    } else {
      displayValue = state.state;
      const unit = state.attributes.unit_of_measurement || "";
      if (unit) displayValue += " " + unit;
    }
    const rawUnit = state.attributes.unit_of_measurement || "";
    if (rawUnit === "Wh") {
      const wh = parseFloat(state.state);
      if (!isNaN(wh)) displayValue = (wh / 1000).toFixed(1) + " kWh";
    }
    entHTML += `
      <div class="sub-entity">
        <span class="sub-entity-name">${escapeHtml(name)}:</span>
        <span class="sub-entity-value" data-eid="${escapeHtml(entityId)}">${escapeHtml(displayValue)}</span>
      </div>
    `;
  }
  return entHTML;
}

export function buildSubDeviceChartsHTML(devId, sub, isBess, powerEid, battLevelEid, battSoeEid) {
  if (isBess) {
    const bessCharts = [
      { key: `${SUB_DEVICE_KEY_PREFIX}${devId}_soc`, title: "SoC", available: !!battLevelEid },
      { key: `${SUB_DEVICE_KEY_PREFIX}${devId}_soe`, title: "SoE", available: !!battSoeEid },
      { key: `${SUB_DEVICE_KEY_PREFIX}${devId}_power`, title: "Power", available: !!powerEid },
    ].filter(c => c.available);

    return `
      <div class="bess-charts">
        ${bessCharts
          .map(
            c => `
          <div class="bess-chart-col">
            <div class="bess-chart-title">${escapeHtml(c.title)}</div>
            <div class="chart-container" data-chart-key="${escapeHtml(c.key)}"></div>
          </div>
        `
          )
          .join("")}
      </div>
    `;
  }
  if (powerEid) {
    return `<div class="chart-container" data-chart-key="${SUB_DEVICE_KEY_PREFIX}${escapeHtml(devId)}_power"></div>`;
  }
  return "";
}
```

- [ ] **Step 2: Update `span-panel-card.js` to import from core**

```js
// At top, add:
import { buildSubDevicesHTML } from "../core/sub-device-renderer.js";

// In _render(), replace:
//   const subDevHTML = this._buildSubDevicesHTML(topo, hass, durationMs);
// with:
const subDevHTML = buildSubDevicesHTML(topo, hass, this._config, durationMs);

// Delete methods: _buildSubDevicesHTML, _buildSubEntityHTML,
//   _buildSubDeviceChartsHTML
```

- [ ] **Step 3: Verify build**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Build succeeds.

- [ ] **Step 4: Lint check**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npx eslint src/ && npx prettier --check src/`

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/core/sub-device-renderer.js src/card/span-panel-card.js
git commit -m "refactor: extract sub-device renderer to core module"
```

---

### Task 3: Extract history loader to core module

**Files:**

- Create: `src/core/history-loader.js`
- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Create `src/core/history-loader.js`**

Extract `_loadHistory`, `_loadStatisticsHistory`, `_loadRawHistory`, and `_collectSubDeviceEntityIds`. These are async functions that take `hass`, topology,
config, and the `powerHistory` map.

```js
// src/core/history-loader.js
import { getHistoryDurationMs, getMaxHistoryPoints, getMinGapMs, recordSample, deduplicateAndTrim } from "../helpers/history.js";
import { getCircuitChartEntity } from "../helpers/chart.js";
import { findSubDevicePowerEntity, findBatteryLevelEntity, findBatterySoeEntity } from "../helpers/entity-finder.js";
import { SUB_DEVICE_TYPE_BESS, SUB_DEVICE_KEY_PREFIX } from "../constants.js";

const STATISTICS_THRESHOLD_MS = 2 * 60 * 60 * 1000; // 2 hours

export async function loadHistory(hass, topology, config, powerHistory) {
  const durationMs = getHistoryDurationMs(config);
  if (durationMs <= 0) return;

  const entityIds = [];
  const uuidByEntity = new Map();

  for (const [uuid, circuit] of Object.entries(topology.circuits)) {
    const eid = getCircuitChartEntity(circuit, config);
    if (eid) {
      entityIds.push(eid);
      uuidByEntity.set(eid, uuid);
    }
  }

  const subEntityIds = collectSubDeviceEntityIds(topology, hass);
  for (const { chartKey, entityId } of subEntityIds) {
    entityIds.push(entityId);
    uuidByEntity.set(entityId, chartKey);
  }

  if (entityIds.length === 0) return;

  const useStatistics = durationMs > STATISTICS_THRESHOLD_MS;
  if (useStatistics) {
    await loadStatisticsHistory(hass, entityIds, uuidByEntity, durationMs, powerHistory, config);
  } else {
    await loadRawHistory(hass, entityIds, uuidByEntity, durationMs, powerHistory, config);
  }
}

async function loadStatisticsHistory(hass, entityIds, uuidByEntity, durationMs, powerHistory, config) {
  const now = new Date();
  const startTime = new Date(now.getTime() - durationMs).toISOString();
  const maxPoints = getMaxHistoryPoints(config);

  const BATCH_SIZE = 50;
  for (let i = 0; i < entityIds.length; i += BATCH_SIZE) {
    const batch = entityIds.slice(i, i + BATCH_SIZE);
    const resp = await hass.callWS({
      type: "recorder/statistics_during_period",
      statistic_ids: batch,
      period: "5minute",
      start_time: startTime,
      types: ["mean"],
    });

    for (const [eid, stats] of Object.entries(resp || {})) {
      const key = uuidByEntity.get(eid);
      if (!key) continue;
      if (!powerHistory.has(key)) powerHistory.set(key, []);
      const arr = powerHistory.get(key);
      for (const pt of stats) {
        if (pt.mean == null) continue;
        arr.push({ time: new Date(pt.start).getTime(), value: pt.mean });
      }
      while (arr.length > maxPoints) arr.shift();
    }
  }
}

async function loadRawHistory(hass, entityIds, uuidByEntity, durationMs, powerHistory, config) {
  const now = new Date();
  const startTime = new Date(now.getTime() - durationMs).toISOString();
  const endTime = now.toISOString();
  const maxPoints = getMaxHistoryPoints(config);
  const minGap = getMinGapMs(config);

  const BATCH_SIZE = 50;
  for (let i = 0; i < entityIds.length; i += BATCH_SIZE) {
    const batch = entityIds.slice(i, i + BATCH_SIZE);
    const resp = await hass.callWS({
      type: "history/history_during_period",
      start_time: startTime,
      end_time: endTime,
      entity_ids: batch,
      minimal_response: true,
      no_attributes: true,
    });

    for (const [eid, states] of Object.entries(resp || {})) {
      const key = uuidByEntity.get(eid);
      if (!key) continue;
      if (!powerHistory.has(key)) powerHistory.set(key, []);
      const arr = powerHistory.get(key);
      for (const s of states) {
        const val = parseFloat(s.s ?? s.state);
        if (isNaN(val)) continue;
        const time = new Date(s.lu ?? s.last_updated).getTime();
        recordSample(arr, time, val, maxPoints, minGap);
      }
      deduplicateAndTrim(arr, maxPoints);
    }
  }
}

export function collectSubDeviceEntityIds(topology, hass) {
  const result = [];
  if (!topology.sub_devices) return result;

  for (const [devId, sub] of Object.entries(topology.sub_devices)) {
    const powerEid = findSubDevicePowerEntity(sub);
    if (powerEid && hass.states[powerEid]) {
      result.push({
        chartKey: `${SUB_DEVICE_KEY_PREFIX}${devId}_power`,
        entityId: powerEid,
      });
    }
    if (sub.type === SUB_DEVICE_TYPE_BESS) {
      const levelEid = findBatteryLevelEntity(sub);
      if (levelEid && hass.states[levelEid]) {
        result.push({
          chartKey: `${SUB_DEVICE_KEY_PREFIX}${devId}_soc`,
          entityId: levelEid,
        });
      }
      const soeEid = findBatterySoeEntity(sub);
      if (soeEid && hass.states[soeEid]) {
        result.push({
          chartKey: `${SUB_DEVICE_KEY_PREFIX}${devId}_soe`,
          entityId: soeEid,
        });
      }
    }
  }
  return result;
}
```

- [ ] **Step 2: Update `span-panel-card.js` to import from core**

```js
// At top, add:
import { loadHistory, collectSubDeviceEntityIds } from "../core/history-loader.js";

// Replace _loadHistory method body:
async _loadHistory() {
  try {
    await loadHistory(this._hass, this._topology, this._config, this._powerHistory);
  } catch (err) {
    console.warn("SPAN Panel: history fetch failed, charts will populate live", err);
  }
}

// Delete methods: _loadStatisticsHistory, _loadRawHistory,
//   _collectSubDeviceEntityIds
```

- [ ] **Step 3: Verify build**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/core/history-loader.js src/card/span-panel-card.js
git commit -m "refactor: extract history loader to core module"
```

---

### Task 4: Extract DOM updater to core module

**Files:**

- Create: `src/core/dom-updater.js`
- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Create `src/core/dom-updater.js`**

Extract `_updateDOM` and `_updateSubDeviceDOM`. These read from `hass.states` and update DOM elements in a shadow root.

```js
// src/core/dom-updater.js
import { formatPowerSigned, formatPowerUnit, formatKw } from "../helpers/format.js";
import { getChartMetric, getCircuitChartEntity } from "../helpers/chart.js";
import {
  findSubDevicePowerEntity,
  findBatteryLevelEntity,
  findBatterySoeEntity,
  findBatteryCapacityEntity,
} from "../helpers/entity-finder.js";
import { updateChart } from "../chart/chart-update.js";
import {
  DEVICE_TYPE_PV,
  RELAY_STATE_CLOSED,
  SUB_DEVICE_TYPE_BESS,
  SUB_DEVICE_KEY_PREFIX,
} from "../constants.js";

export function updateCircuitDOM(root, hass, topology, config, powerHistory) {
  if (!root || !topology || !hass) return;

  const chartMetric = getChartMetric(config);
  const showCurrent = chartMetric.entityRole === "current";

  let totalConsumption = 0;
  let solarProduction = 0;

  for (const [uuid, circuit] of Object.entries(topology.circuits)) {
    const entityId = circuit.entities?.power;
    const state = entityId ? hass.states[entityId] : null;
    const powerW = state ? parseFloat(state.state) || 0 : 0;
    const isProducer = circuit.device_type === DEVICE_TYPE_PV || powerW < 0;

    if (isProducer) {
      solarProduction += Math.abs(powerW);
    } else {
      totalConsumption += Math.abs(powerW);
    }

    const el = root.querySelector(`[data-uuid="${uuid}"]`);
    if (!el) continue;

    const powerEl = el.querySelector(".power-value");
    if (powerEl) {
      if (showCurrent) {
        const currentEid = circuit.entities?.current;
        const currentState = currentEid ? hass.states[currentEid] : null;
        const amps = currentState ? parseFloat(currentState.state) || 0 : 0;
        powerEl.innerHTML = `<strong>${chartMetric.format(amps)}</strong><span class="power-unit">A</span>`;
      } else {
        powerEl.innerHTML = `<strong>${formatPowerSigned(powerW)}</strong><span class="power-unit">${formatPowerUnit(powerW)}</span>`;
      }
    }

    const switchEntityId = circuit.entities?.switch;
    const switchState = switchEntityId ? hass.states[switchEntityId] : null;
    const isOn = switchState
      ? switchState.state === "on"
      : (state?.attributes?.relay_state || circuit.relay_state) === RELAY_STATE_CLOSED;

    el.classList.toggle("circuit-off", !isOn);
    el.classList.toggle("circuit-producer", isProducer);

    const pill = el.querySelector(".toggle-pill");
    if (pill) {
      pill.classList.toggle("toggle-on", isOn);
      pill.classList.toggle("toggle-off", !isOn);
      const label = pill.querySelector(".toggle-label");
      if (label) label.textContent = isOn ? "On" : "Off";
    }

    const chartContainer = el.querySelector(".chart-container");
    if (chartContainer) {
      const chartEntityId = getCircuitChartEntity(circuit, config);
      const history = chartEntityId ? powerHistory.get(uuid) : null;
      if (history && history.length > 0) {
        const durationMs = config._durationMs || 300000;
        const breakerRatingA = circuit.breaker_rating_a;
        updateChart(
          chartContainer, hass, history, durationMs, chartMetric,
          isProducer, null, breakerRatingA,
        );
      }
    }
  }

  _updateHeaderStats(root, hass, topology, totalConsumption, solarProduction);
}

function _updateHeaderStats(
  root, hass, topology, totalConsumption, solarProduction,
) {
  const panelPowerEntity = _findPanelEntity(hass, topology, "current_power");
  if (panelPowerEntity) {
    const state = hass.states[panelPowerEntity];
    if (state) totalConsumption = Math.abs(parseFloat(state.state) || 0);
  }

  const consumptionEl = root.querySelector(".stat-consumption .stat-value");
  if (consumptionEl) consumptionEl.textContent = formatKw(totalConsumption);

  const currentEl = root.querySelector(".stat-current .stat-value");
  if (currentEl) {
    const panelPowerEid = _findPanelEntity(hass, topology, "current_power");
    const panelPowerState = panelPowerEid ? hass.states[panelPowerEid] : null;
    const amperage = panelPowerState
      ? parseFloat(panelPowerState.attributes?.amperage)
      : NaN;
    currentEl.textContent = Number.isFinite(amperage)
      ? amperage.toFixed(1)
      : "--";
  }

  const solarEl = root.querySelector(".stat-solar .stat-value");
  if (solarEl) {
    solarEl.textContent = solarProduction > 0 ? formatKw(solarProduction) : "--";
  }

  const batteryEl = root.querySelector(".stat-battery .stat-value");
  if (batteryEl) {
    const battPowerEid = _findPanelEntity(hass, topology, "battery_power");
    const battPowerState = battPowerEid ? hass.states[battPowerEid] : null;
    if (battPowerState) {
      const battW = parseFloat(battPowerState.state) || 0;
      batteryEl.innerHTML = battW === 0
        ? "&mdash;"
        : `${formatPowerSigned(battW)} <span class="stat-unit">${formatPowerUnit(battW)}</span>`;
    }
  }
}

function _findPanelEntity(hass, topology, suffix) {
  const deviceName = topology.device_name || "";
  const prefix = deviceName.toLowerCase().replace(/[^a-z0-9]/g, "_");
  const candidates = [
    `sensor.${prefix}_${suffix}`,
    `sensor.span_panel_${suffix}`,
  ];
  for (const eid of candidates) {
    if (hass.states[eid]) return eid;
  }
  return null;
}

export function updateSubDeviceDOM(root, hass, topology, config, powerHistory) {
  if (!topology.sub_devices) return;

  for (const [devId, sub] of Object.entries(topology.sub_devices)) {
    const container = root.querySelector(`[data-subdev="${devId}"]`);
    if (!container) continue;

    const powerEid = findSubDevicePowerEntity(sub);
    const powerState = powerEid ? hass.states[powerEid] : null;
    const powerW = powerState ? parseFloat(powerState.state) || 0 : 0;
    const powerEl = container.querySelector(".sub-power-value");
    if (powerEl && powerEid) {
      powerEl.innerHTML = `<strong>${formatPowerSigned(powerW)}</strong> <span class="power-unit">${formatPowerUnit(powerW)}</span>`;
    }

    const isBess = sub.type === SUB_DEVICE_TYPE_BESS;
    const chartMetric = getChartMetric(config);

    if (powerEid) {
      const chartKey = `${SUB_DEVICE_KEY_PREFIX}${devId}_power`;
      const chartContainer = container.querySelector(
        `[data-chart-key="${chartKey}"]`,
      );
      if (chartContainer) {
        const history = powerHistory.get(chartKey);
        if (history && history.length > 0) {
          const durationMs = config._durationMs || 300000;
          updateChart(
            chartContainer, hass, history, durationMs, chartMetric,
            false, null, null,
          );
        }
      }
    }

    if (isBess) {
      const battLevelEid = findBatteryLevelEntity(sub);
      const battSoeEid = findBatterySoeEntity(sub);

      for (const { suffix, eid, metricKey } of [
        { suffix: "soc", eid: battLevelEid, metricKey: "soc" },
        { suffix: "soe", eid: battSoeEid, metricKey: "soe" },
      ]) {
        if (!eid) continue;
        const chartKey = `${SUB_DEVICE_KEY_PREFIX}${devId}_${suffix}`;
        const chartContainer = container.querySelector(
          `[data-chart-key="${chartKey}"]`,
        );
        if (chartContainer) {
          const history = powerHistory.get(chartKey);
          if (history && history.length > 0) {
            const { BESS_CHART_METRICS } = await import("../constants.js");
            const metric = BESS_CHART_METRICS[metricKey];
            const durationMs = config._durationMs || 300000;
            updateChart(
              chartContainer, hass, history, durationMs, metric,
              false, null, null,
            );
          }
        }
      }
    }

    for (const valueEl of container.querySelectorAll(".sub-entity-value[data-eid]")) {
      const eid = valueEl.dataset.eid;
      const state = hass.states[eid];
      if (!state) continue;
      let displayValue;
      if (hass.formatEntityState) {
        displayValue = hass.formatEntityState(state);
      } else {
        displayValue = state.state;
        const unit = state.attributes.unit_of_measurement || "";
        if (unit) displayValue += " " + unit;
      }
      const rawUnit = state.attributes.unit_of_measurement || "";
      if (rawUnit === "Wh") {
        const wh = parseFloat(state.state);
        if (!isNaN(wh)) displayValue = (wh / 1000).toFixed(1) + " kWh";
      }
      valueEl.textContent = displayValue;
    }
  }
}
```

**Note:** The `BESS_CHART_METRICS` dynamic import in the sub-device updater should be changed to a static import at module top to avoid async in a DOM update
function. Fix during implementation:

```js
// At top of dom-updater.js:
import { BESS_CHART_METRICS } from "../constants.js";
// Then use BESS_CHART_METRICS[metricKey] directly (no await import)
```

- [ ] **Step 2: Update `span-panel-card.js`**

```js
// At top, add:
import { updateCircuitDOM, updateSubDeviceDOM } from "../core/dom-updater.js";

// Replace _updateDOM():
_updateDOM() {
  updateCircuitDOM(
    this.shadowRoot, this._hass, this._topology,
    { ...this._config, _durationMs: this._durationMs },
    this._powerHistory,
  );
  updateSubDeviceDOM(
    this.shadowRoot, this._hass, this._topology,
    { ...this._config, _durationMs: this._durationMs },
    this._powerHistory,
  );
}

// Delete methods: _updateDOM (old body), _updateSubDeviceDOM, _findPanelEntity
```

- [ ] **Step 3: Verify build**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/core/dom-updater.js src/card/span-panel-card.js
git commit -m "refactor: extract DOM updater to core module"
```

---

### Task 5: Extract header renderer to core module

**Files:**

- Create: `src/core/header-renderer.js`
- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Create `src/core/header-renderer.js`**

Extract the header HTML from `_render()` into a standalone function. This is the existing header plus new fields (upstream, downstream, grid state, battery SoC,
A/W toggle, gear icon).

```js
// src/core/header-renderer.js
import { escapeHtml } from "../helpers/sanitize.js";
import { formatKw } from "../helpers/format.js";

export function buildHeaderHTML(topology, config) {
  const panelName = escapeHtml(topology.device_name || "SPAN Panel");
  const serial = escapeHtml(topology.serial || "");
  const firmware = escapeHtml(topology.firmware || "");
  const isAmpsMode = (config.chart_metric || "power") === "current";

  return `
    <div class="panel-header">
      <div class="header-left">
        <div class="panel-identity">
          <h1 class="panel-title">${panelName}</h1>
          <span class="panel-serial">${serial}</span>
          <button class="gear-icon panel-gear" title="Panel monitoring settings">
            <ha-icon icon="mdi:cog"></ha-icon>
          </button>
        </div>
        <div class="panel-stats">
          <div class="stat stat-consumption">
            <span class="stat-label">Site</span>
            <div class="stat-row">
              <span class="stat-value">0</span>
              <span class="stat-unit">${isAmpsMode ? "A" : "kW"}</span>
            </div>
          </div>
          <div class="stat stat-grid-state">
            <span class="stat-label">Grid</span>
            <div class="stat-row">
              <span class="stat-value">--</span>
            </div>
          </div>
          <div class="stat stat-upstream">
            <span class="stat-label">Upstream</span>
            <div class="stat-row">
              <span class="stat-value">--</span>
              <span class="stat-unit">${isAmpsMode ? "A" : "kW"}</span>
            </div>
          </div>
          <div class="stat stat-downstream">
            <span class="stat-label">Downstream</span>
            <div class="stat-row">
              <span class="stat-value">--</span>
              <span class="stat-unit">${isAmpsMode ? "A" : "kW"}</span>
            </div>
          </div>
          <div class="stat stat-solar">
            <span class="stat-label">Solar</span>
            <div class="stat-row">
              <span class="stat-value">--</span>
              <span class="stat-unit">${isAmpsMode ? "A" : "kW"}</span>
            </div>
          </div>
          <div class="stat stat-battery">
            <span class="stat-label">Battery</span>
            <div class="stat-row">
              <span class="stat-value">&mdash;</span>
              <span class="stat-unit">%</span>
            </div>
          </div>
        </div>
      </div>
      <div class="header-right">
        <span class="meta-item">${firmware}</span>
        <div class="unit-toggle" title="Toggle Watts / Amps">
          <button class="unit-btn ${isAmpsMode ? "" : "unit-active"}" data-unit="power">W</button>
          <button class="unit-btn ${isAmpsMode ? "unit-active" : ""}" data-unit="current">A</button>
        </div>
      </div>
    </div>
  `;
}
```

- [ ] **Step 2: Update `span-panel-card.js` `_render()` to use it**

```js
// At top, add:
import { buildHeaderHTML } from "../core/header-renderer.js";

// In _render(), replace the inline header HTML block with:
const headerHTML = buildHeaderHTML(topo, this._config);
// And use ${headerHTML} in the template where the header was.
```

- [ ] **Step 3: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/core/header-renderer.js src/card/span-panel-card.js
git commit -m "refactor: extract header renderer to core module with new stats"
```

---

### Task 6: Add monitoring status fetcher

**Files:**

- Create: `src/core/monitoring-status.js`

- [ ] **Step 1: Create `src/core/monitoring-status.js`**

Fetches monitoring status via the `get_monitoring_status` service and provides helper functions for the rendering modules.

```js
// src/core/monitoring-status.js
import { INTEGRATION_DOMAIN } from "../constants.js";

const MONITORING_POLL_INTERVAL_MS = 30_000;

export class MonitoringStatusCache {
  constructor() {
    this._status = null;
    this._lastFetch = 0;
    this._fetching = false;
  }

  async fetch(hass) {
    const now = Date.now();
    if (this._fetching) return this._status;
    if (this._status && now - this._lastFetch < MONITORING_POLL_INTERVAL_MS) {
      return this._status;
    }

    this._fetching = true;
    try {
      const resp = await hass.callService(
        INTEGRATION_DOMAIN,
        "get_monitoring_status",
        {},
        undefined,
        true // returnResponse
      );
      this._status = resp?.response || null;
      this._lastFetch = now;
    } catch {
      // Monitoring may be disabled or service unavailable
      this._status = null;
    } finally {
      this._fetching = false;
    }
    return this._status;
  }

  get status() {
    return this._status;
  }

  clear() {
    this._status = null;
    this._lastFetch = 0;
  }
}

export function getCircuitMonitoringInfo(status, entityId) {
  if (!status?.circuits) return null;
  return status.circuits[entityId] || null;
}

export function getMainsMonitoringInfo(status, entityId) {
  if (!status?.mains) return null;
  return status.mains[entityId] || null;
}

export function hasCustomOverrides(monitoringInfo) {
  if (!monitoringInfo) return false;
  // If any threshold differs from global, it has overrides
  // The presence of the circuit in the response means it's monitored;
  // custom overrides are indicated by non-null override fields
  return monitoringInfo.continuous_threshold_pct !== undefined;
}

export function getUtilizationClass(monitoringInfo) {
  if (!monitoringInfo?.utilization_pct) return "";
  const pct = monitoringInfo.utilization_pct;
  if (pct >= 100) return "utilization-alert";
  if (pct >= 80) return "utilization-warning";
  return "utilization-normal";
}

export function isAlertActive(monitoringInfo) {
  if (!monitoringInfo) return false;
  return monitoringInfo.over_threshold_since != null || monitoringInfo.last_spike_alert != null;
}
```

- [ ] **Step 2: Verify build**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Build succeeds (module is importable but not yet consumed).

- [ ] **Step 3: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add src/core/monitoring-status.js
git commit -m "feat: add monitoring status fetcher and helpers"
```

---

### Task 7: Add shedding and monitoring indicators to circuit cells

**Files:**

- Modify: `src/core/grid-renderer.js`
- Modify: `src/core/dom-updater.js`
- Modify: `src/card/card-styles.js`
- Modify: `src/constants.js`

- [ ] **Step 1: Add shedding constants**

In `src/constants.js`, add:

```js
// ── Shedding priority ──────────────────────────────────────────────────────

export const SHEDDING_PRIORITIES = {
  never: { icon: "mdi:shield-check", color: "#4caf50", label: "Never" },
  soc_threshold: { icon: "mdi:battery-alert-variant-outline", color: "#9c27b0", label: "SoC Threshold" },
  off_grid: { icon: "mdi:transmission-tower", color: "#ff9800", label: "Off-Grid" },
  unknown: { icon: "mdi:help-circle-outline", color: "#888", label: "Unknown" },
};

export const MONITORING_COLORS = {
  normal: "#4caf50",
  warning: "#ff9800",
  alert: "#f44336",
  custom: "#ff9800",
};
```

- [ ] **Step 2: Update `renderCircuitSlot` in `grid-renderer.js`**

Add an optional `monitoringInfo` and `sheddingPriority` parameter. The function renders the shedding icon and gear icon on every circuit, and adds monitoring
indicator classes when monitoring data is available.

Add after the existing `valueHTML` block:

```js
// Shedding icon
const priority = sheddingPriority || "unknown";
const shedInfo = SHEDDING_PRIORITIES[priority] || SHEDDING_PRIORITIES.unknown;
const sheddingHTML = `<ha-icon class="shedding-icon"
  icon="${shedInfo.icon}"
  style="color:${shedInfo.color};--mdc-icon-size:16px;"
  title="${shedInfo.label}"></ha-icon>`;

// Gear icon
const hasOverrides = monitoringInfo && hasCustomOverrides(monitoringInfo);
const gearColor = hasOverrides ? MONITORING_COLORS.custom : "#555";
const gearHTML = `<button class="gear-icon circuit-gear"
  data-uuid="${escapeHtml(uuid)}" style="color:${gearColor};"
  title="Configure circuit">
  <ha-icon icon="mdi:cog" style="--mdc-icon-size:16px;"></ha-icon>
</button>`;

// Utilization (only in amps mode when monitoring active)
let utilizationHTML = "";
if (monitoringInfo?.utilization_pct != null) {
  const pct = monitoringInfo.utilization_pct;
  const utilClass = getUtilizationClass(monitoringInfo);
  utilizationHTML = `<span class="utilization ${utilClass}">${Math.round(pct)}%</span>`;
}

// Alert state
const alertActive = isAlertActive(monitoringInfo);
const alertClass = alertActive ? "circuit-alert" : "";
const customClass = hasOverrides ? "circuit-custom-monitoring" : "";
```

Update the returned HTML template to include these elements in the circuit-header section, between the circuit-info and circuit-controls divs:

```html
<div class="circuit-status">${sheddingHTML} ${utilizationHTML} ${gearHTML}</div>
```

And add `${alertClass} ${customClass}` to the circuit-slot div's class list.

Update the function signature:

```js
export function renderCircuitSlot(
  uuid, circuit, row, col, layout, _durationMs, hass, config,
  monitoringInfo, sheddingPriority,
) {
```

Add imports at the top of `grid-renderer.js`:

```js
import { SHEDDING_PRIORITIES, MONITORING_COLORS } from "../constants.js";
import { hasCustomOverrides, getUtilizationClass, isAlertActive } from "./monitoring-status.js";
```

- [ ] **Step 3: Update `buildGridHTML` to pass monitoring and shedding data**

Update the signature to accept `monitoringStatus` and add the data lookups when calling `renderCircuitSlot`:

```js
export function buildGridHTML(
  topology, totalRows, durationMs, hass, config, monitoringStatus,
) {
  // ... existing code ...
  // In each renderCircuitSlot call, add:
  const circuitEntityId = entry.circuit.entities?.current || entry.circuit.entities?.power;
  const monInfo = monitoringStatus ? getCircuitMonitoringInfo(monitoringStatus, circuitEntityId) : null;
  const selectEid = entry.circuit.entities?.select;
  const sheddingPriority = selectEid && hass.states[selectEid] ? hass.states[selectEid].state : "unknown";
```

- [ ] **Step 4: Add CSS for new elements**

In `src/card/card-styles.js`, add styles for:

```css
.circuit-status {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
}
.shedding-icon {
  opacity: 0.8;
  cursor: default;
}
.gear-icon {
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px;
  opacity: 0.6;
  transition: opacity 0.2s;
}
.gear-icon:hover {
  opacity: 1;
}
.utilization {
  font-size: 0.75em;
  font-weight: 600;
}
.utilization-normal {
  color: #4caf50;
}
.utilization-warning {
  color: #ff9800;
}
.utilization-alert {
  color: #f44336;
}
.circuit-alert {
  border-color: #f44336 !important;
  box-shadow: 0 0 8px rgba(244, 67, 54, 0.3);
}
.circuit-custom-monitoring {
  border-left: 3px solid #ff9800;
}
```

- [ ] **Step 5: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/core/grid-renderer.js src/core/dom-updater.js \
  src/card/card-styles.js src/constants.js
git commit -m "feat: add shedding icons, monitoring indicators, and gear icons to circuit cells"
```

---

### Task 8: Add A/W toggle functionality

**Files:**

- Modify: `src/card/span-panel-card.js`
- Modify: `src/core/header-renderer.js`
- Modify: `src/core/dom-updater.js`

- [ ] **Step 1: Add click handler for A/W toggle in `span-panel-card.js`**

In the click handler setup (where `_handleToggleClick` is bound), add a second delegated listener for the unit toggle buttons:

```js
// In constructor, add:
this._handleUnitToggle = this._onUnitToggle.bind(this);

// In connectedCallback, after existing listener:
this.shadowRoot?.addEventListener("click", this._handleUnitToggle);

// In disconnectedCallback:
this.shadowRoot?.removeEventListener("click", this._handleUnitToggle);

// New method:
_onUnitToggle(event) {
  const btn = event.target.closest(".unit-btn");
  if (!btn) return;
  const unit = btn.dataset.unit;
  if (!unit || unit === (this._config.chart_metric || "power")) return;
  this._config = { ...this._config, chart_metric: unit };
  // Fire config-changed for Lovelace card persistence
  this.dispatchEvent(new CustomEvent("config-changed", {
    detail: { config: this._config },
    bubbles: true, composed: true,
  }));
  // Re-render with new unit
  this._rendered = false;
  this._render();
}
```

- [ ] **Step 2: Update header DOM updater for A/W mode**

In `src/core/dom-updater.js`, update `_updateHeaderStats` to read the amperage attribute when in amps mode, and update units accordingly. The function already
receives `config` — check `config.chart_metric`.

```js
// In _updateHeaderStats, add:
const isAmpsMode = (config.chart_metric || "power") === "current";

// For consumption:
if (isAmpsMode) {
  const panelPowerState = panelPowerEntity ? hass.states[panelPowerEntity] : null;
  const amps = panelPowerState ? parseFloat(panelPowerState.attributes?.amperage) : NaN;
  if (consumptionEl) consumptionEl.textContent = Number.isFinite(amps) ? amps.toFixed(1) : "--";
} else {
  if (consumptionEl) consumptionEl.textContent = formatKw(totalConsumption);
}
```

Apply the same pattern for upstream, downstream, solar stats — read `.attributes.amperage` when in amps mode, `.state` when in watts mode.

- [ ] **Step 3: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/card/span-panel-card.js src/core/header-renderer.js \
  src/core/dom-updater.js
git commit -m "feat: add A/W toggle switching all values and chart axes"
```

---

### Task 9: Build the side panel web component

**Files:**

- Create: `src/core/side-panel.js`

- [ ] **Step 1: Create `src/core/side-panel.js`**

A web component that slides in from the right. Shows circuit config (relay, shedding, monitoring) or panel-level monitoring config.

```js
// src/core/side-panel.js
import { escapeHtml } from "../helpers/sanitize.js";
import { INTEGRATION_DOMAIN, SHEDDING_PRIORITIES } from "../constants.js";

const SIDE_PANEL_STYLES = `
  :host {
    position: fixed; top: 0; right: 0; bottom: 0;
    width: 360px; max-width: 90vw;
    background: var(--card-background-color, #1c1c1c);
    border-left: 1px solid var(--divider-color, #333);
    z-index: 100; overflow-y: auto;
    transform: translateX(100%);
    transition: transform 0.25s ease;
    padding: 16px;
    box-sizing: border-box;
  }
  :host([open]) { transform: translateX(0); }
  .side-panel-header {
    display: flex; justify-content: space-between; align-items: start;
    margin-bottom: 16px;
  }
  .side-panel-title { font-size: 1.2em; font-weight: 600; }
  .side-panel-subtitle {
    font-size: 0.85em; color: var(--secondary-text-color); margin-top: 4px;
  }
  .close-btn {
    background: none; border: none; cursor: pointer;
    color: var(--primary-text-color); font-size: 1.5em;
  }
  .config-section {
    border-top: 1px solid var(--divider-color, #333);
    padding: 12px 0;
  }
  .config-section h3 {
    font-size: 0.9em; margin: 0 0 8px;
    color: var(--primary-text-color);
  }
  .config-row {
    display: flex; justify-content: space-between;
    align-items: center; margin: 8px 0;
  }
  .config-label { font-size: 0.85em; color: var(--primary-text-color); }
  .config-value { font-size: 0.85em; }
  select, input[type="number"] {
    background: var(--secondary-background-color, #333);
    border: 1px solid var(--divider-color);
    color: var(--primary-text-color);
    border-radius: 4px; padding: 4px 8px;
    font-size: 0.85em;
  }
  input[type="number"] { width: 60px; text-align: right; }
  .radio-group { margin: 8px 0; }
  .radio-group label {
    display: block; margin: 4px 0; font-size: 0.85em;
    color: var(--primary-text-color); cursor: pointer;
  }
  .threshold-fields { margin-left: 16px; }
  .threshold-fields.disabled { opacity: 0.4; pointer-events: none; }
`;

export class SpanSidePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._circuitData = null;
    this._monitoringInfo = null;
    this._panelMode = false;
  }

  set hass(val) {
    this._hass = val;
  }

  open(data) {
    if (data.panelMode) {
      this._panelMode = true;
      this._circuitData = null;
    } else {
      this._panelMode = false;
      this._circuitData = data;
    }
    this._monitoringInfo = data.monitoringInfo || null;
    this._render();
    this.setAttribute("open", "");
  }

  close() {
    this.removeAttribute("open");
  }

  _render() {
    if (this._panelMode) {
      this._renderPanelConfig();
    } else {
      this._renderCircuitConfig();
    }
  }

  _renderCircuitConfig() {
    const c = this._circuitData;
    if (!c) return;

    const name = escapeHtml(c.name || "Unknown");
    const rating = c.breaker_rating_a ? `${Math.round(c.breaker_rating_a)}A` : "";
    const voltage = c.voltage || "120";
    const tabs = c.tabs ? `Tabs [${c.tabs.join(", ")}]` : "";
    const subtitle = [rating, `${voltage}V`, tabs].filter(Boolean).join(" · ");

    const selectEid = c.entities?.select;
    const currentPriority = selectEid && this._hass?.states[selectEid] ? this._hass.states[selectEid].state : "unknown";

    const switchEid = c.entities?.switch;
    const switchState = switchEid && this._hass?.states[switchEid];
    const isOn = switchState ? switchState.state === "on" : null;
    const showRelay = c.is_user_controllable !== false && switchEid;

    const mon = this._monitoringInfo;
    const hasOverrides = mon && mon.continuous_threshold_pct !== undefined;

    this.shadowRoot.innerHTML = `
      <style>${SIDE_PANEL_STYLES}</style>
      <div class="side-panel-header">
        <div>
          <div class="side-panel-title">${name}</div>
          <div class="side-panel-subtitle">${subtitle}</div>
        </div>
        <button class="close-btn" id="close-btn">&times;</button>
      </div>

      ${
        showRelay
          ? `
        <div class="config-section">
          <div class="config-row">
            <span class="config-label">Relay</span>
            <ha-switch id="relay-toggle" ${isOn ? "checked" : ""}></ha-switch>
          </div>
        </div>
      `
          : ""
      }

      <div class="config-section">
        <h3>Shedding Priority</h3>
        <select id="shedding-select">
          ${Object.entries(SHEDDING_PRIORITIES)
            .filter(([k]) => k !== "unknown")
            .map(([k, v]) => `<option value="${k}" ${k === currentPriority ? "selected" : ""}>${escapeHtml(v.label)}</option>`)
            .join("")}
        </select>
      </div>

      <div class="config-section">
        <div class="config-row">
          <h3 style="margin:0;">Monitoring</h3>
          <ha-switch id="monitoring-enabled" ${mon ? "checked" : ""}></ha-switch>
        </div>
        <div class="radio-group" id="monitoring-mode" ${!mon ? 'style="display:none"' : ""}>
          <label>
            <input type="radio" name="mon-mode" value="global" ${!hasOverrides ? "checked" : ""}>
            Global defaults
          </label>
          <label>
            <input type="radio" name="mon-mode" value="custom" ${hasOverrides ? "checked" : ""}>
            Custom
          </label>
        </div>
        <div class="threshold-fields ${!hasOverrides ? "disabled" : ""}" id="threshold-fields">
          <div class="config-row">
            <span class="config-label">Continuous threshold</span>
            <div><input type="number" id="continuous-pct" value="${mon?.continuous_threshold_pct ?? 80}" min="1" max="200">%</div>
          </div>
          <div class="config-row">
            <span class="config-label">Spike threshold</span>
            <div><input type="number" id="spike-pct" value="${mon?.spike_threshold_pct ?? 100}" min="1" max="200">%</div>
          </div>
          <div class="config-row">
            <span class="config-label">Window duration</span>
            <div><input type="number" id="window-m" value="${mon?.window_duration_m ?? 15}" min="1" max="180">m</div>
          </div>
          <div class="config-row">
            <span class="config-label">Cooldown</span>
            <div><input type="number" id="cooldown-m" value="${mon?.cooldown_duration_m ?? 15}" min="1" max="180">m</div>
          </div>
        </div>
      </div>
    `;

    this._attachListeners();
  }

  _renderPanelConfig() {
    // Panel-level global monitoring settings
    // Rendered similarly but without relay/shedding sections
    // Uses the global thresholds from monitoring status
    this.shadowRoot.innerHTML = `
      <style>${SIDE_PANEL_STYLES}</style>
      <div class="side-panel-header">
        <div>
          <div class="side-panel-title">Panel Monitoring Settings</div>
          <div class="side-panel-subtitle">Global defaults for all circuits</div>
        </div>
        <button class="close-btn" id="close-btn">&times;</button>
      </div>
      <div class="config-section">
        <p style="color:var(--secondary-text-color);font-size:0.85em;">
          Global monitoring settings are managed in the integration's
          options flow. Open Settings &gt; Devices &amp; Services &gt;
          SPAN Panel &gt; Configure to change global thresholds.
        </p>
      </div>
    `;
    this.shadowRoot.getElementById("close-btn")?.addEventListener("click", () => this.close());
  }

  _attachListeners() {
    const hass = this._hass;
    const c = this._circuitData;
    if (!hass || !c) return;

    // Close button
    this.shadowRoot.getElementById("close-btn")?.addEventListener("click", () => this.close());

    // Relay toggle
    const relayToggle = this.shadowRoot.getElementById("relay-toggle");
    if (relayToggle) {
      relayToggle.addEventListener("change", () => {
        const service = relayToggle.checked ? "turn_on" : "turn_off";
        hass.callService("switch", service, {
          entity_id: c.entities.switch,
        });
      });
    }

    // Shedding dropdown
    const sheddingSelect = this.shadowRoot.getElementById("shedding-select");
    if (sheddingSelect) {
      sheddingSelect.addEventListener("change", () => {
        const selectEid = c.entities?.select;
        if (selectEid) {
          hass.callService("select", "select_option", {
            entity_id: selectEid,
            option: sheddingSelect.value,
          });
        }
      });
    }

    // Monitoring mode radio
    const modeRadios = this.shadowRoot.querySelectorAll('input[name="mon-mode"]');
    const thresholdFields = this.shadowRoot.getElementById("threshold-fields");
    for (const radio of modeRadios) {
      radio.addEventListener("change", () => {
        const isCustom = radio.value === "custom" && radio.checked;
        thresholdFields?.classList.toggle("disabled", !isCustom);
        if (!isCustom) {
          // Switching to global: clear overrides
          const circuitEid = c.entities?.current || c.entities?.power;
          if (circuitEid) {
            hass.callService(INTEGRATION_DOMAIN, "clear_circuit_threshold", {
              circuit_id: circuitEid,
            });
          }
        }
      });
    }

    // Threshold inputs (debounced)
    let debounceTimer = null;
    const thresholdInputs = this.shadowRoot.querySelectorAll(".threshold-fields input[type=number]");
    for (const input of thresholdInputs) {
      input.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => this._saveThresholds(), 500);
      });
    }
  }

  _saveThresholds() {
    const hass = this._hass;
    const c = this._circuitData;
    if (!hass || !c) return;

    const circuitEid = c.entities?.current || c.entities?.power;
    if (!circuitEid) return;

    const getValue = id => {
      const el = this.shadowRoot.getElementById(id);
      return el ? parseInt(el.value, 10) : undefined;
    };

    hass.callService(INTEGRATION_DOMAIN, "set_circuit_threshold", {
      circuit_id: circuitEid,
      continuous_threshold_pct: getValue("continuous-pct"),
      spike_threshold_pct: getValue("spike-pct"),
      window_duration_m: getValue("window-m"),
    });
  }
}

customElements.define("span-side-panel", SpanSidePanel);
```

- [ ] **Step 2: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/core/side-panel.js
git commit -m "feat: add side panel web component for circuit and panel config"
```

---

### Task 10: Wire gear icon clicks to side panel in card

**Files:**

- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Add side panel element and click handlers**

```js
// In _render(), after the ha-card closing tag, add:
    <span-side-panel></span-side-panel>

// In _render(), after attaching the toggle click listener:
    const sidePanel = this.shadowRoot.querySelector("span-side-panel");
    if (sidePanel) sidePanel.hass = hass;

// Add delegated click handler for gear icons:
// In constructor:
this._handleGearClick = this._onGearClick.bind(this);

// In _render() after listener setup:
this.shadowRoot.addEventListener("click", this._handleGearClick);

// New method:
_onGearClick(event) {
  const gearBtn = event.target.closest(".gear-icon");
  if (!gearBtn) return;

  const sidePanel = this.shadowRoot.querySelector("span-side-panel");
  if (!sidePanel) return;
  sidePanel.hass = this._hass;

  if (gearBtn.classList.contains("panel-gear")) {
    sidePanel.open({ panelMode: true });
    return;
  }

  const uuid = gearBtn.dataset.uuid;
  if (!uuid || !this._topology) return;

  const circuit = this._topology.circuits[uuid];
  if (!circuit) return;

  const monitoringInfo = this._monitoringCache?.status?.circuits?.[
    circuit.entities?.current || circuit.entities?.power
  ] || null;

  sidePanel.open({
    ...circuit,
    uuid,
    monitoringInfo,
  });
}

// At top, add import:
import "../core/side-panel.js";
```

- [ ] **Step 2: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/card/span-panel-card.js
git commit -m "feat: wire gear icon clicks to side panel in card"
```

---

### Task 11: Add monitoring cache to card lifecycle

**Files:**

- Modify: `src/card/span-panel-card.js`

- [ ] **Step 1: Integrate MonitoringStatusCache**

```js
// At top, add:
import { MonitoringStatusCache } from "../core/monitoring-status.js";

// In constructor:
this._monitoringCache = new MonitoringStatusCache();

// In set hass(), after topology discovery succeeds and before render:
// Fetch monitoring status (non-blocking)
this._monitoringCache.fetch(hass).then(() => {
  if (this._rendered) this._updateDOM();
});

// In _render(), pass monitoring status to buildGridHTML:
const monitoringStatus = this._monitoringCache.status;
const gridHTML = buildGridHTML(topo, totalRows, durationMs, hass, this._config, monitoringStatus);

// In setConfig(), clear cache:
this._monitoringCache.clear();
```

- [ ] **Step 2: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/card/span-panel-card.js
git commit -m "feat: integrate monitoring status cache into card lifecycle"
```

---

### Task 12: Add second rollup output for integration panel

**Files:**

- Modify: `rollup.config.mjs`
- Create: `src/panel/index.js`
- Create: `src/panel/span-panel.js`

- [ ] **Step 1: Update `rollup.config.mjs` for two outputs**

```js
import terser from "@rollup/plugin-terser";

const dev = process.env.ROLLUP_WATCH === "true";
const plugins = dev ? [] : [terser()];

export default [
  {
    input: "src/index.js",
    output: {
      file: "dist/span-panel-card.js",
      format: "iife",
      sourcemap: false,
    },
    plugins,
  },
  {
    input: "src/panel/index.js",
    output: {
      file: "dist/span-panel.js",
      format: "iife",
      sourcemap: false,
    },
    plugins,
  },
];
```

- [ ] **Step 2: Create `src/panel/index.js`**

```js
import { CARD_VERSION } from "../constants.js";
import { SpanPanelElement } from "./span-panel.js";

customElements.define("span-panel", SpanPanelElement);

console.warn(
  `%c SPAN-PANEL %c v${CARD_VERSION} `,
  "background: var(--primary-color, #4dd9af); color: #000; font-weight: 700; padding: 2px 6px; border-radius: 4px 0 0 4px;",
  "background: var(--secondary-background-color, #333); color: var(--primary-text-color, #fff); padding: 2px 6px; border-radius: 0 4px 4px 0;"
);
```

- [ ] **Step 3: Create `src/panel/span-panel.js` (shell)**

```js
// src/panel/span-panel.js
import { INTEGRATION_DOMAIN } from "../constants.js";
import "../core/side-panel.js";

const PANEL_STYLES = `
  :host {
    display: block;
    padding: 16px;
    max-width: 900px;
    margin: 0 auto;
  }
  .panel-tabs {
    display: flex; gap: 0;
    border-bottom: 2px solid var(--divider-color, #333);
    margin-bottom: 16px;
  }
  .panel-tab {
    padding: 8px 20px; cursor: pointer;
    font-size: 0.9em; font-weight: 500;
    color: var(--secondary-text-color);
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    background: none; border-top: none; border-left: none; border-right: none;
  }
  .panel-tab.active {
    color: var(--primary-color);
    border-bottom-color: var(--primary-color);
  }
  .panel-selector {
    margin-bottom: 16px;
  }
  .panel-selector select {
    background: var(--secondary-background-color, #333);
    border: 1px solid var(--divider-color);
    color: var(--primary-text-color);
    border-radius: 4px; padding: 6px 12px; font-size: 0.9em;
  }
  .tab-content { min-height: 400px; }
`;

export class SpanPanelElement extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._panels = [];
    this._selectedPanelId = null;
    this._activeTab = "dashboard";
  }

  set hass(val) {
    this._hass = val;
    if (!this._panels.length) {
      this._discoverPanels();
    }
  }

  setConfig(config) {
    this._config = config || {};
  }

  async _discoverPanels() {
    if (!this._hass) return;
    const devices = await this._hass.callWS({
      type: "config/device_registry/list",
    });
    this._panels = devices.filter(d => d.identifiers?.some(id => id[0] === INTEGRATION_DOMAIN));

    // Restore selection from localStorage
    const stored = localStorage.getItem("span_panel_selected");
    if (stored && this._panels.some(p => p.id === stored)) {
      this._selectedPanelId = stored;
    } else if (this._panels.length > 0) {
      this._selectedPanelId = this._panels[0].id;
    }

    this._render();
  }

  _render() {
    const showSelector = this._panels.length > 1;

    this.shadowRoot.innerHTML = `
      <style>${PANEL_STYLES}</style>

      ${
        showSelector
          ? `
        <div class="panel-selector">
          <select id="panel-select">
            ${this._panels
              .map(
                p => `
              <option value="${p.id}" ${p.id === this._selectedPanelId ? "selected" : ""}>
                ${p.name_by_user || p.name || p.id}
              </option>
            `
              )
              .join("")}
          </select>
        </div>
      `
          : ""
      }

      <div class="panel-tabs">
        <button class="panel-tab ${this._activeTab === "dashboard" ? "active" : ""}" data-tab="dashboard">Panel</button>
        <button class="panel-tab ${this._activeTab === "monitoring" ? "active" : ""}" data-tab="monitoring">Monitoring</button>
        <button class="panel-tab ${this._activeTab === "settings" ? "active" : ""}" data-tab="settings">Settings</button>
      </div>

      <div class="tab-content" id="tab-content">
        <!-- Tab content rendered by sub-modules -->
      </div>
    `;

    // Panel selector
    const select = this.shadowRoot.getElementById("panel-select");
    if (select) {
      select.addEventListener("change", () => {
        this._selectedPanelId = select.value;
        localStorage.setItem("span_panel_selected", select.value);
        this._renderTab();
      });
    }

    // Tab clicks
    for (const tab of this.shadowRoot.querySelectorAll(".panel-tab")) {
      tab.addEventListener("click", () => {
        this._activeTab = tab.dataset.tab;
        for (const t of this.shadowRoot.querySelectorAll(".panel-tab")) {
          t.classList.toggle("active", t.dataset.tab === this._activeTab);
        }
        this._renderTab();
      });
    }

    this._renderTab();
  }

  async _renderTab() {
    const container = this.shadowRoot.getElementById("tab-content");
    if (!container) return;

    switch (this._activeTab) {
      case "dashboard":
        container.innerHTML = "<p>Dashboard view loading...</p>";
        // Task 13 will implement this fully
        break;
      case "monitoring":
        container.innerHTML = "<p>Monitoring settings — coming soon</p>";
        // Task 14 will implement this
        break;
      case "settings":
        container.innerHTML = "<p>General settings — coming soon</p>";
        // Task 15 will implement this
        break;
    }
  }
}
```

- [ ] **Step 4: Verify build produces both files**

Run: `cd /Users/bflood/projects/HA/cards/span-card && npm run build`

Expected: Both `dist/span-panel-card.js` and `dist/span-panel.js` produced.

```bash
ls -la dist/
# Should show:
#   span-panel-card.js
#   span-panel.js
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card
git add rollup.config.mjs src/panel/index.js src/panel/span-panel.js dist/
git commit -m "feat: add integration panel shell with tab router and multi-panel selector"
```

---

### Task 13: Implement dashboard tab using core modules

**Files:**

- Create: `src/panel/tab-dashboard.js`
- Modify: `src/panel/span-panel.js`

- [ ] **Step 1: Create `src/panel/tab-dashboard.js`**

Reuses the core modules (grid-renderer, header-renderer, dom-updater, history-loader) to render the same physical panel view inside the full-page panel.

```js
// src/panel/tab-dashboard.js
import { discoverTopology } from "../card/card-discovery.js";
import { buildHeaderHTML } from "../core/header-renderer.js";
import { buildGridHTML } from "../core/grid-renderer.js";
import { buildSubDevicesHTML } from "../core/sub-device-renderer.js";
import { updateCircuitDOM, updateSubDeviceDOM } from "../core/dom-updater.js";
import { loadHistory } from "../core/history-loader.js";
import { MonitoringStatusCache } from "../core/monitoring-status.js";
import { CARD_STYLES } from "../card/card-styles.js";
import { getHistoryDurationMs } from "../helpers/history.js";
import { recordSample, getMaxHistoryPoints, getMinGapMs } from "../helpers/history.js";
import { getCircuitChartEntity } from "../helpers/chart.js";
import { LIVE_SAMPLE_INTERVAL_MS } from "../constants.js";

export class DashboardTab {
  constructor() {
    this._topology = null;
    this._panelSize = 0;
    this._powerHistory = new Map();
    this._monitoringCache = new MonitoringStatusCache();
    this._updateInterval = null;
  }

  async render(container, hass, deviceId, config) {
    this.stop();

    try {
      const result = await discoverTopology(hass, deviceId);
      this._topology = result.topology;
      this._panelSize = result.panelSize;
    } catch (err) {
      container.innerHTML = `<p style="color:var(--error-color);">${err.message}</p>`;
      return;
    }

    await this._monitoringCache.fetch(hass);

    const topo = this._topology;
    const totalRows = Math.ceil(this._panelSize / 2);
    const durationMs = getHistoryDurationMs(config);
    const monitoringStatus = this._monitoringCache.status;

    const headerHTML = buildHeaderHTML(topo, config);
    const gridHTML = buildGridHTML(topo, totalRows, durationMs, hass, config, monitoringStatus);
    const subDevHTML = buildSubDevicesHTML(topo, hass, config, durationMs);

    container.innerHTML = `
      <style>${CARD_STYLES}</style>
      ${headerHTML}
      <div class="panel-grid" style="grid-template-rows: repeat(${totalRows}, auto);">
        ${gridHTML}
      </div>
      ${subDevHTML ? `<div class="sub-devices">${subDevHTML}</div>` : ""}
    `;

    // Load history
    try {
      await loadHistory(hass, topo, config, this._powerHistory);
    } catch {
      // Charts will populate live
    }

    // Start live updates
    this._updateInterval = setInterval(() => {
      this._recordSamples(hass, config);
      updateCircuitDOM(container, hass, topo, { ...config, _durationMs: durationMs }, this._powerHistory);
      updateSubDeviceDOM(container, hass, topo, { ...config, _durationMs: durationMs }, this._powerHistory);
    }, LIVE_SAMPLE_INTERVAL_MS);
  }

  _recordSamples(hass, config) {
    if (!this._topology) return;
    const maxPoints = getMaxHistoryPoints(config);
    const minGap = getMinGapMs(config);
    const now = Date.now();

    for (const [uuid, circuit] of Object.entries(this._topology.circuits)) {
      const eid = getCircuitChartEntity(circuit, config);
      if (!eid) continue;
      const state = hass.states[eid];
      if (!state) continue;
      const val = parseFloat(state.state);
      if (isNaN(val)) continue;
      if (!this._powerHistory.has(uuid)) this._powerHistory.set(uuid, []);
      recordSample(this._powerHistory.get(uuid), now, val, maxPoints, minGap);
    }
  }

  stop() {
    if (this._updateInterval) {
      clearInterval(this._updateInterval);
      this._updateInterval = null;
    }
  }
}
```

- [ ] **Step 2: Wire dashboard tab into `span-panel.js`**

```js
// At top of span-panel.js, add:
import { DashboardTab } from "./tab-dashboard.js";

// In constructor:
this._dashboardTab = new DashboardTab();

// In _renderTab(), replace the dashboard case:
case "dashboard": {
  container.innerHTML = "";
  const config = {
    chart_metric: "power",
    history_minutes: 5,
    show_panel: true,
    show_battery: true,
    show_evse: true,
  };
  await this._dashboardTab.render(
    container, this._hass, this._selectedPanelId, config,
  );
  break;
}
```

- [ ] **Step 3: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/panel/tab-dashboard.js src/panel/span-panel.js
git commit -m "feat: implement dashboard tab reusing core rendering modules"
```

---

### Task 14: Implement monitoring tab

**Files:**

- Create: `src/panel/tab-monitoring.js`
- Modify: `src/panel/span-panel.js`

- [ ] **Step 1: Create `src/panel/tab-monitoring.js`**

Shows global monitoring settings and a table of per-circuit overrides. Uses `get_monitoring_status` service for data, existing threshold services for mutations.

```js
// src/panel/tab-monitoring.js
import { INTEGRATION_DOMAIN } from "../constants.js";
import { escapeHtml } from "../helpers/sanitize.js";

export class MonitoringTab {
  async render(container, hass) {
    let status = null;
    try {
      const resp = await hass.callService(INTEGRATION_DOMAIN, "get_monitoring_status", {}, undefined, true);
      status = resp?.response || null;
    } catch {
      container.innerHTML = `
        <div style="padding:16px;">
          <h2>Monitoring</h2>
          <p style="color:var(--secondary-text-color);">
            Monitoring is not enabled. Enable it in the integration's
            options flow (Settings &gt; Devices &amp; Services &gt;
            SPAN Panel &gt; Configure &gt; Monitoring).
          </p>
        </div>
      `;
      return;
    }

    const circuits = status?.circuits || {};
    const mains = status?.mains || {};
    const circuitEntries = Object.entries(circuits);
    const mainsEntries = Object.entries(mains);

    const overrideRows = [...circuitEntries, ...mainsEntries]
      .filter(([, info]) => info.continuous_threshold_pct !== undefined)
      .map(
        ([entityId, info]) => `
        <tr>
          <td>${escapeHtml(info.name || entityId)}</td>
          <td>${info.continuous_threshold_pct ?? "--"}%</td>
          <td>${info.spike_threshold_pct ?? "--"}%</td>
          <td>${info.window_duration_m ?? "--"}m</td>
          <td>
            <button class="reset-btn" data-entity="${escapeHtml(entityId)}"
                    data-type="${mainsEntries.some(([e]) => e === entityId) ? "mains" : "circuit"}">
              Reset
            </button>
          </td>
        </tr>
      `
      )
      .join("");

    container.innerHTML = `
      <div style="padding:16px;">
        <h2>Monitoring</h2>
        <p style="color:var(--secondary-text-color);margin-bottom:16px;">
          Global monitoring settings are managed in the integration's
          options flow. Per-circuit overrides are shown below.
        </p>

        <h3>Per-Circuit Overrides</h3>
        ${
          overrideRows
            ? `
          <table style="width:100%;border-collapse:collapse;">
            <thead>
              <tr style="text-align:left;border-bottom:1px solid var(--divider-color);">
                <th style="padding:8px;">Circuit</th>
                <th style="padding:8px;">Continuous</th>
                <th style="padding:8px;">Spike</th>
                <th style="padding:8px;">Window</th>
                <th style="padding:8px;"></th>
              </tr>
            </thead>
            <tbody>${overrideRows}</tbody>
          </table>
        `
            : `
          <p style="color:var(--secondary-text-color);">
            All circuits using global defaults.
          </p>
        `
        }
      </div>
    `;

    // Reset button handlers
    for (const btn of container.querySelectorAll(".reset-btn")) {
      btn.addEventListener("click", async () => {
        const entityId = btn.dataset.entity;
        const type = btn.dataset.type;
        const service = type === "mains" ? "clear_mains_threshold" : "clear_circuit_threshold";
        const param = type === "mains" ? { leg: entityId } : { circuit_id: entityId };
        await hass.callService(INTEGRATION_DOMAIN, service, param);
        // Re-render to reflect the change
        await this.render(container, hass);
      });
    }
  }
}
```

- [ ] **Step 2: Wire into `span-panel.js`**

```js
// At top:
import { MonitoringTab } from "./tab-monitoring.js";

// In constructor:
this._monitoringTab = new MonitoringTab();

// In _renderTab(), monitoring case:
case "monitoring":
  container.innerHTML = "";
  await this._monitoringTab.render(container, this._hass);
  break;
```

- [ ] **Step 3: Verify build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/panel/tab-monitoring.js src/panel/span-panel.js
git commit -m "feat: implement monitoring tab with overrides table"
```

---

### Task 15: Implement settings tab

**Files:**

- Create: `src/panel/tab-settings.js`
- Modify: `src/panel/span-panel.js`

- [ ] **Step 1: Create `src/panel/tab-settings.js`**

Shows general integration settings. Since HA config entry options can only be updated via the config flow, this tab provides a link to it.

```js
// src/panel/tab-settings.js
export class SettingsTab {
  render(container, hass) {
    container.innerHTML = `
      <div style="padding:16px;">
        <h2>Settings</h2>
        <p style="color:var(--secondary-text-color);margin-bottom:16px;">
          General integration settings (entity naming, device prefix,
          circuit numbers) are managed through the integration's options
          flow.
        </p>
        <a href="/config/integrations/integration/span_panel"
           style="color:var(--primary-color);">
          Open SPAN Panel Integration Settings
        </a>
      </div>
    `;
  }
}
```

- [ ] **Step 2: Wire into `span-panel.js`**

```js
// At top:
import { SettingsTab } from "./tab-settings.js";

// In constructor:
this._settingsTab = new SettingsTab();

// In _renderTab(), settings case:
case "settings":
  container.innerHTML = "";
  this._settingsTab.render(container, this._hass);
  break;
```

- [ ] **Step 3: Verify both bundles build, commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
ls -la dist/span-panel-card.js dist/span-panel.js
git add src/panel/tab-settings.js src/panel/span-panel.js
git commit -m "feat: implement settings tab with integration link"
```

---

## Plan 2 tasks follow — see separate file for integration backend
