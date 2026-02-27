# V2 Sensor Alignment

## Context

The v2 MQTT migration is functionally complete (181 tests passing, both repos on `ebus_integration` branch). The integration receives all Homie data from the
panel's private eBus MQTT broker, but several sensors still expose v1-derived shim values (DSM_GRID_UP, PANEL_ON_GRID, etc.) instead of the actual v2 MQTT
values. Additionally, the panel's MQTT schema exposes rich topology and metadata (breaker ratings, dipole status, per-phase currents/voltages) that is not
surfaced to users at all.

**Goal:** Extend the HA entity model with honest v2 state values, new measurements, and enriched attributes — exposing panel topology and metadata through
sensor attributes rather than a separate MQTT bridge. All structural data (tabs, breaker ratings, device types, panel size) is accessible through entity
attributes, allowing custom Lovelace cards to read everything they need from the HA entity model directly.

## Architecture

```text
SPAN eBus MQTT Broker (private, TLS, panel-specific creds)
    │
    └── SpanMqttClient (library) ── subscribes to ebus/5/{serial}/#
            │
            └── HomieDeviceConsumer → SpanPanelSnapshot
                    │
                    └── Coordinator → Entities (sensors, switches, selects, binary_sensors)
                                         │
                                         ├── Sensors: live measurements, state values, energy tracking
                                         └── Attributes: topology, metadata, per-leg voltages, breaker ratings
```

## Part 1: Sensor & Attribute Changes

### 1A. Replace v1-derived panel status sensors with honest v2 values

**Remove:**

| Key         | Name      | Value source                             | Problem                                                 |
| ----------- | --------- | ---------------------------------------- | ------------------------------------------------------- |
| `dsm_state` | DSM State | `_derive_dsm_state()` → DSM_GRID_UP/DOWN | Lossy: DPS=PV → "GRID_DOWN" even when grid is connected |

`dsm_state` conflates "what is providing power" with "is the grid connected" — solar-dominant panels report DSM_GRID_DOWN while the grid is still up. Replaced
by `dominant_power_source` which exposes the raw enum without lossy interpretation.

**Keep (improved derivation):**

| Key                  | Name               | Value source                        | Change                                                |
| -------------------- | ------------------ | ----------------------------------- | ----------------------------------------------------- |
| `dsm_grid_state`     | DSM Grid State     | `_derive_dsm_grid_state()` improved | Multi-signal heuristic instead of BESS-only lookup    |
| `current_run_config` | Current Run Config | `_derive_run_config()` improved     | Tri-state from dsm_grid_state + grid_islandable + DPS |

**`dsm_grid_state` — improved derivation:**

The current `_derive_dsm_grid_state()` only checks `bess/grid-state`, returning UNKNOWN for every non-BESS panel. The improved derivation combines three signals
already on the snapshot (see Part 2D for implementation):

```text
1. bess/grid-state available?           → use it (authoritative)
2. dominant_power_source == GRID?       → DSM_ON_GRID (grid is primary source)
3. dominant_power_source != GRID
   AND instant_grid_power_w != 0?       → DSM_ON_GRID (grid still exchanging power)
4. dominant_power_source != GRID
   AND instant_grid_power_w == 0?       → DSM_OFF_GRID (nothing flowing + grid not dominant)
```

Case 4 avoids the net-zero false positive: if the panel is grid-connected but at net-zero exchange, DPS is still GRID (the grid is the primary source/sink), so
case 2 fires — not case 4.

**`current_run_config` — improved derivation:**

The v1 derivation collapsed `dominant_power_source` to PANEL_ON_GRID/PANEL_OFF_GRID, losing the PANEL_BACKUP value entirely. Now that `dsm_grid_state` reliably
answers "is the grid connected?" and we have `grid_islandable` and `dominant_power_source`, we can reconstruct the full tri-state:

| `grid_islandable` | `dsm_grid_state` | `dominant_power_source` | → `current_run_config` | Reasoning                                            |
| ----------------- | ---------------- | ----------------------- | ---------------------- | ---------------------------------------------------- |
| false             | DSM_ON_GRID      | \*                      | PANEL_ON_GRID          | Non-islandable panel, grid connected                 |
| true              | DSM_ON_GRID      | \*                      | PANEL_ON_GRID          | Islandable panel, grid connected                     |
| true              | DSM_OFF_GRID     | BATTERY                 | PANEL_BACKUP           | Islanded, running on battery — grid failure          |
| true              | DSM_OFF_GRID     | PV / GENERATOR          | PANEL_OFF_GRID         | Islanded, running on local generation — intentional  |
| true              | DSM_OFF_GRID     | NONE / UNKNOWN          | UNKNOWN                | Islanded, power source unclear                       |
| false             | DSM_OFF_GRID     | \*                      | UNKNOWN                | Shouldn't happen — non-islandable panel can't island |

The key insight: when the panel is off-grid and running on **battery**, that's a backup scenario (grid failed, battery keeping things alive). When off-grid and
running on **PV or generator**, that's intentional off-grid operation. This distinction was available in the v1 REST API but lost in the original v2 derivation.

**Add:**

| Key                     | Name                  | Value source              | MQTT property                                                             |
| ----------------------- | --------------------- | ------------------------- | ------------------------------------------------------------------------- |
| `dominant_power_source` | Dominant Power Source | `s.dominant_power_source` | core/dominant-power-source (enum: GRID,BATTERY,PV,GENERATOR,NONE,UNKNOWN) |

**Kept unchanged:** `main_relay_state` (Main Relay State) — already maps directly to core/relay.

**Rationale:** `dsm_state` derived grid-up/grid-down from `dominant_power_source`, conflating "what is providing power" with "is the grid connected". Now that
`dominant_power_source` is exposed directly as a sensor, `dsm_state` is redundant. `current_run_config` is retained because it answers a distinct question —
"what operational mode is the panel in?" — by combining `dsm_grid_state`, `grid_islandable`, and `dominant_power_source` into a meaningful tri-state that the
other sensors individually cannot express. `grid_islandable` is a static boolean (panel capability, not state) — exposed as an attribute on the panel power
sensor.

### 1B. New panel-level sensors

| Key            | Name         | Device class | Unit | Value source     | MQTT property                                           |
| -------------- | ------------ | ------------ | ---- | ---------------- | ------------------------------------------------------- |
| `vendor_cloud` | Vendor Cloud | —            | —    | `s.vendor_cloud` | core/vendor-cloud (enum: UNKNOWN,UNCONNECTED,CONNECTED) |

**Removed from binary_sensor.py:** The `SYSTEM_CELLULAR_LINK` ("Vendor Cloud") entry is removed from `BINARY_SENSORS`. It was coercing a tri-state enum
(UNKNOWN/UNCONNECTED/CONNECTED) to boolean. The new regular sensor exposes the actual value.

### 1C. New power-flows sensors

The `energy.ebus.device.power-flows` node provides system-level power flow data. Of the four properties (pv, battery, grid, site), two provide value as sensors:

- `power_flow_grid` — redundant with `instant_grid_power_w` (upstream lugs active-power), not exposed
- `power_flow_pv` — derivable from PV circuit power sensors (circuits with device_type "pv"), not exposed
- `power_flow_battery` — **genuinely new**: battery charge/discharge rate, no existing sensor
- `power_flow_site` — mathematically grid + PV + battery, but valuable as a direct historical metric

**Entity ID consistency:** These sensors follow the established `*_power` suffix pattern used by existing panel power sensors (`current_power`,
`feed_through_power`). The sensor definition `key` doubles as the entity suffix since these are v2-native (no legacy camelCase key to map from). Unique ID and
entity ID construction are handled by the existing helper infrastructure (`construct_synthetic_unique_id_for_entry()`, `get_panel_entity_suffix()`) — the same
code paths used by `instantGridPowerW` and `feedthroughPowerW`.

| Key             | Name          | Suffix          | Device class | Unit | Value source           |
| --------------- | ------------- | --------------- | ------------ | ---- | ---------------------- |
| `battery_power` | Battery Power | `battery_power` | `POWER`      | W    | `s.power_flow_battery` |
| `site_power`    | Site Power    | `site_power`    | `POWER`      | W    | `s.power_flow_site`    |

**Naming infrastructure updates:**

- `helpers.py` `PANEL_ENTITY_SUFFIX_MAPPING`: add `"battery_power": "battery_power"`, `"site_power": "site_power"`
- `helpers.py` `PANEL_SUFFIX_MAPPING`: add `"battery_power": "battery_power"`, `"site_power": "site_power"`
- `entity_id_naming_patterns.py` `panel_level_suffixes`: add `"battery_power"`, `"site_power"`

**Battery:** Positive = discharging (providing power to the home), negative = charging. Enables HA consumption/production/net energy tracking for the battery —
the missing piece for battery owners.

**Site:** Total site consumption/production regardless of source. For a solar+battery panel this shows the complete picture of that installation over time — a
different value than grid (import/export) or battery (charge/discharge) alone. Useful for HA energy dashboard without requiring a template sensor to sum the
parts.

The library parses all four power-flow properties (stored on `SpanPanelSnapshot`), but only battery, pv, and site are exposed as sensors. Grid remains available
on the snapshot for internal use (e.g., the `_derive_dsm_grid_state()` heuristic).

### 1D. PV and BESS metadata as sensor attributes

The PV and BESS Homie nodes publish commissioning metadata (vendor, product, nameplate capacity) that is useful context for users monitoring their systems.
These are exposed as attributes on the corresponding power sensors:

**PV Power sensor** (`pvPowerW`) attributes:

| Attribute               | Value source                 | Notes                                         |
| ----------------------- | ---------------------------- | --------------------------------------------- |
| `vendor_name`           | `s.pv.vendor_name`           | PV inverter vendor (e.g., "Enphase", "Other") |
| `product_name`          | `s.pv.product_name`          | PV inverter product (e.g., "IQ8+")            |
| `nameplate_capacity_kw` | `s.pv.nameplate_capacity_kw` | Rated inverter capacity in kW                 |

**Battery Power sensor** (`batteryPowerW`) attributes:

| Attribute                | Value source                       | Notes                         |
| ------------------------ | ---------------------------------- | ----------------------------- |
| `vendor_name`            | `s.battery.vendor_name`            | BESS vendor name              |
| `product_name`           | `s.battery.product_name`           | BESS product name             |
| `nameplate_capacity_kwh` | `s.battery.nameplate_capacity_kwh` | Rated battery capacity in kWh |

**Library models:**

- `SpanPVSnapshot` (new): `vendor_name`, `product_name`, `nameplate_capacity_kw` — populated from first PV metadata node
- `SpanBatterySnapshot` (extended): `vendor_name`, `product_name`, `nameplate_capacity_kwh` — parsed from BESS metadata node

### 1E. Enriched circuit sensor attributes

**Circuit power sensor** (`SpanCircuitPowerSensor.extra_state_attributes`) currently exposes: `tabs`, `voltage`, `amperage`.

The existing `voltage` attribute derives 120/240 from tab count via `construct_voltage_attribute()`. This is equivalent to `circuit.is_240v` (dipole), so
`is_240v` is not added as a separate attribute — the information is already present. The existing helper could be refactored to use `circuit.is_240v` directly
instead of counting tabs, but that's an implementation detail, not a new attribute.

**Add:**

| Attribute         | Value source               | Notes                                              |
| ----------------- | -------------------------- | -------------------------------------------------- |
| `breaker_rating`  | `circuit.breaker_rating_a` | Integer, amps. "15A"/"20A" badge on SPAN dashboard |
| `device_type`     | `circuit.device_type`      | "circuit", "pv", or "evse"                         |
| `always_on`       | `circuit.always_on`        | Boolean                                            |
| `relay_state`     | `circuit.relay_state`      | OPEN/CLOSED/UNKNOWN                                |
| `relay_requester` | `circuit.relay_requester`  | Who requested the relay state                      |
| `shed_priority`   | `circuit.priority`         | NEVER/SOC_THRESHOLD/OFF_GRID/UNKNOWN               |
| `is_sheddable`    | `circuit.is_sheddable`     | Boolean                                            |

These are all already on `SpanCircuitSnapshot` — no library changes needed.

### 1F. Enriched panel power sensor attributes

**Panel power sensor** (`SpanPanelPowerSensor.extra_state_attributes`) currently hardcodes `voltage=240`, calculates `amperage=power/240`.

**Update:**

- Keep `voltage=240` as the nominal value (this is "it's a 240V split-phase panel", not a measurement)
- Add actual measured per-leg voltages:
  - `l1_voltage` — L1 leg voltage (V), from core/l1-voltage (actual, e.g., 121.3)
  - `l2_voltage` — L2 leg voltage (V), from core/l2-voltage (actual, e.g., 119.8)
  - L1/L2 show leg symmetry at a glance (both should be ~120V); they're not interesting as time-series sensors but are useful as live attributes on the power
    measurement they relate to
- Use actual voltage (L1 + L2) for the amperage calculation when available, fall back to nominal 240
- Add `main_breaker_rating` — the main breaker limits total panel current, which is what this sensor measures. Same pattern as circuit `breaker_rating` on
  circuit power sensors. Can change with service upgrade.
- Add `grid_islandable` (boolean — static panel capability, contextual to power)

**Software version sensor** (`SpanPanelStatus.extra_state_attributes`) — add panel metadata attributes:

- `panel_size` — total number of breaker spaces (e.g., 32, 40). Static panel hardware spec.
- `wifi_ssid` — informational, current Wi-Fi network

### 1G. Lugs per-phase current as attributes on panel power sensors

The lugs nodes have `l1-current` and `l2-current` properties that are not currently parsed. Add these to `SpanPanelSnapshot` and expose as attributes on the
panel power sensors — upstream lugs on Current Power, downstream lugs on Feed Through Power:

**Current Power (upstream lugs):**

| Attribute     | Value source              | Notes                    |
| ------------- | ------------------------- | ------------------------ |
| `l1_amperage` | `s.upstream_l1_current_a` | Upstream lugs L1 current |
| `l2_amperage` | `s.upstream_l2_current_a` | Upstream lugs L2 current |

**Feed Through Power (downstream lugs):**

| Attribute     | Value source                | Notes                      |
| ------------- | --------------------------- | -------------------------- |
| `l1_amperage` | `s.downstream_l1_current_a` | Downstream lugs L1 current |
| `l2_amperage` | `s.downstream_l2_current_a` | Downstream lugs L2 current |

### 1H. Entity registry migration

Old unique IDs must be migrated to preserve history:

```text
span_{serial}_dsm_state          → (removed, replaced by dominant_power_source)
span_{serial}_dsm_grid_state     → (kept, derivation improved — no migration needed)
span_{serial}_current_run_config → (kept, derivation improved — no migration needed)
span_{serial}_wwanLink           → (removed, binary sensor platform can't migrate to sensor)
```

The vendor-cloud binary sensor entity is simply removed — its boolean history is not meaningful for the new tri-state sensor. `dsm_state` is removed —
`dominant_power_source` exposes the raw enum with higher fidelity. `dsm_grid_state` and `current_run_config` are retained with improved derivations (see 1A).

## Part 2: Library Changes (span-panel-api)

### 2A. Add power-flows node parsing

Currently `TYPE_POWER_FLOWS` is defined in `mqtt/const.py` but never used in `homie.py`.

**`models.py` — add fields to `SpanPanelSnapshot`:**

```python
# Power flows (None when node not present)
power_flow_pv: float | None = None
power_flow_battery: float | None = None
power_flow_grid: float | None = None
power_flow_site: float | None = None
```

**`mqtt/homie.py` — parse power-flows node in `_build_snapshot()`:**

```python
pf_node = self._find_node_by_type(TYPE_POWER_FLOWS)
power_flow_pv = _parse_float(self._get_prop(pf_node, "pv")) if pf_node else None
# ... etc for battery, grid, site
```

### 2B. Add PV and BESS metadata models

**`models.py` — new `SpanPVSnapshot`:**

```python
@dataclass(frozen=True, slots=True)
class SpanPVSnapshot:
    vendor_name: str | None = None
    product_name: str | None = None
    nameplate_capacity_kw: float | None = None
```

**`models.py` — extend `SpanBatterySnapshot`:**

```python
# Added fields:
vendor_name: str | None = None
product_name: str | None = None
nameplate_capacity_kwh: float | None = None
```

**`models.py` — add `pv` field to `SpanPanelSnapshot`:**

```python
pv: SpanPVSnapshot = field(default_factory=SpanPVSnapshot)
```

**`mqtt/homie.py` — new `_build_pv()` and extended `_build_battery()`:**

Parses `vendor-name`, `product-name`, `nameplate-capacity` from the first PV and BESS metadata nodes.

### 2C. Add lugs per-phase current

**`models.py` — add fields to `SpanPanelSnapshot`:**

```python
# Upstream lugs per-phase current (None when not available)
upstream_l1_current_a: float | None = None
upstream_l2_current_a: float | None = None
```

**`mqtt/homie.py` — parse in `_build_snapshot()`:**

```python
if upstream_lugs is not None:
    l1_i = self._get_prop(upstream_lugs, "l1-current")
    upstream_l1_current = _parse_float(l1_i) if l1_i else None
    # ... etc
```

### 2D. Improve `_derive_dsm_grid_state()` heuristic

The current implementation only checks `bess/grid-state`, returning UNKNOWN for non-BESS panels. Replace with a multi-signal approach using values already on
the snapshot:

**`mqtt/homie.py` — updated `_derive_dsm_grid_state()`:**

```python
def _derive_dsm_grid_state(self, core_node: str | None, grid_power: float) -> str:
    """Derive v1-compatible dsm_grid_state from multiple signals.

    Priority:
    1. bess/grid-state — authoritative when BESS is commissioned
    2. dominant-power-source == GRID — grid is the primary source
    3. grid_power != 0 — grid is exchanging power (even if not dominant)
    4. grid_power == 0 AND DPS != GRID — islanded (no flow + grid not dominant)
    """
    # 1. BESS grid-state is authoritative when available
    bess_node = self._find_node_by_type(TYPE_BESS)
    if bess_node is not None:
        gs = self._get_prop(bess_node, "grid-state")
        if gs == "ON_GRID":
            return "DSM_ON_GRID"
        if gs == "OFF_GRID":
            return "DSM_OFF_GRID"

    # 2. Dominant power source == GRID → on-grid by definition
    if core_node is not None:
        dps = self._get_prop(core_node, "dominant-power-source")
        if dps == "GRID":
            return "DSM_ON_GRID"

        # 3. Grid still exchanging power → on-grid (just not dominant)
        if dps in ("BATTERY", "PV", "GENERATOR") and grid_power != 0.0:
            return "DSM_ON_GRID"

        # 4. Non-grid dominant + zero grid power → off-grid
        if dps in ("BATTERY", "PV", "GENERATOR") and grid_power == 0.0:
            return "DSM_OFF_GRID"

    return "UNKNOWN"
```

The `grid_power` parameter is `instant_grid_power_w` from upstream lugs — already parsed in `_build_snapshot()`. The method signature changes from
`_derive_dsm_grid_state(self)` to `_derive_dsm_grid_state(self, core_node, grid_power)`, and the call site in `_build_snapshot()` passes the already-computed
values.

Also remove `_derive_dsm_state()` — no longer needed. Remove the `dsm_state` field from `SpanPanelSnapshot` (or keep as deprecated with a fixed "UNKNOWN" value
if backward compatibility is needed during transition).

**`mqtt/homie.py` — updated `_derive_run_config()`:**

The improved derivation combines `dsm_grid_state`, `grid_islandable`, and `dominant_power_source` to reconstruct the full v1 tri-state. The method now takes the
already-computed values as parameters instead of re-querying MQTT properties:

```python
def _derive_run_config(
    self, dsm_grid_state: str, grid_islandable: bool | None, dps: str | None
) -> str:
    """Derive current_run_config from grid state, islandability, and power source.

    Decision table:
    ┌─────────────────┬───────────────┬─────────────────────────┬────────────────────────┐
    │ grid_islandable │ dsm_grid_state│ dominant_power_source   │ current_run_config     │
    ├─────────────────┼───────────────┼─────────────────────────┼────────────────────────┤
    │ false           │ DSM_ON_GRID   │ *                       │ PANEL_ON_GRID          │
    │ true            │ DSM_ON_GRID   │ *                       │ PANEL_ON_GRID          │
    │ true            │ DSM_OFF_GRID  │ BATTERY                 │ PANEL_BACKUP           │
    │ true            │ DSM_OFF_GRID  │ PV / GENERATOR          │ PANEL_OFF_GRID         │
    │ true            │ DSM_OFF_GRID  │ NONE / UNKNOWN          │ UNKNOWN                │
    │ false           │ DSM_OFF_GRID  │ *                       │ UNKNOWN (shouldn't happen) │
    └─────────────────┴───────────────┴─────────────────────────┴────────────────────────┘
    """
    if dsm_grid_state == "DSM_ON_GRID":
        return "PANEL_ON_GRID"

    if dsm_grid_state == "DSM_OFF_GRID":
        if not grid_islandable:
            return "UNKNOWN"  # Non-islandable panel reporting off-grid — unexpected
        if dps == "BATTERY":
            return "PANEL_BACKUP"
        if dps in ("PV", "GENERATOR"):
            return "PANEL_OFF_GRID"
        return "UNKNOWN"

    return "UNKNOWN"
```

The call site in `_build_snapshot()` chains the two derivations:

```python
dsm_grid_state = self._derive_dsm_grid_state(core_node, grid_power)
current_run_config = self._derive_run_config(dsm_grid_state, grid_islandable, dominant_power_source)
```

### 2E. Simulation engine updates

`DynamicSimulationEngine` should populate the new fields (`power_flow_*`, `upstream_l1_current_a`, etc.) in its generated snapshots so simulation mode continues
to work with the new sensors.

## Files Modified

### span-panel-api (`/Users/bflood/projects/HA/span-panel-api`)

| File                               | Changes                                                                                                                                                                          |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/span_panel_api/models.py`     | Add SpanPVSnapshot, extend SpanBatterySnapshot with metadata; add power*flow*\*, upstream/downstream l1/l2_current_a, pv fields to SpanPanelSnapshot                             |
| `src/span_panel_api/mqtt/homie.py` | Parse power-flows node, parse lugs l1/l2-current, parse PV/BESS metadata; improve `_derive_dsm_grid_state()` and `_derive_run_config()` heuristics; remove `_derive_dsm_state()` |
| `src/span_panel_api/simulation.py` | Populate new snapshot fields in simulated data                                                                                                                                   |
| `tests/`                           | Update snapshot fixtures, add power-flows, lugs current, PV metadata, and BESS metadata tests                                                                                    |

### span (HA integration) (`/Users/bflood/projects/HA/span`)

| File                                                        | Changes                                                                                                                                                                                                                                                     |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `custom_components/span_panel/const.py`                     | Remove DSM_GRID_UP/DOWN, SYSTEM_CELLULAR_LINK                                                                                                                                                                                                               |
| `custom_components/span_panel/sensor_definitions.py`        | Remove dsm_state; add dominant_power_source, vendor_cloud, battery_power, site_power (keep dsm_grid_state, current_run_config)                                                                                                                              |
| `custom_components/span_panel/binary_sensor.py`             | Remove SYSTEM_CELLULAR_LINK entry from BINARY_SENSORS                                                                                                                                                                                                       |
| `custom_components/span_panel/sensors/circuit.py`           | Add breaker_rating, device_type, always_on, relay_state, relay_requester, shed_priority, is_sheddable to extra_state_attributes                                                                                                                             |
| `custom_components/span_panel/sensors/panel.py`             | Add l1/l2 voltage, l1/l2 amperage (upstream + downstream lugs), main_breaker_rating, grid_islandable to panel power attributes; add PV/BESS metadata attributes to pvPowerW/batteryPowerW sensors; add panel_size, wifi_ssid to software version attributes |
| `custom_components/span_panel/sensors/factory.py`           | Wire new sensors                                                                                                                                                                                                                                            |
| `custom_components/span_panel/helpers.py`                   | Remove dsmState from suffix mappings; add battery_power, site_power mappings                                                                                                                                                                                |
| `custom_components/span_panel/migration.py`                 | Update native_sensor_map for new keys                                                                                                                                                                                                                       |
| `custom_components/span_panel/entity_id_naming_patterns.py` | Update panel sensor key list                                                                                                                                                                                                                                |
| `custom_components/span_panel/__init__.py`                  | Entity registry migration (remove dsm_state entity)                                                                                                                                                                                                         |
| `tests/`                                                    | Update sensor expectations, add new sensor/attribute tests                                                                                                                                                                                                  |

## Implementation Order

**Phase A — Library (span-panel-api):**

1. Add new fields to SpanPanelSnapshot and SpanCircuitSnapshot
2. Add SpanPVSnapshot model, extend SpanBatterySnapshot with metadata fields
3. Parse power-flows node in homie.py
4. Parse lugs l1/l2-current in homie.py
5. Parse PV and BESS metadata in homie.py
6. Improve `_derive_dsm_grid_state()` and `_derive_run_config()` heuristics; remove `_derive_dsm_state()`
7. Update simulation engine
8. Update tests

**Phase B — Integration sensors & attributes:**

1. const.py — remove v1-derived constants (DSM_GRID_UP/DOWN); keep DSM_ON_GRID/OFF_GRID, PANEL_ON_GRID/OFF_GRID/BACKUP
2. sensor_definitions.py — remove dsm_state; add dominant_power_source, vendor_cloud, battery_power, site_power sensors (keep dsm_grid_state,
   current_run_config)
3. binary_sensor.py — remove vendor-cloud binary sensor
4. helpers.py — update suffix mappings
5. sensors/factory.py — wire new sensors
6. sensors/circuit.py — enrich extra_state_attributes
7. sensors/panel.py — add l1/l2 voltage, l1/l2 amperage (upstream lugs on Current Power, downstream lugs on Feed Through Power), main_breaker_rating,
   grid_islandable to panel power attributes; add PV metadata to pvPowerW, BESS metadata to batteryPowerW; add panel_size and wifi_ssid to software version
   attributes
8. migration.py — update legacy mapping
9. entity_id_naming_patterns.py — update key list
10. `__init__.py` — entity registry migration (remove dsm_state entity)

**Phase C — Tests & cleanup:**

1. Update all affected tests

## Verification

1. `cd /Users/bflood/projects/HA/span-panel-api && python -m pytest tests/ -q` — all tests pass
2. `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -q` — all tests pass
3. `grep -r "DSM_GRID_UP" custom_components/` — no hits outside migration/simulation
4. `grep -r "dsm_state" custom_components/span_panel/sensor_definitions.py` — no hits (dsm_grid_state and current_run_config should still be present)
5. `grep -r "SYSTEM_CELLULAR_LINK" custom_components/span_panel/binary_sensor.py` — no hits
6. New sensors appear: dominant_power_source, vendor_cloud, battery_power, site_power; dsm_grid_state and current_run_config retained with improved derivations
7. Circuit power sensor attributes include: breaker_rating, device_type, always_on, relay_state, shed_priority, is_sheddable
8. Panel power sensor (Current Power) attributes include: l1_voltage, l2_voltage, l1_amperage, l2_amperage, main_breaker_rating, grid_islandable 8b. Panel power
   sensor (Feed Through Power) attributes include: l1_amperage, l2_amperage
9. PV Power sensor attributes include: vendor_name, product_name, nameplate_capacity_kw
10. Battery Power sensor attributes include: vendor_name, product_name, nameplate_capacity_kwh
11. Software version sensor attributes include: panel_size, wifi_ssid
