# Feature Gap Analysis: span (SpanPanel) vs span-hass (dcj/electrification-bus)

> Date: 2026-02-23 Context: SPAN has released a new official API (v2 REST + MQTT/Homie via eBus). The v1 REST API used by `span-panel-api` is deprecated, sunset
> 2026-12-31. Developer "dcj" (Don Jackson, electrification-bus org) has published `span-hass`, a new HA integration built on the new API. This document
> catalogs features present in our `span` integration that are absent from `span-hass`.

## Summary

| Category                                          | span (ours) | span-hass (dcj) |
| ------------------------------------------------- | :---------: | :-------------: |
| Entity ID naming options                          |     Yes     |       No        |
| Entity ID migration                               |     Yes     |       No        |
| Statistics preservation across changes            |     Yes     |       No        |
| Circuit name sync with entity ID rename           |     Yes     |     Partial     |
| Energy spike cleanup service                      |     Yes     |       No        |
| Statistics undo service                           |     Yes     |       No        |
| Main meter reset detection                        |     Yes     |       No        |
| Energy grace period                               |     Yes     |       No        |
| Net energy sensors                                |     Yes     |       No        |
| Solar / inverter (dual-leg) sensors               |     Yes     |       No        |
| Simulation mode                                   |     Yes     |       No        |
| Options flow (runtime reconfiguration)            |     Yes     |       No        |
| Configurable scan interval                        |     Yes     |   N/A (push)    |
| Display precision control                         |     Yes     |       No        |
| API retry / backoff configuration                 |     Yes     |       N/A       |
| SSL/TLS toggle                                    |     Yes     |    TLS only     |
| Multi-language translations                       |   5 langs   |     1 lang      |
| Config entry version migration                    |     Yes     |       No        |
| Persistent notifications on errors                |     Yes     |       No        |
| Custom entity attributes (tabs, voltage, amps)    |     Yes     |       No        |
| Panel diagnostic sensors (DSM, relay, run config) |     Yes     |     Partial     |

---

## 1. Entity ID Naming Options

### What span has

Users choose between two naming patterns at setup and can switch at any time via the Options flow:

- **Friendly Names**: entity IDs derived from SPAN app circuit names (e.g., `sensor.span_panel_kitchen_outlets_power`)
- **Circuit Numbers**: entity IDs based on tab/space numbers (e.g., `sensor.span_panel_circuit_15_power`, `sensor.span_panel_circuit_30_32_power` for 240V)

There is also a **device name prefix toggle** (`USE_DEVICE_PREFIX`) that controls whether the HA device name is included in entity IDs.

### What span-hass has

Entity IDs are generated once at creation time from the Homie node ID and property ID (`{serial}_{node_id}_{property_id}`). There is no user-facing naming
choice, no circuit-number mode, and no ability to change naming after setup. Circuit names update the device registry name via MQTT callbacks but do not affect
entity IDs.

---

## 2. Entity ID & Unique ID Migration Infrastructure

### What span has

- `EntityIdMigrationManager` (990 lines) handles three migration types: legacy (no prefix → prefix), naming pattern (friendly ↔ circuit numbers), and combined
- `migration.py` handles config entry v1→v2 unique ID normalization with suffix remapping tables
- `migration_utils.py` classifies sensors from unique IDs for grouping
- All migrations use `entity_registry.async_update_entity()` which preserves HA long-term statistics (statistic_id follows entity_id rename)
- Migrations are triggered from the coordinator update cycle and execute in a single atomic pass

### What span-hass has

No migration infrastructure. No config entry versioning. Unique IDs are `{serial}_{node_id}_{property_id}` with no normalization or remapping.

### Why this matters

**Energy history preservation.** HA long-term statistics are keyed to `statistic_id == entity_id`. If a user migrates from span to span-hass, every energy
sensor gets a new entity_id under a different domain (`span_ebus` vs `span_panel`), with different unique IDs (Homie node IDs vs v1 REST circuit UUIDs), and
different property names (`exported-energy` vs `energy_consumed`). All accumulated energy statistics — daily, weekly, monthly totals — are orphaned. There is no
migration path in span-hass.

---

## 3. Circuit Name Syncing

### What span has

Every coordinator update checks whether circuit names have changed in the SPAN app. If a circuit was renamed AND the HA entity has not been manually renamed by
the user (detected by comparing `original_name` vs `name` in the entity registry), the integration triggers a config entry reload to pick up the new name. This
applies across sensors, switches, and selects. User renames are respected and not overwritten.

### What span-hass has

Registers per-circuit MQTT callbacks for the `name` property. When a name arrives, it updates the device registry name. However, entity IDs are frozen at
creation — there is no mechanism to propagate a circuit rename into entity IDs, and no detection of user-applied renames to suppress overwrites. A `_on_ready`
callback refreshes all device names on reconnection.

---

## 4. Energy Spike Cleanup Service (`span_panel.cleanup_energy_spikes`)

### What span has

A 1069-line service that detects and repairs negative energy spikes in HA long-term statistics caused by SPAN panel firmware resets:

- Queries hourly statistics for negative deltas (energy counter resets)
- Auto-detects or accepts user-specified main meter sensor
- Adjusts statistics iteratively via `async_adjust_statistics`
- Estimates missing energy using post-reset consumption rates
- Supports `dry_run` mode for preview
- Returns structured JSON results for auditing
- Multi-panel safe with thread-safe registration
- Maximum 100 iterations per sensor (safety limit)

### What span-hass has

Nothing. No spike detection, no statistics repair capability.

---

## 5. Statistics Undo Service (`span_panel.undo_stats_adjustments`)

### What span has

A companion service (468 lines) to reverse spike cleanup or apply manual statistics adjustments:

- **Reverse applied cleanup**: accepts JSON from a previous `dry_run=false` result and negates each adjustment
- **Reverse proposed cleanup**: accepts JSON from a previous `dry_run=true` result and applies the proposed corrections
- **Manual adjustment**: directly adjust a specific sensor's statistics at a given timestamp by a specified Wh amount

### What span-hass has

Nothing.

---

## 6. Main Meter Reset Detection

### What span has

`main_meter_monitoring.py` automatically tracks the main meter consumed energy sensor via `async_track_state_change_event`. When any decrease in a
`TOTAL_INCREASING` sensor value is detected (indicating a firmware reset), it creates a **persistent notification** (`span_panel_firmware_reset_detected`)
instructing the user to run the `cleanup_energy_spikes` service. Set up automatically during integration initialization.

### What span-hass has

Nothing. No monitoring, no notification on anomalous energy values.

---

## 7. Energy Grace Period

### What span has

- Configurable from 0–60 minutes (default 15) in the Options flow
- When the panel goes offline, energy sensors continue reporting their **last valid state** for the grace period instead of immediately becoming `unavailable`
- Uses `RestoreSensor` mixin to persist state across HA restarts via custom `SpanEnergyExtraStoredData` dataclass
- Extra state attributes expose `grace_period_remaining` (minutes) and `using_grace_period` (boolean)
- Prevents sudden statistics gaps during brief network interruptions

### What span-hass has

Entities go unavailable when the MQTT `$state` transitions away from `ready`. No grace period, no state persistence, no configurable timeout for energy sensors.

---

## 8. Net Energy Sensors

### What span has

Three independent toggles in the Options flow:

- `enable_panel_net_energy_sensors` — net Wh at panel level (main meter, feedthrough)
- `enable_circuit_net_energy_sensors` — net Wh per circuit
- `enable_solar_net_energy_sensors` — net Wh for solar

Net energy = produced − consumed, using `SensorStateClass.TOTAL` device class. Each toggle can be independently enabled/disabled.

### What span-hass has

No net energy sensors. The integration creates `Energy` (exported) and `Energy Returned` (imported) per circuit, but no computed net value.

---

## 9. Solar / Inverter Support (Dual-Leg Combination)

### What span has

- User configures two tab numbers (`inverter_leg1`, `inverter_leg2`) via Options flow
- Four combined solar sensors are created (power, produced energy, consumed energy, net energy)
- Each sensor sums data from both legs' unmapped circuit measurements
- Extra attributes include combined tabs list, voltage (120V × tab count), and computed amperage
- Unmapped tab entities are auto-enabled when solar is configured
- Solar tabs validated during options flow (integer, range 1–40)

### What span-hass has

PV support exists but is schema-driven from Homie `$description`. If the panel reports a PV node, entities are created for `nameplate-capacity`, vendor, feed
circuit reference. However, there is no user-configurable dual-leg combination, no computed power/energy from raw tab data, and no ability to manually specify
which tabs are solar legs.

---

## 10. Simulation Mode

### What span has

Full simulation support for development and testing without hardware:

- Toggle at setup time (`simulator_mode=True`)
- 4 bundled YAML simulation configs (32-circuit, 40-circuit with battery, etc.)
- **Clone Panel to Simulation**: generates simulation YAML from live panel data with circuit template inference (lighting, HVAC, EV charger, etc.)
- Configurable simulation start time offset (`HH:MM:SS`)
- Configurable offline minutes (panel goes offline after N minutes)
- Environment variable override (`SPAN_USE_REAL_SIMULATION`)
- Uses `span-panel-api`'s `DynamicSimulationEngine` for realistic data
- Simulators use slugified device name as identifier (allows multiple simulators)

### What span-hass has

Nothing. No simulation mode, no test configs, no offline simulation.

---

## 11. Options Flow (Runtime Reconfiguration)

### What span has

Menu-driven options flow with 5 sections:

1. **General Options**: scan interval, battery toggle, solar toggle + leg config, net energy toggles, display precision (power/energy), energy grace period, API
   retry settings (count, timeout, backoff multiplier)
2. **Entity Naming Options**: naming pattern toggle, device prefix toggle (triggers migration)
3. **Simulation Start Time** (simulator only)
4. **Simulation Offline Minutes** (simulator only)
5. **Clone Panel to Simulation** (live panels only)

Changes to most options trigger a config entry reload. Entity naming changes trigger the migration manager.

### What span-hass has

No options flow. All configuration is set once during initial setup and cannot be changed without removing and re-adding the integration.

---

## 12. Display Precision Control

### What span has

Two configurable precision settings in the Options flow:

- `power_display_precision` (default 0) — decimal places for power (W) sensors
- `energy_display_precision` (default 2) — decimal places for energy (Wh) sensors

Applied via `_attr_suggested_display_precision` on sensor entities.

### What span-hass has

No precision configuration. Uses HA defaults.

---

## 13. API Resilience Configuration

### What span has

Three user-configurable retry parameters in Options flow:

- `api_retries` (default 3) — retry count for failed API calls
- `retry_timeout` (default 30s) — per-request timeout
- `retry_backoff_multiplier` (default 2.0) — exponential backoff factor

The coordinator also implements graceful degradation: on transient errors it returns last-known data rather than raising `UpdateFailed`, keeping entities
available during brief outages.

### What span-hass has

MQTT reconnection is handled by paho-mqtt's built-in reconnect. No user-facing configuration. The ebus-sdk `Controller` handles reconnection automatically. REST
API calls (auth only) have a fixed 15-second timeout.

---

## 14. SSL/TLS Configuration

### What span has

User-selectable `use_ssl` toggle during initial config flow setup. When enabled, all REST API calls use `https://` and the `configuration_url` in device info
reflects the SSL scheme.

### What span-hass has

TLS is always used for MQTT (port 8883). The CA certificate is automatically downloaded from the panel during config flow. REST API calls during setup use
`http://` (the panel's v2 auth endpoint). There is no user toggle — TLS is enforced by the protocol (MQTTS).

---

## 15. Custom Entity Attributes

### What span has

Rich extra attributes on entities:

| Entity Type    | Attributes                                                               |
| -------------- | ------------------------------------------------------------------------ |
| Circuit Power  | `tabs`, `voltage` (120/240), `amperage` (P/V)                            |
| Circuit Energy | `tabs`, `voltage`, `grace_period_remaining`, `using_grace_period`        |
| Panel Power    | `voltage` (240), `amperage`                                              |
| Solar          | `tabs` (combined), `voltage` (120×count), `amperage`, grace period attrs |

### What span-hass has

No custom extra attributes. All entities expose only the state value and standard HA attributes (device_class, state_class, unit).

---

## 16. Panel Diagnostic Sensors

### What span has

Four panel data status sensors (all `entity_category=DIAGNOSTIC`):

| Sensor             | Example Values                                    |
| ------------------ | ------------------------------------------------- |
| DSM State          | `DSM_GRID_UP`, `DSM_GRID_DOWN`                    |
| DSM Grid State     | `DSM_ON_GRID`, `DSM_OFF_GRID`                     |
| Current Run Config | `PANEL_ON_GRID`, `PANEL_OFF_GRID`, `PANEL_BACKUP` |
| Main Relay State   | `OPEN`, `CLOSED`, `UNKNOWN`                       |

Plus: Software Version sensor (diagnostic).

### What span-hass has

`Firmware Version` sensor, `Main Relay` binary sensor (OPEN/CLOSED), `Dominant Power Source` select entity. No dedicated DSM, grid state, or run config sensors.
Some of this data may be available on MQTT properties but is not mapped to dedicated entities.

---

## 17. Multi-Language Translations

### What span has

5 translation files: English, Spanish, French, Japanese, Portuguese. Covers all config flow steps, options flow, error messages, and abort reasons.

### What span-hass has

English only (`translations/en.json`, `strings.json`).

---

## 18. Persistent Notifications for Errors

### What span has

- **Firmware reset notification**: auto-created when main meter energy decreases, with instructions to run `cleanup_energy_spikes`
- **Priority change error**: persistent notification created when circuit priority API call fails (select platform)

### What span-hass has

No persistent notifications. Errors are logged only.

---

## 19. Coordinator Performance Logging

### What span has

The coordinator logs cycle duration at INFO level on every update, measuring both data fetch time and total processing time. This aids in diagnosing performance
issues and tuning scan intervals.

### What span-hass has

Memory diagnostics logged every 30 minutes (peak RSS, tracemalloc, paho-mqtt queue depths). No per-update timing.

---

## 20. Config Export Service (`span_panel.export_synthetic_config`)

### What span has

A service to export the current sensor configuration to a YAML file on disk. Accepts a directory path or full file path. Used for development, debugging, and
simulation config generation.

### What span-hass has

Nothing.

---

## Features span-hass Has That span Does Not

For completeness, capabilities present in span-hass but absent from span:

| Feature                          | Notes                                      |
| -------------------------------- | ------------------------------------------ |
| MQTT push (no polling)           | Real-time updates, no scan interval        |
| Schema-driven entity generation  | Auto-creates from Homie `$description`     |
| EVSE (SPAN Drive) entities       | Charger status, lock state, current        |
| PCS entities                     | Power Control System diagnostic sensors    |
| Power flow entities              | Generic power flow sensors                 |
| Upstream/downstream lug entities | Grid power + energy + current              |
| Multi-panel hierarchy            | `link_subpanel` service, `via_device`      |
| Sub-device grouping              | Circuits, BESS, PV, EVSE as HA sub-devices |
| Memory diagnostics               | Periodic RSS/tracemalloc/queue monitoring  |
| BESS metadata entities           | Vendor, model, serial, position, capacity  |
| PV metadata entities             | Vendor, product, position, feed circuit    |
| Forward-compatible with new API  | Not deprecated; works on new firmware      |
