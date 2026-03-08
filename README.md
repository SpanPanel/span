# SPAN Panel Integration for Home Assistant

[Home Assistant](https://www.home-assistant.io/) Integration for [SPAN Panel](https://www.span.io/panel), a smart electrical panel that provides circuit-level
monitoring and control of your home's electrical system.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub Release](https://img.shields.io/github/release/SpanPanel/span.svg?style=flat-square)](https://github.com/SpanPanel/span/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/SpanPanel/span.svg?style=flat-square)](https://github.com/SpanPanel/span/commits)
[![License](https://img.shields.io/github/license/SpanPanel/span.svg?style=flat-square)](LICENSE)

[![Python](https://img.shields.io/badge/python-3.13.2-blue.svg)](https://www.python.org/downloads/release/python-3132/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/)
[![prettier](https://img.shields.io/badge/code_style-prettier-ff69b4.svg)](https://github.com/prettier/prettier)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

The software is provided as-is with no warranty or guarantee of performance or suitability to your particular setting.

**IMPORTANT:** This integration controls real electrical equipment. Circuit switches open and close physical relays. The GFE override button changes how the
panel manages load shedding during power outages. These actions carry the same consequences as operating the panel manually — because they are. Automations can
execute these actions without user presence; design them with the same care you would apply to any unattended electrical control. This integration is not a
safety device and must not be relied upon for life-safety applications. Use this software at your own risk. If you cannot accept that risk, do not use this
software. See [LICENSE](LICENSE) for the full warranty disclaimer.

This integration provides sensors and controls for understanding an installation's power consumption, energy usage, and controlling user-manageable panel
circuits.

This integration communicates with the SPAN Panel over your local network using SPAN's official
[Electrification Bus (eBus)](https://github.com/spanio/SPAN-API-Client-Docs) framework — an open, multi-vendor integration standard for home energy
infrastructure. eBus uses the [Homie Convention](https://homieiot.github.io/) for MQTT topics and messages, with the panel's built-in MQTT broker delivering
real-time state updates without polling.

## 1.1.x Integration Sunset (v1)

Users MUST upgrade by the end 2026 to avoid disruption.

## 2.0.x Breaking Changes (v2)

**Do NOT upgrade unless your panel is running firmware `spanos2/r202603/05` or later.**

**What you need:**

- SPAN Panel firmware `spanos2/r202603/05` or later
- Panel passphrase (found in the SPAN mobile app, On-premise settings) **or** physical access to the panel door for proof-of-proximity authentication

**Breaking:**

- Requires firmware `spanos2/r202603/05` or later — panels on older firmware will not work
- `Cellular` binary sensor removed — replaced by `Vendor Cloud` sensor

> Running older firmware? See [v1 Legacy Documentation](docs/v1-legacy.md).

See [CHANGELOG.md](CHANGELOG.md) for all additions or value changes.

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) installed
- [HACS](https://hacs.xyz/) installed
- SPAN Panel with firmware `spanos2/r202603/05` or later
- Span Panel/span integration v1.3.1
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
11. Optionally adjust the scan interval - 0 is realtime, up to 15 seconds based on CPU

### Upgrade Process

When upgrading through HACS:

1. **Create a backup** of your Home Assistant configuration and database
2. **Review the changes** in this README and CHANGELOG
3. **Check your automations** — review any references to removed entities
4. **Update during a quiet period** when you can monitor the upgrade

If you encounter issues, restore from your backup or check the [troubleshooting section](#troubleshooting) below.

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
| DSM State                    | —            | —    | DSM_ON_GRID (grid connected), DSM_OFF_GRID (islanded), UNKNOWN. Derived from multiple eBus signals                     |
| Current Run Config           | —            | —    | PANEL_ON_GRID (grid connected), PANEL_OFF_GRID (islanded on PV/generator), PANEL_BACKUP (islanded on battery), UNKNOWN |
| Grid Forming Entity          | —            | —    | (v2) GRID, BATTERY, PV, GENERATOR, NONE, UNKNOWN. See [Grid Forming Entity](#grid-forming-entity)                      |
| Main Relay State             | —            | —    | CLOSED (power flowing), OPEN (disconnected), UNKNOWN                                                                   |
| Vendor Cloud                 | —            | —    | (v2) CONNECTED, UNCONNECTED, UNKNOWN                                                                                   |
| Software Version             | —            | —    | Firmware version string                                                                                                |

### Panel Diagnostic Sensors (v2 only)

| Sensor                | Device Class | Unit | Notes                      |
| --------------------- | ------------ | ---- | -------------------------- |
| L1 Voltage            | Voltage      | V    | L1 leg actual voltage      |
| L2 Voltage            | Voltage      | V    | L2 leg actual voltage      |
| Upstream L1 Current   | Current      | A    | Upstream lugs L1 current   |
| Upstream L2 Current   | Current      | A    | Upstream lugs L2 current   |
| Downstream L1 Current | Current      | A    | Downstream lugs L1 current |
| Downstream L2 Current | Current      | A    | Downstream lugs L2 current |
| Main Breaker Rating   | Current      | A    | Main breaker amperage      |

### Power Flow Sensors (v2 only, conditional)

| Sensor        | Device Class | Unit | Notes                                                                       |
| ------------- | ------------ | ---- | --------------------------------------------------------------------------- |
| Battery Power | Power        | W    | Battery charge/discharge (+discharge, -charge). Only when BESS commissioned |
| PV Power      | Power        | W    | PV generation (+producing). Only when PV commissioned                       |
| Site Power    | Power        | W    | Total site power (grid + PV + battery). Only when power-flows node active   |

### PV Metadata Sensors (v2 only, on main panel device)

| Sensor             | Device Class | Unit | Notes                                         |
| ------------------ | ------------ | ---- | --------------------------------------------- |
| PV Vendor          | —            | —    | PV inverter vendor (e.g., "Enphase", "Other") |
| PV Product         | —            | —    | PV inverter product (e.g., "IQ8+")            |
| Nameplate Capacity | Power        | kW   | Rated inverter capacity                       |

**Deprecated:**

| Sensor         | Reason                                                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------------------------------- |
| DSM Grid State | Deprecated — still available, but users should rely on `DSM State` as `DSM Grid State` may be removed in a future version |

### Power Sensor Attributes

Applies to Current Power, Feed Through Power, Battery Power, PV Power, and Site Power sensors.

| Attribute  | Type   | Notes                                |
| ---------- | ------ | ------------------------------------ |
| `voltage`  | string | Nominal panel voltage ("240")        |
| `amperage` | string | Calculated current (power / voltage) |

### Software Version Sensor Attributes

| Attribute    | Type   | Notes                               |
| ------------ | ------ | ----------------------------------- |
| `panel_size` | int    | Total breaker spaces (e.g., 32, 40) |
| `wifi_ssid`  | string | Current Wi-Fi network               |

### BESS Sub-Device (v2 only, conditional)

When a Battery Energy Storage System (BESS) is commissioned, the integration creates a separate BESS sub-device linked to the panel via `via_device`. The BESS
device uses manufacturer, model, serial number, and software version from battery metadata as device info attributes.

#### BESS Sensors

| Sensor             | Device Class   | Unit | Notes                                     |
| ------------------ | -------------- | ---- | ----------------------------------------- |
| Battery Level      | Battery        | %    | State of energy as percentage             |
| Battery Power      | Power          | W    | Charge/discharge (+discharge, -charge)    |
| BESS Vendor        | —              | —    | Battery system vendor (diagnostic)        |
| BESS Model         | —              | —    | Battery system model (diagnostic)         |
| BESS Serial Number | —              | —    | Battery system serial number (diagnostic) |
| BESS Firmware      | —              | —    | Battery system firmware (diagnostic)      |
| Nameplate Capacity | Energy Storage | kWh  | Rated battery capacity (diagnostic)       |
| Stored Energy      | Energy Storage | kWh  | Current stored energy (diagnostic)        |

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
| Current         | Current      | A    | (v2) Measured circuit current. Only when panel reports `current_a`    |
| Breaker Rating  | Current      | A    | (v2) Circuit breaker amperage (diagnostic). Only when reported        |

### Circuit Power Sensor Attributes

| Attribute         | Type   | Notes                                                 |
| ----------------- | ------ | ----------------------------------------------------- |
| `tabs`            | string | Breaker slot position(s)                              |
| `voltage`         | string | 120 or 240 (derived from tab count)                   |
| `always_on`       | bool   | Whether circuit is always-on                          |
| `relay_state`     | string | OPEN / CLOSED / UNKNOWN                               |
| `relay_requester` | string | Who requested relay state                             |
| `shed_priority`   | string | API value: NEVER / SOC_THRESHOLD / OFF_GRID / UNKNOWN |
| `is_sheddable`    | bool   | Whether circuit can be shed                           |

### Circuit Energy Sensor Attributes

| Attribute | Type   | Notes                               |
| --------- | ------ | ----------------------------------- |
| `tabs`    | string | Breaker slot position(s)            |
| `voltage` | string | 120 or 240 (derived from tab count) |

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

#### EVSE Sensors (per charger)

| Sensor             | Device Class | Unit | Notes                                                                            |
| ------------------ | ------------ | ---- | -------------------------------------------------------------------------------- |
| Charger Status     | Enum         | —    | OCPP-based states: AVAILABLE, PREPARING, CHARGING, SUSPENDED_EV, etc. Translated |
| Advertised Current | Current      | A    | Amps offered to the vehicle                                                      |
| Lock State         | Enum         | —    | LOCKED, UNLOCKED, UNKNOWN. Translated                                            |

#### EVSE Binary Sensors (per charger)

| Sensor       | Device Class     | Notes                                                              |
| ------------ | ---------------- | ------------------------------------------------------------------ |
| Charging     | Battery Charging | ON when status is CHARGING                                         |
| EV Connected | Plug             | ON when status is PREPARING, CHARGING, SUSPENDED\_\*, or FINISHING |

#### EVSE Device Info Attributes

| Attribute        | Source             |
| ---------------- | ------------------ |
| Manufacturer     | `vendor-name`      |
| Model            | `product-name`     |
| Serial Number    | `serial-number`    |
| Software Version | `software-version` |

### Binary Sensors

| Sensor          | Device Class | Notes                                                               |
| --------------- | ------------ | ------------------------------------------------------------------- |
| Door State      | Tamper       | Panel door open/closed                                              |
| Ethernet Link   | Connectivity | Wired network status                                                |
| Wi-Fi Link      | Connectivity | Wireless network status                                             |
| Panel Status    | Connectivity | Overall panel online/offline                                        |
| Grid Islandable | —            | (v2) Whether the panel can island from the grid. Only when reported |

**Removed from binary sensors:**

| Sensor          | Reason                                                 |
| --------------- | ------------------------------------------------------ |
| Cellular (wwan) | Replaced by `Vendor Cloud` sensor (cloud connectivity) |

### Circuit Controls (per user-controllable circuit)

| Entity                | Type   | Notes                                                                      |
| --------------------- | ------ | -------------------------------------------------------------------------- |
| Breaker               | Switch | On/off relay control                                                       |
| Circuit Shed Priority | Select | (v2) Controls when circuit is shed during off-grid (translated, see below) |

### Circuit Shed Priority Options

Labels match the SPAN Home On-Premise app. Translations are provided for all supported languages.

| Option Key      | Display Label (EN)               | API Value     |
| --------------- | -------------------------------- | ------------- |
| `never`         | Stays on in an outage            | NEVER         |
| `soc_threshold` | Stays on until battery threshold | SOC_THRESHOLD |
| `off_grid`      | Turns off in an outage           | OFF_GRID      |

### Panel Controls

| Entity                       | Type   | Notes                                                                                |
| ---------------------------- | ------ | ------------------------------------------------------------------------------------ |
| GFE Override: Grid Connected | Button | (v2) Tell the panel the grid is up. Only present on MQTT-connected panels. See below |

### Grid Forming Entity

The Grid Forming Entity (GFE) identifies which power source provides the frequency and voltage reference for the home. When GFE is Grid, the utility grid sets
the reference and all circuits remain on. When GFE is Battery, the battery inverter is the reference and circuits are shed based on each circuit's configured
shed priority.

When a battery system (BESS) is installed, the panel relies on the BESS to determine whether the grid is online and to set the GFE accordingly. If BESS
communication is lost while the panel is islanded, the GFE value becomes stale — it may show Battery when the grid has actually been restored, causing
unnecessary shedding to continue.

The panel cannot detect grid restoration while islanded because the Microgrid Interconnect Device (MID) is open. The panel's power sensors are on the home side
of the open switch and measure only battery-supplied power — grid restoration on the utility side is invisible to any panel-side measurement. This is a physical
limitation, not a software gap. The `DSM State` sensor inherits the same blind spot for the same reason.

The **GFE Override: Grid Connected** button exists for this scenario. It publishes a temporary `GRID` command to the panel telling it the grid is back and
shedding can stop. When the BESS restores communication, it automatically reclaims control and the override is superseded.

#### Detecting grid restoration

The panel requires an external signal to know the grid is back while islanded. Options include:

- An Automatic Transfer Switch (ATS) or Manual Transfer Switch (MTS) with a utility-side contact closure, integrated into Home Assistant as a binary sensor
- Utility notification, neighbor confirmation, or physical observation

**WARNING** - Do _not_ automate the GFE override button based on `dsm_state` — it will read `DSM_OFF_GRID` even after the grid is restored because the panel's
sensors cannot see past the open MID. Manual confirmation or an external sensor is required before pressing the button.

Pressing "GFE Override: Grid Connected" when actually off-grid will prevent shedding and drain the battery faster. The battery protects itself by disconnecting
when depleted, so there is no overload risk, but runtime will be reduced.

When `bess_connected` returns to on, no action is needed — firmware resumes normal GFE management automatically.

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
   - Automatically updates when you rename circuits in the SPAN panel
   - More intuitive for automations and scripts

2. **Circuit Numbers** (Stable entity IDs)
   - Entity IDs use generic circuit numbers
   - Example: `sensor.span_panel_circuit_15_power`
   - Entity IDs remain stable even when circuits are renamed
   - Friendly names still sync from SPAN panel for display

### Energy Dip Compensation

SPAN panels occasionally report lower energy readings for cumulative energy sensors after firmware updates or resets. Home Assistant's statistics engine
interprets any decrease as a counter reset, creating negative spikes in the energy dashboard.

When enabled, the integration automatically detects these dips and maintains a cumulative offset per sensor so Home Assistant always sees a monotonically
increasing value.

- **Default for new installs:** ON
- **Default for existing installs:** OFF (enable via General Options)
- **Threshold:** 1.0 Wh minimum to avoid false triggers from float precision noise
- **Disabling:** Clears all accumulated offsets (starts fresh if re-enabled)

When a dip is detected, a persistent notification lists the affected sensors and their dip amounts.

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

## Troubleshooting

### Energy Dashboard Spikes After Firmware Updates

When the SPAN panel undergoes a firmware update or reset, it may report decreased energy values in otherwise `TOTAL_INCREASING` sensors, losing some range of
data. This errant drop causes a massive spike in the Home Assistant Energy Dashboard. This issue is on the SPAN firmware/cloud side and the only remedy we have
is to adjust Home Assistant statistics for SPAN sensors upward to their previous value. That adjustment is carried forward for all future stat-reporting times.

**Prevention:**

Enable **Energy Dip Compensation** in General Options (on by default for new installs). This automatically compensates for counter dips so they never reach Home
Assistant's statistics engine. See [Energy Dip Compensation](#energy-dip-compensation) above.

**Symptoms (if compensation is not enabled):**

- Huge energy consumption spikes appearing after panel firmware updates
- Charts showing unrealistic untracked values unrelated to a single sensor that dwarf normal usage
- Negative energy values in statistics

**Solution for existing spikes:**

Use **Developer Tools > Statistics** to find and adjust individual statistics entries. Search for the affected sensor (e.g.,
`sensor.span_panel_main_meter_consumed_energy`) to locate the errant spike and use the "Adjust sum" option to correct it.

**Note:** The integration monitors for decreases in the main meter consumed (TOTAL_INCREASING) sensor and will display a notification when one is detected.

### High CPU Usage

The integration rebuilds a full panel snapshot from MQTT messages at a configurable interval (default: 5 seconds). On low-power hardware such as a Raspberry Pi,
this can contribute to elevated CPU usage.

To reduce CPU load, increase the **Snapshot Update Interval** in **General Options**. A value of 10–15 seconds is recommended for resource-constrained systems.
Setting the interval to 0 disables debouncing entirely and rebuilds on every MQTT message, which is not recommended for most setups.

### Common Issues

1. **Door Sensor Unavailable** - The SPAN API may return UNKNOWN if the cabinet door has not been operated recently. This is a defect in the SPAN API — we
   report that sensor as unavailable until it reports a proper value. Opening or closing the door will reflect the proper value. The door state is classified as
   a tamper sensor (reflecting "Detected" or "Clear") to differentiate it from a normal entry door.
2. **No Switch** - If a circuit is set in the SPAN App as one of the "Always on Circuits", it will not have a switch because the API does not allow the user to
   control it.

## Development

See [Developer Documentation](docs/developer.md) for setup instructions, prerequisites, and tooling.

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
