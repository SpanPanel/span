# SPAN Panel Integration for Home Assistant

[Home Assistant](https://www.home-assistant.io/) Integration for [SPAN Panel](https://www.span.io/panel).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs) [![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/) [![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff) [![Mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/) [![isort](https://img.shields.io/badge/%20imports-isort-%231674b1?style=flat&labelColor=ef8336)](https://pycqa.github.io/isort/) [![prettier](https://img.shields.io/badge/code_style-prettier-ff69b4.svg)](https://github.com/prettier/prettier)

As SPAN has not published a documented API, we cannot guarantee this integration will work for you. The integration may break as your panel is updated if SPAN changes the API in an incompatible way.

The author(s) will try to keep this integration working, but cannot provide technical support for either SPAN or your homes electrical system. The software is provided as-is with no warranty or guarantee of performance or suitability to your particular setting.

What this integration does do is provide the user a Home Assistant integration that a user would find useful if they wanted so understand their power consumption, energy usage, and control panel circuits.

## Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS, select `Integrations`
3. This repository is not currently the default in HACs so you need to use it as a custom repository (We need two HACs developers to [approve](https://github.com/hacs/default/pull/2560) it). In the upper right of HACS/Integrations click on the three dots and add a custom repository with the HTTPS URL of this repository.
4. Select the repository you added in the list of integrations in HACS and select "Download". You can follow the URL to ensure you have the repository you want.
5. Restart Home Assistant.
6. In the Home Assistant UI go to `Settings`.
7. Click `Devices & Services' and you should see this integration.
8. Click `+ Add Integration`.
9. Search for "Span".
10. Enter the IP of your SPAN Panel to begin setup, or select the automatically discovered panel if it shows up.
11. Create an authentication token (see below) or use the door proximity authentication. Obtaining a token may be more durable to network changes, for example,if you change client hostname or IP.
12. See post install steps for solar or scan frequency configuration.

### Authorization (Auth) Token

The SPAN API requires an auth token.
If you already have one from some previous setup, you can reuse it.
If you don't already have a token (most people), you just need to prove that you are physically near the panel, and then the integration can get its own token.

#### Proof of Proximity

Simply open the door to your SPAN Panel and press the door sensor button at the top 3 times in succession.
The lights ringing the frame of your panel should blink momentarily, and the Panel will now be "unlocked" for 15 minutes (it may in fact be significantly longer, but 15 is the documented period).
While the panel is unlocked, it will allow the integration to create a new auth token.

#### Technical Details

These details were provided by a SPAN engineer, and have been implemented in the integration.
They are documented here in the hope someone may find them useful.

To get an auth token:

1. Using a tool like the VS code extension 'Thunder Client' or curl make a POST to `{Span_Panel_IP}/api/v1/auth/register` with a JSON body of `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local SPAN Integration"}`.
   - Use a unique value for UNIQUEID. Six random alphanumeric characters would be a reasonable choice. If the name conflicts with one that's already been created, then the request will fail.
   - Example via CLI: `curl -X POST https://192.168.1.2/api/v1/auth/register -H 'Content-Type: application/json' -d '{"name": "home-assistant-123456", "description": "Home Assistant Local SPAN Integration"}'`
2. If the panel is already "unlocked", you will get a 2xx response to this call containing the `"accessToken"`. If not, then you will be prompted to open and close the door of the panel 3 times, once every two seconds, and then retry the query.
3. Store the value from the `"accessToken"` property of the response. (It will be a long string of random characters). The token can be used with all future SPAN integration configurations.
4. This token can be used in the intial configuration. If you were calling the SPAN API directly all requests would load the HTTP header `"Authorization: Bearer <your token here>"`

_(If you have multiple SPAN Panels, you will need to repeat this process for each panel, as tokens are only accepted by the panel that generated them.)_

If you have this auth token, you can enter it in the "Existing Auth Token" flow in the configuration menu.

## Post-Install

Optional configuration

- Integration Scan Frequency (poll time in seconds), default is 15 seconds.
- Battery Storage Percentage.
- Enable/Map Solar Inverter Sensors to circuit(s).
  The solar in the USA is normally a combination of one or two leg poistions 1-32 or 0 indicating none.
  Look in your SPAN app for "solar" if any and identify the individual circuit(s).
  The leg values are combined into a single set of "inverter" sensors, for example in the USA two 120v legs of a 240v circuit positions 30/32.
  In Europe this configuration could be a single 230v leg where one leg is set to 0.

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

## Known Issues

"Feed Through" sensors may produce erroneous data if your panel is configured in certain ways that interact with solar or if the SPAN panel itself is returning data that is not meaningful to your installation. These sensors are related to the feed through lugs which may be used for a downstream panel.
If you are getting warnings in the log about a feed through sensor that has state class total_increasing, but its state is not strictly increasing you can opt to disable these sensors in the Home Assistant settings/devices/entities section:

- sensor.feed_through_consumed_energy
- sensor.feed_through_produced_energy

## Devices & Entities

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

### Entity Precision

The power sensors provided by this add-on report with the exact precision from the SPAN panel, which may be more decimal places than you will want for practical purposes.
By default the sensors will display with precision 2, for example `0.00`, with the exception of battery percentage. Battery percentage will have precision of 0, for example `39`.

You can change the display precision for any entity in Home Assistant via `Settings` -> `Devices & Services` -> `Entities` tab.
find the entity you would like to change in the list and click on it, then click on the gear wheel in the top right.
Select the precision you prefer from the "Display Precision" menu and then press `UPDATE`.

## License

This integration is published under the MIT license.

## Attribution and Contributions

This repository is a fork in a long line of span forks that may or may not be stable (from newer to older):

- SpanPanel/Span (current GitHub organization)
- cayossarian/span
- gdgib/span
- thetoothpick/span-hacs
- wez/span-hacs
- galak/span-hacs

Additional contributors:

- pavandave
- sargonas

## Issues

If you have a problem with the integration, feel free to [open an issue](https://github.com/SpanPanel/Span/issues), but please know issues regarding your network, SPAN configuration, or home electrical system are outside of our purview.

For those motivated, please consider offering suggestions for improvement in the discussions or opening a [pull request](https://github.com/SpanPanel/Span/pulls). We're generally very happy to have a starting point when making a change.

## Developer Notes

This project uses [poetry](https://python-poetry.org/) for dependency management. Linting and type checking is accomplished using [pre-commit](https://pre-commit.com/) which is installed by poetry.

If you are running Home Assistant (HA) core development locally in another location you can link this project's directory to your HA core directory. This arrangment will allow you to use the SPAN Panel integration in your Home Assistant instance while debugging in the HA core project and using the `SpanPanel/Span` workspace for git and other project operations.

For instance you can:

```bash
ln -s <span project path>/span/custom_components/span_panel <HA core path>/config/custom_components/span_panel
```

### Developer Setup

1. Install [poetry](https://python-poetry.org/).
2. Set the `HA_CORE_PATH` environment variable to the location of your Home Assistant core directory.
3. In the project root run `poetry install --with dev` to install dependencies.
4. Run `poetry run pre-commit install` to install pre-commit hooks.
5. Optionally use `poetry run pre-commit run --all-files` to manually run pre-commit hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed by the pre-commit hooks will be unstaged. After reviewing these changes, you can re-stage the changes and rerun the checks. After the pre-commit hook run succeeds, your commit can proceed.

### VS Code

You can set the `HA_CORE_PATH` environment for VS Code allowing you to use vscode git commands within the workspace GUI. See the .vscode/settings.json.example file for settings that configure the Home Assistant core location.
