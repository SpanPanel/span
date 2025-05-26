# SPAN Panel Integration for Home Assistant

[Home Assistant](https://www.home-assistant.io/) Integration for [SPAN Panel](https://www.span.io/panel), a smart electrical panel that provides circuit-level monitoring and control of your home's electrical system.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs) [![Python](https://img.shields.io/badge/python-3.13.2-blue.svg)](https://www.python.org/downloads/release/python-3132/) [![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff) [![Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black) [![Mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/) [![Pyright](https://microsoft.github.io/pyright/img/pyright_badge.svg)](https://microsoft.github.io/pyright/) [![prettier](https://img.shields.io/badge/code_style-prettier-ff69b4.svg)](https://github.com/prettier/prettier)

As SPAN has not published a documented API, we cannot guarantee this integration will work for you. The integration may break as your panel is updated if SPAN changes the API in an incompatible way.

We will try to keep this integration working, but cannot provide technical support for either SPAN or your home's electrical system. The software is provided as-is with no warranty or guarantee of performance or suitability to your particular setting.

What this integration does is provide the user with sensors and controls that are useful in understanding an installation's power consumption, energy usage, and the ability to control user-manageable panel circuits.

## What's New

### Version 1.1.0+ - Configurable Entity Naming Patterns

**Circuit Name Sync with SPAN**: All versions of the integration now support automatic friendly name updates when circuits are renamed in the SPAN panel. Names sync on the next poll interval. However, if you customize an entity's friendly name in Home Assistant, your customization will be preserved and won't be overwritten during sync. To re-enable sync for a customized entity, clear the custom name in Home Assistant.

**Flexible Entity Naming**: The integration now provides configurable entity naming patterns that can be changed at any time through the configuration options:

- **Friendly Names Pattern**: Entity IDs use descriptive circuit names for the entity ID (e.g., `sensor.span_panel_kitchen_outlets_power`) - ideal for installations where your circuits are less likely to change from their original purpose
- **Circuit Numbers Pattern**: Entity IDs use stable circuit numbers (e.g., `sensor.span_panel_circuit_15_power`) - the default for new installations as a generic entity ID is less likely to lose its meaning when repurposing circuits.

In either pattern, the friendly name provides the circuit meaning for easy identification.

**Migration Support**: Installations can migrate between entity naming patterns without losing entity history. Pre-1.0.4 installations can only migrate forward to other patterns that have device name prefixes added to entities.

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) installed
- [HACS](https://hacs.xyz/) installed
- SPAN Panel installed and connected to your network
- SPAN Panel's IP address

## Features

### Available Devices & Entities

This integration will provide a device for your SPAN panel. This device will have entities for:

- User Managed Circuits
  - On/Off Switch (user managed circuits)
  - Priority Selector (user managed circuits)
- Power Sensors
  - Power Usage / Generation (Watts)
  - Energy Usage / Generation (Wh)
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
12. Use the door proximity authentication (see below) and optionally create a token for future configurations. Obtaining a token **_may_** be more durable against network changes, for example, if you change client hostname or IP and don't want to access the panel for authorization.
13. See post install steps for solar or scan frequency configuration to optionally add additional sensors if applicable.

## Authorization Methods

### Method 1: Door Proximity Authentication

1. Open your SPAN Panel door
2. Press the door sensor button at the top 3 times in succession
3. Wait for the frame lights to blink, indicating the panel is "unlocked" for 15 minutes
4. Complete the integration setup in Home Assistant

### Method 2: Authentication Token (Optional)

To acquire an authorization token, proceed as follows while the panel is in its unlocked period:

1. To record the token use a tool like the VS code extension 'Rest Client' or curl to make a POST to `{Span_Panel_IP}/api/v1/auth/register` with a JSON body of `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local SPAN Integration"}`.
   - Replace UNIQUEID with your own random unique value. If the name conflicts with one that's already been created, then the request will fail.
   - Example via CLI: `curl -X POST https://192.168.1.2/api/v1/auth/register -H 'Content-Type: application/json' -d '{"name": "home-assistant-123456", "description": "Home Assistant Local SPAN Integration"}'`
2. If the panel is already "unlocked", you will get a 2xx response to this call containing the `"accessToken"`. If not, then you will be prompted to open and close the door of the panel 3 times, once every two seconds, and then retry the query.
3. Store the value from the `"accessToken"` property of the response (it will be a long random string of characters). The token can be used with future SPAN integration configurations of the same panel.
4. If you are calling the SPAN API directly for testing, requests would load the HTTP header `"Authorization: Bearer <your token here>"`

_(If you have multiple SPAN Panels, you will need to repeat this process for each panel, as tokens are only accepted by the panel that generated them.)_

If you have this auth token, you can enter it in the "Existing Auth Token" flow in the configuration menu.

## Configuration Options

### Basic Settings

- Integration scan frequency (default: 15 seconds)
- Battery storage percentage display
- Solar inverter mapping

### Entity Naming Pattern Options

The integration provides flexible entity naming patterns to suit different preferences and use cases. You can configure these options through the integration's configuration menu:

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

#### Changing Naming Patterns

You can switch between naming patterns at any time through:

1. Go to **Settings** → **Devices & Services**
2. Find your SPAN Panel integration
3. Click **Configure**
4. Select **Entity Naming Pattern**
5. Choose your preferred pattern and confirm

**Important Notes:**

- Changing patterns will rename existing entities in your Home Assistant registry
- Entity history is preserved during renaming
- Automations and scripts may need manual updates to use new entity IDs
- Consider backing up your configuration before making changes

### Solar Configuration

If the inverter sensors are enabled, three sensors are created. The entity naming pattern depends on your configured naming pattern:

**Circuit Numbers Pattern**:

```yaml
sensor.span_panel_circuit_30_32_instant_power    # (watts) - dual circuit
sensor.span_panel_circuit_30_32_energy_produced  # (Wh) - dual circuit
sensor.span_panel_circuit_30_32_energy_consumed  # (Wh) - dual circuit
```

**Friendly Names Pattern**:

```yaml
sensor.span_panel_solar_inverter_instant_power   # (watts)
sensor.span_panel_solar_inverter_energy_produced # (Wh)
sensor.span_panel_solar_inverter_energy_consumed # (Wh)
```

**Note**: For circuit numbers pattern, the numbers in the entity IDs (e.g., `30_32`) correspond to your configured inverter leg circuits. For single-circuit configurations, only one number appears (e.g., `circuit_30_instant_power`).

Disabling the inverter in the configuration removes these specific sensors. No reboot is required to add/remove these inverter sensors.

Although the solar inverter configuration is primarily aimed at installations that don't have a way to monitor their solar directly from their inverter, one could use this configuration to monitor any circuit(s) not provided directly by the underlying SPAN API for whatever reason. The two circuits are always added together to indicate their combined power if both circuits are enabled.

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

The power sensors provided by this add-on report with the exact precision from the SPAN panel, which may be more decimal places than you will want for practical purposes.
By default the sensors will display with precision 2, for example `0.00`, with the exception of battery percentage. Battery percentage will have precision of 0, for example `39`.

You can change the display precision for any entity in Home Assistant via `Settings` -> `Devices & Services` -> `Entities` tab.
Find the entity you would like to change in the list and click on it, then click on the gear wheel in the top right.
Select the precision you prefer from the "Display Precision" menu and then press `UPDATE`.

## Troubleshooting

### Common Issues

1. Door Sensor Unavailable - We have observed the SPAN API returning UNKNOWN if the cabinet door has not been operated recently. This behavior is a defect in the SPAN API, so we report that sensor as unavailable until it reports a proper value. Opening or closing the door will reflect the proper value. The door state is classified as a tamper sensor (reflecting 'Detected' or 'Clear') to differentiate the sensor from a normal entry door.

2. State Class Warnings - "Feed Through" sensors may produce erroneous data in the sense that logs may complain the sensor data is not constantly increasing when the sensor statistics type is set to total/increasing. These sensors reflect the feed through lugs which may be used for a downstream panel. If you are getting warnings in the log about a feed through sensor that has state class total_increasing, but its state is not strictly increasing, you can opt to disable these sensors in the Home Assistant settings/devices/entities section:

   ```text
   sensor.span_panel_feed_through_consumed_energy
   sensor.span_panel_feed_through_produced_energy
   ```

   **Note**: The exact entity names depend on your configured naming pattern. For circuit numbers pattern, feed through sensors use generic naming (e.g., `sensor.span_panel_circuit_X_consumed_energy` where X is the circuit number). For friendly names pattern, they use descriptive names based on the circuit purpose.

3. No Switch - If a circuit is set in the SPAN App as one of the "Always on Circuits", then that circuit will not have a switch because the API does not allow the user to control it.

4. Circuit Priority - The SPAN API doesn't allow the user to set the circuit priority. We leave this dropdown active because SPAN's browser also shows the dropdown. The circuit priority is affected by two settings the user can adjust in the SPAN app - the "Always-on circuits" which define critical or other must-have circuits. The PowerUp circuits are less clear, but what we know is that those at the top of the PowerUp list tend to be "Non-Essential", but this rule is inconsistent with respect to all circuit order, which may indicate a defect in SPAN PowerUp, the API, or indicate something we don't fully understand.

## Development Notes

### Developer Prerequisites

- Poetry
- Pre-commit
- Python 3.13.2+

This project uses [poetry](https://python-poetry.org/) for dependency management. Linting and type checking are accomplished using [pre-commit](https://pre-commit.com/) which is installed by poetry.

### Developer Setup

1. Install [poetry](https://python-poetry.org/).
2. In the project root run `poetry install --with dev` to install dependencies.
3. Run `poetry run pre-commit install` to install pre-commit hooks.
4. Optionally use `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks` or `poetry run pre-commit run --all-files` from the terminal to manually run pre-commit hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed or fail tests will be unstaged. After reviewing these changes or making corrections, you can re-stage the changes and recommit or rerun the checks. After the pre-commit hook run succeeds, your commit can proceed.

### VS Code

You can set the `HA_CORE_PATH` environment variable for VS Code, allowing you to use VS Code git commands within the workspace GUI. See the .vscode/settings.json.example file for settings that configure the Home Assistant core location.

## License

This integration is published under the MIT license.

## Attribution and Contributions

This repository is set up as part of an organization so a single committer is not the weak link. The repository is a fork in a long line of SPAN forks that may or may not be stable (from newer to older):

- SpanPanel/span (current GitHub organization, current repository, currently listed in HACS)
- SpanPanel/Span (was moved to https://github.com/SpanPanel/SpanCustom)
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

If you have a problem with the integration, feel free to [open an issue](https://github.com/SpanPanel/span/issues), but please know that issues regarding your network, SPAN configuration, or home electrical system are outside of our purview.

For those motivated, please consider offering suggestions for improvement in the discussions or opening a [pull request](https://github.com/SpanPanel/span/pulls). We're generally very happy to have a starting point when making a change.
