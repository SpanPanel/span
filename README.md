# SPAN Panel Integration for Home Assistant

[Home Assistant](https://www.home-assistant.io/) Integration for [SPAN Panel](https://www.span.io/panel), a smart electrical panel that provides circuit-level
monitoring and control of your home's electrical system.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub Release](https://img.shields.io/github/release/SpanPanel/span.svg?style=flat-square)](https://github.com/SpanPanel/span/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/SpanPanel/span.svg?style=flat-square)](https://github.com/SpanPanel/span/commits)
[![License](https://img.shields.io/github/license/SpanPanel/span.svg?style=flat-square)](LICENSE)

[![Python](https://img.shields.io/badge/python-3.14.2-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/)
[![prettier](https://img.shields.io/badge/code_style-prettier-ff69b4.svg)](https://github.com/prettier/prettier)
[![prek](https://img.shields.io/badge/prek-enabled-brightgreen)](https://github.com/j178/prek)

The software is provided as-is with no warranty or guarantee of performance or suitability to your particular setting.

**IMPORTANT:** This integration controls real electrical equipment. Circuit switches open and close physical relays. The GFE override button changes how the
panel manages load shedding during power outages. These actions carry the same consequences as operating the panel manually — because they are. Automations can
execute these actions without user presence; design them with the same care you would apply to any unattended electrical control. This integration is not a
safety device and must not be relied upon for life-safety applications. Use this software at your own risk. If you cannot accept that risk, do not use this
software. See [LICENSE](LICENSE) for the full warranty disclaimer.

The SPAN Client documentation has warnings regarding the use of the API (the API used by this integration) which should be heeded just as if you were using that
API directly:

> An API client that attempts to implement its own load-shedding decisions, grid-state detection, or other critical automation is operating outside the scope of
> what SPAN API was designed and engineered for. Such use is entirely at the client developer's and homeowner's own risk and may void the SPAN Panel Limited
> Warranty. See the SPAN API Scope & Responsibility Model in the [SPAN API documentation](https://github.com/spanio/SPAN-API-Client-Docs).

This integration provides sensors and controls for understanding an installation's power consumption, energy usage, and controlling user-manageable panel
circuits.

The integration includes a built-in dashboard accessible from the Home Assistant sidebar, providing real-time circuit-level power visualization, current
monitoring with configurable alerts, and circuit settings for relays and load shedding. See [Frontend Dashboard](frontend.md) for details. You can optionally
use the [span-card](https://github.com/SpanPanel/span-card) Lovelace card for visualization and switch control.

The [SPAN Panel Simulator](https://github.com/SpanPanel/simulator) HA App lets you clone your panel's circuit layout for testing, or model an upgrade to
evaluate firmware or integration changes in a sandbox before applying them to your real panel.

This integration communicates with the SPAN Panel over your local network using SPAN's official
[Electrification Bus (eBus)](https://github.com/spanio/SPAN-API-Client-Docs) framework — an open, multi-vendor integration standard for home energy
infrastructure. eBus uses the [Homie Convention](https://homieiot.github.io/) for MQTT topics and messages, with the panel's built-in MQTT broker delivering
real-time state updates without polling.

## ⚠️ Backup and Upgrade to v2.1.x before your panel's firmware updates, or the integration will stop working (upgrade only from v2.0.8!)

**SPAN firmware `r202633` changes the API in a non-compatible way after the firmware hits** When your panel takes that update, 2.0.8 stops being able to read
it. The integration still connects, still shows as loaded, and reports every circuit as missing — sensors go unavailable, automations stop firing, dashboards go
blank. It does not fail loudly. It goes quiet.

**Nobody outside SPAN knows when your panel will update, and you cannot defer it from Home Assistant.** Panels update on SPAN's timing. There is no schedule to
plan around, which is why the safe move is to be on 2.1.x already rather than to wait for a signal that is less appealing.

**Upgrading first costs you nothing.** On your current firmware, 2.1.x reads your panel exactly as 2.0.8 does.

The changeover is designed to be seamless.** The integration detects the new format on the wire, reloads itself, and carries on:

- no re-pairing, no re-authentication.
- Entity ids, unique ids and long-term statistics survive. Dashboards, automations and history follow.
- New entities appear because the new firmware genuinely publishes more. Nothing you already had is removed.

Upgrade afterwards instead and you reach the same place — after however long it takes you to notice, and to work out that a firmware update is the reason your
panel went silent. Worst case should be a reload.

Take another backup after the upgrade.

<details>
<summary>What changes in the firmware</summary>

The firmware upgrade `r202633` rewrites _how_ the panel publishes its self-describing BOM. The MQTT topic structure changes, and the panel stops presenting
itself as a long list of properties. It presents a tree that can proxy other devices instead. Every topic moves.

The old format is retired in the same update that introduces the new one — there is no overlap and no setting to keep the old behaviour.

</details>

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) installed
- [HACS](https://hacs.xyz/) installed
- SPAN Panel with firmware `spanos2/r202603/05` or later
- SPAN Panel integration v1.3.0 or later
- Panel passphrase (found via the SPAN app) **or** physical access to the panel door

## Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS in the left side bar of your Home Assistant installation
3. Search for "Span"
4. Open the repository and click "Download"
5. Restart Home Assistant (you will be prompted by a repair notification)
6. Go to `Settings` > `Devices & Services`
7. Click `+ Add Integration` and search for "Span"
8. Enter the IP address of your SPAN Panel
9. The integration detects the panel as v2 and presents an authentication choice:
   - **Enter Panel Passphrase** — type the passphrase found in the SPAN mobile app under On-premise settings
   - **Proof of Proximity** — open and close the panel door 3 times, then click Submit
10. Choose your entity naming pattern
11. Optionally adjust the snapshot update interval — 0 is real-time, up to 15 seconds based on CPU

### Upgrade Process

When upgrading through HACS:

1. **Create a backup** of your Home Assistant configuration and database
2. **Review the changes** in this README and CHANGELOG
3. **Check your automations** — review any references to removed entities
4. **Update during a quiet period** when you can monitor the upgrade

If you encounter issues, restore from your backup or check the [troubleshooting section](#troubleshooting) below.

## Key Terms

The following terms appear throughout this document and in the integration's sensors:

- **Grid-forming entity (GFE)** — The power source that sets the voltage and frequency reference for the home. When the utility grid is up, it is the GFE. When
  islanded on battery, the battery inverter becomes the GFE.
- **Islanded** — The home is electrically disconnected from the utility grid and running on its own power source, typically battery. Circuits may be shed to
  conserve battery life.
- **Microgrid** — When the home is islanded, the battery inverter creates a small, self-contained electrical grid for the home. This local grid functions
  independently of the utility — the inverter generates AC power at the correct voltage and frequency, and the home's circuits run on it just as they would on
  utility power.
- **Microgrid Interconnect Device (MID)** — A switch, part of or alongside the battery system, that disconnects the home from the utility grid during an outage.
  While open, the panel's sensors can only see the home side.
- **Shedding** — Automatically turning off lower-priority circuits to conserve battery during an outage, based on each circuit's configured shed priority.

## Entity Reference

### Panel-Level Sensors

| Sensor                       | Device Class | Unit | Notes                                                                                                                  |
| ---------------------------- | ------------ | ---- | ---------------------------------------------------------------------------------------------------------------------- |
| Current Power                | Power        | W    | Total panel power (grid import/export)                                                                                 |
| Feed Through Power           | Power        | W    | Feedthrough (non-breaker) power                                                                                        |
| Main Meter Produced Energy   | Energy       | Wh   | Grid energy exported                                                                                                   |
| Main Meter Consumed Energy   | Energy       | Wh   | Grid energy imported                                                                                                   |
| Main Meter Net Energy        | Energy       | Wh   | Consumed minus produced                                                                                                |
| Feed Through Produced Energy | Energy       | Wh   | Feedthrough energy exported                                                                                            |
| Feed Through Consumed Energy | Energy       | Wh   | Feedthrough energy imported                                                                                            |
| Feed Through Net Energy      | Energy       | Wh   | Feedthrough net energy                                                                                                 |
| DSM State                    | —            | —    | dsm_on_grid (grid connected), dsm_off_grid (islanded), unknown. Derived from multiple eBus signals                     |
| Current Run Config           | —            | —    | panel_on_grid (grid connected), panel_off_grid (islanded on PV/generator), panel_backup (islanded on battery), unknown |
| Grid Forming Entity          | —            | —    | GRID, BATTERY, PV, GENERATOR, NONE, UNKNOWN. See[Grid Forming Entity](#grid-forming-entity)                            |
| Main Relay State             | —            | —    | closed (power flowing), open (disconnected), unknown                                                                   |
| Vendor Cloud                 | —            | —    | CONNECTED, UNCONNECTED, UNKNOWN                                                                                        |
| Software Version             | —            | —    | Firmware version string                                                                                                |

### Panel Diagnostic Sensors

| Sensor                | Device Class | Unit | Notes                                                             |
| --------------------- | ------------ | ---- | ----------------------------------------------------------------- |
| L1 Voltage            | Voltage      | V    | L1 leg actual voltage                                             |
| L2 Voltage            | Voltage      | V    | L2 leg actual voltage                                             |
| Upstream L1 Current   | Current      | A    | Upstream lugs L1 current                                          |
| Upstream L2 Current   | Current      | A    | Upstream lugs L2 current                                          |
| Downstream L1 Current | Current      | A    | Downstream lugs L1 current. Off by default from 2.1.x — see below |
| Downstream L2 Current | Current      | A    | Downstream lugs L2 current. Off by default from 2.1.x — see below |
| Main Breaker Rating   | Current      | A    | Main breaker amperage. Off by default                             |

L1/L2 Voltage and Main Breaker Rating have always been off by default; enable them from the panel's device page if you want them.

**The three Feedthrough sensors and the two Downstream current sensors are off by default from 2.1.x.** The eBus specification's maintainer has documented that
the panel's feedthrough (downstream lugs) figures cannot be relied on: the energy registers can decrease or go negative, the power reading is inverted relative
to every other terminal, and the downstream currents report the **upstream** service conductors. The defects predate `r202633`.

Existing installations keep all five, with their history and entity ids — Home Assistant applies the setting only when an entity is first created. If you use
them somewhere, removing them is worth considering, but that is your call.

### Shed Forecast Sensors

Created only when your panel publishes the `shed-forecast` capability, and only for the estimates it actually publishes.

| Sensor                | Device Class | Unit | Notes                                                            |
| --------------------- | ------------ | ---- | ---------------------------------------------------------------- |
| Time to Priority Shed | Duration     | min  | Estimated time before the next priority tier of circuits is shed |
| Backup Time Remaining | Duration     | min  | Estimated time before every sheddable circuit is shed (off-grid) |

#### Shed Forecast Sensor Attributes

Present only when the panel publishes them.

| Attribute                           | Type   | On                    | Notes                                                 |
| ----------------------------------- | ------ | --------------------- | ----------------------------------------------------- |
| `full_charge_time_to_priority_shed` | int    | Time to Priority Shed | The same estimate assuming the battery starts full    |
| `full_charge_total_time_remaining`  | int    | Backup Time Remaining | The same estimate assuming the battery starts full    |
| `forecast_confidence`               | string | both                  | The panel's own assessment:`LOW`, `MEDIUM`, or `HIGH` |

### Power Control System Sensors

Created only when your panel publishes the `pcs` capability, and created whether or not the PCS is switched on — a PCS reporting a limit of 0 A is reporting a
state, not an absence.

| Sensor             | Device Class | Unit | Notes                                                                                                                      |
| ------------------ | ------------ | ---- | -------------------------------------------------------------------------------------------------------------------------- |
| Import Limit       | Current      | A    | The limit actually being enforced: the most restrictive active constraint (diagnostic)                                     |
| Binding Constraint | Enum         | —    | Which constraint sets that limit: Firm Service Rating, Grid Envelope, Voltage Support, Off-Grid, Requested, Operator, None |

#### Power Control System Sensor Attributes

On **Import Limit**, and present only when the panel publishes them. These are the inputs the panel reconciled to produce the enforced limit above.

| Attribute                | Type   | Notes                                                          |
| ------------------------ | ------ | -------------------------------------------------------------- |
| `pcs_enabled`            | bool   | Whether the panel's PCS is enabled at all                      |
| `feed_import_limit`      | float  | The Firm Service Rating: the commissioned, always-on floor (A) |
| `operator_import_limit`  | float  | A cap imposed by a fleet or aggregator operator (A)            |
| `off_grid_import_limit`  | float  | The import cap while islanded (A)                              |
| `requested_import_limit` | float  | A voluntary limit requested by the owner or installer (A)      |
| `<name>_enablement`      | string | Per limit:`UNSPECIFIED`, `UNCONFIGURED`, `DISABLED`, `ENABLED` |
| `<name>_active`          | bool   | Per limit: whether that constraint is currently enforcing      |

### Power Flow Sensors

| Sensor        | Device Class | Unit | Notes                                                                               |
| ------------- | ------------ | ---- | ----------------------------------------------------------------------------------- |
| Grid Power    | Power        | W    | Grid power flow                                                                     |
| Site Power    | Power        | W    | Total site power (grid + PV + battery)                                              |
| Battery Power | Power        | W    | Battery charge/discharge (**+discharging, −charging**). Only when BESS commissioned |
| PV Power      | Power        | W    | PV generation (+producing). Only when PV commissioned                               |

### PV Metadata Sensors (on the Solar sub-device)

From 2.1.x these live on a **Solar** device of their own rather than on the panel's card, alongside PV Power and PV Panel Link.

| Sensor             | Device Class | Unit | Notes                                         |
| ------------------ | ------------ | ---- | --------------------------------------------- |
| PV Vendor          | —            | —    | PV inverter vendor (e.g., "Enphase", "Other") |
| PV Product         | —            | —    | PV inverter product (e.g., "IQ8+")            |
| Nameplate Capacity | Power        | kW   | Rated inverter capacity. Off by default       |

If you upgraded, these keep their entity ids, unique ids and history — but not the panel's area, since an entity takes its area from its device and the Solar
device starts without one. Assign it an area, or anything area-scoped (dashboards, automations, voice targeting a room) stops matching them. New installations
get ids from the new device name — `sensor.span_panel_solar_pv_vendor` rather than `sensor.span_panel_pv_vendor`. Both are correct and neither changes again.

**Deprecated:**

| Sensor         | Reason                                                                                                                   |
| -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| DSM Grid State | Deprecated — still available, but users should rely on`DSM State` as `DSM Grid State` may be removed in a future version |

### Microgrid Interconnect Device

Your panel publishes its Microgrid Interconnect Device — the switch that disconnects your home from the utility during an outage — as a device of its own,
linked to the panel. It appears automatically where the panel reports one; nothing existing moves onto it.

| Sensor     | Device Class | Unit | Notes                                                                     |
| ---------- | ------------ | ---- | ------------------------------------------------------------------------- |
| Grid State | Enum         | —    | Health of the utility supply itself:`up`, `down`, `degraded` or `unknown` |

This is new information — the previous firmware did not report the utility supply at all. It is not **DSM Grid State**, which is whether **your home** is
islanded: the grid can be down while your home runs happily off the battery.

**A panel with no battery has no MID, and that is itself an answer.** The specification makes backup capability structural — having a MID is what says a panel
can island — so **Grid Islandable** reads `Off` and **Grid Forming Entity** reads `Grid` rather than either going unavailable.

`DSM Grid State` keeps its entity id and history but is no longer inferred from the battery or the dominant power source; it now reads the islanding state the
MID actually senses.

### Adopted Devices

The eBus schema is vendor-extensible, so your panel can publish a device type this integration has never modelled. Rather than ignoring it, the integration
gives it a card of its own hanging off the panel, carrying whatever identity it publishes, with its readings as entities beneath it.

Everything adopted arrives **disabled and diagnostic**, so nothing reaches a dashboard uninvited, and the new-entity notification names the device so you can
find it. A property the device accepts writes to becomes a control rather than a reading — a `boolean` becomes a switch, an enumeration becomes a select, a
number becomes a number entity — and those arrive switched off too.

Two things worth knowing before you build on one:

- **Nothing adopted enters long-term statistics.** No adopted entity carries a `state_class`, because the correct one is not published on the wire and guessing
  wrong writes corrupt statistics that fixing the panel afterwards does not repair. If you want statistics from an adopted reading, wrap it in a template
  sensor, a Riemann-sum integration or a utility meter — a deliberate choice on an entity you enabled.
- **A new property on a device this integration already models is adopted too**, but as a reading on that device's existing card rather than as a device of its
  own. See [Adopted Vendor Readings](#adopted-vendor-readings) below.

### Adopted Vendor Readings

The other half of vendor extensibility. A publisher can add a property to a device this integration already models — the battery, a charger, the solar inverter,
a circuit or the panel itself — and until 2.1.x that reading went nowhere: it appeared in the diagnostics download and in no entity list. It now becomes an
entity on that device's own card. The wire already says which device and node it belongs to, so it has a home; what it does not say is how important it is.

They behave like adopted devices in the ways that matter, and differ in two:

- **They arrive switched off and filed as diagnostics**, so nothing lands on a dashboard uninvited. The new-entity notification names each one, up to five per
  device; beyond that it gives the device and a count instead, because fifteen new vendor readings at once would otherwise cost you the curated additions in the
  same message.
- **They are readings only — never switches, selects or number boxes**, even where the panel says the property accepts writes. These sit beside curated controls
  that do real work, such as the charge limit that refuses a value above what your charger was commissioned for, and a generic control would sit there with none
  of that. If a control is worth having, it arrives curated in a release.
- **They keep the panel's own wording**, so a vendor property on the battery reads `Battery 2 Cell Temperature` rather than something tidied up. Deliberately
  plainer than a curated entity's name: it is how you tell at a glance which entities this integration designed and which it is passing through.
- **Nothing adopted enters long-term statistics**, exactly as for adopted devices, and here it buys something extra: with no statistics behind them, a later
  release can correct one of these entities' units, device class or category with nothing to repair.

**What the delete button does**, since it is not quite what you would expect:

- Delete one while your panel is still publishing that property and it comes back — switched off — at the next reload. There is no setting to suppress it,
  because leaving it switched off is already that.
- Delete one after your panel has stopped publishing it and it stays gone, because nothing exists to recreate it from.

So deletion means "hide it until next time" for a live reading and "clear it out" for a dead one, and your panel decides which. A property your panel stops
publishing is left in place reading unknown rather than removed: silence on the wire does not distinguish a property that is gone from one that has not arrived
yet, and deleting your entity on a guess is not something an upgrade should do.

**These entities are permanent in id, not in identity.** If one of these readings is later curated properly, the curated entity is a new entity with its own id
and its own history — the adopted one is not renamed into it. That is the trade for surfacing a reading the moment it appears rather than waiting for a release
to model it, and it is why a vendor reading you have come to depend on is worth mentioning in an issue: curation is what turns it into something with a real
name, a proper category and statistics.

### Power Sensor Attributes

Applies to Current Power, Feed Through Power, Battery Power, PV Power, Grid Power, and Site Power sensors.

| Attribute  | Type   | Notes                                |
| ---------- | ------ | ------------------------------------ |
| `voltage`  | string | Nominal panel voltage ("240")        |
| `amperage` | string | Calculated current (power / voltage) |

**Grid Power** carries one more, because its name is only true in some wiring:

| Attribute             | Type    | Notes                                                                      |
| --------------------- | ------- | -------------------------------------------------------------------------- |
| `at_service_entrance` | boolean | Whether this panel's upstream lugs are where the utility actually connects |

Grid Power reads the upstream lugs. That is grid flow when those lugs are the utility connection point, which is the ordinary case. Put a battery between the
utility and your main lugs, or feed this panel from another panel, and the same reading becomes **this panel's** supply while **Grid Power Flow** stays the
whole-site figure — so the two legitimately disagree. When `at_service_entrance` is `false`, use Grid Power Flow for site-level grid import and export.

### Software Version Sensor Attributes

| Attribute    | Type | Notes                               |
| ------------ | ---- | ----------------------------------- |
| `panel_size` | int  | Total breaker spaces (e.g., 32, 40) |

`wifi_ssid` used to appear here. It moved to the Wi-Fi Link binary sensor below; a template reading it from this sensor should be pointed there.

### Wi-Fi Link Binary Sensor Attributes

| Attribute   | Type   | Notes                                                            |
| ----------- | ------ | ---------------------------------------------------------------- |
| `wifi_ssid` | string | Network this link is to. Absent when the panel publishes no SSID |

### EVSE (EV Charger) Entities

Created automatically when a SPAN Drive or other EVSE is commissioned on the panel. Each EVSE appears as a separate sub-device linked to the panel via
`via_device`. Vendor, product, serial number, and software version are surfaced as device info attributes — not separate entities.

#### EVSE Device Naming

The EVSE device name includes the panel device name prefix for collision avoidance across multi-panel installations and to support HA's bulk device rename
feature. A display suffix differentiates multiple chargers on the same panel:

- **Friendly names** (`USE_CIRCUIT_NUMBERS=False`): suffix is the fed circuit's panel name (e.g., "Garage")
- **Circuit numbers** (`USE_CIRCUIT_NUMBERS=True`): suffix is the EVSE serial number (e.g., "SN-EVSE-001")
- **No suffix available**: the display suffix is omitted entirely (no empty parentheses)

| Naming Mode     | Example Device Name                   | Example Entity ID                                         |
| --------------- | ------------------------------------- | --------------------------------------------------------- |
| Friendly names  | `Main House SPAN Drive (Garage)`      | `sensor.main_house_span_drive_garage_charger_status`      |
| Circuit numbers | `Main House SPAN Drive (SN-EVSE-001)` | `sensor.main_house_span_drive_sn_evse_001_charger_status` |
| No suffix       | `Main House SPAN Drive`               | `sensor.main_house_span_drive_charger_status`             |

The circuit that feeds a charger has its sensors shown on the charger's device. On new installations their entity IDs name the charger alone —
`sensor.main_house_span_drive_garage_power` — matching the charger's other sensors; installations that already have those sensors keep the IDs they have, which
name the panel.

#### EVSE Sensors (per charger)

| Sensor             | Device Class | Unit | Notes                                                                            |
| ------------------ | ------------ | ---- | -------------------------------------------------------------------------------- |
| Charger Status     | Enum         | —    | OCPP-based states: AVAILABLE, PREPARING, CHARGING, SUSPENDED_EV, etc. Translated |
| Advertised Current | Current      | A    | Amps offered to the vehicle                                                      |
| Lock State         | Enum         | —    | LOCKED, UNLOCKED, UNKNOWN. Translated                                            |
| Part Number        | —            | —    | Charger part number (diagnostic,**off by default**)                              |

#### EVSE Binary Sensors (per charger)

| Sensor          | Device Class     | Notes                                                                                            |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------ |
| Charging        | Battery Charging | ON when status is CHARGING                                                                       |
| EV Connected    | Plug             | ON when status is PREPARING, CHARGING, SUSPENDED\_\*, or FINISHING — a vehicle is plugged in     |
| EVSE Panel Link | Connectivity     | Whether the panel can reach the charger. A different fact from EV Connected, and it can disagree |

**EVSE Panel Link is not EV Connected.** EV Connected is what the charger says about the cable in front of it; EVSE Panel Link is what the panel says about
whether it can reach the charger at all. A charger part-way through a session behind a lost link reports a plugged-in vehicle and a dead link at the same time.
EVSE Panel Link is a diagnostic and appears only where the circuit feeding that charger publishes the link record.

#### EVSE Controls (per charger)

| Control                   | Platform | Unit | Notes                                                                                   |
| ------------------------- | -------- | ---- | --------------------------------------------------------------------------------------- |
| EVSE Charge Current Limit | Number   | A    | The charge-current ceiling you can lower. Bounded by the installer-commissioned maximum |

The maximum is read from the panel, never assumed: it is the current the charger was commissioned for, and a value above it is refused rather than clamped. The
control is created only where the panel declares the limit settable, and reports unavailable while the panel has not published the commissioned maximum that
bounds it. A change the panel has acknowledged but not yet applied appears as a `charge_current_limit_target` attribute while the state stays the limit the
charger is still enforcing.

#### EVSE Device Info Attributes

| Attribute        | Source             |
| ---------------- | ------------------ |
| Manufacturer     | `vendor-name`      |
| Model            | `product-name`     |
| Serial Number    | `serial-number`    |
| Software Version | `software-version` |

### BESS Sub-Device (conditional)

When a Battery Energy Storage System (BESS) is commissioned, the integration creates a separate BESS sub-device linked to the panel via `via_device`. The BESS
device uses manufacturer, model, serial number, and software version from battery metadata as device info attributes.

#### BESS Sensors

| Sensor              | Device Class   | Unit | Notes                                                                           |
| ------------------- | -------------- | ---- | ------------------------------------------------------------------------------- |
| Battery Level       | Battery        | %    | State of energy as percentage                                                   |
| Battery Power       | Power          | W    | Same entity as Power Flow Battery Power, shown on BESS sub-device               |
| Meter Power         | Power          | W    | The BESS's own meter (**+discharging, −charging**), agreeing with Battery Power |
| Communication State | —              | —    | The BESS's report of its own link health (diagnostic, disabled by default)      |
| BESS Vendor         | —              | —    | Battery system vendor (diagnostic)                                              |
| BESS Model          | —              | —    | Battery system model (diagnostic)                                               |
| BESS Part Number    | —              | —    | Battery system part number (diagnostic,**off by default**)                      |
| BESS Serial Number  | —              | —    | Battery system serial number (diagnostic)                                       |
| BESS Firmware       | —              | —    | Battery system firmware (diagnostic)                                            |
| Nameplate Capacity  | Energy Storage | kWh  | Rated battery capacity (diagnostic,**off by default**)                          |
| Stored Energy       | Energy Storage | kWh  | Current stored energy (diagnostic)                                              |

#### BESS Binary Sensors

| Sensor         | Device Class | Notes                                        |
| -------------- | ------------ | -------------------------------------------- |
| BESS Connected | Connectivity | Whether the BESS is communicating with panel |

### Panel Energy Sensor Attributes

Applies to Main Meter and Feed Through energy sensors.

| Attribute | Type   | Notes                         |
| --------- | ------ | ----------------------------- |
| `voltage` | string | Nominal panel voltage ("240") |

### Circuit-Level Sensors (per circuit)

| Sensor          | Device Class | Unit | Notes                                                                 |
| --------------- | ------------ | ---- | --------------------------------------------------------------------- |
| Power           | Power        | W    | Instantaneous circuit power (+producing for PV, +consuming otherwise) |
| Produced Energy | Energy       | Wh   | Cumulative energy produced                                            |
| Consumed Energy | Energy       | Wh   | Cumulative energy consumed                                            |
| Net Energy      | Energy       | Wh   | Net energy (sign depends on device type — PV circuits invert)         |
| Current         | Current      | A    | Measured circuit current. Only when panel reports`current_a`          |
| Breaker Rating  | Current      | A    | Circuit breaker amperage (diagnostic). Only when reported             |

### Circuit Power Sensor Attributes

| Attribute         | Type   | Notes                                                                                                           |
| ----------------- | ------ | --------------------------------------------------------------------------------------------------------------- |
| `tabs`            | string | Breaker slot position(s)                                                                                        |
| `voltage`         | string | 120 or 240 (derived from tab count)                                                                             |
| `always_on`       | bool   | Whether circuit is always-on                                                                                    |
| `relay_state`     | string | OPEN / CLOSED / UNKNOWN                                                                                         |
| `relay_requester` | string | Who requested relay state                                                                                       |
| `shed_priority`   | string | API value: NEVER / SOC_THRESHOLD / OFF_GRID / UNKNOWN                                                           |
| `is_sheddable`    | bool   | Whether circuit can be shed                                                                                     |
| `pcs_managed`     | bool   | Whether the panel's Power Control System manages this circuit. Present only when the circuit reports it         |
| `pcs_priority`    | int    | This circuit's shed order under an active import limit — distinct from`shed_priority`, which is the backup tier |

### Circuit Energy Sensor Attributes

| Attribute | Type   | Notes                               |
| --------- | ------ | ----------------------------------- |
| `tabs`    | string | Breaker slot position(s)            |
| `voltage` | string | 120 or 240 (derived from tab count) |

### Binary Sensors

| Sensor          | Device Class | Notes                                                                                       |
| --------------- | ------------ | ------------------------------------------------------------------------------------------- |
| Door State      | Tamper       | Panel door open/closed                                                                      |
| Ethernet Link   | Connectivity | Wired network status                                                                        |
| Wi-Fi Link      | Connectivity | Wireless network status                                                                     |
| Panel Status    | Connectivity | Overall panel online/offline                                                                |
| Grid Islandable | —            | Whether the panel can island from the grid. Off on a panel with no MID — see below          |
| PCS Active      | Running      | Whether the Power Control System is limiting import right now. Only when the panel runs one |
| PV Panel Link   | Connectivity | Whether the panel can reach the solar inverter. Only when the feeding circuit reports it    |

**Removed from binary sensors:**

| Sensor          | Reason                                                |
| --------------- | ----------------------------------------------------- |
| Cellular (wwan) | Replaced by`Vendor Cloud` sensor (cloud connectivity) |

### Circuit Controls (per user-controllable circuit)

| Entity           | Type   | Notes                                                                     |
| ---------------- | ------ | ------------------------------------------------------------------------- |
| Breaker          | Switch | On/off relay control                                                      |
| Circuit Priority | Select | Controls when the circuit is shed during off-grid (translated, see below) |

### Panel Controls

| Entity                       | Type   | Notes                                                             |
| ---------------------------- | ------ | ----------------------------------------------------------------- |
| GFE Override: Grid Connected | Button | Tell the panel the grid is up when BESS communication interrupted |

### BESS & Grid Management

This section explains how the SPAN panel manages power sources and load shedding when a Battery Energy Storage System (BESS) is installed, and what the
integration can and cannot tell you about grid status.

#### Grid Forming Entity

The Grid Forming Entity (GFE) sensor identifies which power source provides the voltage and frequency reference for the home — not which source is producing the
most watts. When GFE is Grid, the utility grid sets the reference and all circuits remain on, even if 100% of consumption comes from solar. When GFE is Battery,
the battery inverter is the reference and circuits are shed based on each circuit's configured shed priority.

| GFE Value | Meaning                                                           |
| --------- | ----------------------------------------------------------------- |
| GRID      | Panel is grid-connected (includes generator power, see deep dive) |
| BATTERY   | Panel is islanded, running on battery                             |
| PV        | Panel is islanded, running on solar (future)                      |
| GENERATOR | Panel is islanded, running on generator (future)                  |
| NONE      | Panel is islanded with no power source                            |
| UNKNOWN   | State not yet determined or fault condition                       |

When a BESS is installed, the panel relies on the BESS to determine whether the grid is online and to set the GFE accordingly. If BESS communication is lost
while the panel is islanded, the GFE value becomes stale — it may show Battery when the grid has actually been restored, causing unnecessary shedding to
continue.

**On a panel with no battery, GFE is Grid, on both firmware generations.** The newer firmware moves this value onto the Microgrid Interconnect Device, which is
part of a battery system — so a panel without one has nothing publishing it. The answer is still settled, by what cannot be there: Battery needs a BESS (which
brings a MID), PV cannot form a grid on its own (anything that can is a grid-forming inverter, which is a MID), and None describes a panel supplying nothing,
which is a panel that is not reporting at all. What remains is a generator, and SPAN with no generator interface treats one as the grid. So Grid is what a
battery-less panel reports, which is what the older firmware reported too.

#### What the Panel Can Detect

**Grid loss** — The panel independently detects grid loss via its own voltage monitoring, even if BESS communication is already lost. The MID is still closed at
this point, so the panel's sensors see the real voltage drop and respond immediately.

**Grid restoration while islanded** — Not detectable by the panel. While the MID is open, the panel's sensors are on the home side and measure only
battery-supplied power. Grid restoration on the utility side of the open MID is invisible to any panel-side measurement. This is a physical limitation, not a
software gap. A utility-side sensor — such as a current clamp (e.g., Emporia Vue), ATS/MTS contact closure, or any device that can see the grid side of the MID
— integrated into Home Assistant as a binary sensor can provide this signal.

#### DSM State Sensor

The integration's `DSM State` sensor combines multiple panel signals to provide defense-in-depth for grid status detection. It corroborates the Grid Forming
Entity with BESS grid state and power measurements, which adds confidence during transient inconsistencies and detects some edge cases — for example, when BESS
communication is lost while on-grid and the grid subsequently drops, the panel self-corrects via voltage detection and the corroborating signals confirm it.

However, when the panel is islanded and the MID is open, all of the panel's signals measure the home side. No combination of panel-sourced data can detect grid
restoration in this state. Only an external signal (utility-side sensor) or manual confirmation via the GFE Override button can resolve it.

#### GFE Override Button

The **GFE Override: Grid Connected** button tells the panel that the grid is back and shedding can stop. When the BESS restores communication, it automatically
reclaims control and the override is superseded — no manual undo is needed.

**Risk asymmetry** — Telling the panel to shed (conservative direction) is low-risk; worst case is unnecessary circuit disruption. Telling the panel the grid is
back when it is not means unmanaged battery drain and reduced runtime, which could affect critical equipment. The battery protects itself by disconnecting when
depleted, so there is no overload risk, but runtime will be reduced. Use the override button only with confidence that the grid has actually been restored — via
a utility-side sensor or manual confirmation.

**WARNING** — Do _not_ automate the GFE override button based on `DSM State` — it inherits the same MID blind spot described above and will read `dsm_off_grid`
even after the grid is restored. Manual confirmation or an external sensor is required before pressing the button.

When `bess_connected` returns to on, no action is needed — firmware resumes normal GFE management automatically.

For a detailed discussion of failure scenarios, the MID topology, generator and non-integrated BESS behavior, and `/set` risk analysis, see
[BESS & Grid Management Deep Dive](bess-grid-management.md).

## Configuration Options

### Snapshot Update Interval

Controls how often the integration rebuilds the panel snapshot from incoming MQTT data. The SPAN panel publishes high-frequency MQTT messages (~100/second), but
each individual message is a cheap dictionary write. The expensive operation — rebuilding the full snapshot and dispatching entity updates — is rate-limited by
this timer.

- **Default:** 1 second
- **Range:** 0–15 seconds
- **Set to 0** for no debounce (every MQTT message triggers a snapshot rebuild)
- **Increase on low-power hardware** (e.g., Raspberry Pi) to reduce CPU usage

Configure via `Settings` > `Devices & Services` > `SPAN Panel` > `Configure` > `General Options`.

### Entity Naming Pattern

The integration provides flexible entity naming patterns, configured during initial setup:

1. **Friendly Names** (Recommended for new installations)

   - Entity IDs use descriptive circuit names from your SPAN panel
   - Example: `sensor.span_panel_kitchen_outlets_power`
   - Renaming a circuit in the SPAN app updates the displayed name automatically; the entity ID changes only if you accept the offer from **Recreate entity
     IDs**
   - More intuitive for automations and scripts

2. **Circuit Numbers** (Stable entity IDs)

   - Entity IDs use generic circuit numbers
   - Example: `sensor.span_panel_circuit_15_power`
   - Entity IDs stay stable when circuits are renamed
   - Friendly names still sync from SPAN panel for display

The integration supplies only the circuit half of the ID shown above — `Kitchen Outlets Power` or `Circuit 15 Power`. Home Assistant composes the rest from your
own entity ID settings (`Settings` > `System` > `General`, Home Assistant 2026.8 and newer), which decide whether the device name and the area are prefixed;
entities you already have keep the IDs they have until you press **Recreate entity IDs**.

### Energy Dip Compensation

SPAN panels occasionally report lower energy readings for cumulative energy sensors after firmware updates or resets. Home Assistant's statistics engine
interprets any decrease as a counter reset, creating negative spikes in the energy dashboard.

When enabled, the integration automatically detects these dips and maintains a cumulative offset per sensor so Home Assistant always sees a monotonically
increasing value.

- **Default for new installs:** ON
- **Default for existing installs:** OFF (enable via General Options)
- **Threshold:** 1.0 Wh minimum to avoid false triggers from float precision noise
- **Disabling:** Clears all accumulated offsets (starts fresh if re-enabled)

A dip is compensated as soon as it is seen, but not believed straight away. A counter reset is permanent — the counter restarts low and counts up from there —
so a reading that drops and then returns to where it was is a transport artifact rather than a reset, and its offset is taken back. The offset stays provisional
until a later reading either disproves it (the counter comes back) or corroborates it (the counter climbs from the new, lower base).

**The persistent notification therefore lists a dip once it is corroborated, one reading after it is seen, and a dip that is disproved produces no notification
at all** — the sensor was compensated the whole time and nothing needs your attention. Seeing no notification after a momentary dip is the feature working, not
failing.

**Diagnostic attributes** (visible when compensation is active):

| Attribute        | Description                                   |
| ---------------- | --------------------------------------------- |
| `energy_offset`  | Cumulative Wh compensation applied (when > 0) |
| `last_dip_delta` | Size of the most recent dip in Wh             |

Configure via `Settings` > `Devices & Services` > `SPAN Panel` > `Configure` > `General Options`.

### Customizing Entity Precision

The power sensors report with the exact precision from the SPAN panel, which may be more decimal places than you need. By default, sensors display with
precision 2 (e.g., `0.00`), except battery percentage which uses precision 0 (e.g., `39`).

You can change the display precision for any entity via `Settings` > `Devices & Services` > `Entities` tab. Find the entity, click on it, click the gear wheel,
and select your preferred precision from the "Display Precision" menu.

## Security

### What the integration stores

Setting up a v2 panel exchanges your panel passphrase for two long-lived secrets, which are kept in the config entry (`.storage/core.config_entries`):

- the **eBus MQTT broker password**, which grants control of every relay, circuit priority, islanding state and EVSE limit on the panel and its sub-devices, and
- the **REST access token**, which grants panel management — client registration, FQDN registration and credential rotation.

The **panel passphrase itself is no longer stored.** It is an input to registration and nothing afterwards reads it, but a stored copy could mint fresh
credentials at any time. Upgrading migrates the config entry to version 7 and removes it; nothing else in the entry changes and no entity is affected. If you
have not yet upgraded, reauthenticating also removes it.

> **Downgrade note.** Once an entry has reached version 7, installing an older build of the integration will fail to set it up — Home Assistant refuses to load
> a config entry whose version is newer than the installed integration supports. Restore a backup taken before the upgrade if you need to roll back.

These are the limits of what the integration can enforce. Anything that already holds the broker password — including another integration running in the same
Home Assistant process — talks to the panel directly and is not subject to Home Assistant's permission model at all.

### The panel's certificate authority

The panel issues its own certificate authority and signs its TLS certificate with it. **Before you are asked for your passphrase**, setup fetches that
authority, checks that the certificate the panel actually serves is signed by it, and pins it. Everything after that — registration itself, and every later
connection — runs over that authority, and the integration stops rather than accepting a different one.

The ordering is the point. Registration is the exchange that sends your passphrase and returns both the access token and the broker password, so it is the
single most valuable message on your network. It now travels over a verified connection instead of in the clear.

**What that does and does not buy you.** The authority is fetched over your local network on a connection that has nothing to verify itself against — it is the
anchor everything else is checked against. So:

- Anyone merely **listening** on your network can no longer read your passphrase or the credentials the panel returns for it. That is the common case, and it is
  closed.
- A device **actively standing between** Home Assistant and your panel at that first fetch could answer with an authority of its own, sign a certificate with
  it, and still see them. Pinning cannot detect that on its own.

Comparing the fingerprint against another source is what closes the second case. Setup does not stop to ask you to do that, because at first contact there is
nothing to compare against: SPAN does not publish the value, so the question could only be answered by pressing Submit. The fingerprint is put where it can
actually be used instead — diagnostics report it under `panel_ca`, it is logged at setup, and another install of this integration on the same panel reports the
same value.

After the first pin, nothing can change the authority without stopping and asking you — and _that_ is a question you can answer, because there is a prior value
to compare against. See the certificate-authority-changed entry in Troubleshooting.

**There is no "continue without pinning" for a new panel.** If the authority cannot be read, or the certificate the panel serves is not signed by it, setup
shows an error and stops. Submitting the form again retries the fetch, which is what a panel that was briefly unreachable needs. An opt-out would quietly
restore the plaintext credential exchange this ordering exists to remove, at the moment you are least likely to weigh it.

**Panels configured before this release are pinned differently, and are not risk-free until they are.** They are pinned on the first startup that reaches the
panel, logged at `WARNING` with the fingerprint so you can find the value afterwards. Until that succeeds, the connection to the broker still authenticates with
your panel's password while the authority is re-fetched over plaintext HTTP on every connection and whatever answers is trusted — the substitution pinning
exists to close.

If the panel is unreachable the integration starts anyway and retries on the next startup. That is deliberate. The exposure in the meantime is exactly the one
the entry already had before this release, and refusing to start would not remove it — it would only remove the integration, leaving the credential no safer
while guaranteeing an outage. Retrying closes it at the first opportunity instead.

Diagnostics report the fingerprint under `panel_ca`. The certificate itself is not included — it is public, but multi-KB, and the fingerprint is the part worth
reading.

If your panel serves TLS somewhere other than port 443 — behind a reverse proxy, say — the setup flow asks for the port, but only when you have already changed
the HTTP port from 80.

### Restricting who can operate the panel

Four options in **Settings → Devices & Services → Span Panel → Configure**. **Every one defaults to the behaviour your panel already has**, so upgrading changes
nothing until you choose otherwise.

| Option                                     | What it does                                                                                                                                                                                  | What it does not do                                                                                                        |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Who may operate the panel**              | `Administrators only` refuses circuit switches, priority selects, the GFE override, EVSE limits and adopted controls from non-admin users. `Nobody` stops creating those entities altogether. | Neither affects sensors, and neither constrains anything holding the broker password.                                      |
| **Allow control without a logged-in user** | Turning it off refuses commands from automations, scripts and other integrations, which arrive with no user attached.                                                                         | It cannot tell a well-behaved automation from a runaway one — only that neither has a user.                                |
| **Control lock auto-relock**               | Adds a switch that, while armed, refuses every control command. Anyone can arm it; only an administrator can disarm it, and never an automation.                                              | It is not a password. It defends against misclicks and runaway automations, which is what a local control can actually do. |
| **Relay debounce**                         | Refuses a second command to the same circuit's relay within the window.                                                                                                                       | It is per circuit, so an automation cycling many circuits still gets through.                                              |

**Why `Administrators only` is worth setting.** Home Assistant's default user policy grants every non-admin user control of every entity. Until you change this,
a dashboard-only household member can open any breaker in the house.

**What none of this defends against.** These options constrain callers arriving through Home Assistant. They do not constrain anything that already holds the
broker credential — a second Home Assistant instance, a script you wrote, or a malicious custom integration running inside this same Home Assistant process,
which reads the credential straight out of memory. For that, see [Recommended deployment](#recommended-deployment) below; network topology and a locked
enclosure are the real boundary.

**Nothing is deleted when you choose `Nobody`.** The control entities stop being created and read as unavailable; their registry entries, names, areas and
customizations are kept, so turning the option back on restores exactly the entities you had. Dashboards and automations referencing them will show them
unavailable in the meantime — including the shipped SPAN Panel card, whose toggles call `switch.turn_on` and `select.select_option` directly and have no
card-side message for a refusal. A non-admin using that card under `Administrators only` gets a refusal with no explanation on the card.

### The record of what was commanded

Every control command fires a `span_panel_control_command` event and appears in the logbook, whether it succeeded, was refused, or never reached the panel. When
there is no user — an automation — the originating automation or script is named instead, so an unattended write is attributed to _what_ rather than left blank.
Every command is also logged at `INFO`.

Commands report one of four outcomes, and the distinctions matter:

| Outcome       | Meaning                                                                                  |
| ------------- | ---------------------------------------------------------------------------------------- |
| `confirmed`   | The panel reported the value you asked for.                                              |
| `accepted`    | The broker acknowledged the message and the panel did not report a change.               |
| `unconfirmed` | Nothing came back within the deadline. **Not an error** — see the troubleshooting entry. |
| `failed`      | The command was never handed to the broker and will not be delivered.                    |
| `refused:…`   | This integration refused it, for the named reason.                                       |

### Rotating panel credentials

The `span_panel.rotate_credentials` action asks the panel for a new eBus MQTT broker password, stores it, and reloads the integration.

```yaml
action: span_panel.rotate_credentials
data: {}
```

Run it after a contractor visit, a suspected credential exposure, or any event that put someone else in front of the panel.

**Know the blast radius before you run it.** The previous broker password stops working the moment the panel issues the new one, so every other local client
using it — a second Home Assistant instance, a script, third-party tooling — must be re-provisioned from the panel before it will reconnect. This integration
re-provisions itself automatically; nothing else does. The panel access token and the panel passphrase are not changed.

Only a Home Assistant administrator can run it, and it cannot be called from an automation or script: a call arriving without a logged-in user is refused
outright. If the panel rejects the stored access token, reauthenticate the integration first — the existing credentials are left untouched on every failure
path.

### Recommended deployment

The panel credential is a single all-or-nothing secret, and anyone standing at the panel can mint a fresh one with three presses of the door switch. Network
topology and physical control of the panel are the real boundary; everything above is defense in depth behind it.

- **Put the panel on its own VLAN**, with default-deny between VLANs, and allow only the Home Assistant host to reach it. The integration currently uses
  `tcp/80` for the REST bootstrap and `tcp/8883` for MQTTS. Deny `tcp/9001` and `tcp/9002` (plaintext and WebSocket MQTT) from every source unless you are
  actively using the SPAN Home on-premise UI.
- **Use the IP address or an FQDN, not the `.local` name.** mDNS does not cross VLAN boundaries. The panel's IP is in its certificate SAN, so hostname
  verification still works; for an FQDN, the integration registers it with the panel so the panel adds it to the SAN.
- **If Home Assistant cannot be on the panel's VLAN**, put a reverse proxy on the panel's VLAN and restrict its inbound to the Home Assistant host. The proxy
  holds no panel credential; it only relays. Note that MQTTS is a TCP stream, not HTTP — a plain HTTP reverse proxy covers the REST port only, and `tcp/8883`
  needs a stream proxy (HAProxy, nginx `stream`, or Caddy's `layer4` plugin).
- **Lock the panel enclosure.** The three-press proximity bypass hands out full credentials to anyone who can open the door; it is the equivalent of a printed
  root password. This outranks every software control on this page.
- **Put dashboard-only household members in Home Assistant's read-only group.** Home Assistant's default user policy grants every non-admin user control of
  every entity, which includes this integration's circuit switches and priority selects.
- **Encrypt your Home Assistant backups.** An unencrypted backup contains `.storage/core.config_entries`, and the panel credentials in it are in plaintext.
- **Enable multi-factor authentication on Home Assistant** and do not expose its API directly to the Internet. A compromised Home Assistant administrator
  account is a compromised panel.

## WebSocket API

The integration provides a `span_panel/panel_topology` WebSocket command that returns the full physical layout of a panel in a single call — circuits with their
breaker slot positions, entity IDs grouped by role, and sub-devices (BESS, EVSE) with their entities.

See [WebSocket API Reference](websocket-api.md) for the full schema, response format, and usage examples.

## Troubleshooting

| Issue                                                                        | Symptoms                                                                                                                                                                                                                                                                                                                                                                                                                            | Resolution                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Energy Dashboard spikes after firmware updates**                           | Huge energy-consumption spikes after panel firmware updates; charts showing untracked values that dwarf normal usage; negative energy values in statistics. Caused by the panel reporting decreased values on otherwise`TOTAL_INCREASING` sensors.                                                                                                                                                                                  | **Prevention:** enable [Energy Dip Compensation](#energy-dip-compensation) in General Options (on by default for new installs). **Fix existing spikes:** in **Developer Tools → Statistics**, search for the affected sensor (e.g. `sensor.span_panel_main_meter_consumed_energy`) and use **Adjust sum** to correct the errant entry. The integration also notifies when a decrease in the main meter consumed sensor is detected. **If you enabled compensation before 2.1.0 and saw spikes at restart:** check `energy_offset` on your energy sensors in **Developer Tools → States** — an offset far larger than the sensor's own reading is compensation for a dip that never happened, and toggling Energy Dip Compensation off discards it. Offsets booked before 2.1.0 were never corroborated, so the integration cannot tell a good one from a bad one retrospectively; the spikes already in statistics still need **Adjust sum** either way. |
| **High CPU usage**                                                           | Elevated CPU on low-power hardware (e.g. Raspberry Pi). The integration rebuilds a full panel snapshot from MQTT messages at a configurable interval (default 1 s).                                                                                                                                                                                                                                                                 | Increase**Snapshot Update Interval** in **General Options**. 10–15 s is recommended for resource-constrained systems. Setting it to 0 disables debouncing and rebuilds on every MQTT message — not recommended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Replaced sub-device shows the old serial number**                          | After replacing a SPAN sub-device (Drive / EVSE, BESS, PV inverter), the device entry in Home Assistant keeps showing the previous hardware's serial number. The integration keys entities off the panel-assigned node identity, which is intentionally stable across hardware swaps so long-term history (e.g. lifetime charging kWh for a Drive) is preserved. The device-registry serial number, however, does not auto-refresh. | In**Settings → Devices & Services → Span Panel**, open the affected sub-device and delete it, then reload the integration (or restart Home Assistant). The device re-registers with the new serial number. Entity IDs and their recorded history are preserved.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Door sensor unavailable**                                                  | The SPAN API returns UNKNOWN if the cabinet door has not been operated recently. This is a defect in the SPAN API.                                                                                                                                                                                                                                                                                                                  | The integration reports the sensor as unavailable until a proper value arrives. Opening or closing the door publishes the correct state. The door is classified as a tamper sensor (`Detected` / `Clear`) to differentiate it from a normal entry door.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **No switch on a circuit**                                                   | A circuit has no switch entity exposed in Home Assistant.                                                                                                                                                                                                                                                                                                                                                                           | The circuit is configured in the SPAN App as one of the "Always on Circuits". The API does not permit user control of those circuits, so no switch is created.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Reinstalling to change the entity ID style gives back the old entity IDs** | The naming style is chosen at install and cannot be changed from the options, so reinstalling looks like the way to switch. It is not: every entity returns with the entity ID it had before.                                                                                                                                                                                                                                       | Home Assistant remembers a removed entity for **30 days**, keyed on its unique ID, and restores that record's entity ID — along with its name, area, labels and icon — as soon as an entity with the same unique ID appears again. This integration's unique IDs do not change with the naming style, so the remembered ID wins over the one the new style asks for. Either clear the leftover registry entries between removing and reinstalling, or wait out the 30 days and let Home Assistant discard them. A tool such as [ha-registry-clean](https://github.com/LegoTypes/ha-registry-clean) can do the clearing; it is a separate project, not part of this integration. Clearing also discards the names, areas and labels you had assigned.                                                                                                                                                                                                     |
| **Setup fails after downgrading the integration**                            | After installing an older release, the SPAN Panel config entry fails to set up and Home Assistant reports an unsupported configuration version.                                                                                                                                                                                                                                                                                     | The release that stopped storing the panel passphrase migrated the config entry to version 7. Home Assistant refuses to load a config entry whose version is newer than the installed integration understands, and there is no automatic downgrade. Reinstall the newer release, or restore a backup taken before the upgrade. Removing and re-adding the integration also works and preserves entity IDs, but needs the panel passphrase or physical access to the door again.                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **"SPAN Panel certificate authority changed" repair**                        | The integration has stopped connecting and a repair reports two fingerprints: the one it pinned and the one the panel now advertises. Entities are unavailable.                                                                                                                                                                                                                                                                     | The panel is presenting a different certificate authority than the one you accepted at setup. Two things look identical from here and only you can tell them apart: a firmware upgrade or a factory reset rotates the authority legitimately, and so does a device on your network standing in for your panel. **If you know why it changed**, open the repair, compare the new fingerprint, and accept it — that re-pins and reconnects. **If nothing should have changed**, do not accept. Check what else is on the panel's network segment first. The integration will not reconnect on its own and will not re-pin on its own, deliberately: retrying would mean waiting to succeed against whatever is answering.                                                                                                                                                                                                                                  |
| **A circuit re-commissioned in the SPAN App has the wrong controls**         | A circuit whose configuration changed in the SPAN App — made controllable, locked, or set to never back up — still has the controls it had before. A newly controllable circuit has no Breaker switch. A newly locked one still shows its switch, and operating it reports that the command was refused.                                                                                                                            | Reload the integration (**Settings → Devices & Services → Span Panel → ⋮ → Reload**). Which control entities exist is decided when the integration starts, from what the panel declares about each circuit at that moment; a change made afterwards is picked up at the next reload or restart. Reloading is safe — entity IDs, history, names and areas are preserved, because they are keyed on identifiers that do not change. Until you reload, a control the panel no longer accepts is refused rather than published: the command is not queued and the breaker does not move.                                                                                                                                                                                                                                                                                                                                                                     |
| **A control reports `unconfirmed`**                                          | The logbook or the `span_panel_control_command` event says a command was `unconfirmed`. Nothing appears broken.                                                                                                                                                                                                                                                                                                                     | **This is not an error.** It means the panel took the command and did not report a change within the deadline, and the most common reason by far is that there was no change to report — the relay was already open, the priority was already that value. It is also indistinguishable from a silent rejection by the panel, because SPAN's firmware sends no reason code; the integration reports what it observed rather than guessing. The two outcomes that do mean something went wrong are `failed`, which means the command was never sent, and `refused:…`, which means this integration refused it and names why.                                                                                                                                                                                                                                                                                                                               |

## Development

See [Developer Documentation](developer.md) for setup instructions, prerequisites, and tooling.

## License

This integration is published under the MIT license.

## Attribution and Contributions

This repository is set up as part of an organization so a single committer is not the weak link. The repository is a fork in a long line of SPAN forks that may
or may not be stable (from newer to older):

- SpanPanel/span (current GitHub organization, current repository, currently listed in HACS)
- SpanPanel/Span (was moved to [SpanPanel/SpanCustom](https://github.com/SpanPanel/SpanCustom))
- cayossarian/span
- haext/span
- gdgib/span
- thetoothpick/span-hacs
- wez/span-hacs
- galak/span-hacs

Additional contributors:

- pavandave
- sargonas
- NickBorgersOnLowSecurityNode

## Issues

If you have a problem with the integration, feel free to [open an issue](https://github.com/SpanPanel/span/issues), but please know that issues regarding your
network, SPAN configuration, or home electrical system are outside of our purview.

For those motivated, please consider offering suggestions for improvement in the discussions or opening a
[pull request](https://github.com/SpanPanel/span/pulls). We're generally very happy to have a starting point when making a change.
