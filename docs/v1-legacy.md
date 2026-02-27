# SPAN Panel Integration — v1 Legacy Documentation

> **Deprecation Notice:** v1 REST API support is deprecated. SPAN is rolling out v2 firmware with eBus MQTT support across all panels. Users should upgrade to
> v2 firmware when available. The main [README](../README.md) documents the current v2 integration.

## Overview

The v1 integration communicates with the SPAN Panel via its OpenAPI REST interface, polling at a configurable interval. This document preserves the v1-specific
setup instructions for users still running pre-v2 firmware.

## Authorization Methods

### Method 1: Door Proximity Authentication

1. Open your SPAN Panel door
2. Press the door sensor button at the top 3 times in succession
3. Wait for the frame lights to blink, indicating the panel is "unlocked" for 15 minutes
4. Complete the integration setup in Home Assistant

### Method 2: Authentication Token (Optional)

To acquire an authorization token, proceed as follows while the panel is in its unlocked period:

1. Use a tool like the VS Code extension "Rest Client" or curl to make a POST to `{Span_Panel_IP}/api/v1/auth/register` with a JSON body of
   `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local SPAN Integration"}`.
   - Replace UNIQUEID with your own random unique value. If the name conflicts with one that's already been created, the request will fail.
   - Example via CLI:

     ```bash
     curl -X POST https://192.168.1.2/api/v1/auth/register \
       -H 'Content-Type: application/json' \
       -d '{"name": "home-assistant-123456", "description": "Home Assistant Local SPAN Integration"}'
     ```

2. If the panel is already "unlocked", you will get a 2xx response containing the `"accessToken"`. If not, you will be prompted to open and close the door of
   the panel 3 times, once every two seconds, and then retry the query.
3. Store the value from the `"accessToken"` property of the response (a long random string). The token can be used with future SPAN integration configurations
   of the same panel.
4. For direct API testing, load the HTTP header `"Authorization: Bearer <your token here>"`

### Multiple SPAN Panels

If you have multiple SPAN Panels, repeat the authorization process for each panel — tokens are only accepted by the panel that generated them.

If you have an auth token, enter it in the "Existing Auth Token" flow in the configuration menu.

## Configuration Options (v1-specific)

### Scan Frequency

The v1 integration polls the SPAN REST API at a configurable interval (default: 15 seconds). This setting is not applicable to v2, which uses real-time MQTT
push.

### Solar Inverter Leg Configuration

The solar configuration is for solar that is directly connected to the panel tabs. If the inverter sensors are enabled, four sensors are created (power,
produced energy, consumed energy, and net energy). The entity naming pattern depends on your configured naming pattern:

**Circuit Numbers Pattern:**

```yaml
sensor.span_panel_circuit_30_32_instant_power    # (watts) - dual circuit
sensor.span_panel_circuit_30_32_energy_produced  # (Wh) - dual circuit
sensor.span_panel_circuit_30_32_energy_consumed  # (Wh) - dual circuit
sensor.span_panel_circuit_30_32_energy_net       # (Wh) - dual circuit
```

**Friendly Names Pattern:**

```yaml
sensor.span_panel_solar_inverter_instant_power   # (watts)
sensor.span_panel_solar_inverter_energy_produced # (Wh)
sensor.span_panel_solar_inverter_energy_consumed # (Wh)
sensor.span_panel_solar_inverter_energy_net      # (Wh)
```

**Note:** For circuit numbers pattern, the numbers in the entity IDs (e.g., `30_32`) correspond to your configured inverter leg circuits. For single-circuit
configurations, only one number appears (e.g., `circuit_30_instant_power`).

Disabling the inverter in the configuration removes these specific sensors. No reboot is required to add/remove these inverter sensors.

Although the solar inverter configuration is primarily aimed at installations that don't have a way to monitor their solar directly from their inverter, one
could use this configuration to monitor any circuit(s) not provided directly by the underlying SPAN API. The two circuits are always added together to indicate
their combined power if both circuits are enabled.

### Custom kWh Sensor

Adding your own platform integration sensor converts Wh to kWh:

```yaml
sensor:
  - platform: integration
    source: sensor.span_panel_solar_inverter_instant_power # Use appropriate entity ID
    name: Solar Inverter Produced kWh
    unique_id: sensor.solar_inverter_produced_kwh
    unit_prefix: k
    round: 2
```

## OpenAPI Reference

The v1 integration relies on the OpenAPI interface contract sourced from the SPAN Panel. The integration may break if SPAN changes the API in an incompatible
way. This contract is specific to the SPAN Panel MAIN 32 hardware.
