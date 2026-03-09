# SPAN Panel Integration Architecture

This document describes the high-level architecture of the SPAN Panel Home Assistant integration and the `span-panel-api` library it depends on. It covers
component responsibilities, data flow, and the key design decisions that shape runtime behavior. Implementation details and code-level documentation live in the
other docs in this directory.

## System Overview

The system is split across two repositories:

| Repository         | Role                                                                                   |
| ------------------ | -------------------------------------------------------------------------------------- |
| **span-panel-api** | Transport library — MQTT connection, Homie parsing, snapshot building, circuit control |
| **span**           | HA integration — coordinator, entity platforms, config flow, services, migrations      |

The library knows nothing about Home Assistant. The integration knows nothing about MQTT framing or Homie parsing. All communication between them flows through
a small set of protocols and a single snapshot model.

## span-panel-api

### Protocols

The library defines three runtime protocols that any transport must satisfy:

| Protocol                   | Responsibility                                                      |
| -------------------------- | ------------------------------------------------------------------- |
| `SpanPanelClientProtocol`  | Connect, close, ping, `get_snapshot()`, expose `capabilities` flags |
| `StreamingCapableProtocol` | Register snapshot callbacks, start/stop streaming                   |
| `CircuitControlProtocol`   | Set circuit relay state, set circuit shed priority                  |

Integration code programs against these protocols, never against concrete transport classes. This lets the simulation engine and the MQTT client be used
interchangeably.

### Capability Flags

`PanelCapability` is a set of runtime feature flags advertised by the client after connection:

| Flag              | Meaning                                         |
| ----------------- | ----------------------------------------------- |
| `PUSH_STREAMING`  | Client supports push callbacks                  |
| `EBUS_MQTT`       | v2 MQTT transport is available                  |
| `CIRCUIT_CONTROL` | Client can change relay state and shed priority |
| `BATTERY_SOE`     | Battery state-of-energy data is present         |

The integration reads these flags at setup time to decide which entity platforms to load and which sensors to create.

### Snapshot Model

`SpanPanelSnapshot` is the single point-in-time view of all panel state. It is transport-agnostic — the same dataclass is returned whether the data came from
MQTT, REST (legacy), or simulation.

Key sections of the snapshot:

| Section     | Contents                                                                                                      |
| ----------- | ------------------------------------------------------------------------------------------------------------- |
| Identity    | Serial number, firmware version                                                                               |
| Grid state  | `dsm_grid_state`, `current_run_config`, `dominant_power_source`, `main_relay_state`                           |
| Main meter  | Instantaneous grid power, consumed/produced energy counters, L1/L2 voltage and current                        |
| Feedthrough | Feedthrough power and energy counters, downstream lug currents                                                |
| Power flows | Aggregate PV, battery, grid, and site power (instantaneous only, no energy counters)                          |
| Circuits    | `dict[circuit_id, SpanCircuitSnapshot]` — per-circuit power, energy, relay state, tabs, device type, metadata |
| Battery     | `SpanBatterySnapshot` — state-of-energy percentage, kWh, vendor metadata                                      |
| PV          | `SpanPVSnapshot` — vendor metadata, nameplate capacity                                                        |
| Hardware    | Door state, ethernet/WiFi/cellular link, panel size, WiFi SSID                                                |

`SpanCircuitSnapshot` carries the circuit's identity (UUID, name, tabs), real-time measurements (power, energy counters, current), relay/priority state, and
metadata (device type, breaker rating, always-on flag). The `device_type` field distinguishes load circuits from PV and EVSE circuits, which affects sign
conventions and entity naming.

### MQTT Transport

The MQTT transport is composed of three layers:

| Layer  | Class                 | Responsibility                                                                                    |
| ------ | --------------------- | ------------------------------------------------------------------------------------------------- |
| Socket | `AsyncMqttBridge`     | Event-loop-driven paho-mqtt wrapper, TLS, reconnection                                            |
| Parser | `HomieDeviceConsumer` | Homie v5 message routing, property accumulation, snapshot assembly                                |
| Client | `SpanMqttClient`      | Composition root — wires bridge to parser, implements all three protocols, manages debounce timer |

#### Connection Flow

1. The bridge downloads the panel's CA certificate over REST and configures TLS
2. paho-mqtt connects to the panel's MQTT broker (TCP or WebSocket) using credentials from the v2 auth registration
3. The client subscribes to the panel's Homie topic tree (`ebus/5/{serial}/#`)
4. The parser waits for the Homie `$state=ready` signal, then polls until circuit names are populated
5. Once names arrive the connection is considered fully established

#### Property Accumulation

Every MQTT message updates a single entry in the parser's in-memory property dictionary — a cheap dict write. No snapshot is built at this stage. The dictionary
is the authoritative store of all Homie property values and their arrival timestamps.

#### Snapshot Building

`build_snapshot()` iterates the accumulated properties and assembles a `SpanPanelSnapshot`. This is the expensive operation — it walks all node types, extracts
typed values, derives grid state and run config, correlates PV/EVSE metadata to circuits via the `feed` property, and synthesizes unmapped tab entries for
breaker positions with no physical circuit.

Snapshot building is triggered in one of two ways:

- **On demand** — `get_snapshot()` calls `build_snapshot()` synchronously from the property store. No network call.
- **On push** — when streaming is active, the debounce timer fires and builds a snapshot, then dispatches it to all registered callbacks.

#### Debounce Timer

The SPAN panel publishes roughly 100 MQTT messages per second. Without rate-limiting, each message would trigger a full snapshot rebuild and entity update
cycle. The debounce timer prevents this:

1. An MQTT message arrives and updates the property store
2. If no timer is running and streaming is active, a timer is scheduled for `snapshot_interval` seconds (default 1.0, configurable 0–15)
3. Further messages during the window are absorbed — they update properties but do not reset or extend the timer
4. When the timer fires it builds one snapshot and dispatches it to all callbacks
5. Setting the interval to 0 disables debouncing — every message triggers immediate dispatch

The interval is user-configurable via the integration's options flow and can be adjusted at runtime without reconnecting.

#### Circuit Control

Relay and priority commands are published to the circuit's Homie `/set` topic at QoS 1. The panel applies the change and publishes the updated property value
back through the normal Homie message flow, which the parser picks up on the next snapshot cycle.

#### Reconnection

The bridge runs an exponential-backoff reconnection loop (1s → 60s) that activates after the initial connection succeeds. Reconnection is transparent to the
client and parser — the property store retains its last known state during the gap, and streaming resumes automatically once the broker connection is
re-established.

### Auth and Detection

| Function               | Purpose                                                                                                            |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `register_v2()`        | POST to the panel's v2 auth endpoint with the user's passphrase, returns MQTT broker credentials and serial number |
| `detect_api_version()` | GET the panel's unauthenticated v2 status endpoint to determine firmware generation                                |
| `download_ca_cert()`   | Fetch the panel's CA certificate PEM for TLS                                                                       |

### Simulation Engine

`DynamicSimulationEngine` implements `SpanPanelClientProtocol` (but not `StreamingCapableProtocol`) and generates snapshots from YAML configuration files. It is
used for integration testing and development without hardware. The coordinator treats it identically to a real client — it just polls instead of streaming.

## span (HA Integration)

### Setup and Initialization

The integration's `async_setup_entry()` determines the transport mode from the config entry's `api_version` field:

| Mode        | Client                                 | Coordinator               | Update Path                 |
| ----------- | -------------------------------------- | ------------------------- | --------------------------- |
| v1 (legacy) | Blocked — raises `ConfigEntryNotReady` | —                         | Users must upgrade firmware |
| v2 (MQTT)   | `SpanMqttClient`                       | Streaming + fallback poll | Push via MQTT, 60s fallback |
| Simulation  | `DynamicSimulationEngine`              | Poll only                 | Configurable interval       |

For v2, the setup sequence is:

1. Build `MqttClientConfig` from stored broker credentials
2. Create `SpanMqttClient` and call `connect()`
3. Create `SpanPanelCoordinator` with `is_streaming=True`
4. Call `async_config_entry_first_refresh()` — runs one poll cycle, handles any pending migrations
5. Call `async_setup_streaming()` — registers the push callback and starts MQTT streaming
6. Register the device in the device registry
7. Forward setup to entity platforms (sensor, binary_sensor, switch, select)
8. Register services (energy spike cleanup, main meter monitoring)

### Coordinator

`SpanPanelCoordinator` extends HA's `DataUpdateCoordinator` and is the central hub between the transport client and all entity platforms.

#### Two Update Paths

The coordinator has two paths for receiving data, and both feed into the same entity update machinery:

**Push path** (streaming mode) — `_on_snapshot_push()` is called by the MQTT client's debounce timer with a freshly built snapshot. It marks the panel as
online, checks for hardware capability changes, calls `async_set_updated_data()` to dispatch to entities, then runs post-update maintenance tasks.

**Poll path** — `_async_update_data()` is called by the coordinator's built-in timer. It calls `get_snapshot()` on the client, performs the same capability
check, runs post-update tasks, and returns the snapshot.

#### Fallback Poll Behavior

HA's `DataUpdateCoordinator` resets its poll timer every time `async_set_updated_data()` is called. During active MQTT streaming, pushes arrive faster than the
60-second fallback interval, so the timer perpetually restarts and the poll path effectively never fires. This is correct for its intended purpose — the
fallback exists to detect MQTT silence, not to run periodically alongside push updates. If pushes stop (panel disconnect, broker crash), the last timer fires 60
seconds later.

Because the poll path rarely fires during streaming, all maintenance logic that needs to run on every update lives in a shared `_run_post_update_tasks()` method
called from both paths. This includes reload request handling, pending migration checks, and solar entity migration.

#### Post-Update Tasks

After every snapshot update (push or poll), the coordinator runs:

1. **Reload request check** — if any entity or capability detector set the `_reload_requested` flag, schedule an async reload of the config entry
2. **Legacy migration** — if `pending_legacy_migration` flag is set in options, migrate entity IDs from v1 naming to v2 device-prefixed naming
3. **Naming pattern migration** — if `pending_naming_migration` flag is set, migrate entity IDs between friendly names and circuit numbers
4. **Solar migration** — if `solar_migration_pending` flag is set in config data, rewrite v1 virtual solar entity unique IDs to v2 PV circuit unique IDs

All migration flags are one-shot — they are cleared after execution to prevent loops. They typically run during `async_config_entry_first_refresh()` before
streaming starts, but the shared task runner ensures they also execute if set later (e.g., via options flow while streaming is active).

#### Capability Detection

The coordinator tracks which optional hardware features are present in the snapshot (BESS, PV, power-flows). When a new capability appears — for example, a
battery is commissioned after the integration is already running — the coordinator requests a reload. On reload, the sensor factory re-evaluates the snapshot
and creates the appropriate new entities.

#### Reload Mechanism

`request_reload()` sets a flag rather than reloading immediately. The actual reload is scheduled as an async task after the current update cycle completes. This
avoids reloading mid-update and allows multiple reload requests within a single cycle to coalesce into one reload.

#### Offline Handling

When a data fetch fails (connection error, timeout, server error), the coordinator sets `_panel_offline = True` and returns the last known snapshot. This keeps
entities updating with stale-but-valid data, which is critical for the energy sensor grace period logic. On the next successful update (push or poll), the
offline flag is cleared.

### Entity Platforms

#### Sensor Factory

The sensor factory inspects the snapshot and config options to decide which entities to create:

| Factory Function                    | Entities Created                                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------------------------------ |
| `create_panel_sensors()`            | Status sensors, power sensors, energy sensors, battery level, software version                   |
| `create_circuit_sensors()`          | Per-circuit power and energy sensors for all named circuits                                      |
| `create_unmapped_circuit_sensors()` | Invisible backing sensors for unmapped breaker positions (used by synthetic sensor calculations) |
| `create_battery_sensors()`          | Battery power sensor (conditional — only when BESS commissioned)                                 |
| `create_power_flow_sensors()`       | PV power and site power sensors (conditional — only when PV or power-flows data present)         |

Conditional sensors (battery, PV, site) are gated on snapshot data, not configuration flags. If the hardware is not present, the sensors are not created. If
hardware appears later, capability detection triggers a reload and the factory creates them on the next setup cycle.

#### Sensor Base Classes

All sensors inherit from `SpanSensorBase`, which provides:

- **Unique ID generation** — delegated to subclasses, ensuring stable IDs across renames and migrations
- **Name generation** — two strategies: flag-based friendly name (initial install) and panel-driven name (existing entity, for name sync)
- **Name sync** — detects circuit name changes from the panel and requests a reload to update entity names
- **Offline handling** — power sensors report 0.0 when offline, energy sensors report unknown, string sensors report UNKNOWN
- **Availability** — entities remain available during offline so grace period state is visible

`SpanEnergySensorBase` extends the base with grace period tracking: it persists the last valid energy value and timestamp across HA restarts using
`RestoreSensor`, and continues reporting that value for a configurable window after the panel goes offline. This prevents statistics spikes from brief
disconnects.

#### Name Sync

When a user renames a circuit in the SPAN mobile app, the new name arrives via MQTT and appears in the next snapshot. The name sync mechanism detects this
change and updates entity names in HA:

1. On entity init, the current circuit name is stored for comparison
2. On each coordinator update, the current name is compared to the stored name
3. If the user has customized the entity name in HA's entity registry, sync is skipped — user overrides take precedence
4. If the name changed, a reload is requested
5. On reload, entities are recreated with the new panel name

Name sync operates identically across sensors, switches, and selects — each entity type implements the same comparison logic in its
`_handle_coordinator_update()` method.

#### Circuit Sensors

Circuit sensors bind to a specific `circuit_id` and read their data from `snapshot.circuits[circuit_id]`. The power sensor carries extra state attributes
(amperage, tabs, voltage, breaker rating, device type, relay state, shed priority). PV circuit power sensors also expose inverter vendor metadata.

Sign conventions are applied at the entity layer: PV circuit power is negated so positive values represent production, and PV net energy uses
`produced - consumed` while load circuits use `consumed - produced`.

#### Panel Sensors

Panel sensors read directly from the top-level snapshot fields. They include grid/feedthrough power and energy, battery and PV power (from power-flows), status
strings (grid state, run config, relay state), and hardware info (firmware version, battery level).

#### Switch and Select

Switch entities control circuit relay state (open/closed) via the client's `set_circuit_relay()` method. Select entities control circuit shed priority
(never/soc_threshold/off_grid) via `set_circuit_priority()`. Both use the same naming and name sync patterns as circuit sensors.

#### Binary Sensors

Binary sensors report panel hardware status: door state (tamper class), ethernet link, WiFi link, and overall panel connectivity. The door sensor reports
unavailable when the panel returns UNKNOWN (a known firmware quirk that resolves when the door is physically operated).

### Config Flow

The config flow handles initial setup and runtime reconfiguration:

**Setup flow:**

1. User enters panel IP (or discovered via Zeroconf)
2. Integration detects API version via unauthenticated status endpoint
3. User enters passphrase for v2 auth registration
4. Broker credentials are stored in config entry data
5. User selects entity naming pattern (friendly names or circuit numbers)

**Options flow:**

- Entity naming pattern (with migration between patterns)
- Snapshot update interval (debounce timer, 0–15s)
- Energy reporting grace period
- Net energy sensor toggles (panel-level and circuit-level)
- Display precision

Naming pattern changes trigger an entity ID migration: the old and new flags are stored in options, a `pending_naming_migration` flag is set, and the
coordinator picks it up on the next update cycle.

### Entity ID Migration

`EntityIdMigrationManager` handles three migration scenarios:

1. **Legacy → device prefix** — v1 entities without a device name prefix are renamed to include one
2. **Friendly names ↔ circuit numbers** — entity IDs are rewritten to match the selected naming pattern
3. **Combined** — both prefix and pattern changes in one operation

Migrations rewrite entity IDs in the entity registry while preserving unique IDs, which keeps statistics and history intact. A reload follows each migration to
apply the new IDs.

### Services

| Service                 | Purpose                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------- |
| `cleanup_energy_spikes` | Detect and correct erroneous negative energy deltas in statistics caused by firmware resets |
| `undo_stats_adjustment` | Reverse a previous cleanup operation using saved adjustment data                            |
| `main_meter_monitoring` | Watch main meter consumed energy for firmware-reset-induced drops and notify the user       |

### Zeroconf Discovery

The integration registers three Zeroconf service types in `manifest.json`: `_span._tcp.local.`, `_ebus._tcp.local.`, and `_secure-mqtt._tcp.local.`. When HA
discovers a device advertising any of these services, it triggers the config flow with the panel's host address pre-filled.
