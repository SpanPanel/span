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

This integration relies on the OpenAPI interface contract sourced from the SPAN Panel. The integration may break if SPAN changes the API in an incompatible way.

We cannot provide technical support for either SPAN or your home's electrical system. The software is provided as-is with no warranty or guarantee of
performance or suitability to your particular setting.

This integration provides the user with sensors and controls that are useful in understanding an installation's power consumption, energy usage, and the ability
to control user-manageable panel circuits.

## What's New

### Major Upgrade

**Before upgrading to version 1.2.x, please backup your Home Assistant configuration and database.** This version introduces significant architectural changes.
While we've implemented migration logic to preserve your existing entities and automations, it's always recommended to have a backup before major upgrades.

**OpenAPI Support**: The integration now uses the OpenAPI specification provided by the SPAN panel. This change provides a reliable foundation for future
interface changes but some users have reported that newer panels might have closed off the interface (see trouble shooting). If and when SPAN provides
additional support we may adapt.

**New Features**: This version introduces net energy calculations, simulation support, configurable timeouts, SSL support, circuit name sync, and flexible
entity naming patterns. See the [CHANGELOG.md](CHANGELOG.md) for detailed information about all new features and improvements.

### HACS Upgrade Process

When upgrading through HACS, you'll see a notification about the new version. Before clicking "Update":

1. **Create a backup** of your Home Assistant configuration and database
2. **Review the changes** in this README
3. **Check your automations** to ensure they reference the correct entity IDs
4. **Update during a quiet period** when you can monitor the upgrade process

If you encounter any issues during the upgrade, you can:

- Restore from your backup
- Check the [troubleshooting section](#troubleshooting) below
- Open an issue on GitHub with details about your installation

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) installed
- [HACS](https://hacs.xyz/) installed
- SPAN Panel installed and connected to your network
- SPAN Panel's IP address

## Features

### Available Devices & Entities

This integration provides a Home Assistant device for your SPAN panel with entities for:

- User Managed Circuits
  - On/Off Switch (user managed circuits)
  - Priority Selector (user managed circuits)
- Power Sensors
  - Power Usage / Generation (Watts)
  - Energy Usage / Generation (Wh)
  - Net Energy (Wh) - Calculated as consumed energy minus produced energy
- Panel and Grid Status
  - Main Relay State (e.g., CLOSED)
  - Current Run Config (e.g., PANEL_ON_GRID)
  - DSM State (e.g., DSM_GRID_UP)
  - DSM Grid State (e.g., DSM_ON_GRID)
  - Network Connectivity Status (Wi-Fi, Wired, & Cellular)
  - Door State (device class is tamper)
- Storage Battery
  - Battery percentage (options configuration)

## Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS in the left side bar of your Home Assistant installation
3. Search for "Span"
4. Open the repository
5. Click on the "Download" button at the lower right
6. Restart Home Assistant - You will be prompted for this by a Home Assistant repair notification
7. In the Home Assistant UI go to `Settings`.
8. Click `Devices & Services` and you should see this integration.
9. Click `+ Add Integration`.
10. Search for "Span". This entry should correspond to this repository and offer the current version.
11. Enter the IP of your SPAN Panel to begin setup, or select the automatically discovered panel if it shows up or another address if you have multiple panels.
12. Use the door proximity authentication (see below) and optionally create a token for future configurations. Obtaining a token **_may_** be more durable
    against network changes, for example, if you change client hostname or IP and don't want to access the panel for authorization.
13. See post install steps for solar or scan frequency configuration to optionally add additional sensors if applicable.

## Authorization Methods

### Method 1: Door Proximity Authentication

1. Open your SPAN Panel door
2. Press the door sensor button at the top 3 times in succession
3. Wait for the frame lights to blink, indicating the panel is "unlocked" for 15 minutes
4. Complete the integration setup in Home Assistant

### Method 2: Authentication Token (Optional)

To acquire an authorization token, proceed as follows while the panel is in its unlocked period:

1. To record the token use a tool like the VS code extension 'Rest Client' or curl to make a POST to `{Span_Panel_IP}/api/v1/auth/register` with a JSON body of
   `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local SPAN Integration"}`.
   - Replace UNIQUEID with your own random unique value. If the name conflicts with one that's already been created, then the request will fail.
   - Example via CLI:

     ```bash
     curl -X POST https://192.168.1.2/api/v1/auth/register \
       -H 'Content-Type: application/json' \
       -d '{"name": "home-assistant-123456", "description": "Home Assistant Local SPAN Integration"}'
     ```

2. If the panel is already "unlocked", you will get a 2xx response to this call containing the `"accessToken"`. If not, then you will be prompted to open and
   close the door of the panel 3 times, once every two seconds, and then retry the query.
3. Store the value from the `"accessToken"` property of the response (it will be a long random string of characters). The token can be used with future SPAN
   integration configurations of the same panel.
4. If you are calling the SPAN API directly for testing, requests would load the HTTP header `"Authorization: Bearer <your token here>"`

#### Multiple SPAN Panels

If you have multiple SPAN Panels, you will need to repeat this process for each panel, as tokens are only accepted by the panel that generated them.

If you have this auth token, you can enter it in the "Existing Auth Token" flow in the configuration menu.

## Configuration Options

### Basic Settings

- Integration scan frequency (default: 15 seconds)
- Battery storage percentage display
- Solar inverter mapping

### Entity Naming Pattern Options

The integration provides flexible entity naming patterns to suit different preferences and use cases. You can configure these options through the integration's
configuration menu when you first install:

#### Available Naming Patterns

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

### Solar Configuration

The solar configuration is only for solar that is directly connected to the panel tabs. SPAN does expose data for inverters otherwise. If the inverter sensors
are enabled, four sensors are created (power, produced energy, consumed energy, and net energy). The entity naming pattern depends on your configured naming
pattern:

**Circuit Numbers Pattern**:

```yaml
sensor.span_panel_circuit_30_32_instant_power    # (watts) - dual circuit
sensor.span_panel_circuit_30_32_energy_produced  # (Wh) - dual circuit
sensor.span_panel_circuit_30_32_energy_consumed  # (Wh) - dual circuit
sensor.span_panel_circuit_30_32_energy_net       # (Wh) - dual circuit
```

**Friendly Names Pattern**:

```yaml
sensor.span_panel_solar_inverter_instant_power   # (watts)
sensor.span_panel_solar_inverter_energy_produced # (Wh)
sensor.span_panel_solar_inverter_energy_consumed # (Wh)
sensor.span_panel_solar_inverter_energy_net      # (Wh)
```

**Note**: For circuit numbers pattern, the numbers in the entity IDs (e.g., `30_32`) correspond to your configured inverter leg circuits. For single-circuit
configurations, only one number appears (e.g., `circuit_30_instant_power`).

Disabling the inverter in the configuration removes these specific sensors. No reboot is required to add/remove these inverter sensors.

Although the solar inverter configuration is primarily aimed at installations that don't have a way to monitor their solar directly from their inverter, one
could use this configuration to monitor any circuit(s) not provided directly by the underlying SPAN API for whatever reason. The two circuits are always added
together to indicate their combined power if both circuits are enabled.

Adding your own platform integration sensor like so converts to kWh:

```yaml
sensor
    - platform: integration
      source: sensor.span_panel_solar_inverter_instant_power  # Use appropriate entity ID for your installation
      name: Solar Inverter Produced kWh
      unique_id: sensor.solar_inverter_produced_kwh
     unit_prefix: k
     round: 2
```

### Customizing Entity Precision

The power sensors provided by this add-on report with the exact precision from the SPAN panel, which may be more decimal places than you will want for practical
purposes. By default the sensors will display with precision 2, for example `0.00`, with the exception of battery percentage. Battery percentage will have
precision of 0, for example `39`.

You can change the display precision for any entity in Home Assistant via `Settings` -> `Devices & Services` -> `Entities` tab. Find the entity you would like
to change in the list and click on it, then click on the gear wheel in the top right. Select the precision you prefer from the "Display Precision" menu and then
press `UPDATE`.

## Limitations

The original SPAN Panel MAIN 32 has a standardized OpenAPI endpoint that is leveraged by this integration.

However, the new SPAN Panel MAIN 40 and MLO 48 that were released in Q2 of 2025 leverage a different hardware/software stack, even going so far as to use a
different mobile app logins. This stack is not yet publicly documented and as such, we have not had a chance to discern how to support this stack at the time of
writing this. The underlying software may be the same codebase as the MAIN 32, so in theory, SPAN may provide access that we have yet to discover or that they
will eventually expose.

## Troubleshooting

### Energy Dashboard Spikes After Firmware Updates

When the SPAN panel undergoes a firmware update or reset, it may temporarily report incorrect energy values. This causes massive spikes (positive or negative)
in the Home Assistant Energy Dashboard.

**Symptoms:**

- Huge energy consumption spikes appearing after panel firmware updates
- Charts showing unrealistic values that dwarf normal usage
- Negative energy values in statistics

**Solution:**

Use the built-in cleanup service to remove the problematic statistics entries:

1. Go to **Developer Tools → Services**
2. Search for `span_panel.cleanup_energy_spikes`
3. First run with `dry_run: true` to preview what will be deleted
4. Review the persistent notification showing affected timestamps
5. Run again with `dry_run: false` to delete the problematic entries

```yaml
service: span_panel.cleanup_energy_spikes
data:
  days_back: 1 # Scan last 24 hours (up to 365 days)
  dry_run: false # Set to false to actually delete
```

**Note:** The integration automatically monitors for firmware resets and will send a notification when one is detected, prompting you to run the cleanup
service.

### Common Issues

1. Door Sensor Unavailable - We have observed the SPAN API returning UNKNOWN if the cabinet door has not been operated recently. This behavior is a defect in
   the SPAN API, so we report that sensor as unavailable until it reports a proper value. Opening or closing the door will reflect the proper value. The door
   state is classified as a tamper sensor (reflecting 'Detected' or 'Clear') to differentiate the sensor from a normal entry door.
2. No Switch - If a circuit is set in the SPAN App as one of the "Always on Circuits", then that circuit will not have a switch because the API does not allow
   the user to control it.
3. Circuit Priority - The SPAN API doesn't allow the user to set the circuit priority. We leave this dropdown active because SPAN's browser also shows the
   dropdown. The circuit priority is affected by two settings the user can adjust in the SPAN app - the "Always-on circuits" which define critical or other
   must-have circuits. The PowerUp circuits are less clear, but what we know is that those at the top of the PowerUp list tend to be "Non-Essential", but this
   rule is inconsistent with respect to all circuit order, which may indicate a defect in SPAN PowerUp, the API, or indicate something we don't fully
   understand.

## Development Notes

### Developer Prerequisites

- Poetry
- Pre-commit
- Python 3.13.2+

This project uses [poetry](https://python-poetry.org/) for dependency management. Linting and type checking are accomplished using
[pre-commit](https://pre-commit.com/) which is installed by poetry.

### Developer Setup

1. Install [poetry](https://python-poetry.org/).
2. In the project root run `poetry install --with dev` to install dependencies.
3. Run `poetry run pre-commit install` to install pre-commit hooks.
4. Optionally use `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks` or `poetry run pre-commit run --all-files` from the terminal to
   manually run pre-commit hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed or fail tests will be unstaged. After
reviewing these changes or making corrections, you can re-stage the changes and recommit or rerun the checks. After the pre-commit hook run succeeds, your
commit can proceed.

### VS Code

See the .vscode/settings.json.example file for starter settings

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

## Issues

If you have a problem with the integration, feel free to [open an issue](https://github.com/SpanPanel/span/issues), but please know that issues regarding your
network, SPAN configuration, or home electrical system are outside of our purview.

For those motivated, please consider offering suggestions for improvement in the discussions or opening a
[pull request](https://github.com/SpanPanel/span/pulls). We're generally very happy to have a starting point when making a change.
