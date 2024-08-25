# SPAN integration for HomeAssistant/HACS

[Home Assistant](https://www.home-assistant.io/) Integration for [Span smart panel](https://www.span.io/panel).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This integration should not have any negative impact on your Span installation, but as Span has not published a documented API, we cannot guarantee this will work for you, or that will not break as your panel is updated.
The author(s) will try to keep this integration working, but cannot provide technical support for either Span or your homes electrical system.

# Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS, select `Integrations`
3. If your using the default HACS repository (which may not be the latest repository) you can select it in the lower right repositories.  If your using a newer repository that is not the HACS default, in the upper right of HACS/Integrations click on the three dots and add a custom repository with the https URL of this repository.
5. Select the repository you added in the list of integrations in HACS and select "Download".  You can follow the URL to ensure you have the repository you want.
6. Restart Home Assistant
7. In the Home Assistant UI go to `Settings`
8. Click `Devices & Services' and you should see this integration
9. Click `+ Add Integration`
10. Search for "Span"
11. Enter the IP of your Span Panel to begin setup, or select the automatically discovered panel if it shows up.
12. Create an authentication token (see below) or the door proximity authenticaion.  Obtaining a token may be more durable to network changes, e.g., if you change client host name or IP.
13. See post intstall steps for solar or scan frequency configuration.

## Auth Token

The SPAN api requires an auth token.
If you already have one from some previous setup, you can reuse it.
If you don't already have a token (most people), you just need to prove that you are physically near the panel, and then the integration can get its own token.

### Proof of Proximity

Simply open the door to your Span Panel and press the door sensor button at the top 3 times in succession.
The lights ringing the frame of your panel should blink momentarily, and the Panel will now be "unlocked" for 15 minutes (it may in fact be significantly longer, but 15 is the documented period).
While the panel is unlocked, it will allow the integration to create a new auth token. 

### Technical Details

These details were provided by a SPAN engineer, and have been implemented in the integration.
They are documented here in the hope someone may find them useful.

To get an auth token:

1. Using a tool like the VS code extension 'Thunder Client' or curl make a POST to `{Span_Panel_IP}/api/v1/auth/register` with a JSON body of `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local Span Integration"}`.  
    * Use a unique value for UNIQUEID. Six random alphanumeric characters would be a reasonable choice. If the name conflicts with one that's already been created, then the request will fail.
    * Example via CLI: `curl -X POST https://192.168.1.2/api/v1/auth/register -H 'Content-Type: application/json' -d '{"name": "home-assistant-123456", "description": "Home Assistant Local Span Integration"}'`
2. If the panel is already "unlocked", you will get a 2xx response to this call containing the `"accessToken"`. If not, then you will be prompted to open and close the door of the panel 3 times, once every two seconds, and then retry the query.
3. Store the value from the `"accessToken"` property of the response. (It will be a long string, such as `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"` for example). This is the token which should be included with all future requests.
4. This token can be used in the intial configuration.   Send all future requests with the HTTP header `"Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"` (Remember, this is just a dummy example token!)

_(If you have multiple Span Panels, you will need to repeat this process for each panel, as tokens are only accepted by the panel that generated them.)_

If you have this auth token, you can enter it in the "Existing Auth Token" flow in the configuration menu.

# Post-Install

Optional configuration

* Integration Scan Frequency (poll time in seconds), default is 15 seconds
* Enable/Map Solar Inverter Sensors to circuit(s) (a combination of one or two leg poistions 1-32 or 0 indicating none).  Look in your Span app for "solar" if any and identify the individual circuit(s).  The leg values are combined into a single set of "inverter" sensors, e.g., two 120v legs of a 240v circuit in the US position 30/32.  In Europe this configuration could be a single 230v leg where one leg is set to 0.  

If the inverter sensors are enabled three sensors are created:

```yaml
sensor.solar_inverter_instant_power # (watts)
sensor.solar_inverter_energy_produce # (Wh)
sensor.solar_inverter_energy_consumed # (Wh)
```

Disabling the inverter in the configuration removes these specific sensors. No reboot is required to add/remove these inverter sensors.  

Although the solar inverter configuration is primarily aimed at installations that don't have a way to monitor their solar directly from their inverter one could use this configuration to monitor any circuit(s) not provided directly by the underlying SPAN API for whatever reason.  The two circuits are always added together to indicate their combined power if both circuits are enabled.

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

# Known Issue
"Feed Through" sensors may produce erroneous data if your panel is configured in certain ways that interact with solar or if the SPAN panel itself is returning bad data.  These sensors are related to the feed through lugs which may be used for a downstream panel.
If you are getting warnings in the log about a feed through sensor that has state class total_increasing, but its state is not strictly increasing you can opt to disable these sensors in the Home Assistant settings/devices/entities section:
* sensor.feed_through_consumed_energy
* sensor.feed_through_produced_energy

# Devices & Entities

This integration will provide a device for your span panel. This device will have entities for:

* User Managed Circuits
  * On/Off Switch (user managed circuits)
  * Priority Selector (user managed circuits)
* Power Sensors
  * Power Usage / Generation (Watts)
  * Energy Usage / Generation (wH)
* Panel and Grid Status
   * Main Relay State (e.g., CLOSED)
   * Current Run Config (e.g., PANEL_ON_GRID)
   * DSM State (e.g., DSM_GRID_UP)
   * DSM Grid State (e.g., DSM_ON_GRID)
   * Network Connectivity Status (Wi-Fi, Wired, & Cellular)
   * Door State (device class is tamper)

## Entity Precision

The power sensors provided by this add-on report with the exact precision from the SPAN panel, which may be more decimal places than you will want for practical purposes.
By default the sensors will display with precision 2 (e.g. `0.00`).

You can change the display precision for any entity in HomeAssistant via `Settings` -> `Devices & Services` -> `Entities` tab.
find the entity you would like to change in the list and click on it, then click on the gear wheel in the top right.
Select the precision you prefer from the "Display Precision" menu and then press `UPDATE`.

# License

This integration is published under the MIT license.

# Attribution

This repository is a fork in a long line of span forks that may or may not be stable (from newer to older):
  gdgib/span
  thetoothpick/span-hacs
  wez/span-hacs
  galak/span-hacs  

# Issues & Contribution
If you have a problem, feel free to [open an issue](https://github.com/cayossarian/span/issues), but please know issues regarding your network, Span configuration, or home electrical system are outside of our purview.
For those capable, please consider opening even a low quality [pull request](https://github.com/cayossarian/span/pulls) when possible, as we're generally very happy to have a starting point when making a change.
