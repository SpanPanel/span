# SPAN Panel Integration for Home Assistant

[Home Assistant](https://www.home-assistant.io/) Integration for [SPAN Panel](https://www.span.io/panel), a smart electrical panel that provides circuit-level monitoring and control of your home's electrical system.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs) [![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/) [![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff) [![Mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/) [![isort](https://img.shields.io/badge/%20imports-isort-%231674b1?style=flat&labelColor=ef8336)](https://pycqa.github.io/isort/) [![prettier](https://img.shields.io/badge/code_style-prettier-ff69b4.svg)](https://github.com/prettier/prettier)

As SPAN has not published a documented API, we cannot guarantee this integration will work for you. The integration may break as your panel is updated if SPAN changes the API in an incompatible way.

We will try to keep this integration working, but cannot provide technical support for either SPAN or your homes electrical system. The software is provided as-is with no warranty or guarantee of performance or suitability to your particular setting.

What this integration does do is provide the user sensors and controls that are useful in understanding an installations power consumption, energy usage, and the ability to control user-manageable panel circuits.

## Notice on Forks

This https://github.com/SpanPanel/span repository is the default listed in the HACS store.

If you were using the custom HACs repository https://github.com/SpanPanel/Span (note the upper case Span) before 2025-01-14 that repository has been moved to https://github.com/SpanPanel/SpanCustom, and will not be updated. See the notes there to migrate to this repository without affecting your sensor names. As of 1.06 the default sensor names are prefixed with the device name so the entities can be renamed enmasse but retaining the old names is possible as noted in the custom repository notes.

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
  - Energy Usage / Generation (wH)
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
6. Restart Home Assistant - You will be prompted for this by a HomeAssistant repair
7. In the Home Assistant UI go to `Settings`.
8. Click `Devices & Services` and you should see this integration.
9. Click `+ Add Integration`.
10. Search for "Span". This entry should correspond to this repository and offer the current version.
11. Enter the IP of your SPAN Panel to begin setup, or select the automatically discovered panel if it shows up or another address if you have multiple panels.
12. Use the door proximity authentication (see below) and optionally create a token for future configurations. Obtaining a token **_may_** be more durable to network changes, for example, if you change client hostname or IP and don't want to access the panel for authorization.
13. See post install steps for solar or scan frequency configuration to optionally add additional sensors if applicable.

## Authorization Methods

### Method 1: Door Proximity Authentication

1. Open your SPAN Panel door
2. Press the door sensor button at the top 3 times in succession
3. Wait for the frame lights to blink, indicating the panel is "unlocked" for 15 minutes
4. Complete the integration setup in Home Assistant

### Method 2: Authentication Token (Optional)

To acquire an authorization token proceed as follows while the panel is in its unlocked period:

1. To record the token use a tool like the VS code extension 'Rest Client' or curl to make a POST to `{Span_Panel_IP}/api/v1/auth/register` with a JSON body of `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local SPAN Integration"}`.
   - Replace UNIQUEID with your own random unique value. If the name conflicts with one that's already been created, then the request will fail.
   - Example via CLI: `curl -X POST https://192.168.1.2/api/v1/auth/register -H 'Content-Type: application/json' -d '{"name": "home-assistant-123456", "description": "Home Assistant Local SPAN Integration"}'`
2. If the panel is already "unlocked", you will get a 2xx response to this call containing the `"accessToken"`. If not, then you will be prompted to open and close the door of the panel 3 times, once every two seconds, and then retry the query.
3. Store the value from the `"accessToken"` property of the response. (It will be a long random string of characters). The token can be used with future SPAN integration configurations of the same panel.
4. If you are calling the SPAN API directly for testing requests would load the HTTP header `"Authorization: Bearer <your token here>"`

_(If you have multiple SPAN Panels, you will need to repeat this process for each panel, as tokens are only accepted by the panel that generated them.)_

If you have this auth token, you can enter it in the "Existing Auth Token" flow in the configuration menu.

## Configuration Options

### Basic Settings

- Integration scan frequency (default: 15 seconds)
- Battery storage percentage display
- Solar inverter mapping

### Solar Configuration

If the inverter sensors are enabled three sensors are created:

```yaml
sensor.solar_inverter_instant_power # (watts)
sensor.solar_inverter_energy_produce # (Wh)
sensor.solar_inverter_energy_consumed # (Wh)
```

Disabling the inverter in the configuration removes these specific sensors. No reboot is required to add/remove these inverter sensors.

Although the solar inverter configuration is primarily aimed at installations that don't have a way to monitor their solar directly from their inverter one could use this configuration to monitor any circuit(s) not provided directly by the underlying SPAN API for whatever reason. The two circuits are always added together to indicate their combined power if both circuits are enabled.

Adding your own platform integration sensor like so converts to kWh:

```yaml
sensor
    - platform: integration
      source: sensor.solar_inverter_instant_power
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

1. Door Sensor Unavailable - We have observed the SPAN API returning UNKNOWN if the cabinet door has not been operated recently. This behavior is a defect in the SPAN API so we report that sensor as unavailable until it reports a proper value. Opening or closing the door will reflect the proper value. The door state is classified as a tamper sensor (reflecting 'Detected' or 'Clear') to differentiate the sensor from a normal entry door.

2. State Class Warnings - "Feed Through" sensors may produce erroneous data in the sense the logs may complain the sensor data is not constantly increasing when the sensor statistics type is set to total/increasing. These sensors reflect the feed through lugs which may be used for a downstream panel. If you are getting warnings in the log about a feed through sensor that has state class total_increasing, but its state is not strictly increasing you can opt to disable these sensors in the Home Assistant settings/devices/entities section:

   ```text
   sensor.feed_through_consumed_energy
   sensor.feed_through_produced_energy
   ```

3. Entity Names and Device Renaming Errors - Prior to version 1.0.4 entity names were not prefixed with the device name so renaming a device did not allow a user to rename the entities accordingly. Newer versions of the integration use the device name prefix on a **new** configuration. An existing, pre-1.0.4 integration that is upgraded will not result in device prefixes in entity names to avoid breaking dependent dashboards and automations. If you want device name prefixes, install at least 1.0.4, delete the configuration and reconfigure it.

4. Circuit Priority - The SPAN API doesn't allow the user to set the circuit priority. We leave this drop down active because SPAN's browser also shows the drop down. The circuit priority is affected by two settings the user can adjust in the SPAN app - the "Always-on circuits" which define router or other must have circuits. Always On circuits are set to "must-have" and are subsequently not user controlled (meaining you can't turn them off and no switch is provided for these circuits in the integration). If you remove a circuit from the always-on list and reload the integration you should see a switch for that circuit. The PowerUp circuits are less clear but what we know is that those at the top of the PowerUp list tend to be "Non-Essential" but this rule is inconsistent with respect to all circuit order which may indicate a defect in SPAN PowerUp, the API, or indicate something we don't fully know the details.

## Development Notes

### Developer Prerequisites

- Poetry
- Pre-commit
- Python 3.12+

This project uses [poetry](https://python-poetry.org/) for dependency management. Linting and type checking is accomplished using [pre-commit](https://pre-commit.com/) which is installed by poetry.

If you are running Home Assistant (HA) core development locally in another location you can link this project's directory to your HA core directory. This arrangement will allow you to use the SPAN Panel integration in your Home Assistant instance while debugging in the HA core project and using the `SpanPanel/Span` workspace for git and other project operations.

For instance you can:

```bash
ln -s <span project path>/span/custom_components/span_panel <HA core path>/config/custom_components/span_panel
```

### Developer Setup

1. Install [poetry](https://python-poetry.org/).
2. Set the `HA_CORE_PATH` environment variable to the location of your Home Assistant core directory.
3. In the project root run `poetry install --with dev` to install dependencies.
4. Run `poetry run pre-commit install` to install pre-commit hooks.
5. Optionally use `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks` or `poetry run pre-commit run --all-files` from the terminal to manually run pre-commit hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed or fail tests will be unstaged. After reviewing these changes or making corrections, you can re-stage the changes and recommit or rerun the checks. After the pre-commit hook run succeeds, your commit can proceed.

### VS Code

You can set the `HA_CORE_PATH` environment for VS Code allowing you to use vscode git commands within the workspace GUI. See the .vscode/settings.json.example file for settings that configure the Home Assistant core location.

## License

This integration is published under the MIT license.

## Attribution and Contributions

This repository is set up as part of an organization so a single committer is not the weak link. The repostiorry is a fork in a long line of span forks that may or may not be stable (from newer to older):

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

If you have a problem with the integration, feel free to [open an issue](https://github.com/SpanPanel/span/issues), but please know issues regarding your network, SPAN configuration, or home electrical system are outside of our purview.

For those motivated, please consider offering suggestions for improvement in the discussions or opening a [pull request](https://github.com/SpanPanel/span/pulls). We're generally very happy to have a starting point when making a change.
