# Changelog

All notable changes to this project will be documented in this file.

## [2.1.0] - 8/2026

### You will need this release before SPAN updates your panel

SPAN firmware `r202633` replaces the way the panel publishes its data — the wire model every release up to 2.0.8 is retired in the same update that introduces
the new one. There is no wire overlap and no setting to keep the old behaviour. **2.0.8 cannot read a panel on `r202633`**: it stays connected, reports every
circuit as missing, and shows nothing useful.

**The transition itself is seamless.** Install 2.1.0 early and your only outage is the firmware update itself. A log line and a one-time notification tell you
exactly what happened, so take a screenshot.

**A panel that is still rebooting is waited out, for as long as your panel takes.** Taking the firmware upgrade drops the panel's connection for several minutes
— four or more is not uncommon once the panel receives its upgrade file. If you experience an extended delay (over 5 minutes), reload the integration (reload is
a menu item, not a reinstallation).

### Requires Home Assistant 2026.8.0 or newer

This release raises the minimum from 2026.5.4. Home Assistant 2026.8 replaced the two device-registry calls this integration relies on — the old forms stop
working entirely in 2027.8.

### Added

- **Your panel's Microgrid Interconnect Device appears as its own device**, carrying **Grid State** — the health of the utility supply itself, which your panel
  never reported before.
- **Grid Power now says whether it is really measuring the grid**, through a new `at_service_entrance` attribute: false where a battery or another panel sits
  between the utility and your main lugs, which is when it and **Grid Power Flow** legitimately disagree.

- **Your solar inverter gets a device of its own** rather than rendering as diagnostic sensors on the panel's card; the five PV entities keep the entity ids,
  unique ids and history they have today.
- **Assign the new Solar device to an area** — those five no longer inherit the panel's area, which quietly drops them from area-scoped dashboards, automations
  and voice commands until you do.
- New installations get `sensor.span_panel_solar_pv_vendor` and friends where existing ones keep `sensor.span_panel_pv_vendor`; both are correct and neither
  will change again.

- **EVSE Charge Current Limit** — a settable ceiling on each commissioned SPAN Drive, the first control this integration has that changes something on a charger
  rather than on the panel.
- The maximum is the one your installer commissioned, read from the panel: a higher value is refused rather than quietly rounded down, and the control is
  unavailable rather than invented where the rating is not published.
- The control appears only where the panel says the limit can be changed, and reports a change still in flight as a `charge_current_limit_target` attribute.

- **PV Panel Link and EVSE Panel Link** — the panel's own report of the link to your inverter and to each charger, which is the fact **BESS Connected** has
  always shown for the battery.
- **EVSE Panel Link is not EV Connected**: one is the panel reaching the charger at all, the other is the charger reporting a plugged-in vehicle, and they can
  disagree mid-session.

- **BESS Meter Power** — what the battery itself reports it is charging or discharging at, beside the panel's arbitrated **Battery Power**. Enabled by default.
- **BESS Communication State** — the battery's own `OK` / `DEGRADED` / `LOST` / `UNKNOWN` view of its link, which can differ from the panel's. Diagnostic, off
  by default.
- **Both battery power sensors read positive when discharging**, settled by measurement on a live panel — the same convention as PV Power producing and Grid
  Power Flow importing.

- **Time to Priority Shed and Backup Time Remaining** — how long before the panel starts shedding circuits and how long before the battery is spent. Enabled by
  default.
- Each forecast carries its full-charge equivalent and the panel's own `forecast_confidence` as attributes, rather than as two more near-constant entities.

- **Import Limit, Binding Constraint and PCS Active** — the current limit your panel enforces, which rule set it, and whether anything is being throttled right
  now. Filed under Diagnostics with the panel's other electrical characteristics. Import Limit carries the whole arbitration as attributes.
- **Every circuit's power sensor gains `pcs_managed` and `pcs_priority`** where the circuit reports them — the shed order when an import limit binds, which is
  not the backup tier the existing `shed_priority` names.
- **New kinds of device your panel gains no longer wait for a release.** SPAN's data model is vendor-extensible, so a device type nobody has modelled can turn
  up at any time; one now gets a card of its own hanging off the panel, with whatever it publishes as entities beneath it, all disabled and diagnostic.
- **A property an adopted device accepts writes to becomes a control**: a boolean becomes a switch, an enum a select, a bounded number a number entity,
  constrained to what the device declared and nothing invented.
- **Nothing adopted enters long-term statistics** — `state_class` is not declared on the wire and a wrong guess writes corrupt statistics; wrap an adopted
  reading in a template sensor or utility meter if you want them.
- **A new reading a vendor adds to a device you already have now appears too**, on the card that device already has — the battery, the Microgrid Interconnect
  Device, a SPAN Drive or the solar inverter, with circuit and lugs readings landing on the panel's own card. Previously only whole new _devices_ were picked
  up, so a battery vendor adding a field reached you nowhere.
- These new readings arrive switched off and filed as diagnostics, like everything else adopted, and they are readings only — never switches or number boxes,
  because a control here would sit beside the curated ones without their limits and translations.
- **They keep the panel's own wording** (`Battery 2 Cell Temperature`), which is deliberately plainer than a curated entity's name so you can tell at a glance
  which is which.
- **Deleting one hides it until the next reload while your panel is still publishing it, and removes it for good once your panel stops.** There is no setting to
  suppress one, because the delete button already does both jobs, decided by what your panel is actually sending.

- **Your panel's own card shows what the panel says it is** — manufacturer, model and hardware revision read from the enclosure rather than assumed, once your
  panel publishes them.
- **Part Number** diagnostic sensor on every SPAN Drive, matching the one the battery already has. Off by default.
- **Circuit Priority's shed policy is readable**: `dsm_state` gains `shed_algorithm` and the two state-of-charge thresholds that decide when circuits shed and
  when they come back.
- **Grid-forming device name** as an attribute on the Grid Forming Entity sensor.
- **Diagnostics include your entity registry** — every entity this integration owns, its unique id, and what disabled it, which Home Assistant will not
  otherwise tell you.

### Changed

- **`DSM Grid State` is now more trustworthy**, reading the islanding state the Microgrid Interconnect Device actually senses rather than inferring it from the
  battery and the dominant power source, and keeping its entity id and its history.
- **`Grid Islandable` keeps working across the upgrade** by reflecting whether a Microgrid Interconnect Device is present, so a panel without one reads `Off`
  rather than going unavailable.
- **Battery model may read differently after upgrading**, now showing the human-readable designation rather than the SKU, normalised in the library on both
  sides of the upgrade so it lands once at this release.
- **Five panel sensors are switched off for new installations** (#234) because of three SPAN-documented defects predating firmware r202633: **Feedthrough
  Produced Energy**, **Feedthrough Consumed Energy**, **Feedthrough Power**, and the two **Downstream** current sensors.
- **Existing installations keep those five exactly where they are**, with their history and entity ids, because Home Assistant consults the enabled-by-default
  setting only when an entity is first created.
- **Your other panel readings are unaffected**, checked against a capture from a live upgraded panel rather than assumed.
- **New entities are now announced in a notification** that splits what arrived into ready to use and switched off and names each one.
- **The Wi-Fi network name moved to the Wi-Fi Link sensor** as a `wifi_ssid` attribute, absent rather than blank on a panel that publishes no SSID.
- **It is no longer an attribute of the Software Version sensor**, so a template reading `state_attr('sensor.span_panel_software_version', 'wifi_ssid')` should
  point at the Wi-Fi Link binary sensor instead.
- **Circuit entity ids are now composed by Home Assistant from your own entity-id settings**, the way every other entity this integration creates already was:
  the integration supplies the circuit half — `Circuit 15 Power` or `Kitchen Outlets Power`, as your naming option says — and Home Assistant adds the device
  name, and the area as well if your entity-id settings (`entity_id_parts`) include it.
- **Nothing you already have moves**: an entity keeps the id it has until you press **Recreate entity IDs**, which offers an entity its own id back where
  nothing changed, and an id reflecting your own configuration where something did — a circuit you renamed, an area you assigned, or the name you gave the panel
  device.
- **Three kinds of installation may be offered a different id when you press Recreate entity IDs**: a sensor on a SPAN Drive's feed circuit (offered the
  charger's name, the device it is actually on), a second panel whose device this integration called "Span Panel 2" (offered `span_panel_2_`), and an
  installation predating the `use_device_prefix` option whose circuit ids carry no device at all (offered one). Each is your own entity-id settings composing
  the id, and no id moves until you accept the offer.
- **A panel device you renamed yourself is offered ids carrying that name**, since Home Assistant composes from the name you gave the device ahead of the one
  this integration generated.
- **New circuit energy sensors get `consumed_energy`, `produced_energy` and `net_energy`**, the same word order as the panel-level energy sensors; existing
  sensors keep the spelling they have, whichever of the two it is, and are never offered the other one.
- **Circuit sensors on a SPAN Drive's feed circuit are named for the charger alone** — `sensor.<charger>_power`, matching the charger's other sensors.
- **The legacy `use_device_prefix` option can no longer keep the device out of a composed entity id**, since Home Assistant includes the device unless your own
  entity-id settings exclude it.
- **An unnamed circuit in friendly-names mode is now named for the tab it occupies** (`Circuit 7 Power`), so two unnamed circuits no longer collide — the old
  scheme called every one of them `single_circuit` and let the registry tell them apart with `_2`, `_3`.
- **An unnamed circuit you already have may be offered a `circuit_7` id** if you press **Recreate entity IDs**, which is the one place this release proposes a
  new id for a circuit you did not rename. Its current id keeps working until you accept.
- **Circuit-numbers installations now show the device in the displayed name** — `Span Panel Kitchen Outlets Power`, as friendly-names installations always have
  — so a template or a voice alias matching the old exact string needs updating.
- **A name you set in an entity's own settings still outranks the panel's**, and this integration no longer writes that field, clearing only the value an older
  release of its own put there.

### Fixed

- **Energy dip compensation no longer books a dip that never happened** (#259), where a partial reading at startup looked like a counter reset and every restart
  added each circuit's whole lifetime counter to its offset.
- **Recreate entity IDs proposes the ids your panel would produce now** (#252), in your installation's naming style, leaving unique ids and statistics
  untouched.
- **The registry `Name` an older release wrote on circuit-numbers installations is handed back to you** at the first start after upgrading, for every label
  those sensors have ever carried, so that field is yours again and no longer decides what Recreate proposes.
- **Enum sensors advertise the states they can actually report**, where nine sensors declared only `unknown` and `DSM Grid State` sitting at `On Grid` showed
  "Possible states: Unknown".
- **The README described Battery Power's sign backwards**, since the sensor has always reported discharging as positive (#184) and only the documentation was
  ever wrong.
- **A breaker your panel refuses to operate now says so**, reporting the panel's own reason where you pressed it instead of a traceback in the log and a toggle
  that quietly springs back.
- **A control command that never reached your panel now says so**, reported where you issued it rather than only in the log and stating plainly that nothing was
  queued.
- **A refused circuit priority no longer blames your firmware**, carrying the panel's own reason on the control you used rather than as a notification you
  dismiss by hand.

### Security

- **The panel passphrase is no longer stored** — upgrading migrates the config entry to version 7 and removes it, after which an older build of the integration
  will refuse to load that entry, so roll back from a backup rather than by reinstalling.
- **New admin-only action `span_panel.rotate_credentials`** replaces the eBus MQTT broker password on demand, which immediately breaks any other local client
  using the old one.
- **The panel's certificate authority is pinned before your passphrase is ever sent**, so the registration exchange that carries it — and everything after — no
  longer crosses your network in the clear.
- **Re-authenticating an entry that was never pinned pins it first**, so an entry that arrived from an older release sends its new credentials over a verified
  connection and keeps the authority afterwards.
- **Setting up or moving a panel to a hostname works with pinning on**, because the panel is set up over the address its certificate already names and the
  hostname is verified after the panel has added it.
- **A changed authority stops the integration and raises a repair** carrying both fingerprints, because a legitimate rotation and an impersonated panel look
  identical from here.
- **Four new options decide who may operate the panel** — administrators only, no commands without a logged-in user, an admin-only control lock, and a relay
  debounce — every one defaulting to what your panel already does.
- **The control lock arrives armed and stays where you left it**, so turning the option on locks control straight away and a restart or a settings save no
  longer quietly unlocks the panel; a pending auto-relock comes back with the time it had left rather than starting its countdown over.
- **Every control command is now recorded** in the logbook, on the event bus as `span_panel_control_command`, and at `INFO`, attributed to the automation that
  issued it rather than to nobody.
- **Controls now report what happened to them**, so a command that was refused or never handed to the broker no longer looks like one the panel confirmed.
- **A new [Security](README.md#security) section in the README** covers what the integration stores, what rotation costs, and the deployment choices that
  actually bound the panel's exposure.

## [2.0.8] - 5/2026

### Fixed

- **Integration reconnects automatically after a panel firmware upgrade** — previously, if the panel renewed its security certificate (for example during a
  firmware update), the integration could get stuck offline and require a manual reload from the Devices & Services page. It now recovers on its own.
- **Non-default panel ports now connect correctly** — panels configured to use a port other than the default are reached on the right port at startup.

## [2.0.7] - 5/2026

### Fixed

- **Door sensor no longer shows "Unavailable" when the panel reports an unknown door state** — The underlying firmware reports the door state as `UNKNOWN`
  rather than `OPEN` or `CLOSED` on boot. The door sensor now correctly shows "Unknown" state instead of becoming unavailable. The same fix applies to the
  grid-islandable and BESS connected binary sensors.

- **Energy statistics no longer spike when the panel reconnects quickly after an integration reload** — If the panel came back online within ~1 second of a
  reload (e.g. a brief network blip or panel restart), the dip compensation offset could fail to apply before the first coordinator update fired, causing HA
  statistics to record the raw panel counter as a fresh counter-reset value and permanently inflate cumulative energy totals. The offset is now restored before
  the coordinator listener is registered.

- **Favorites view no longer goes blank** after returning to Home Assistant from a backgrounded browser tab.
- **Circuit names display fully on narrow displays** — the row folds to a second line when the name would otherwise truncate.
- **Favoriting an EVSE now shows it as a device card** instead of a circuit row, matching the By Panel view.

### Changed

- **Dashboard now ships its own frontend components** so it no longer breaks when Home Assistant migrates its internal UI library (per
  [Frontend Component Updates 2026.4](https://developers.home-assistant.io/blog/2026/03/25/frontend-component-updates-2026.4)). No visual change; bundle grows
  ~500 KB.

## [2.0.6] - 4/2026

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

### Fixed

- **Force dependency re-resolution** — Version bump to ensure HACS re-installs `span-panel-api` for users who had the earlier 2.0.2 release. Users upgrading HA
  without re-downloading the integration could be left with a stale library missing required imports. (#191)

## [2.0.2] - 3/2026

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

**This is the release that moved the integration to the SPAN official eBus API**, which every release since reads and every panel now runs. The prerequisites
below applied while panels were still being updated to `spanos2/r202603/05`; they are recorded here for history.

### Breaking Changes

- Required firmware `spanos2/r202603/05` or later (the eBus MQTT API)
- You had to already be on v1.3.0 or later of the SpanPanel/span integration to upgrade
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
