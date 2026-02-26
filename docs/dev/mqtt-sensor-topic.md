# V2 Sensor Alignment & MQTT Topology Bridge

## Context

The v2 MQTT migration is functionally complete (181 tests passing, both repos on `ebus_integration` branch). The integration receives all Homie data from the
panel's private eBus MQTT broker, but several sensors still expose v1-derived shim values (DSM_GRID_UP, PANEL_ON_GRID, etc.) instead of the actual v2 MQTT
values. Additionally, the panel's MQTT schema exposes rich topology and metadata (breaker ratings, dipole status, space assignments, per-phase
currents/voltages) that is not surfaced to users at all.

The SPAN app dashboard (screenshot provided) shows this data visually: breaker ratings (15A/20A), tab positions, circuit names, live power — all from MQTT
properties we already receive. Users cannot replicate this dashboard in HA today without manually reading MQTT topics.

**Goal:** A two-layer approach:

1. **Sensors & attributes** — extend the HA entity model with live measurements and honest v2 state values
2. **MQTT topology bridge** — republish structural/static panel layout data to HA's MQTT broker so custom Lovelace cards can render a SPAN-style panel
   visualization

## Architecture

```text
SPAN eBus MQTT Broker (private, TLS, panel-specific creds)
    │
    ├── SpanMqttClient (library) ── subscribes to ebus/5/{serial}/#
    │       │
    │       ├── HomieDeviceConsumer → SpanPanelSnapshot
    │       │       │
    │       │       └── Coordinator → Entities (sensors, switches, selects, binary_sensors)
    │       │
    │       └── [NEW] Raw message stream → TopologyBridge
    │                                          │
    │                                          └── HA MQTT broker (user's Mosquitto/etc.)
    │                                                  │
    │                                                  └── Custom Lovelace card reads topology
```

**Why two layers?**

- **Sensors** = time-series data, state machines, measurements that users consume in dashboards, automations, and energy management. These belong in HA's entity
  model.
- **Topology** = structural data describing the physical panel layout (which tabs each circuit occupies, breaker ratings, dipole spans, space groupings). This
  changes infrequently (panel reconfiguration, circuit renaming) but is not static — the bridge diffs and republishes on change. This data is best consumed by a
  visualization card, not individual sensor entities.

## Part 1: Sensor & Attribute Changes

### 1A. Replace v1-derived panel status sensors with honest v2 values

**Current derived sensors (remove):**

| Key                  | Name               | Value source                                      | Problem                                  |
| -------------------- | ------------------ | ------------------------------------------------- | ---------------------------------------- |
| `dsm_state`          | DSM State          | `_derive_dsm_state()` → DSM_GRID_UP/DOWN          | Lossy mapping from dominant-power-source |
| `dsm_grid_state`     | DSM Grid State     | `_derive_dsm_grid_state()` → DSM_ON_GRID/OFF_GRID | Lossy mapping from bess/grid-state       |
| `current_run_config` | Current Run Config | `_derive_run_config()` → PANEL_ON_GRID/OFF_GRID   | Lossy mapping from dominant-power-source |

**Replacement sensors (add):**

| Key                     | Name                  | Value source              | MQTT property                                                             |
| ----------------------- | --------------------- | ------------------------- | ------------------------------------------------------------------------- |
| `dominant_power_source` | Dominant Power Source | `s.dominant_power_source` | core/dominant-power-source (enum: GRID,BATTERY,PV,GENERATOR,NONE,UNKNOWN) |
| `grid_state`            | Grid State            | `s.grid_state`            | bess/grid-state (enum: UNKNOWN,ON_GRID,OFF_GRID)                          |

**Kept unchanged:** `main_relay_state` (Main Relay State) — already maps directly to core/relay.

**Rationale:** `current_run_config` was derived from `dominant-power-source` via `_derive_run_config()`, collapsing 6 enum values to 3. Now that
`dominant_power_source` is exposed directly, `current_run_config` is redundant. `grid_islandable` is a static boolean (panel capability, not state) — better as
an attribute on the panel power sensor than a sensor.

### 1B. New panel-level sensors

| Key            | Name         | Device class | Unit | Value source     | MQTT property                                           |
| -------------- | ------------ | ------------ | ---- | ---------------- | ------------------------------------------------------- |
| `vendor_cloud` | Vendor Cloud | —            | —    | `s.vendor_cloud` | core/vendor-cloud (enum: UNKNOWN,UNCONNECTED,CONNECTED) |
| `l1_voltage`   | L1 Voltage   | `VOLTAGE`    | V    | `s.l1_voltage`   | core/l1-voltage                                         |
| `l2_voltage`   | L2 Voltage   | `VOLTAGE`    | V    | `s.l2_voltage`   | core/l2-voltage                                         |

**Removed from binary_sensor.py:** The `SYSTEM_CELLULAR_LINK` ("Vendor Cloud") entry is removed from `BINARY_SENSORS`. It was coercing a tri-state enum
(UNKNOWN/UNCONNECTED/CONNECTED) to boolean. The new regular sensor exposes the actual value.

### 1C. New power-flows sensors

The `energy.ebus.device.power-flows` node provides system-level power flow data that's currently not parsed at all. These are live measurements (float, Watts)
that change every few seconds.

| Key                  | Name               | Device class | Unit | Value source           |
| -------------------- | ------------------ | ------------ | ---- | ---------------------- |
| `power_flow_pv`      | PV Power Flow      | `POWER`      | W    | `s.power_flow_pv`      |
| `power_flow_battery` | Battery Power Flow | `POWER`      | W    | `s.power_flow_battery` |
| `power_flow_grid`    | Grid Power Flow    | `POWER`      | W    | `s.power_flow_grid`    |
| `power_flow_site`    | Site Power Flow    | `POWER`      | W    | `s.power_flow_site`    |

These give users a direct "where is my power coming from / going to" view without having to infer from individual circuit and lugs sensors.

### 1D. Enriched circuit sensor attributes

**Circuit power sensor** (`SpanCircuitPowerSensor.extra_state_attributes`) currently exposes: `tabs`, `voltage`, `amperage`.

**Add:**

| Attribute          | Value source               | Notes                                                       |
| ------------------ | -------------------------- | ----------------------------------------------------------- |
| `breaker_rating_a` | `circuit.breaker_rating_a` | Integer, amps. Shown as "15A"/"20A" badge on SPAN dashboard |
| `is_240v`          | `circuit.is_240v`          | Boolean. Corresponds to `dipole` MQTT property              |
| `device_type`      | `circuit.device_type`      | "circuit", "pv", or "evse"                                  |
| `always_on`        | `circuit.always_on`        | Boolean                                                     |
| `relay_state`      | `circuit.relay_state`      | OPEN/CLOSED/UNKNOWN                                         |
| `relay_requester`  | `circuit.relay_requester`  | Who requested the relay state                               |
| `shed_priority`    | `circuit.priority`         | NEVER/SOC_THRESHOLD/OFF_GRID/UNKNOWN                        |
| `is_sheddable`     | `circuit.is_sheddable`     | Boolean                                                     |

These are all already on `SpanCircuitSnapshot` — no library changes needed.

### 1E. Enriched panel power sensor attributes

**Panel power sensor** (`SpanPanelPowerSensor.extra_state_attributes`) currently hardcodes `voltage=240`, calculates `amperage=power/240`.

**Update:**

- Use real `l1_voltage + l2_voltage` when available (split-phase: total = L1 + L2)
- Add `grid_islandable` (boolean — static panel capability)
- Add `main_breaker_rating_a` (static)
- Add `wifi_ssid` (informational)

### 1F. Lugs per-phase current as attributes on panel power sensors

The lugs nodes have `l1-current` and `l2-current` properties that are not currently parsed. Add these to `SpanPanelSnapshot` and expose as attributes on the
panel current power sensor:

| Attribute      | Value source              | Notes                    |
| -------------- | ------------------------- | ------------------------ |
| `l1_current_a` | `s.upstream_l1_current_a` | Upstream lugs L1 current |
| `l2_current_a` | `s.upstream_l2_current_a` | Upstream lugs L2 current |

### 1G. Entity registry migration

Old unique IDs must be migrated to preserve history:

```text
span_{serial}_dsm_state       → span_{serial}_dominant_power_source
span_{serial}_dsm_grid_state  → span_{serial}_grid_state
span_{serial}_current_run_config → (removed, no replacement)
span_{serial}_wwanLink         → (removed, binary sensor platform can't migrate to sensor)
```

The vendor-cloud binary sensor entity is simply removed — its boolean history is not meaningful for the new tri-state sensor. The `current_run_config` entity is
removed — `dominant_power_source` replaces its function with higher fidelity.

## Part 2: MQTT Topology Bridge

### 2A. Concept

The integration already maintains a live MQTT connection to the panel's private eBus broker. When enabled (opt-in config option), it republishes
**topology/structural data** to the user's HA MQTT broker under a structured topic tree. This data is published as **retained messages** so dashboard cards can
read it at any time without waiting for updates.

The bridge does NOT republish:

- Power/energy values (already available as HA sensors)
- State values (already available as HA sensors)
- Control topics (switches/selects already handle this)

It DOES republish:

- Panel layout topology (circuit→tab mapping, dipole spans, panel size)
- Breaker ratings per circuit
- Space assignments
- Circuit names and device types
- Static panel metadata (serial, firmware, main breaker rating)

### 2B. Topic structure

```text
span_panel/{serial}/topology/panel_size          → "32"
span_panel/{serial}/topology/main_breaker_a      → "200"
span_panel/{serial}/topology/firmware             → "spanos2/r202603/05"

span_panel/{serial}/topology/circuits/{circuit_id}/name            → "Master bedroom"
span_panel/{serial}/topology/circuits/{circuit_id}/tabs            → "[1,2]"
span_panel/{serial}/topology/circuits/{circuit_id}/breaker_rating  → "15"
span_panel/{serial}/topology/circuits/{circuit_id}/is_240v         → "true"
span_panel/{serial}/topology/circuits/{circuit_id}/device_type     → "circuit"
span_panel/{serial}/topology/circuits/{circuit_id}/space           → "1"
span_panel/{serial}/topology/circuits/{circuit_id}/always_on       → "false"
```

All messages are retained. The bridge republishes on every coordinator update by diffing the current topology against the last-published state. Topology can
change when circuits are renamed, breakers reconfigured, spaces reassigned, or circuits commissioned/decommissioned — not frequent, but not static either. The
diff avoids unnecessary MQTT publishes while ensuring changes propagate within one coordinator cycle.

### 2C. Implementation approach

**Option dependency:** Add `"mqtt"` to `"after_dependencies"` in `manifest.json`. This makes the MQTT bridge available only when the user has HA's MQTT
integration configured. The bridge is opt-in via a config option (`enable_mqtt_topology_bridge`).

**Where the bridge lives:**

- New file: `custom_components/span_panel/mqtt_bridge.py`
- Class: `TopologyBridge`
- Initialized in `__init__.py:async_setup_entry()` after coordinator setup
- Publishes via `homeassistant.components.mqtt.async_publish()`
- Listens to coordinator updates to detect topology changes

**Bridge lifecycle:**

```python
class TopologyBridge:
    def __init__(self, hass, coordinator, serial_number):
        self._last_topology_hash: dict[str, str] = {}  # topic → payload cache

    async def async_start(self) -> None:
        """Subscribe to coordinator updates and publish initial topology."""

    def _on_coordinator_update(self) -> None:
        """Diff current topology against last-published state, publish changes."""

    async def _publish_if_changed(self, topic: str, payload: str) -> None:
        """Publish retained message only if payload differs from cache."""

    async def async_cleanup(self) -> None:
        """Remove retained messages on unload (publish empty payloads)."""
```

The bridge listens to coordinator updates (via `coordinator.async_add_listener()`). On each update, it extracts the topology from the current snapshot and
compares each topic's payload against its cache. Only changed topics are republished. This handles panel reconfiguration (renamed circuits, reassigned spaces,
changed breaker ratings) without flooding MQTT on every power update.

### 2D. Config option

Add `enable_mqtt_topology_bridge` to options flow (default: False). Only shown when HA's MQTT integration is available:

```python
ENABLE_MQTT_TOPOLOGY_BRIDGE = "enable_mqtt_topology_bridge"
```

## Part 3: Library Changes (span-panel-api)

### 3A. Add power-flows node parsing

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

### 3B. Add lugs per-phase current

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

### 3C. Add `space` field to `SpanCircuitSnapshot`

Currently the homie parser maps `space` to `tabs` via some conversion logic. The raw `space` value (integer 1-32) should also be preserved for the topology
bridge.

**`models.py`:**

```python
space: int | None = None  # Breaker space number (1-32), distinct from tabs
```

**`mqtt/homie.py` — set in `_build_circuit()`:** Already parses space, just needs to store the raw value alongside tabs.

### 3D. Simulation engine updates

`DynamicSimulationEngine` should populate the new fields (`power_flow_*`, `upstream_l1_current_a`, etc.) in its generated snapshots so simulation mode continues
to work with the new sensors.

## Files Modified

### span-panel-api (`/Users/bflood/projects/HA/span-panel-api`)

| File                               | Changes                                                                                                |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `src/span_panel_api/models.py`     | Add power*flow**, upstream_l*\_current_a fields to SpanPanelSnapshot; add space to SpanCircuitSnapshot |
| `src/span_panel_api/mqtt/homie.py` | Parse power-flows node, parse lugs l1/l2-current, store raw space on circuit                           |
| `src/span_panel_api/simulation.py` | Populate new snapshot fields in simulated data                                                         |
| `tests/`                           | Update snapshot fixtures, add power-flows and lugs current tests                                       |

### span (HA integration) (`/Users/bflood/projects/HA/span`)

| File                                                        | Changes                                                                                                                                    |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `custom_components/span_panel/const.py`                     | Remove DSM_GRID_UP/DOWN, DSM_ON_GRID/OFF_GRID, PANEL_ON_GRID/OFF_GRID/BACKUP, SYSTEM_CELLULAR_LINK; add ENABLE_MQTT_TOPOLOGY_BRIDGE        |
| `custom_components/span_panel/sensor_definitions.py`        | Replace dsm*state/dsm_grid_state/current_run_config sensors; add vendor_cloud, l1/l2_voltage, power_flow*\* sensors                        |
| `custom_components/span_panel/binary_sensor.py`             | Remove SYSTEM_CELLULAR_LINK entry from BINARY_SENSORS                                                                                      |
| `custom_components/span_panel/sensors/circuit.py`           | Add breaker_rating_a, is_240v, device_type, always_on, relay_state, relay_requester, shed_priority, is_sheddable to extra_state_attributes |
| `custom_components/span_panel/sensors/panel.py`             | Use real l1/l2 voltage; add grid_islandable, main_breaker_rating_a, wifi_ssid, lugs l1/l2 current to attributes                            |
| `custom_components/span_panel/sensors/factory.py`           | Wire new PANEL_STATUS_SENSORS tuple                                                                                                        |
| `custom_components/span_panel/helpers.py`                   | Remove dsmState from suffix mappings                                                                                                       |
| `custom_components/span_panel/migration.py`                 | Update native_sensor_map for new keys                                                                                                      |
| `custom_components/span_panel/entity_id_naming_patterns.py` | Update panel sensor key list                                                                                                               |
| `custom_components/span_panel/__init__.py`                  | Entity registry migration; topology bridge setup; add after_dependencies mqtt                                                              |
| `custom_components/span_panel/manifest.json`                | Add `"mqtt"` to `after_dependencies`                                                                                                       |
| `custom_components/span_panel/mqtt_bridge.py`               | **New file** — TopologyBridge class                                                                                                        |
| `custom_components/span_panel/options.py`                   | Add ENABLE_MQTT_TOPOLOGY_BRIDGE option                                                                                                     |
| `custom_components/span_panel/config_flow_utils/options.py` | Add topology bridge toggle to options schema                                                                                               |
| `tests/`                                                    | Update sensor expectations, add topology bridge tests                                                                                      |
| `docs/dev/v2_sensor_alignment.md`                           | **New file** — copy of this design doc                                                                                                     |

## Implementation Order

**Phase A — Library (span-panel-api):**

1. Add new fields to SpanPanelSnapshot and SpanCircuitSnapshot
2. Parse power-flows node in homie.py
3. Parse lugs l1/l2-current in homie.py
4. Store raw space on SpanCircuitSnapshot
5. Update simulation engine
6. Update tests

**Phase B — Integration sensors:**

1. const.py — remove v1-derived constants
2. sensor_definitions.py — replace/add sensors
3. binary_sensor.py — remove vendor-cloud binary sensor
4. helpers.py — update suffix mappings
5. sensors/factory.py — wire new sensors
6. sensors/circuit.py — enrich extra_state_attributes
7. sensors/panel.py — use real voltages, add attributes
8. migration.py — update legacy mapping
9. entity_id_naming_patterns.py — update key list
10. **init**.py — entity registry migration

**Phase C — MQTT topology bridge:**

1. manifest.json — add mqtt to after_dependencies
2. mqtt_bridge.py — new TopologyBridge class
3. options.py / config_flow_utils/options.py — add bridge toggle
4. **init**.py — wire bridge setup/teardown
5. Tests

**Phase D — Tests & cleanup:**

1. Update all affected tests
2. Create docs/dev/v2_sensor_alignment.md

## Verification

1. `cd /Users/bflood/projects/HA/span-panel-api && python -m pytest tests/ -q` — all tests pass
2. `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -q` — all tests pass
3. `grep -r "DSM_GRID_UP\|DSM_ON_GRID\|PANEL_ON_GRID" custom_components/` — no hits outside migration/simulation
4. `grep -r "dsm_state\|dsm_grid_state\|current_run_config" custom_components/span_panel/sensor_definitions.py` — no hits
5. `grep -r "SYSTEM_CELLULAR_LINK" custom_components/span_panel/binary_sensor.py` — no hits
6. New sensors appear: dominant*power_source, grid_state, vendor_cloud, l1_voltage, l2_voltage, power_flow*\*
7. Circuit power sensor attributes include: breaker_rating_a, is_240v, device_type, always_on
8. Panel power sensor uses real voltage when l1/l2 available
9. With HA MQTT configured and bridge enabled: `span_panel/{serial}/topology/circuits/...` topics visible in MQTT Explorer
10. Without HA MQTT: bridge silently skipped, all sensors work normally
