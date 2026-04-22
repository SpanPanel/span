# Changelog

All notable changes to this project will be documented in this file.

## [2.0.7] - 4/2026

### Fixed

- **Favorites view no longer goes blank** after returning to Home Assistant from a backgrounded browser tab.
- **Circuit names display fully on narrow displays** — the row folds to a second line when the name would otherwise truncate.
- **Favoriting an EVSE now shows it as a device card** instead of a circuit row, matching the By Panel view.

### Changed

- **Dashboard now ships its own frontend components** so it no longer breaks when Home Assistant migrates its internal UI library (per
  [Frontend Component Updates 2026.4](https://developers.home-assistant.io/blog/2026/03/25/frontend-component-updates-2026.4)). No visual change; bundle grows
  ~500 KB.

## [2.0.6] - 4/2026

**Important** 2.0.x cautions still apply — read those carefully if not already on 2.0.x BEFORE proceeding:

- Requires firmware `spanos2/r202603/05` or later (v2 eBus MQTT)
- You _must_ already be on v1.3.x or later of the SpanPanel/span integration if upgrading

### Added

- **By Activity and By Area views** — Two new circuit views available in both the integration panel and the Lovelace card (span-card 0.9.2):
  - By Activity: circuits sorted by power consumption with expandable graphs and search filtering
  - By Area: circuits grouped by Home Assistant area with live area registry updates
  - Shared tab bar across panel and card with configurable text/icon style
- **Cross-panel Favorites view** (span-card 0.9.4) — A synthetic "Favorites" entry in the dashboard panel dropdown aggregates favorited circuits and sub-devices
  (BESS, EVSE) across every configured SPAN panel into a single workspace. Heart toggles in the Graph Settings and per-circuit / per-sub-device side panels
  persist favorites and the view to the integration storage so the Favorites view is reconstituted on restart. See the Favorites explanation in the frontend
  dashboard link via the README.md.

### Fixed

- **Dashboard goes blank after idle** — Panel and card migrated to LitElement and refresh after losing focus (span-card 0.9.1)
- **Dashboard graph fidelity** — Circuit charts now use step interpolation instead of linear, eliminating misleading diagonal ramps between data points.
  Continuous signals (PV solar output, BESS SoC/SoE) retain linear interpolation to faithfully represent their gradual behavior.
- **Panel status showing "Connected" while the panel is offline** — the panel status sensor now reflects the true connection state and updates within a second
  of the panel going offline or coming back online (including the bump to span-panel-api v2.6.2)

## [2.0.5] - 4/2026

**Important** 2.0.x cautions still apply — read those carefully if not already on 2.0.x BEFORE proceeding:

- Requires firmware `spanos2/r202603/05` or later (v2 eBus MQTT)
- You _must_ already be on v1.3.x or later of the SpanPanel/span integration if upgrading

### Added

- **Current monitoring and dashboard** — Real-time monitoring of circuit and mains current draw, managed from a new sidebar panel with Panel, Monitoring, and
  Settings tabs.
  - Configurable spike and continuous overload thresholds (percentage of breaker rating, window duration, cooldown)
  - Per-circuit and per-mains-leg threshold overrides with reset-to-global
  - Notification targets and device trackers
  - Persistent HA notifications and event bus alerts
  - Customizable notification title and message templates with placeholder substitution
  - Breaker grid view with live utilization indicators, shedding icons, and per-circuit side panel

- **Frontend i18n** — Dashboard panel and card editor translated into English, Spanish, French, Japanese, and Portuguese.

- **Local brand images** — Icon and logo assets are now shipped inside the integration (`brand/` directory) instead of relying on the Home Assistant brands CDN.
  Requires Home Assistant 2026.3 or later.

### Changed

- **Services use entity IDs** — Monitoring services accept entity IDs instead of internal circuit UUIDs, matching HA conventions.
- **`span-panel-api` updated to 2.5.1** — Improved HTTP connection handling and performance.
- **`span-card`** no longer needs to be loaded through a custom HACS repository; it is loaded by the integration and can be embedded into dashboards. If using
  the `span-card` separately from the built-in dashboard, remove the custom resource.

### Fixed

- **Circuit switch toggle bounce** — Toggling a breaker switch no longer bounces (changes → reverts → settles).

- **Breaker rating and nameplate capacity sensors** — Corrected device classes on breaker ratings (main and per-circuit) and BESS/PV nameplate capacity sensors.
  These are static configuration values that rarely change, so they are now disabled by default in new installs to reduce recorder writes. The data is still
  available via the panel topology service; enable the sensors from entity settings if you need them in dashboards or automations.

## [2.0.4] - 3/2026

**Important** 2.0.1 cautions still apply — read those carefully if not already on 2.0.1 BEFORE proceeding:

- Requires firmware `spanos2/r202603/05` or later (v2 eBus MQTT)
- You _must_ already be on v1.3.x or later of the SpanPanel/span integration if upgrading

### Added

- **Grid Power sensor** — New `Grid Power`. Previously only `Current Power` (upstream lugs measurement) was available; the new sensor surfaces the panel's own
  grid power accounting alongside Battery Power, PV Power, and Site Power. Without BESS `Grid Power` is the same as `Current Power`. Note that if your panel has
  an integrated BESS and the BESS loses communication with the panel the Grid Power sensor is not accurate. In such a case HA would need a current clamp
  upstream of the BESS to accurately reflect whether the Grid is up.
- **FQDN registration support** — Config flow detects FQDN-based connections and registers the domain with the panel for TLS certificate SAN inclusion. Blocked
  by an upstream API permission issue ([SPAN-API-Client-Docs#10](https://github.com/spanio/SPAN-API-Client-Docs/issues/10)); the integration falls back to
  IP-based connections until resolved.

### Changed

- **Simulation moved to dedicated add-on** — Panel cloning and simulation are no longer part of the integration's options flow. A new `export_circuit_manifest`
  service provides panel parameters to the standalone [SPAN Panel Simulator](https://github.com/SpanPanel/simulator) add-on, which now supports upgrade
  modelling (evaluate firmware or integration upgrades in a sandbox before applying them to your real panel) and panel clone (replicate your panel's circuit
  layout for testing).

### Fixed

- **MQTT broker connection** — The eBus broker connection now uses the panel host from zeroconf discovery or user configuration instead of the panel-advertised
  `.local` address, which may not resolve in all HA environments (#193).

- **PV nameplate capacity unit** — Corrected the PV nameplate capacity sensor unit to watts.

- **Recorder database growth** — Energy sensors still expose grace-period and dip-compensation diagnostics, plus circuit `tabs` and `voltage`, on the entity,
  but those attributes are no longer written to the recorder, which greatly reduces churn in the `state_attributes` table (#197).

## [2.0.3] - 3/2026

**Important** 2.0.1 cautions still apply — read those carefully if not already on 2.0.1 BEFORE proceeding:

- Requires firmware `spanos2/r202603/05` or later (v2 eBus MQTT)
- You _must_ already be on v1.3.x or later of the SpanPanel/span integration if upgrading

### Fixed

- **Force dependency re-resolution** — Version bump to ensure HACS re-installs `span-panel-api` for users who had the earlier 2.0.2 release. Users upgrading HA
  without re-downloading the integration could be left with a stale library missing required imports. (#191)

## [2.0.2] - 3/2026

**Important** 2.0.1 cautions still apply — read those carefully if not already on 2.0.1 BEFORE proceeding:

- Requires firmware `spanos2/r202603/05` or later (v2 eBus MQTT)
- You _must_ already be on v1.3.x or later of the SpanPanel/span integration if upgrading

### Fixed

- **Panel size always available** — `panel_size` is now sourced from the Homie schema by the underlying `span-panel-api` Previously some users could see fewer
  unmapped sensors when trailing breaker positions were empty. Topology service reflects panel size.
- **Battery power sign inverted** — Battery power sensor now uses the correct sign convention. Previously, charging was reported as positive and discharging as
  negative, which caused HA energy cards to show the battery discharging when it was actually charging. The panel reports power from its own perspective; the
  sensor now negates the value to match HA conventions (positive = discharging), consistent with how PV power is already handled. (#184)
- **Idle circuits showing -0W** — Power sensors that negate values (PV circuits, battery, PV power) could produce IEEE 754 negative zero (`-0.0`) when the
  circuit was idle, causing HA to display `-0W` instead of `0W`. All negation sites now normalize zero to positive. (#185)
- **Net energy inconsistent with dip-compensated consumed/produced** — When energy dip compensation was enabled, consumed and produced sensors applied an offset
  but net energy computed from raw snapshot values, causing a visible mismatch. Net energy now reads dip offsets from its sibling sensors so the displayed value
  always equals compensated consumed minus compensated produced.

## [2.0.1] - 3/2026

⚠️ **STOP — If your SPAN panel is not on firmware `spanos2/r202603/05` or later, do not upgrade. Ensure you are on v1.3.0 or later BEFORE upgrading to 2.0. This
upgrade migrates to the SPAN official eBus API. Make a backup first.** ⚠️

### Breaking Changes

- Requires firmware `spanos2/r202603/05` or later (v2 eBus MQTT)
- You _must_ already be on v1.3.0 or later of the SpanPanel/span integration if upgrading
- After upgrading, you must re-authenticate using your **panel passphrase** (found in the SPAN mobile app under On-premise settings) or **proof of proximity**
  (open and close the panel door 3 times). See the [README](README.md) for details.
- If you were running a beta or RC, ensure you reload the integration after upgrade
- `Cellular` binary sensor removed — replaced by `Vendor Cloud` sensor
- `DSM Grid State` deprecated — still available, but users should rely on `DSM State` as `DSM Grid State` may be removed in a future version since it is an
  alias for `DSM State`
- **Sensor state values are now lowercase** — The following sensors now report lowercase state values with translated display names. Automations or scripts that
  compare against the old uppercase values must be updated:
  - `DSM State`: `DSM_ON_GRID` → `dsm_on_grid`, `DSM_OFF_GRID` → `dsm_off_grid`
  - `DSM Grid State`: same as DSM State (deprecated alias)
  - `Current Run Config`: `PANEL_ON_GRID` → `panel_on_grid`, `PANEL_OFF_GRID` → `panel_off_grid`
  - `Main Relay State`: `CLOSED` → `closed`, `OPEN` → `open`

  The UI displays localized names (e.g., `dsm_on_grid` displays as "On Grid"). Automations use the lowercase values shown above. This change was made to support
  translations in enumerations.

### New Features

- **EVSE (SPAN Drive) Support**: Each commissioned EV charger appears as a sub-device (e.g., "Main House SPAN Drive (Garage)")
- **BESS sub-device**: Battery entities live on a dedicated BESS sub-device
- **Energy Dip Compensation**: Automatically compensates when the panel reports lower energy readings for `TOTAL_INCREASING` sensors, maintaining a cumulative
  offset to prevent negative spikes in the energy dashboard. Enabled by default for new installs; existing installs can enable via General Options. Includes
  diagnostic attributes (`energy_offset`, `last_dip_delta`) and persistent notifications.
- Real-time MQTT push via eBus broker — no more polling intervals
- **Grid Forming Entity (GFE) sensor** — shows the panel's current grid-forming power source (GRID, BATTERY, PV, GENERATOR, NONE, UNKNOWN). Identifies which
  source provides the frequency and voltage reference.
- **GFE Override button** — publishes a temporary `GRID` override when the battery system (BESS) loses communication and the GFE value becomes stale. The BESS
  automatically reclaims control when communication is restored. See [Grid Forming Entity](README.md#grid-forming-entity) for details
- `Site Power` sensor (grid + PV + battery from power-flows node)
- **Panel diagnostic sensors**: L1/L2 Voltage, Upstream/Downstream L1/L2 Current, Main Breaker Rating — promoted from attributes to dedicated diagnostic
  entities
- **Circuit Current and Breaker Rating sensors**: promoted from circuit power sensor attributes to dedicated per-circuit entities (conditionally created when
  the panel reports the data)
- **PV metadata sensors**: PV Vendor, PV Product, Nameplate Capacity — on the main panel device (conditionally created when PV is commissioned)
- **Grid Islandable binary sensor**: indicates whether the panel can island from the grid (conditionally created)
- `PV Power` sensor with inverter metadata attributes (vendor, product, nameplate capacity)
- **Reconfigure flow** — update the panel host/IP address without removing and re-adding the integration.
- Circuit Shed Priority select now works — controls off-grid shedding (NEVER / SOC_THRESHOLD / OFF_GRID)
- Panel size and Wi-Fi SSID as software version attributes

### Removed

- Post-install entity naming pattern switching — the naming pattern is now set once during initial setup. The `EntityIdMigrationManager` and all associated
  migration machinery have been removed
- `cleanup_energy_spikes` and `undo_stats_adjustments` services — energy dip compensation handles counter dips automatically. For existing historical spikes,
  use Developer Tools > Statistics to adjust individual entries

### Developer / Card Support

- **WebSocket Topology API**: New `span_panel/panel_topology` WebSocket command that returns the full physical layout of a panel in a single call — circuits
  with breaker slot positions, entity IDs grouped by role, and sub-devices (BESS, EVSE) with their entities. See [WebSocket API Reference](websocket-api.md) for
  schema and examples

### Improvements

- `DSM State` — multi-signal heuristic deriving grid connectivity from battery grid-state, dominant power source, upstream lugs power, and power-flows grid
- `Current Run Config` — full tri-state derivation (PANEL_ON_GRID / PANEL_OFF_GRID / PANEL_BACKUP)
- Configurable snapshot update interval (0–15s, default 1s) reduces CPU on low-power hardware

## [1.3.1] - 2026-01-19

### 🐛 Bug Fixes

- **Fix reload loop when circuit name is None (#162)**: Fixed infinite reload loop that caused entity flickering when the SPAN panel API returns None for
  circuit names. Uses sentinel value to distinguish between "never synced" and "circuit name is None" states. When circuit name is None, entity name is set to
  None allowing HA to use default naming behavior. Thanks to @NickBorgers for reporting and correctly analyzing a solution. @cayossarian.

- **Fix spike cleanup service not finding legacy sensor names (#160)**: The `cleanup_energy_spikes` service now correctly finds sensors regardless of naming
  pattern (friendly names, circuit numbers, or legacy names without `span_panel_` prefix). Also adds optional `main_meter_entity_id` parameter allowing users to
  manually specify the spike detection sensor when auto-detection of main meter fails or that sensor has been renamed. Thanks to @mepoland for reporting.
  @cayossarian.

### 🔧 Improvements

- **Respect user-customized entity names**: When a user has customized an entity's friendly name in Home Assistant, the integration skips name sync for that
  entity. @cayossarian

## [1.3.0] - 2025-12-31

### 🔄 Changed

- **Bump span-panel-api to v1.1.14**: Recognize panel Keep-Alive at 5 sec, handle httpx.RemoteProtocolError defensively. Thanks to
  @NickBorgersOnLowSecurityNode.

## [1.2.9] - 2025-12-25

### ✨ New Features

- **Energy Spike Cleanup Service**: New `span_panel.cleanup_energy_spikes` service to detect and remove negative energy spikes from Home Assistant statistics
  caused by panel firmware updates. Includes dry-run mode for safe preview before deletion.
- **Firmware Reset Detection (Beta)**: Monitors the main meter energy sensor for errant decreases (negative energy deltas over time). Sends a persistent
  notification when detected, guiding users to adjust statistics if desired.

### 🔄 Changed

- **Removed Decreasing Energy Protection**: Reverted the TOTAL_INCREASING validation that was ignoring decreasing energy values that were thought to occur
  during a limited number of updates but turned out to be permanent under-reporting of SPAN cloud data that manifested during firmware updates. The bug is on
  the SPAN side and can result in spikes in energy dashboards after firmware updates. See the Trouble-Shooting section of the README.md for more information.

### 📝 Notes

- A future release may implement local energy calculation from power values to eliminate both the freezing issue and negative spikes.

## [1.2.8] - 2025-12-10

### 🔧 Technical Improvements

- **Fix total increasing sensors** against receiving data that is less than previously reported
- **Fix feedthrough sensor types** now set to TOTAL instead of TOTAL_INCREASING

## [1.2.7] - 2025-11-29

### 🔧 Technical Improvements

- **Offline Listener Fix**: Fixed simulation listener to prevent being called when not in simulation mode
- **Grace Period Restoration**: Fixed grace period algorithm to properly restore previous good values from Home Assistant statistics on restart, ensuring energy
  sensors report accurately after system restarts
- **CI/CD Dependencies**: Updated GitHub Actions checkout action to version 6

## [1.2.6] - 2025-09-XX

### 🔧 Technical Improvements

- **Panel Level Net Energy**: Add net energy sensors for main meter and feed-through (consumed - produced)
- **Net Energy Config Options**: Added separate config options to enable/disable panel, circuit, leg-based net energy. Disabling circuit net energy can help
  resource constrained installations since the sensors are not created or updated
- **Circuit Naming Logic**: Fixed logic for circuit-naming patterns to ensure proper entity ID generation and panel prefixes (fresh installs only)
- **Entity ID naming Choices**: Restored the ability to change entity ID naming patterns in live panels (circuit tab-based sensors only, not panel)
- **Panel Friendly Name Sync**: Fixed regression in panel circuit name synchronization. A new install will sync all friendly names once on the first refresh and
  anytime a user changes a name in the SPAN mobile app.
- **API Optimization**: Removed unnecessary signal updates to improve performance and reduce overhead
- **API Dependencies**: Updated span-panel-api OpenAPI package to version 1.1.13 to remove the cache
- **Resolve Cache Config Entry Defect**: Fixed an issue where a 1.2.5 config entry could attempt to set up a cache window in the underlying OpenAPI library that
  was invalid

## [1.2.5] - 2025-09-XX

### 🔧 Technical Improvements

- **Circuit Based Naming**: Circuit based entity_id naming was not using both tabs in the name. Existing entity IDs are unchanged except for fresh installs.
- **Switches and Selects Naming**: were creating proper IDs but not looking up migration names in 1.2.4

## [1.2.4] - 2025-09-XX

### 🔧 Technical Improvements

- **Performance**: Revert to native sensors (non-synthetic) to avoid calculation engine for simple math. Features like net energy, OpenAPI, simulation are still
  present. We may reintroduce the synthetic engine later in a modified form to allow users to add attributes, etc.
- **Fix sensor circuit-based naming**: For new installations with circuit naming provide consistent behavior where all circuits, other than panel have circuit
  names related to the tab (120V) or tabs (240V). We do not modify entity IDs, so if an installation had faulty names from a previous release those must be
  renamed manually
- **Fix Faulty Legacy Single Panel Config**: Provided a repair for a pre-1.0.4 upgraded release where the config entry was missing the device unique ID (serial
  number), causing the new migration for normalized unique keys to fail. This repair only works for single panel installs because we derive the serial number
  from the entities and if more than one serial number is found we cannot determine which config the serial number would match.
- **Fixed Unmapped Tab Behavior for Offline Panel**: Unmapped tab sensors reported erroneous values when the panel was offline

## [1.2.3] - 2025-08-XX Rescinded for performance regression

## [1.2.2] - 2025-06-XX

### Major Upgrade

**Before upgrading to version 1.2.3, please backup your Home Assistant configuration and database.** This version introduces some architectural changes. While
we've implemented migration logic to preserve your existing entities and automations, it's always recommended to have a backup before major upgrades.

### 🚀 Features

- **Grace Period Algorithm**: Developed by @sargonas, keeps statistics from reporting wild spikes and gaps during intermittent outages by providing the previous
  known good value for a grace period
- **Voltage and Amperage Attributes**: Added attributes for voltage and amperage to each power sensor for threshold automations
- **Panel Tabs Attributes**: Added attribute to each sensor to see the specific panel tabs (spaces) associated with sensor
- **Unmapped Tab Sensors**: Added hidden circuits for tabs that are not part of a circuit reported directly by the panel. The user may make these tab sensors
  visible.
- **Panel Offline Sensor**: Added a sensor that indicates whether the panel is offline (cannot return data to the integration)
- **State Visibility**: Attributes show you the formula used in the sensor calculation for grace periods and net energy
- **Net Energy Sensors**: New net energy sensors calculate `consumed energy - produced energy` for circuits, panels, and tab-based solar installations,
  providing real-time net energy consumption/generation data
- **Panel Simulation**: You can clone your own panel or set up a simulation for energy usage based on predefined patterns. You can also take the panel offline
  to see how the grace periods for energy respond. We may extend this feature in order to allow modeling of energy usage or integration with other sensors or
  utilities.

### OpenAPI Support

- **OpenAPI Specification**: Integration now uses the OpenAPI specification provided by the SPAN panel for reliable foundation
- **Future Interface Changes**: Provides reliable foundation for future interface changes

### Simulation Support

- **Virtual Panel Templates**: Support for adding configuration entries for virtual panels based on templates that produce typical power and energy
- **Import/Export Profiles**: You can import or export the simulation profile and even clone your existing panel
- **Custom Profile Building**: See the simulation [guide](https://github.com/SpanPanel/span-panel-api/blob/main/docs/simulation.md) on how to build your own
  profile

### Network Configuration

- **Configurable Timeouts and Retries**: Connection options for different network environments
  - **Timeout Settings**: Customize connection and request timeouts for slower networks
  - **Retry Configuration**: Configure automatic retry attempts for transient network issues
- **SSL/TLS Support**: Added SSL support for remote panel access scenarios
  - **Local Access**: Standard HTTP connection for panels on local network
  - **Remote Access**: HTTPS support for accessing panels through secure proxies

### Circuit Management

- **Circuit Name Sync**: Automatic friendly name updates when circuits are renamed in the SPAN panel
- **Custom Name Preservation**: Custom entity friendly names in Home Assistant are preserved and won't be overwritten during sync
- **Re-enable Sync**: Clear custom name in Home Assistant to re-enable sync for customized entities

### Entity Naming Patterns

- **Configurable Entity Naming**: Provides configurable entity naming patterns upon initial setup
- **Friendly Names Pattern**: Entity IDs use descriptive circuit names (e.g., `sensor.span_panel_kitchen_outlets_power`)
- **Circuit Numbers Pattern**: Entity IDs use stable circuit numbers (e.g., `sensor.span_panel_circuit_15_power`)
- **Pattern Selection**: Choose between friendly names (recommended for new installations) or circuit numbers (stable entity IDs)

### 🔧 Technical Improvements

#### Migration Support

- **Legacy Support**: Pre-1.0.4 installations can only migrate forward to friendly names with device prefixes

### ⚠️ Breaking Changes

- **Major Architectural Changes**: Version 1.2.3 introduces significant architectural changes
- **Backup Required**: Users must backup Home Assistant configuration and database before upgrading
- **Migration Required**: Existing installations require migration to new schema

### 📝 Documentation

- **Simulation Guide**: Documentation for building custom simulation profiles
- **Troubleshooting Section**: Enhanced troubleshooting information

### 🔄 HACS Upgrade Process

This integration should handle migrating your entities seamlessly. Any entity IDs or names should be retained. We do migrate all the unique keys by properly
renaming these in the entity registry so the user should not see any difference.

- **Backup Instructions**: Check backup requirements before upgrade
- **Automation Verification**: Check automations for correct entity ID references

### 👥 Acknowledgments

- **@cayossarian**: Developed the synthetic engine, OpenAPI package, simulator
- **@sargonas**: Researched and developed the grace period algorithm that keeps statistics from reporting wild spikes and gaps during intermittent outages

---

## [1.1.0] - Previous Version

### 🚀 Features

- Basic SPAN Panel integration
- Circuit monitoring and control
- Power and energy sensors
- Panel status monitoring

### 🔧 Technical

- Initial integration release
- Basic API communication
- Entity creation and management

---

## [1.0.4] - Legacy Version

### 🔧 Technical

- Legacy entity naming support
- Device prefix requirements for friendly names
- Pre-migration schema support
