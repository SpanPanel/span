# Changelog

All notable changes to this project will be documented in this file.

## [2.1.0b3] - 8/2026

### Fixed

- **The integration now reloads itself after the firmware upgrade in the case that actually happens.** This is the whole promise of the release, and a live
  upgrade found the one path where it did not hold. When a panel takes the new firmware it drops its MQTT connection, comes back a few minutes later, and
  starts serving HTTP a little after that — and while it is still starting, it answers with `502` rather than refusing the connection, because a booting device
  brings its network front end up before the application behind it. The integration retried a refused connection and a timeout, but treated an answered-with-502
  as a hard failure and gave up on the first try, leaving the old reader in place. It was caught on two Home Assistant instances watching one panel through the
  same upgrade: both went quiet and neither recovered until reloaded by hand. A `502` is now understood as **not ready yet** and waited out, for as long as a real
  reboot takes.
- **If following the upgrade ever fails for some other reason, you are now told what to do about it.** Previously that surfaced as a bare error in the log with
  no indication that anything needed doing, while the integration carried on reading the panel with the wrong reader. It now says plainly that a reload is
  needed once the panel is back up.

### Added

- **Diagnostics include your entity registry.** Every entity this integration owns, with its unique id and — the part you cannot get anywhere else — what
  disabled it, if anything. Home Assistant tells you an entity is disabled without telling you by what, and reading that yourself needs shell access that a
  Home Assistant OS install does not give you.

### Changed

- **The README now leads with the upgrade warning**, and documents the Microgrid Interconnect Device, adopted devices, the `at_service_entrance` attribute on
  Grid Power, and which sensors arrive switched off.
- **The battery power sign was documented backwards in both the README and this file, and is corrected.** Positive means **discharging**, which is what release
  2.0.5 established and what a measured panel confirms. No entity changed and no reading moved; only the documentation was wrong.

## [2.1.0b2] - 8/2026

### You will need this release when SPAN updates your panel

SPAN firmware `r202633` replaces the way the panel publishes its data — the flat model every release up to 2.0.8 reads is retired in the same update that
introduces the new one. There is no overlap and no setting to keep the old behaviour. **2.0.8 cannot read a panel on `r202633`**: it stays connected, reports
every circuit as missing, and shows nothing useful.

We do not control when that update reaches you, and nobody has published a schedule. Panels update on SPAN's timing, not on yours or ours. So the safe order is
to be on this release **before** your panel changes rather than after, because afterwards you are looking at a blank integration while you work out why.

**The transition itself is seamless, and that is the point of this release.** Install it and it keeps reading your panel exactly as before, on either firmware.
When your panel does change over, the integration notices on the wire, reloads itself, and carries on — no reconfiguration, no re-pairing, no lost history. Your
entities keep their entity ids, their unique ids and their statistics across the change. New things appear because the new firmware genuinely publishes more;
nothing you already had goes away.

### Requires Home Assistant 2026.8.0 or newer

This release raises the minimum from 2026.5.4. Home Assistant 2026.8 replaced the two device-registry calls this integration relies on — the old forms stop
working entirely in 2027.8 — and their replacements do not exist in 2026.5 through 2026.7, so there is no version of this release that runs on both. If you are
on an older Home Assistant, stay on 2.0.8 until you can update; HACS will not offer you this release.

### Added

- **Your panel's Microgrid Interconnect Device appears as its own device** on the new data model, carrying **Grid State** — the health of the utility supply
  itself, which the previous firmware never reported. Everything about it is additive; no existing entity moves or changes id.
- **The integration notices a firmware upgrade and reloads itself.** A panel that becomes v1.0 while Home Assistant is running used to keep reading the tree
  with the old parser, reporting every circuit as missing until you reloaded by hand. It now detects the change, reloads, writes a log line, and raises a
  one-time notice explaining what changed.
- **Grid-forming device name** as an attribute on the GFE sensor.
- **Grid Power now says whether it is really measuring the grid**, through a new `at_service_entrance` attribute. That sensor reads your panel's upstream lugs,
  which is grid flow only when those lugs are where the utility actually connects. Put a battery between the utility and your main lugs, or feed the panel from
  another panel, and the same reading becomes that panel's own supply while **Grid Power Flow** stays the whole-site figure — so the two legitimately disagree,
  and until now there was no way to tell that apart from a fault. The eBus specification was corrected on 2026-08-20 to say exactly this, after this project
  supplied the capture that prompted it. Both readings were always correct; only the label was ever conditional.
- **Diagnostics now include your entity registry.** Every entity this integration owns, with its unique id and — the part you cannot get anywhere else — what
  disabled it, if anything. Home Assistant tells you an entity is disabled without telling you by what, and reading that yourself needs shell access to
  `.storage` that a Home Assistant OS install does not give you. Four causes look identical on screen and need four different answers.

### Changed

- **`DSM Grid State` is now more trustworthy on the new data model.** It keeps its entity id and all of its history. Previously it was _inferred_ — from the
  battery if one was fitted, otherwise from the dominant power source and whether power was crossing the grid connection. It now reads the islanding state the
  Microgrid Interconnect Device actually senses.
- **`Grid Islandable` keeps working** across the upgrade. v1.0 publishes no panel-level islandable property, so the entity now reflects whether a Microgrid
  Interconnect Device is present, which is how v1.0 says backup capability is detected.
- **Battery model** may read differently after upgrading: the new data model separates the human-readable designation from the SKU, and this entity now shows
  the designation. This is a library-level normalisation applied to both data models, so it happens once, at this release, rather than unpredictably during a
  firmware update.

- **Five panel sensors are switched off for new installations, because the eBus specification's own maintainer has documented that their values cannot be relied
  on.** A conformance note for SPAN firmware r202633 identifies three defects in what the panel publishes, all of which predate that release: the feedthrough
  (downstream lugs) energy registers are computed from two unrelated counters and can decrease or go negative — on a panel with no feedthrough load they report
  roughly whole-panel figures where the truth is zero — the feedthrough power reading is inverted relative to every other terminal, and the feedthrough currents
  report the _upstream_ service conductors rather than a downstream measurement. The affected entities are **Feedthrough Produced Energy**, **Feedthrough
  Consumed Energy**, **Feedthrough Power**, and the two **Downstream** current sensors.
- **If you already have those five, nothing changes and they stay exactly where they are.** Home Assistant consults the enabled-by-default setting only when an
  entity is first created, so an existing installation keeps them, keeps its history and keeps its entity IDs. This stops new installations picking them up; it
  cannot reach back. If you use any of the five on a dashboard or in an automation, they are worth removing — but that is your decision to make, not something
  an upgrade should do to you.
- **A new BESS Meter Power sensor, reading the battery's own meter alongside the existing Battery Power.** The two answer slightly different questions — one is
  the battery's own view, the other the panel's arbitrated figure — and they agree by construction, so where they ever differ that is worth being able to see.
- **Your battery power readings are unchanged, and that is now checked against a real panel rather than assumed.** A capture from a live upgraded panel arrived
  alongside the conformance note. The panel's four power-flow values sum to zero exactly, in the frame the specification now describes, and the battery power
  sensor's definition is byte-for-byte what 2.0.8 shipped — same source, same conversion. Nothing about what you see has moved.
- **Your other panel readings are unaffected.** The upstream lugs, the main panel meter and every circuit are in the correct frame. So is the power-flow group,
  which the specification has now been corrected to describe the way the panel has always published it.

- **New entities are now announced in a notification that names them — whether or not they arrived switched on.** Previously only entities added _disabled_ were
  mentioned, on the reasoning that an enabled one is already visible in your entity list and its history. That is only true if you are watching your entity
  list, which nobody is: an addition that breaks nothing was indistinguishable from no addition at all. The notification names every entity that was added,
  splits them by whether they are ready to use or still switched off, and tells you where to turn the switched-off ones on.
- **It is a notification rather than a Repair, because an addition is not a repair.** Nothing is broken and nothing needs fixing. Any new-entity item still
  sitting in your Repairs list from a previous version is removed on upgrade.

- **A device the panel publishes that this integration has never modelled now appears, instead of appearing nowhere.** SPAN positions the panel as the hub for
  whatever plugs into it and the eBus schema is explicitly vendor-extensible, so a device type nobody modelled is an expected arrival — and until now it
  produced no device, no entity and no sign it was there. Such a device now gets a card of its own hanging off the panel, carrying whatever `info` it publishes,
  with its readings as entities beneath it. Everything adopted arrives **disabled and diagnostic**: nothing reaches a dashboard uninvited, and the new-entity
  notice names the device so you can find it.
- **Devices this integration does model are left alone, deliberately.** A new property on a circuit, the battery, a charger or the panel is not adopted — it is
  curated in a release, because that is where the judgement lives about whether it should be an entity, an attribute, or a line on a device card. Automatic
  adoption would spend an `entity_id` permanently on a machine-derived shape before anyone made that call. The sixteen Power Control System properties that
  curation collapsed into one entity and thirteen attributes are the worked example of what a rule cannot produce.
- **Nothing adopted enters long-term statistics, and that is a decision rather than an omission.** No adopted entity carries a `state_class`. It is not declared
  on the wire and cannot be derived from one — this integration ships `feedthroughEnergyProducedWh` as `TOTAL` beside `mainMeterEnergyProducedWh` as
  `TOTAL_INCREASING`, same unit and same device class — and a wrong one writes corrupt statistics that fixing the panel afterwards does not repair. Enrolling a
  property nobody asked for into long-term statistics is also a permanent write to your recorder database. If you want statistics from an adopted reading, wrap
  it in a template sensor, a Riemann-sum integration or a utility meter: that is your call, made on an entity you chose to enable.
- **A property an adopted device accepts writes to becomes a control, not just a reading.** A declared `boolean` becomes a switch, an `enum` with its option
  list becomes a select, and a number with its `min:max:step` becomes a number entity — all disabled and diagnostic like every other adopted entity, so a
  control appears only if you go and enable it. A settable property that declares no value domain stays a reading: a select with no options and a number with no
  bounds are broken controls, not safe ones.
- **Your panel stays the authority on the value.** Nothing is translated or clamped on the way out: this integration knows an adopted property's declaration and
  nothing else, so inventing a bound would be inventing a fact about your hardware. The control constrains you to what the device declared, and the panel
  accepts or refuses.

- **Your solar inverter gets a device of its own, on panels running the v1.0 data model.** Its vendor, model and nameplate capacity used to render as diagnostic
  sensors on the _panel's_ card, beside the panel's own manufacturer and model — so the card whose job is telling you which enclosure this is read as though the
  enclosure were an Enphase inverter. It now has a card like the battery and the chargers already do, carrying the firmware version the panel has been
  publishing all along.
- **If you already have these sensors, nothing about them changes.** The five entities that move to the new card — PV Power, PV Vendor, PV Product, PV Nameplate
  Capacity and PV Panel Link — keep the entity ids and unique ids they have today, so dashboards, automations and history follow them across untouched.
- **They do lose the panel's area, though, and nothing warns you.** An entity takes its area from the device it sits on unless you set one, and the new solar
  device starts with no area. So if your panel is assigned to an area, these entities were in it yesterday and are in no area today — which quietly stops them
  matching area-scoped dashboards, area-scoped automations and scripts, and voice commands that target a room. Assign the new **Solar** device to an area and
  they behave as before. Worth doing before you go looking for what broke.
- **New installations get different entity ids for these five, and that is intended.** Home Assistant derives a new entity's id from the name of the device it
  sits on, so a system installed from now on gets `sensor.span_panel_solar_pv_vendor` where a system installed before this release keeps
  `sensor.span_panel_pv_vendor`. Both are correct and neither will change again: an existing system must never have an id renamed under it, and a new one gets
  the id Home Assistant would give it. If you are comparing two SPAN systems and their PV entity ids differ, install date is why. The unique ids are the same on
  both, and both sets of entities sit on the same new card.

- **Your panel's own card now shows what the panel says it is** — manufacturer, model and hardware revision, read from the enclosure rather than assumed. A
  panel on the older data model publishes none of the three and keeps exactly the card it has today; the hardware revision row is left off rather than shown
  blank where no revision is published.
- **Every SPAN Drive gets a Part Number** diagnostic sensor, matching the one the battery already has. Off by default.
- **Circuit Priority's shed policy is readable.** The `dsm_state` sensor gains `shed_algorithm` and the two state-of-charge thresholds that decide when circuits
  shed and when they come back — the numbers that make the panel's shed behaviour predictable rather than surprising. A policy this integration does not
  recognise keeps its name and carries the panel's raw document beside it, so you can read what a parser could not.

- **A charge-current limit you can set, on panels running the v1.0 data model.** Each commissioned SPAN Drive gets an **EVSE Charge Current Limit** number on
  its own device — the ceiling the charger offers your vehicle, which you can lower to charge more slowly and raise back. It is the first control this
  integration has that changes something on a charger rather than on the panel.
- The maximum you can ask for is the one your installer commissioned, read from the panel rather than assumed: the box will not accept a value above the
  charger's rated current, and neither will anything else — a value beyond it is refused before it is sent, not quietly rounded down to something you did not
  ask for. If the panel has not yet published what the charger is rated for, the control reports unavailable instead of offering an invented range.
- The control appears only where the panel says the limit can be changed. A charger that publishes its limit as read-only gets no control, which is the same
  distinction **Circuit Priority** already makes for a circuit commissioned never-backup.
- While the panel is acknowledging a change it has not yet applied, the requested value shows as a `charge_current_limit_target` attribute and the state stays
  the limit the charger is still enforcing — the same way Circuit Priority reports a priority change in flight.

- **Whether your panel can reach your solar inverter and each of your chargers, on panels running the v1.0 data model.** **PV Panel Link** and **EVSE Panel
  Link** are the same fact **BESS Connected** has always shown for the battery: the panel's own report of the link to a device it feeds. The battery's version
  worked because the panel publishes it on the main lugs; the inverter's and each charger's are published by the circuit that feeds them, and nothing read that
  half — so one of your three device classes had a link sensor and the others did not.
- **EVSE Panel Link is not EV Connected.** EV Connected is the charger reporting that a vehicle is plugged in. EVSE Panel Link is the panel reporting that it
  can reach the charger at all. A charger part-way through a session behind a link the panel has lost shows a plugged-in vehicle and a dead link at the same
  time, which is exactly the case where you want to know which of the two you are looking at. The new sensors are diagnostics; EV Connected is unchanged.
- Each sensor is created only where a circuit publishes the link record for that device, and per charger rather than per panel — two chargers whose circuits
  report differently get two sensors that say different things. A circuit that feeds ordinary loads publishes no such record, which is normal and is not
  reported as a fault, and a panel that starts publishing one picks the sensors up on the reload the integration already performs.

- **Your battery's own meter and its own link health, on panels running the v1.0 data model.** **Meter Power** is what the BESS itself reports it is charging or
  discharging at, as distinct from the panel's **Battery Power**, which is the enclosure's arbitrated figure. Both have been on the wire since firmware r202633
  and nothing read either. Meter Power is enabled by default; **Communication State** — the BESS's own `OK` / `DEGRADED` / `LOST` / `UNKNOWN` report on its link
  — is a diagnostic and is off by default, since it is only interesting when something is wrong.
- **Both battery power sensors read positive when the battery is _discharging_**, and that direction was settled by measurement rather than by reading. With the
  battery driven into self-consumption and the grid at exactly zero — PV 4181 W plus battery 1917 W meeting a 6099 W load, so the battery can only be supplying
  — both sensors read `+1917.49`. The two values arrive from the panel in opposite sign conventions and are normalised to this one, so they agree with each
  other and with the sensors beside them: PV Power is positive while producing, Grid Power Flow positive while importing, Battery Power positive while
  discharging. Every one is "positive means power flowing toward the house".
- **Communication State is not the same thing as BESS Connected.** The binary sensor is the _panel's_ view of the link, from the enclosure's connection record;
  the new sensor is the _battery's_ view of it. A BESS can report its own link lost while the panel still claims it, and now you can see that.
- Both sensors are created only where the BESS publishes the reading behind them — a battery on the older data model, or one whose firmware publishes only one
  of the two, gets no entity for what it cannot report rather than one permanently unknown, and a BESS that gains the capability on a firmware upgrade picks the
  sensors up on the reload the integration already performs.
- **Backup planning, in minutes: two new sensors on panels running the v1.0 data model.** **Time to Priority Shed** is how long before the panel starts shedding
  circuits, and **Backup Time Remaining** is how long before the battery is spent. Your panel has been publishing both since firmware r202633 and nothing read
  them; they are the numbers you would actually set an alarm on, so they are enabled by default and sit beside the power and energy sensors rather than under
  diagnostics.
- **Each forecast sensor carries the refinements that qualify it** as attributes: `full_charge_time_to_priority_shed` / `full_charge_total_time_remaining` —
  what the same estimate would be from a full battery — and `forecast_confidence`, the panel's own `LOW` / `MEDIUM` / `HIGH` assessment of the estimate. They
  refine a number already on screen rather than adding two near-constant entities to your entity list.
- Both sensors are created only where the panel publishes the estimate behind them. A panel on the older data model, or one whose firmware publishes only part
  of the forecast, gets no entity for what it cannot report rather than one permanently unknown — and a panel that gains the capability on a firmware upgrade
  picks the sensors up on the reload the integration already performs.
- **What is limiting your import, in amps: three new entities on panels running the v1.0 data model.** **Import Limit** is the current limit your panel is
  actually enforcing, **Binding Constraint** names which rule set it — your service rating, a utility envelope, an operator cap, a limit you asked for — and
  **PCS Active** says whether anything is being throttled right now. Your panel has published all of this since firmware r202633 and nothing read it.
- **Import Limit carries the whole arbitration as attributes**: the four constraint limits the panel reconciled (`feed_import_limit`, `operator_import_limit`,
  `off_grid_import_limit`, `requested_import_limit`), each one's `_enablement` and `_active` flag, and `pcs_enabled`. They explain the enforced number rather
  than being numbers to watch, and most of them change only when somebody reconfigures the panel — so they refine an entity you already have instead of adding
  twelve to your entity list.
- **Every circuit's power sensor gains `pcs_managed` and `pcs_priority`** where the circuit reports them: whether the Power Control System manages that circuit,
  and where it sits in the shed order when an import limit binds. `pcs_priority` is a different thing from the existing `shed_priority`, which is the backup
  tier — a circuit may take part in one policy, both, or neither.
- All three entities are created wherever the panel publishes the capability, **including when the PCS is switched off**. A panel reporting a 0 A limit with
  everything unconfigured is reporting a state, and that is the state most panels are in; entities that vanished until somebody configured a limit would be
  entities nobody could build a dashboard on.

- **The Wi-Fi network name moved to the Wi-Fi Link sensor**, which is where you would look for it: the entity that tells you whether Wi-Fi is up now also tells
  you which network it is up on, as a `wifi_ssid` attribute. It is absent rather than blank on a panel that publishes no SSID.
- **It is no longer an attribute of the Software Version sensor.** A network name on a firmware-version sensor never made sense — it sat there because
  `panel_size` was already in that attribute block. If you have a template reading `state_attr('sensor.span_panel_software_version', 'wifi_ssid')`, point it at
  the Wi-Fi Link binary sensor instead. `panel_size` is unaffected and stays where it is.

### Fixed

- **Enum sensors advertise the states they can actually report.** Nine sensors declared only `unknown`, so `DSM Grid State` sitting at `On Grid` showed
  "Possible states: Unknown". The lists are now derived from the translations and checked against them by a test.
- **The GFE override button reads the right signal** for deciding when it applies, so it is no longer permanently enabled on the new data model.

- **The Wi-Fi network name came back.** Panels on the older data model report the SSID they are joined to, and this integration has shown it as an attribute on
  the panel status sensor for as long as it has existed. On the v1.0 data model nothing read it, so the attribute quietly emptied when your panel upgraded — a
  value you had, silently gone, with no error and nothing in the log. It is read again, and it is now published on the Wi-Fi Link binary sensor rather than on
  the Software Version sensor — see above.
- **A firmware upgrade that adds a capability now actually reloads.** The check that decides whether new hardware warrants a reload knew about four capabilities
  where the rest of the integration knew about nine. A panel that gained the shed forecast, the power control system, battery telemetry or DER link health while
  Home Assistant was running published the data, matched every rule for creating the entities, and asked for no reload — so the new entities appeared only the
  next time you restarted. This affected the Microgrid Interconnect Device before this release too.

- **The README described Battery Power's sign backwards, and an earlier entry in this file "corrected" it the wrong way.** The sensor reports **discharging** as
  positive and always has — that is what release 2.0.5 established (#184) and what a measured panel confirms. A later note claimed the opposite and the README
  was edited to match it, so both documents told you to expect the wrong sign. Both are now right. **No entity changed and no reading moved**; only the
  documentation was ever wrong, in both directions.

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
