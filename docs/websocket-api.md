# WebSocket API

The integration exposes WebSocket commands for programmatic access to panel topology and entity mappings. These commands are available to custom cards,
AppDaemon scripts, or any WebSocket client connected to Home Assistant.

## `span_panel/panel_topology`

Returns the full physical layout of a SPAN panel in a single call — circuits with their breaker slot positions, entity IDs grouped by role (power, energy,
switch, select), and sub-devices (BESS, EVSE) with their entities.

A custom card rendering the physical panel needs to know which breaker slot each circuit occupies, which entity provides its power reading, which switch
controls its relay, and so on. Without this command, the card would need to query the device registry, entity registry, and individual entity states in separate
calls, then infer which entities belong to the same circuit by parsing naming conventions. That correlation is fragile — entity naming patterns can differ
between installs, and EVSE feed circuit sensors live on the EVSE sub-device rather than the panel device. The topology command provides all of these
relationships explicitly, keyed by circuit UUID, so the card reads a single structured response instead of guessing.

### Request

```json
{
  "type": "span_panel/panel_topology",
  "device_id": "<ha_device_registry_id>"
}
```

| Field       | Type   | Description                                                                                             |
| ----------- | ------ | ------------------------------------------------------------------------------------------------------- |
| `device_id` | string | The Home Assistant device registry ID for the SPAN panel. Found in the URL when viewing the device page |

### Response

```json
{
  "serial": "nj-2316-005k6",
  "firmware": "spanos2/r202603/05",
  "panel_size": 32,
  "device_id": "abc123def456",
  "device_name": "SPAN Panel",
  "circuits": {
    "a1b2c3d4e5f6": {
      "tabs": [5, 6],
      "name": "Kitchen",
      "voltage": 240,
      "device_type": "circuit",
      "relay_state": "CLOSED",
      "is_user_controllable": true,
      "breaker_rating_a": 30,
      "entities": {
        "power": "sensor.span_panel_kitchen_power",
        "produced_energy": "sensor.span_panel_kitchen_produced_energy",
        "consumed_energy": "sensor.span_panel_kitchen_consumed_energy",
        "net_energy": "sensor.span_panel_kitchen_net_energy",
        "current": "sensor.span_panel_kitchen_current",
        "breaker_rating": "sensor.span_panel_kitchen_breaker_rating",
        "switch": "switch.span_panel_kitchen_breaker",
        "select": "select.span_panel_kitchen_circuit_priority"
      }
    },
    "f6e5d4c3b2a1": {
      "tabs": [15],
      "name": "Master Bedroom",
      "voltage": 120,
      "device_type": "circuit",
      "relay_state": "CLOSED",
      "is_user_controllable": true,
      "breaker_rating_a": 15,
      "entities": {
        "power": "sensor.span_panel_master_bedroom_power",
        "switch": "switch.span_panel_master_bedroom_breaker"
      }
    }
  },
  "sub_devices": {
    "device_id_bess": {
      "name": "SPAN Panel Battery",
      "type": "bess",
      "manufacturer": "Enphase",
      "model": "IQ Battery 10T",
      "serial_number": "SN-BESS-001",
      "sw_version": "1.2.3",
      "entities": {
        "sensor.span_panel_battery_level": {
          "domain": "sensor",
          "original_name": "Battery Level",
          "unique_id": "..."
        }
      }
    },
    "device_id_evse": {
      "name": "SPAN Panel SPAN Drive (Garage)",
      "type": "evse",
      "manufacturer": "SPAN",
      "model": "SPAN Drive",
      "serial_number": "SN-EVSE-001",
      "sw_version": "2.0.1",
      "entities": {
        "sensor.span_panel_span_drive_garage_charger_status": {
          "domain": "sensor",
          "original_name": "Charger Status",
          "unique_id": "..."
        }
      }
    }
  }
}
```

### Response Fields

#### Top Level

| Field         | Type        | Description                                     |
| ------------- | ----------- | ----------------------------------------------- |
| `serial`      | string      | Panel serial number                             |
| `firmware`    | string      | Panel firmware version                          |
| `panel_size`  | int or null | Total breaker spaces (e.g., 32, 40)             |
| `device_id`   | string      | HA device registry ID (echoed from request)     |
| `device_name` | string      | HA device display name                          |
| `circuits`    | object      | Circuit UUID keyed map (see below)              |
| `sub_devices` | object      | HA device ID keyed map of BESS/EVSE (see below) |

#### Circuit Object

| Field                  | Type        | Description                                    |
| ---------------------- | ----------- | ---------------------------------------------- |
| `tabs`                 | int[]       | Sorted breaker slot positions (1-indexed)      |
| `name`                 | string/null | Circuit name from the panel (null if unnamed)  |
| `voltage`              | int         | 120 (single tab) or 240 (double tab)           |
| `device_type`          | string      | `circuit`, `pv`, or `evse`                     |
| `relay_state`          | string      | `CLOSED`, `OPEN`, or `UNKNOWN`                 |
| `is_user_controllable` | bool        | Whether the circuit relay can be toggled       |
| `breaker_rating_a`     | float/null  | Breaker amperage rating (null if not reported) |
| `entities`             | object      | Role-keyed map of entity IDs (see below)       |

#### Circuit Entity Roles

| Role              | Domain | Description            |
| ----------------- | ------ | ---------------------- |
| `power`           | sensor | Instantaneous power    |
| `produced_energy` | sensor | Cumulative produced Wh |
| `consumed_energy` | sensor | Cumulative consumed Wh |
| `net_energy`      | sensor | Net energy Wh          |
| `current`         | sensor | Measured current       |
| `breaker_rating`  | sensor | Breaker amperage       |
| `switch`          | switch | Relay on/off control   |
| `select`          | select | Shed priority control  |

Not all roles are present on every circuit. Roles are omitted when the entity does not exist (e.g., `current` is absent if the panel does not report per-circuit
current, `switch` is absent for always-on circuits).

#### Sub-Device Object

| Field           | Type        | Description                           |
| --------------- | ----------- | ------------------------------------- |
| `name`          | string      | HA device display name                |
| `type`          | string      | `bess`, `evse`, or `unknown`          |
| `manufacturer`  | string/null | Device manufacturer                   |
| `model`         | string/null | Device model                          |
| `serial_number` | string/null | Device serial number                  |
| `sw_version`    | string/null | Device firmware/software version      |
| `entities`      | object      | Entity ID keyed map with domain, name |

### Errors

| Code             | Description                                   |
| ---------------- | --------------------------------------------- |
| device_not_found | The device_id does not exist in HA            |
| not_span_panel   | The device is not a SPAN Panel device         |
| not_loaded       | The integration or config entry is not loaded |
| no_data          | The coordinator has no panel data yet         |

### Usage from a Custom Card

```javascript
const topology = await this.hass.callWS({
  type: "span_panel/panel_topology",
  device_id: this._config.device_id,
});

// topology.circuits is keyed by circuit UUID
for (const [circuitId, circuit] of Object.entries(topology.circuits)) {
  // circuit.tabs => [5, 6] (breaker positions)
  // circuit.entities.power => "sensor.span_panel_kitchen_power"
  const powerState = this.hass.states[circuit.entities.power];
}
```

### Multi-Panel Homes

Each panel is a separate config entry with its own device ID. To render multiple panels, call `span_panel/panel_topology` once per panel device ID. The response
is scoped to a single panel — circuits, sub-devices, and entity mappings from other panels are never included.
