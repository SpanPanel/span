# SPAN Panel Integration for Home Assistant

[Home Assistant](https://www.home-assistant.io/) Integration for [SPAN Panel](https://www.span.io/panel), a smart electrical panel that provides circuit-level
monitoring and control of your home's electrical system.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub Release](https://img.shields.io/github/release/SpanPanel/span.svg?style=flat-square)](https://github.com/SpanPanel/span/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/SpanPanel/span.svg?style=flat-square)](https://github.com/SpanPanel/span/commits)
[![License](https://img.shields.io/github/license/SpanPanel/span.svg?style=flat-square)](LICENSE)

[![Python](https://img.shields.io/badge/python-3.14.3%2B-blue.svg)](https://www.python.org/downloads/)
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

The [SPAN Panel PanelBench](https://github.com/SpanPanel/panelbench) HA App lets you clone your panel's circuit layout for testing, or model an upgrade to
evaluate firmware or integration changes in a sandbox before applying them to your real panel.

This integration communicates with the SPAN Panel over your local network using SPAN's official
[Electrification Bus (eBus)](https://github.com/spanio/SPAN-API-Client-Docs) framework — an open, multi-vendor integration standard for home energy
infrastructure. eBus uses the [Homie Convention](https://homieiot.github.io/) for MQTT topics and messages, with the panel's built-in MQTT broker delivering
real-time state updates without polling.

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) 2026.11 or later
- [HACS](https://hacs.xyz/) installed
- SPAN Panel firmware newer than spanos2 (see below)
- Panel passphrase (found via the SPAN app) **or** physical access to the panel door; a panel that registers by passphrase only needs the passphrase

**Panels on spanos2 firmware, which publishes the older flat eBus schema, are not supported.** Adding one is refused with a message to update its firmware, and
discovery does not offer it. A configured panel on spanos2 firmware does not load: Home Assistant shows a repair asking for a firmware update and keeps retrying
quietly in the background. Once the panel's firmware is updated, it reconnects on its own, unless the update also changed the panel's certificate authority:
that raises a second repair, which needs you to act (see [Troubleshooting](#troubleshooting)).

## Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS in the left side bar of your Home Assistant installation
3. Search for "Span"
4. Open the repository and click "Download"
5. Restart Home Assistant (you will be prompted by a repair notification)
6. Go to `Settings` > `Devices & Services`
7. Click `+ Add Integration` and search for "Span"
8. Pick your panel from the discovered list, or enter its IP address or `.local` name (both shown in the mobile app)
9. The integration detects the panel as v2 and presents an authentication choice:
   - **Enter Panel Passphrase** — type the passphrase found in the SPAN mobile app under On-premise settings
   - **Proof of Proximity** — open and close the panel door 3 times, then click Submit

   A panel that registers by passphrase only skips the choice and asks for the passphrase. If the panel is limiting registration attempts, setup says how long
   to wait before trying again.

10. Choose your entity ID naming pattern (see naming patterns below)
11. Optionally adjust the snapshot update interval — 0 is real-time, up to 15 seconds based on CPU

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

| Sensor                       | Device Class | Unit | Notes                                                                                                                          |
| ---------------------------- | ------------ | ---- | ------------------------------------------------------------------------------------------------------------------------------ |
| Current Power                | Power        | W    | Total panel power (grid import/export)                                                                                         |
| Feed Through Power           | Power        | W    | Feedthrough (non-breaker) power. Off by default                                                                                |
| Main Meter Produced Energy   | Energy       | Wh   | Grid energy exported                                                                                                           |
| Main Meter Consumed Energy   | Energy       | Wh   | Grid energy imported                                                                                                           |
| Main Meter Net Energy        | Energy       | Wh   | Consumed minus produced. Off by default                                                                                        |
| Feed Through Produced Energy | Energy       | Wh   | Feedthrough energy exported. Off by default                                                                                    |
| Feed Through Consumed Energy | Energy       | Wh   | Feedthrough energy imported. Off by default                                                                                    |
| Feed Through Net Energy      | Energy       | Wh   | Feedthrough net energy. Off by default                                                                                         |
| DSM State                    | —            | —    | dsm_on_grid (grid connected), dsm_off_grid (islanded), unknown. Derived from multiple eBus signals. Diagnostic, off by default |
| Current Run Config           | —            | —    | panel_on_grid (grid connected), panel_off_grid (islanded on PV/generator), panel_backup (islanded on battery), unknown         |
| Grid Forming Entity          | —            | —    | GRID, BATTERY, PV, GENERATOR, NONE, UNKNOWN. See [Grid Forming Entity](#grid-forming-entity)                                   |
| Main Relay State             | —            | —    | closed (power flowing), open (disconnected), unknown                                                                           |
| Vendor Cloud                 | —            | —    | CONNECTED, UNCONNECTED, UNKNOWN. Diagnostic, off by default                                                                    |
| Software Version             | —            | —    | Firmware version string                                                                                                        |
| Frequency                    | Frequency    | Hz   | Line frequency. Only where the panel publishes it                                                                              |

### Panel Diagnostic Sensors

| Sensor                     | Device Class | Unit | Notes                                                                                                  |
| -------------------------- | ------------ | ---- | ------------------------------------------------------------------------------------------------------ |
| L1 Voltage                 | Voltage      | V    | L1 leg actual voltage                                                                                  |
| L2 Voltage                 | Voltage      | V    | L2 leg actual voltage                                                                                  |
| Upstream L1 Current        | Current      | A    | Upstream lugs L1 current. Off by default                                                               |
| Upstream L2 Current        | Current      | A    | Upstream lugs L2 current. Off by default                                                               |
| Downstream L1 Current      | Current      | A    | Downstream lugs L1 current. Off by default — see below                                                 |
| Downstream L2 Current      | Current      | A    | Downstream lugs L2 current. Off by default — see below                                                 |
| Main Breaker Rating        | —            | A    | Main breaker amperage. Off by default                                                                  |
| Busbar Current             | Current      | A    | Busbar current. Only where the panel publishes it                                                      |
| Upstream Protection Rating | —            | A    | Rating of the protection ahead of the upstream lugs. Off by default. Only where the panel publishes it |

L1/L2 Voltage and Main Breaker Rating are off by default; enable them from the panel's device page if you want them.

On a panel with no main breaker, current monitoring judges the mains against the Upstream Protection Rating.

**The three Feedthrough sensors and the two Downstream current sensors are off by default.** The eBus specification's maintainer has documented that the panel's
feedthrough (downstream lugs) figures cannot be relied on: the energy registers can decrease or go negative, the power reading is inverted relative to every
other terminal, and the downstream currents report the **upstream** service conductors.

### Shed Forecast Sensors

Created only when your panel publishes the `shed-forecast` capability, and only for the estimates it actually publishes.

| Sensor                | Device Class | Unit | Notes                                                            |
| --------------------- | ------------ | ---- | ---------------------------------------------------------------- |
| Time to Priority Shed | Duration     | min  | Estimated time before the next priority tier of circuits is shed |
| Backup Time Remaining | Duration     | min  | Estimated time before every sheddable circuit is shed (off-grid) |

#### Shed Forecast Sensor Attributes

Present only when the panel publishes them.

| Attribute                           | Type   | On                    | Notes                                                  |
| ----------------------------------- | ------ | --------------------- | ------------------------------------------------------ |
| `full_charge_time_to_priority_shed` | int    | Time to Priority Shed | The same estimate assuming the battery starts full     |
| `full_charge_total_time_remaining`  | int    | Backup Time Remaining | The same estimate assuming the battery starts full     |
| `forecast_confidence`               | string | both                  | The panel's own assessment: `LOW`, `MEDIUM`, or `HIGH` |

### Power Control System Sensors

Created only when your panel publishes the `pcs` capability, and created whether or not the PCS is switched on — a PCS reporting a limit of 0 A is reporting a
state, not an absence.

| Sensor             | Device Class | Unit | Notes                                                                                                                      |
| ------------------ | ------------ | ---- | -------------------------------------------------------------------------------------------------------------------------- |
| Import Limit       | Current      | A    | The limit actually being enforced: the most restrictive active constraint (diagnostic)                                     |
| Binding Constraint | Enum         | —    | Which constraint sets that limit: Firm Service Rating, Grid Envelope, Voltage Support, Off-Grid, Requested, Operator, None |

#### Power Control System Sensor Attributes

On **Import Limit**, and present only when the panel publishes them. These are the inputs the panel reconciled to produce the enforced limit above.

| Attribute                | Type   | Notes                                                           |
| ------------------------ | ------ | --------------------------------------------------------------- |
| `pcs_enabled`            | bool   | Whether the panel's PCS is enabled at all                       |
| `feed_import_limit`      | float  | The Firm Service Rating: the commissioned, always-on floor (A)  |
| `operator_import_limit`  | float  | A cap imposed by a fleet or aggregator operator (A)             |
| `off_grid_import_limit`  | float  | The import cap while islanded (A)                               |
| `requested_import_limit` | float  | A voluntary limit requested by the owner or installer (A)       |
| `<name>_enablement`      | string | Per limit: `UNSPECIFIED`, `UNCONFIGURED`, `DISABLED`, `ENABLED` |
| `<name>_active`          | bool   | Per limit: whether that constraint is currently enforcing       |

### Power Flow Sensors

| Sensor        | Device Class | Unit | Notes                                                                                  |
| ------------- | ------------ | ---- | -------------------------------------------------------------------------------------- |
| Grid Power    | Power        | W    | Grid power flow                                                                        |
| Site Power    | Power        | W    | Total site power (grid + PV + battery)                                                 |
| Battery Power | Power        | W    | Battery charge/discharge (**+discharging, −charging**). Only when the panel has a BESS |
| PV Power      | Power        | W    | PV generation (+producing). Only when the panel has solar                              |

Grid Power, Site Power, Battery Power and PV Power read unknown until the panel publishes them, and every power sensor reads unknown while the panel is offline.
They read 0 W only when the panel reports 0 W, so an automation or template that uses them needs to handle unknown.

### PV Metadata Sensors (on the Solar sub-device)

A panel that reports solar gets a **Solar** device holding PV Power, the panel's total solar output, PV Panel Link, and the sensors below.

| Sensor                | Device Class | Unit | Notes                                                                                |
| --------------------- | ------------ | ---- | ------------------------------------------------------------------------------------ |
| PV Vendor             | —            | —    | PV inverter vendor (e.g., "Enphase", "Other")                                        |
| PV Product            | —            | —    | PV inverter product (e.g., "IQ8+")                                                   |
| PV Nameplate Capacity | —            | W    | DC array size recorded at installation; informational, never a limit. Off by default |

- **When it appears:** the panel reports a PV power reading (0 W counts), a solar inverter, or, on panels other than MAIN 32, a circuit marked as a solar feed.
- **More than one inverter:** each additional inverter gets a **Solar Inverter** device, named after the circuit that feeds it where one does, with its own PV
  Vendor, PV Product and PV Nameplate Capacity, and PV Panel Link where the panel reports one. The Solar device's sensors describe the first inverter the
  integration found, or all inverters together when the panel cannot tell them apart, such as behind a Tesla Gateway.
- **Inverter power:** each inverter's power is the power of the circuit that feeds it. An inverter upstream of the panel counts toward PV Power only.
- **Solar circuits:** a circuit marked as a solar feed reports its Power and Net Energy as generation.
- **Solar without an inverter**, such as solar DC-coupled into a battery: PV Power still reports it, and PV Vendor, PV Product and PV Nameplate Capacity are
  unknown.

**Deprecated:**

| Sensor         | Reason                                                                                                                   |
| -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| DSM Grid State | Deprecated — still available, but users should rely on`DSM State` as `DSM Grid State` may be removed in a future version |

### Microgrid Interconnect Device

Your panel publishes its Microgrid Interconnect Device — the switch that disconnects your home from the utility during an outage — as a device of its own,
linked to the panel. It appears automatically where the panel reports one.

| Sensor     | Device Class | Unit | Notes                                                                      |
| ---------- | ------------ | ---- | -------------------------------------------------------------------------- |
| Grid State | Enum         | —    | Health of the utility supply itself: `up`, `down`, `degraded` or `unknown` |

**Grid State** is not **DSM Grid State**, which is whether **your home** is islanded: the grid can be down while your home runs happily off the battery.

**A panel with no battery has no MID.** The specification makes backup capability structural — having a MID is what says a panel can island — so **Grid
Islandable** reads `Off` and **Grid Forming Entity** reads `Grid` rather than either going unavailable.

`DSM Grid State` reads the islanding state the MID senses, or your GFE override while the panel is acting on one.

### Power Sensor Attributes

Applies to Current Power, Feed Through Power, Battery Power, PV Power, Grid Power, and Site Power sensors.

| Attribute  | Type   | Notes                                                             |
| ---------- | ------ | ----------------------------------------------------------------- |
| `voltage`  | number | Nominal panel voltage (240)                                       |
| `amperage` | number | Calculated current (power / voltage); null while power is unknown |

**Current Power** carries one more, because whether it reads grid flow depends on the wiring:

| Attribute             | Type    | Notes                                                                      |
| --------------------- | ------- | -------------------------------------------------------------------------- |
| `at_service_entrance` | boolean | Whether this panel's upstream lugs are where the utility actually connects |

Current Power reads the upstream lugs. That is grid flow when those lugs are the utility connection point, which is the ordinary case. Put a battery between the
utility and your main lugs, or feed this panel from another panel, and the same reading becomes **this panel's** supply while **Grid Power** stays the
whole-site figure — so the two legitimately disagree. When `at_service_entrance` is `false`, use Grid Power for site-level grid import and export.

### Software Version Sensor Attributes

| Attribute    | Type | Notes                               |
| ------------ | ---- | ----------------------------------- |
| `panel_size` | int  | Total breaker spaces (e.g., 32, 40) |

### Wi-Fi Link Binary Sensor Attributes

| Attribute   | Type   | Notes                                                                  |
| ----------- | ------ | ---------------------------------------------------------------------- |
| `wifi_ssid` | string | Network this link is to. Present only when the panel publishes an SSID |

### EVSE (EV Charger) Entities

Created when a SPAN Drive or other EVSE is commissioned on the panel. Each EVSE appears as a separate sub-device linked to the panel via `via_device`. Vendor,
product, serial number, and software version are surfaced as device info attributes — not separate entities.

#### EVSE Device Naming

The EVSE device name includes the panel device name prefix. A display suffix differentiates multiple chargers on the same panel:

- **Circuit names** (`USE_CIRCUIT_NUMBERS=False`): suffix is the fed circuit's panel name (e.g., "Garage")
- **Circuit numbers** (`USE_CIRCUIT_NUMBERS=True`): suffix is the EVSE serial number (e.g., "SN-EVSE-001")
- **No suffix available**: the display suffix is omitted (no empty parentheses)

| Naming Mode     | Example Device Name                   | Example Entity ID                                         |
| --------------- | ------------------------------------- | --------------------------------------------------------- |
| Circuit names   | `Main House SPAN Drive (Garage)`      | `sensor.main_house_span_drive_garage_charger_status`      |
| Circuit numbers | `Main House SPAN Drive (SN-EVSE-001)` | `sensor.main_house_span_drive_sn_evse_001_charger_status` |
| No suffix       | `Main House SPAN Drive`               | `sensor.main_house_span_drive_charger_status`             |

The circuit that feeds a charger has its sensors shown on the charger's device. Their entity IDs name the charger alone —
`sensor.main_house_span_drive_garage_power` — matching the charger's other sensors. A feed sensor you already have keeps its current id until you press
**Recreate entity IDs**, which offers the charger-named one.

#### EVSE Sensors (per charger)

| Sensor             | Device Class | Unit | Notes                                                                            |
| ------------------ | ------------ | ---- | -------------------------------------------------------------------------------- |
| Charger Status     | Enum         | —    | OCPP-based states: AVAILABLE, PREPARING, CHARGING, SUSPENDED_EV, etc. Translated |
| Advertised Current | Current      | A    | Amps offered to the vehicle. Not created where the charger does not declare it   |
| Lock State         | Enum         | —    | LOCKED, UNLOCKED, UNKNOWN. Translated                                            |
| Part Number        | —            | —    | Charger part number (diagnostic, **off by default**)                             |

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
| EVSE Lock                 | Lock     | —    | Locks and unlocks the charger. Only where the panel lets the charger be locked          |

The maximum is read from the panel: it is the current the charger was commissioned for. Where the charger declares the range its limit accepts, the control
offers only that range, so it offers nothing below the charger's minimum. The control is created only where the panel declares the limit settable, and reports
unavailable while the panel has not published the commissioned maximum that bounds it. A change the panel has acknowledged but not yet applied appears as a
`charge_current_limit_target` attribute while the state stays the limit the charger is still enforcing.

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
| BESS Part Number    | —              | —    | Battery system part number (diagnostic, **off by default**)                     |
| BESS Serial Number  | —              | —    | Battery system serial number (diagnostic)                                       |
| BESS Firmware       | —              | —    | Battery system firmware (diagnostic)                                            |
| Nameplate Capacity  | Energy Storage | kWh  | Rated battery capacity (diagnostic, **off by default**)                         |
| Stored Energy       | Energy Storage | kWh  | Current stored energy (diagnostic)                                              |

#### BESS Binary Sensors

| Sensor         | Device Class | Notes                                                                                                                   |
| -------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------- |
| BESS Connected | Connectivity | Whether the panel can communicate with the BESS, whether it is connected ahead of the panel or wired to a breaker in it |

### Panel Energy Sensor Attributes

Applies to Main Meter and Feed Through energy sensors.

| Attribute | Type   | Notes                       |
| --------- | ------ | --------------------------- |
| `voltage` | number | Nominal panel voltage (240) |

### Circuit-Level Sensors (per circuit)

Each circuit is a device of its own under the panel's device. It holds the sensors below and, on a user-controllable circuit, the
[circuit controls](#circuit-controls-per-user-controllable-circuit). The circuit that feeds a charger is the exception: its sensors are on the
[charger's device](#evse-device-naming). See [Entity Naming Pattern](#entity-naming-pattern) for how circuit devices and their entities are named.

| Sensor          | Device Class | Unit | Notes                                                                                         |
| --------------- | ------------ | ---- | --------------------------------------------------------------------------------------------- |
| Power           | Power        | W    | Instantaneous circuit power (+producing for PV and solar-role circuits, +consuming otherwise) |
| Produced Energy | Energy       | Wh   | Cumulative energy produced                                                                    |
| Consumed Energy | Energy       | Wh   | Cumulative energy consumed                                                                    |
| Net Energy      | Energy       | Wh   | Net energy (sign depends on what the circuit feeds — PV and solar-role circuits invert)       |
| Current         | Current      | A    | Measured circuit current. Only when panel reports `current_a`                                 |
| Breaker Rating  | —            | A    | Circuit breaker amperage (diagnostic, off by default). Only when reported                     |

### Circuit Power Sensor Attributes

| Attribute              | Type   | Notes                                                                                                                                                                                    |
| ---------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tabs`                 | string | Breaker slot position(s)                                                                                                                                                                 |
| `voltage`              | number | The nominal voltage the circuit declares, otherwise 120 for a one-pole circuit or 240 for a two-pole one. Present where either is known                                                  |
| `always_on`            | bool   | Whether the circuit's relay is locked on. Present only where the circuit has a relay                                                                                                     |
| `relay_state`          | string | OPEN / CLOSED / UNKNOWN. Present only where the circuit has a relay                                                                                                                      |
| `relay_requester`      | string | Who requested relay state. Present only where the circuit has a relay                                                                                                                    |
| `shed_priority`        | string | API value: NEVER / SOC_THRESHOLD / OFF_GRID / UNKNOWN. Present only where the circuit's relay is controllable: a relay locked on stays on, whatever priority the panel stores for it     |
| `is_sheddable`         | bool   | Whether circuit can be shed. Present only where the circuit has a relay                                                                                                                  |
| `pcs_managed`          | bool   | Whether the panel's Power Control System manages this circuit. Present only when the circuit reports it and its relay is controllable                                                    |
| `pcs_priority`         | int    | This circuit's shed order under an active import limit — distinct from `shed_priority`, which is the backup tier. Present only when the circuit reports it and its relay is controllable |
| `protection_functions` | list   | The protections the circuit's breaker provides: OVERCURRENT, SHORT_CIRCUIT, GROUND_FAULT, ARC_FAULT. Present only where the breaker lists them                                           |
| `meter_shared_with`    | list   | Names of the other circuits measured by this circuit's meter. Present only where the circuit declares the list                                                                           |
| `relay_shared_with`    | list   | Names of the other circuits switched by this circuit's relay. Present only where the circuit declares the list                                                                           |

### Circuits That Share a Meter or Relay

Some circuits share one meter or one relay, as the breakers of a tandem, quad or tandem-quad do on SPAN panels other than the MAIN 32. Each keeps its own
entities and names the circuits it shares with in `meter_shared_with` and `relay_shared_with`, which also appear on its Breaker switch. Circuits that share a
meter report that one meter's readings, so their power and energy should not be added together. See [Tandem and Multi-Load Breakers](tandems.md) for how the
panel view and current monitoring treat them, and for the MAIN 32.

### Remote Current Transformers

A remote current transformer (CT) the panel reads appears as **Remote CT** followed by its number, with Power, Produced Energy, Consumed Energy and Net Energy
sensors, and Current where the panel reports it. Its Power is positive for power flowing into the panel. It has no breaker, so those sensors are all it has: a
Breaker switch, a Circuit Priority select, current monitoring, and the relay and shed attributes of a Power sensor come with a breaker. The panel view draws it
outside the breaker grid.

### Circuit Energy Sensor Attributes

| Attribute | Type   | Notes                                                                                                                                   |
| --------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `tabs`    | string | Breaker slot position(s)                                                                                                                |
| `voltage` | number | The nominal voltage the circuit declares, otherwise 120 for a one-pole circuit or 240 for a two-pole one. Present where either is known |

### Binary Sensors

| Sensor          | Device Class | Notes                                                                                                                                        |
| --------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Door State      | Tamper       | Panel door open/closed                                                                                                                       |
| Ethernet Link   | Connectivity | Wired network status                                                                                                                         |
| Wi-Fi Link      | Connectivity | Wireless network status                                                                                                                      |
| Panel Status    | Connectivity | Overall panel online/offline                                                                                                                 |
| Grid Islandable | —            | Whether the panel can island from the grid. Off on a panel with no MID — see [Microgrid Interconnect Device](#microgrid-interconnect-device) |
| PCS Active      | Running      | Whether the Power Control System is limiting import right now. Only when the panel runs one                                                  |
| PV Panel Link   | Connectivity | Whether the panel can reach the solar inverter. Only when the feeding circuit reports it                                                     |

### Circuit Controls (per user-controllable circuit)

| Entity           | Type   | Notes                                             |
| ---------------- | ------ | ------------------------------------------------- |
| Breaker          | Switch | On/off relay control                              |
| Circuit Priority | Select | Controls when the circuit is shed during off-grid |

### Panel Controls

| Entity                       | Type   | Notes                                                             |
| ---------------------------- | ------ | ----------------------------------------------------------------- |
| GFE Override: Grid Connected | Button | Tell the panel the grid is up when BESS communication interrupted |

### Adopted Devices and Readings

The eBus schema is vendor-extensible, so your panel can publish a device type this integration has not modeled. Rather than ignoring it, the integration gives
it a card of its own hanging off the panel, carrying whatever identity it publishes, with its readings as entities beneath it.

Everything adopted arrives **disabled** and **filed under Diagnostics until you promote it** from the dashboard [Adopted tab](#the-adopted-tab); the new-entity
notification names the device so you can find it. A property the device accepts writes to becomes a control rather than a reading — a `boolean` becomes a
switch, an enumeration becomes a select, a number becomes a number entity — and those arrive switched off too.

Three things worth knowing before you build on one:

- **An adopted entity enters long-term statistics only when you say it should.** Adopted entities arrive with no statistics class, because the correct one is
  not published on the wire. You are welcome to supply it in the [Adopted tab](#the-adopted-tab).
- **A new property on a device this integration already models is adopted too**, but as a reading on that device's existing card rather than as a device of its
  own.
- **The delete button hides a reading that is still adopted until the next reload, and clears out one that is not:**

  - Delete a reading that is still adopted — your panel still publishes it and it has not been modeled since — and it comes back, switched off, at the next
    reload, carrying whatever you curated for it. Curation is keyed on the wire address rather than on the entity, so it survives deleting the entity. To
    suppress the entity, leave it switched off.
  - Delete one after your panel has stopped publishing it, or after it has been modeled properly, and it stays gone, since only a reading that is still adopted
    is recreated.

**These entities are permanent in id, not in identity.** If one of these readings is later modeled properly (delivered as an official part of the integration),
that entity is a new entity with its own id and its own history — the adopted one is not renamed into it. The adopted one stops updating and is left behind
until you delete it. That is why a vendor reading you have come to depend on is worth raising in an issue: being modeled is what turns it into something with a
real name, a proper category and statistics out of the box.

### The Adopted Tab

The integration will not guess what an adopted reading means. The built-in dashboard has an **Adopted** tab where you can tell the integration about the
reading.

A row that arrived as a control rather than a reading — a switch, a select or a number box — offers prominence alone, because a device class and a statistics
class both describe a value being read.

You are only offered choices your panel's own declaration allows. A statistics class is offered only on a reading whose declared datatype is numeric; the device
classes listed are the ones that fit both what the panel says the reading is and the unit it publishes, so a class that expects a number is listed only for a
numeric reading, and the unit itself stays whatever the publisher sends.

**Saving reloads the integration**, and setting or clearing a statistics class asks you to confirm before it does.

Two consequences are what that confirmation is about:

- **`total_increasing` tells Home Assistant the reading is a meter that only counts up.** The recorder treats a drop of more than a tenth as the meter being
  reset and starts a new cycle, so choosing it for a reading that legitimately falls will manufacture consumption that did not happen. Choose it only for a
  genuine lifetime total.
- **Removing a statistics class stops statistics, and Home Assistant will say so.** If an entity already has statistics and then loses its statistics class,
  Home Assistant raises a repair notice against it — a warning rather than something with a fix button — and stops compiling new statistics for it. The
  statistics already collected are kept, and the notice clears by itself if you put a statistics class back.

### BESS & Grid Management

This section explains how the SPAN panel manages power sources and load shedding when a Battery Energy Storage System (BESS) is installed, and what the
integration can and cannot tell you about grid status.

#### Grid Forming Entity

The Grid Forming Entity (GFE) sensor identifies which power source provides the voltage and frequency reference for the home — not which source is producing the
most watts. When GFE is Grid, the utility grid sets the reference and circuits are not shed, even if 100% of consumption comes from solar. When GFE is Battery,
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

**On a panel with no battery, GFE is Grid.** The panel publishes this value on the Microgrid Interconnect Device, which is part of a battery system, so a panel
without one does not publish it, and the value follows by elimination: Battery needs a BESS (which brings a MID), PV forms a grid only through a grid-forming
inverter (which is a MID), and None would mean a panel supplying nothing, which could not report it. That leaves a generator, which SPAN, having no generator
interface, treats as the grid.

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

However, when the panel is islanded and the MID is open, all of the panel's signals measure the home side, so only an external signal (a utility-side sensor) or
manual confirmation via the GFE Override button can establish that the grid is back.

#### GFE Override Button

The **GFE Override: Grid Connected** button tells the panel that the grid is back and shedding can stop. When the BESS restores communication, it automatically
reclaims control and the override is superseded — no manual undo is needed.

The button is available only while the panel is not already on grid and the battery's **Communication State** is anything other than OK, which is when the panel
accepts the override. On firmware that does not publish that state, it is available unless **BESS Connected** is on.

**Risk asymmetry** — Telling the panel to shed (conservative direction) is low-risk; worst case is unnecessary circuit disruption. Telling the panel the grid is
back when it is not means unmanaged battery drain and reduced runtime, which could affect critical equipment. Use the override button only with confidence that
the grid has actually been restored — via a utility-side sensor or manual confirmation.

**WARNING** — Do _not_ automate the GFE override button based on `DSM State` — it inherits the same MID blind spot described above and will read `dsm_off_grid`
even after the grid is restored. Manual confirmation or an external sensor is required before pressing the button.

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

Each circuit is a device of its own in Home Assistant, under the panel's device and named by the circuit's name in the SPAN app. A circuit with no name there is
named by its breaker position, such as "Circuit 15", or "Solar" where it is a solar circuit. A meter outside the panel is named by its number, such as "Remote
CT 1". A circuit's entities are named by what they measure or control, such as Power or Breaker, and Home Assistant puts the panel's name and the circuit's in
front when it displays them, as in "Span Panel Kitchen Outlets Power", except where an area of its own changes that, as described below. This requires Home
Assistant 2026.11 or later.

- Renaming a circuit in the SPAN app renames its device, and with it the displayed names of the circuit's entities. The integration reloads itself to pick the
  new name up.
- A name you give a circuit's device in Home Assistant takes precedence over the SPAN app's. A name you give an entity is yours, and a rename in the SPAN app
  does not change it.
- An area you set on a circuit's device applies to every entity of the circuit that has no area of its own, the Breaker switch included, so an action aimed at
  that area, such as turning off everything in a room, turns that breaker off. When the panel adds a Breaker switch to a circuit whose device already has an
  area, a repair names the switch and the area.
- An area also changes the names Home Assistant displays. A circuit's device with an area of its own is shown without the panel's name, as in "Kitchen Outlets
  Power". An entity with an area of its own is shown without its device, as just "Power", and **Recreate entity IDs** offers it an ID to match, such as
  `sensor.kitchen_power`. To show the circuit's name again, set the area on the circuit's device and clear the entity's own.

The naming pattern, chosen during initial setup, decides the entity ID a new circuit entity is created with. It also decides the suffix in the name of a
charger's device and of an additional solar inverter's: the feeding circuit's name under circuit names, and the charger's serial number or the inverter
circuit's breaker position under circuit numbers (see [EVSE Device Naming](#evse-device-naming)). The names of a circuit's own device and its entities follow
the SPAN app either way.

1. **Circuit names** (Recommended for new installations)

   - Example: `sensor.span_panel_kitchen_outlets_power`
   - Home Assistant builds each new entity's ID from the panel's name, the circuit's name and what the entity measures
   - More intuitive for automations and scripts

2. **Circuit numbers**

   - Example: `sensor.span_panel_circuit_15_power`, or `sensor.span_panel_circuit_30_32_power` for a two-pole breaker
   - The integration builds each new entity's ID from its breaker's position in place of the circuit's name, so the ID does not depend on what the circuit is
     called when the entity is created

In either mode, an entity ID changes only when you accept a **Recreate entity IDs** offer, which is the ID Home Assistant builds from the panel's name, the name
of the circuit's device and what the entity measures:

- After a circuit is renamed in the SPAN app, Recreate offers an ID with the new name.
- With circuit numbers, Recreate offers the ID the circuit would have under circuit names, such as `sensor.span_panel_kitchen_outlets_power` for
  `sensor.span_panel_circuit_15_power`, which matches the displayed name. The circuit-number ID stays unless you accept, and entities created later still get
  circuit-number IDs.

The integration supplies only the last part of the ID shown above, such as `power`. Home Assistant composes the rest from your own entity ID settings
(`entity_id_parts`), which decide whether the area and the panel's name go in front of the circuit's name. If you have saved a custom entity ID format in Home
Assistant's settings that leaves out the parent device, circuit entities' IDs are built without the panel's name, so Recreate offers
`sensor.kitchen_outlets_power`.

For an entity that already exists, the wording of that last part is read back from the ID the entity has rather than reworded. Circuit energy IDs come in two
spellings, `consumed_energy` and `energy_consumed`: an installation is offered the one it already uses, and new sensors get `consumed_energy`, the word order
the panel-level sensors use. Because your entity ID settings decide the rest of the ID, an installation whose circuits were named before those settings existed
— a SPAN Drive feed circuit, a second panel, or an installation predating the device prefix — may be offered an ID with a different device half. Entities you
already have keep the IDs they have until you press **Recreate entity IDs** and accept the offer.

Upgrading from a release before 2.3.0: see [Upgrading to Circuit Devices](circuit-devices-upgrade.md).

### Energy Dip Compensation

SPAN panels occasionally report lower energy readings for cumulative energy sensors after firmware updates or resets. Home Assistant's statistics engine
interprets a drop of more than a tenth as a counter reset and adds the lower reading to the energy dashboard as new energy, creating a spike. A smaller drop is
recorded as negative energy.

When enabled, the integration automatically detects these dips and maintains a cumulative offset per sensor so Home Assistant sees a monotonically increasing
value.

- **Default:** ON
- **Threshold:** 1.0 Wh minimum to avoid false triggers from float precision noise
- **Disabling:** Clears all accumulated offsets (starts fresh if re-enabled)

A dip is compensated as soon as it is seen, but not believed straight away. A counter reset is permanent — the counter restarts low and counts up from there —
so a reading that drops and then returns to where it was is a transport artifact rather than a reset, and its offset is taken back. The offset stays provisional
until a later reading disproves it (the counter comes back) or the dip settles, a few readings after it is corroborated (the counter climbs from the new, lower
base).

**The persistent notification lists a dip once it settles**, because the offset can be taken back and a notification cannot. A dip that is disproved is not
listed: the sensor was compensated the whole time and needs no attention.

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

**The integration cannot enforce much on its own.** Anything holding the eBus broker password — including another integration in the same Home Assistant process
— talks to the panel directly, outside Home Assistant's permission model.

**What pinning the panel's certificate authority implies**:

- A **listener** cannot read your passphrase or the credentials the panel returns for it.
- A device **actively in the path at that first fetch** can answer with an authority of its own and read both. Pinning alone cannot detect this (see deployment
  recommendations).

Comparing the fingerprint against another source closes the second case. It is in diagnostics under `panel_ca`, and in the log of the setup that pinned it — at
`INFO` from the config flow, at `WARNING` for an entry pinned on upgrade. After the first pin, any change stops the integration and raises a repair — see
Troubleshooting.

| Situation                                           | What happens                                                                                                                                                                                                                                                                                               |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Setting up by hostname                              | Verified throughout. A domain joins the certificate's SAN only after you authenticate; everything before that runs against an address the certificate already names.                                                                                                                                       |
| Panel announces a new address, or you re-add it     | The entry moves only if the candidate serves a certificate its own anchor validates. Otherwise the move is refused and logged at `WARNING`.                                                                                                                                                                |
| The panel really has moved                          | Use **Reconfigure**. A host that does not chain is refused, one that does not answer is reported unreachable — move it to an **FQDN** (the panel regenerates its certificate around that name) or the panel's **`.local` name** (already covered). A bare new IP the certificate does not name is refused. |
| The entry has no anchor, or a stored one won't load | **Reauthenticate** acquires one before either sign-in method is offered.                                                                                                                                                                                                                                   |

### Restricting who can operate the panel

Four options in **Settings → Devices & Services → Span Panel → Configure**. Each is enforced where commands are published rather than only at the entity, so a
refusal holds however the command arrived. While **Who may operate the panel** is `Nobody`, the form hides the other three, and each keeps its value for when
you choose who may operate the panel again.

| Option                                     | Default                               | What it refuses                                                                                                                                                                                                                                 | What it does not do                                                                                                        |
| ------------------------------------------ | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Who may operate the panel**              | Administrators only                   | `Administrators only` refuses circuit switches, priority selects, the GFE override, EVSE limits and locks, and adopted controls from non-admin users. `Nobody` also stops those entities and the control lock's switch being created.           | Neither touches sensors. Neither reaches an automation, which runs with no user at all — pair it with the next option.     |
| **Allow control without a logged-in user** | Off                                   | While off, refuses every command with no user attached: all automations, and any script or integration an automation calls. A script a person runs keeps that person's user and is unaffected. Turn it on if your automations operate circuits. | It cannot tell a well-behaved automation from a runaway one — only that neither has a user.                                |
| **Control lock auto-relock**               | Off, and no switch exists             | A switch that, while armed, refuses every control command. Anyone can arm it; only a logged-in administrator can disarm it. `0` holds the disarm until someone re-arms; any other value re-arms after that many minutes.                        | It is not a password. It defends against misclicks and runaway automations, which is what a local control can actually do. |
| **Relay debounce**                         | 2 seconds; `0` turns the debounce off | A second command to the same circuit's relay inside the window. Refused rather than queued.                                                                                                                                                     | It is per circuit, so an automation cycling many circuits still gets through.                                              |

**What none of this defends against.** These options constrain only callers arriving through Home Assistant. Anything already holding the broker credential — a
second Home Assistant instance, a script you wrote, a malicious custom integration reading it out of this process's memory — publishes to the panel directly;
see [Recommended deployment](#recommended-deployment), where network topology and a locked enclosure are the real boundary.

**Choosing `Nobody` is reversible.** The control entities stop being created and read as unavailable, but their registry entries, names, areas and
customizations are kept, so turning the option back on restores exactly the entities you had. The control lock comes back armed or disarmed as you left it, with
any auto-relock still due at the same moment, and armed if Home Assistant no longer holds its last state. For an administrator, the shipped SPAN Panel card and
dashboard offer no breaker switch or priority while `Nobody` is chosen. A non-administrator's card finds its entities without the panel's topology, so it shows
those switches dimmed and inert, as other dashboards show them unavailable.

### The record of what was commanded

Every control command fires a `span_panel_control_command` event and appears in the logbook, whether it succeeded, was refused, or did not reach the panel. When
there is no user — an automation — the originating automation or script is named instead, so an unattended write is attributed to _what_ rather than left blank.
Every command is also logged at `INFO`.

Commands report one of four outcomes, or a refusal, and the distinctions matter:

| Outcome       | Meaning                                                                                  |
| ------------- | ---------------------------------------------------------------------------------------- |
| `confirmed`   | The panel reported the value you asked for.                                              |
| `accepted`    | The broker acknowledged the message and the panel did not report a change.               |
| `unconfirmed` | Nothing came back within the deadline. **Not an error** — see the troubleshooting entry. |
| `failed`      | The command was not handed to the broker and will not be delivered.                      |
| `refused:…`   | This integration refused it, for the named reason.                                       |

### Rotating panel credentials

This asks the panel to replace its passphrase, which is currently also the password for the connection the integration uses, stores the new broker password, and
reloads. Access tokens the panel has already issued, including the integration's, are not revoked. Run it after a contractor visit, a suspected exposure, or
anything else that put someone in front of your panel.

```yaml
action: span_panel.rotate_credentials
data: {}
```

**Run it yourself, from the interface, and save the new passphrase.** Only a Home Assistant administrator can, and an automation or script cannot. The new panel
passphrase is shown only in the action's response: the integration does not store it. With more than one panel, set the **Config entry** field to say which —
the action refuses to guess.

**Anything else talking to your panel stops until you set it up again.** A second Home Assistant, a script, third-party tooling: each has to be given the new
password before it reconnects. This integration handles itself: the broker may not accept the new password as soon as the panel returns it, so the integration
keeps retrying with the new password for about a minute.

**A refused rotation leaves the panel and the integration as they were, so fix the cause and run it again.** If the panel rejects the integration's sign-in, or
says its access token has reduced privileges, reauthenticate it with the panel passphrase first. If a repair about the panel's certificate is open, resolve that
first — a new password is sent only over a connection that can be verified.

**If the panel does not report the outcome, assume the passphrase may have changed.** Run the action again: its response gives you a passphrase you know. It
works whether or not the integration is loaded, because the panel does not revoke the integration's access token. If the panel refuses that rotation,
reauthenticate the integration, using proof of proximity if the old passphrase is no longer accepted, or the passphrase shown in the SPAN app on a panel that
registers by passphrase only.

**If the response says `reconnected: false`, save the passphrase, then rotate again.** The integration has stored the new password, but the broker was still
refusing it after about a minute. Run the action again, even though the integration is not loaded: the integration uses the newly returned password, and the
passphrase in that response is the one to keep. As a last resort, restart the panel.

### Recommended deployment

The panel credential is a single all-or-nothing secret, and anyone standing at the panel can mint a fresh one with three presses of the door switch, except on a
panel that registers by passphrase only. Network topology and physical control of the panel are the real boundary; everything above is defense in depth behind
it.

- **Put the panel on a trusted VLAN**, with default-deny between VLANs, and allow only the Home Assistant host to reach it. Open `tcp/443` (REST), `tcp/8883`
  (MQTTS) and `tcp/80` (the plaintext certificate-authority fetch, and REST too until the entry pins), and deny `tcp/9001` and `tcp/9002` unless you are
  actively using the SPAN Home on-premise UI.
- **Use the IP address or an FQDN, and the `.local` name only** if you have an mDNS repeater like Avahi. The panel's IP is already in its certificate SAN, and
  for an FQDN the integration registers it so the panel adds it.
- **Secure the panel enclosure**, because, on a panel that offers proof of proximity, the three-press bypass hands full credentials to anyone who can open the
  door — the equivalent of a printed root password. This matters more than the software controls on this page.
- **Put dashboard-only household members in Home Assistant's read-only group.** Home Assistant's default user policy grants every non-admin user control of
  every entity, which includes this integration's circuit switches and priority selects.
- **Encrypt your Home Assistant backups.** An unencrypted backup contains `.storage/core.config_entries`, and the panel credentials in it are in plaintext.
- **Enable multi-factor authentication on Home Assistant** and do not expose its API directly to the Internet. A compromised Home Assistant administrator
  account is a compromised panel. Consider the use of Tailscale, a free and secure mesh network service, if you need to reach Home Assistant from your own
  devices over the Internet.
- **A proxy may forward traffic, but must not answer for the panel.** One that passes the traffic through untouched as raw TCP works normally, as long as Home
  Assistant still reaches the panel by a name the certificate covers: its IP address, its `.local` name, or a domain set through **Reconfigure**. Point that
  name at the proxy in DNS rather than entering the proxy's own address, which is refused.

## WebSocket API

The integration provides a `span_panel/panel_topology` WebSocket command that returns the full physical layout of a panel in a single call — circuits with their
breaker slot positions, entity IDs grouped by role, and sub-devices (BESS, MID, EVSE, PV) with their entities, and for each solar device the inverter it
describes and the circuit reading that is its power.

See [WebSocket API Reference](websocket-api.md) for the full schema, response format, and usage examples.

## Troubleshooting

| Issue                                                                        | Symptoms                                                                                                                                                                                                                                                                                                                                                                                                                            | Resolution                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Energy Dashboard spikes after firmware updates**                           | Huge energy-consumption spikes after panel firmware updates; charts showing untracked values that dwarf normal usage; negative energy values in statistics. Caused by the panel reporting decreased values on otherwise `TOTAL_INCREASING` sensors.                                                                                                                                                                                 | **Prevention:** enable [Energy Dip Compensation](#energy-dip-compensation) in General Options (on by default). **Fix existing spikes:** in **Developer Tools → Statistics**, search for the affected sensor (e.g. `sensor.span_panel_main_meter_consumed_energy`) and use **Adjust sum** to correct the errant entry. The integration also posts a persistent notification when a compensated dip on any energy sensor settles (see [Energy Dip Compensation](#energy-dip-compensation)). **If you enabled compensation before 2.1.0 and saw spikes at restart:** check `energy_offset` on your energy sensors in **Developer Tools → States** — an offset far larger than the sensor's own reading is compensation for a dip that never happened, and toggling Energy Dip Compensation off discards it. Offsets booked before 2.1.0 were never corroborated, so the integration cannot tell a good one from a bad one retrospectively; the spikes already in statistics still need **Adjust sum** either way. |
| **High CPU usage**                                                           | Elevated CPU on low-power hardware (e.g. Raspberry Pi). The integration rebuilds a full panel snapshot from MQTT messages at a configurable interval (default 1 s).                                                                                                                                                                                                                                                                 | Increase **Snapshot Update Interval** in **General Options**. 10–15 s is recommended for resource-constrained systems. Setting it to 0 disables debouncing and rebuilds on every MQTT message — not recommended.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Replaced sub-device shows the old serial number**                          | After replacing a SPAN sub-device (Drive / EVSE, BESS, PV inverter), the device entry in Home Assistant keeps showing the previous hardware's serial number. The integration keys entities off the panel-assigned node identity, which is intentionally stable across hardware swaps so long-term history (e.g. lifetime charging kWh for a Drive) is preserved. The device-registry serial number, however, does not auto-refresh. | In **Settings → Devices & Services → Span Panel**, open the affected sub-device and delete it, then reload the integration (or restart Home Assistant). The device re-registers with the new serial number. Entity IDs and their recorded history are preserved.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Door sensor unknown**                                                      | The SPAN API returns UNKNOWN if the cabinet door has not been operated recently. This is a defect in the SPAN API.                                                                                                                                                                                                                                                                                                                  | The integration shows the sensor as unknown until a proper value arrives. Opening or closing the door publishes the correct state. The door is classified as a tamper sensor (`Detected` / `Clear`) to differentiate it from a normal entry door.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **No switch on a circuit**                                                   | A circuit has no switch entity exposed in Home Assistant.                                                                                                                                                                                                                                                                                                                                                                           | The panel does not let you control the circuit, so no switch is created. That is the case for circuits set in the SPAN App as "Always on Circuits"; for solar and EV charger circuits; for a remote CT, which has no breaker; and, on panel firmware r202639 or later, for the Commissioned PV System and Commissioned Backup System circuits. A circuit set to never back up keeps its switch but has no priority select.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Reinstalling to change the entity ID style gives back the old entity IDs** | The naming style is chosen at install and cannot be changed from the options, so reinstalling looks like the way to switch. It is not: every entity returns with the entity ID it had before.                                                                                                                                                                                                                                       | Home Assistant remembers a removed entity for **30 days**, keyed on its unique ID, and restores that record's entity ID — along with its name, area, labels and icon — as soon as an entity with the same unique ID appears again. This integration's unique IDs do not change with the naming style, so the remembered ID wins over the one the new style asks for. Either clear the leftover registry entries between removing and reinstalling, or wait out the 30 days and let Home Assistant discard them. A tool such as [ha-registry-clean](https://github.com/LegoTypes/ha-registry-clean) can do the clearing; it is a separate project, not part of this integration. Clearing also discards the names, areas and labels you had assigned.                                                                                                                                                                                                                                                           |
| **Setup fails after downgrading the integration**                            | After installing an older release, the SPAN Panel config entry fails to set up and Home Assistant reports an unsupported configuration version.                                                                                                                                                                                                                                                                                     | An older release refuses to load a config entry that a newer release migrated. Reinstall the newer release, or restore a backup taken before the upgrade.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **"SPAN Panel certificate authority changed" repair**                        | The integration has stopped connecting and a repair reports two fingerprints: the one it pinned and the one the panel now advertises. Entities are unavailable.                                                                                                                                                                                                                                                                     | The panel is presenting a different certificate authority than the one you accepted at setup. Two things look identical from here and only you can tell them apart: a firmware upgrade or a factory reset rotates the authority legitimately, and so does a device on your network standing in for your panel. **If you know why it changed**, open the repair, compare the new fingerprint, and accept it — that re-pins and reconnects. **If nothing should have changed**, do not accept. Check what else is on the panel's network segment first. The integration leaves reconnecting and re-pinning to you, deliberately: retrying would mean waiting to succeed against whatever is answering.                                                                                                                                                                                                                                                                                                           |
| **A circuit re-commissioned in the SPAN App has the wrong controls**         | A circuit whose configuration changed in the SPAN App — made controllable, locked, or set to never back up — seems to have the wrong controls.                                                                                                                                                                                                                                                                                      | This corrects itself: when the panel changes which controls a circuit allows, the integration reloads on its own and creates or removes that circuit's Breaker switch and priority select to match. A removed control returns with its entity ID, name and area when the panel offers it again. If a control still looks wrong, reload the integration (**Settings → Devices & Services → Span Panel → ⋮ → Reload**).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **"SPAN Panel hardware not validated with this release" repair**             | Setup has stopped with a repair naming the panel's hardware version and model, and no entities are created for that panel.                                                                                                                                                                                                                                                                                                          | This release has not been validated with that panel hardware, so it does not set the panel up; a MAIN 32 is set up whatever its hardware version.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **"SPAN panel firmware needs an update" repair**                             | The panel does not load, and a repair says it is on spanos2 firmware; its entities are unavailable.                                                                                                                                                                                                                                                                                                                                 | spanos2 firmware publishes the older flat eBus schema, which this integration does not support. Update the panel's firmware. Home Assistant keeps retrying in the background, so the panel reconnects on its own once updated and the repair clears. If the update also changed the panel's certificate authority, it reconnects once you act on the **"SPAN Panel certificate authority changed"** repair above, and this repair stays until you do.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **The Adopted tab is missing from the dashboard**                            | The dashboard offers By Activity, By Area and Monitoring, but neither **Adopted** nor **By Panel**.                                                                                                                                                                                                                                                                                                                                 | The panel dropdown at the top of the dashboard is on **Favorites**, which merges every configured panel into one workspace rather than being a panel itself, so the tabs that only mean something for a single panel are not offered there. Switch the dropdown to a panel and both come back; the selection is remembered per browser. Adopted is also administrator-only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **A control reports `unconfirmed`**                                          | The logbook or the `span_panel_control_command` event says a command was `unconfirmed`. Nothing appears broken.                                                                                                                                                                                                                                                                                                                     | **This is not an error.** It means the panel took the command and did not report a change within the deadline, usually because there was no change to report — the relay was already open, the priority was already that value. It is also indistinguishable from a silent rejection by the panel, because SPAN's firmware sends no reason code; the integration reports what it observed rather than guessing. The two outcomes that do mean something went wrong are `failed`, which means the command was not sent, and `refused:…`, which means this integration refused it and names why.                                                                                                                                                                                                                                                                                                                                                                                                                 |

### Constant high-current alerts on a tandem or multi-load breaker

**Symptom:** on a MAIN 32, a circuit raises current alerts again and again. In the SPAN app its rating reads as two values, such as "20-20A" or "20-30A", while
the integration shows one, such as "20A".

**Cause:** a MAIN 32 publishes a multi-load breaker as one circuit, with one current reading and only its first breaker's rating, so current monitoring compares
the current of both breakers against one.

**Fix:** for a tandem in one space, copy the second rating the SPAN app shows into the Monitoring tab's Rating column or the circuit's side panel. For a quad or
tandem-quad across two spaces, the dashboard recommends turning monitoring off instead. See [Tandem and Multi-Load Breakers](tandems.md) for examples and
details.

### Debug logging

To collect debug logs, use **Enable debug logging** on the integration's page (**Settings → Devices & Services → Span Panel**), which covers this integration
and `span_panel_api`. Do not enable debug logging for the `homie` logger: it logs every value the panel publishes, including its postal code and Wi-Fi network
name, and a log attached to an issue would publish them.

### Diagnostics

**Download diagnostics** on the integration's page includes the panel's device tree, every device and property it publishes, with serial numbers, device ids and
site details masked. It also lists the ratings you copied from the SPAN app for each circuit, with the spaces they were copied for and whether they apply, are
waiting on a value the panel has not sent yet, or are kept without being applied on a circuit across two spaces, and any the integration removed that a
notification reported and you have not yet dismissed.

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
