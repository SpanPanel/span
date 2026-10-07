# WebSocket API

The integration exposes WebSocket commands for programmatic access to panel topology and entity mappings. These commands are available to custom cards,
AppDaemon scripts, or any WebSocket client connected to Home Assistant.

## `span_panel/panel_topology`

Returns the full physical layout of a SPAN panel in a single call — circuits with their breaker slot positions, entity IDs grouped by role (power, energy,
switch, select), and sub-devices (BESS, MID, EVSE, PV) with their entities.

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

A device ID saved before Home Assistant 2026.8 is still accepted. That release split every device belonging to several config entries into one device per entry,
each with a new ID, so an older card configuration can hold the panel's previous ID; it resolves to the panel device SPAN owns now, and `device_id` in the
response echoes the ID as sent. Take the panel's identity from `panel_device_id` and `config_entry_id` in the response rather than by looking `device_id` up in
Home Assistant's device list, which no longer holds a pre-2026.8 ID.

### Response

```json
{
  "serial": "nj-2316-005k6",
  "firmware": "spanos2/r202603/05",
  "panel_size": 32,
  "device_id": "abc123def456",
  "panel_device_id": "abc123def456",
  "config_entry_id": "e5f6a7b8c9d0",
  "device_name": "SPAN Panel",
  "circuits": {
    "a1b2c3d4e5f6": {
      "tabs": [5, 6],
      "name": "Kitchen",
      "voltage": 240,
      "device_type": "circuit",
      "relay_state": "CLOSED",
      "relay_state_target": null,
      "is_user_controllable": true,
      "breaker_rating_a": 30,
      "always_on": false,
      "priority": "SOC_THRESHOLD",
      "priority_target": null,
      "is_never_backup": false,
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
      "relay_state_target": null,
      "is_user_controllable": true,
      "breaker_rating_a": 15,
      "always_on": false,
      "priority": "NEVER",
      "priority_target": null,
      "is_never_backup": true,
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
    },
    "device_id_solar": {
      "name": "SPAN Panel Solar",
      "type": "pv",
      "manufacturer": "Enphase",
      "model": "IQ8PLUS-72-2-US",
      "serial_number": null,
      "sw_version": null,
      "entities": {
        "sensor.span_panel_solar_pv_power": {
          "domain": "sensor",
          "original_name": "PV Power",
          "unique_id": "..."
        }
      },
      "solar": {
        "role": "site",
        "vendor": "Enphase",
        "model": "IQ8PLUS-72-2-US",
        "feed_circuit_id": "c1d2e3f4a5b6",
        "power_entity_id": "sensor.span_panel_solar_inverter_power",
        "site_power_entity_id": "sensor.span_panel_solar_pv_power"
      }
    },
    "device_id_solar_inverter": {
      "name": "SPAN Panel Solar Inverter (Garage Solar)",
      "type": "pv",
      "manufacturer": "SolarEdge",
      "model": "SE7600H-US",
      "serial_number": null,
      "sw_version": null,
      "entities": {
        "sensor.span_panel_solar_inverter_garage_solar_pv_vendor": {
          "domain": "sensor",
          "original_name": "PV Vendor",
          "unique_id": "..."
        }
      },
      "solar": {
        "role": "inverter",
        "vendor": "SolarEdge",
        "model": "SE7600H-US",
        "feed_circuit_id": "b6a5f4e3d2c1",
        "power_entity_id": "sensor.span_panel_garage_solar_power",
        "site_power_entity_id": null
      }
    }
  }
}
```

### Response Fields

#### Top Level

| Field             | Type        | Description                                            |
| ----------------- | ----------- | ------------------------------------------------------ |
| `serial`          | string      | Panel serial number                                    |
| `firmware`        | string      | Panel firmware version                                 |
| `panel_size`      | int or null | Total breaker spaces (e.g., 32, 40)                    |
| `device_id`       | string      | HA device registry ID (echoed from request)            |
| `panel_device_id` | string      | The panel's current HA device registry ID              |
| `config_entry_id` | string      | The config entry that owns the panel                   |
| `device_name`     | string      | HA device display name                                 |
| `circuits`        | object      | Circuit UUID keyed map (see below)                     |
| `sub_devices`     | object      | HA device ID keyed map of BESS/MID/EVSE/PV (see below) |

#### Circuit Object

| Field                  | Type        | Description                                                                                |
| ---------------------- | ----------- | ------------------------------------------------------------------------------------------ |
| `tabs`                 | int[]       | Sorted breaker slot positions (1-indexed)                                                  |
| `name`                 | string/null | Circuit name from the panel (null if unnamed)                                              |
| `voltage`              | int/null    | 120 (single tab), 240 (double tab), or null where the pole count does not say              |
| `device_type`          | string      | `circuit`, `pv`, or `evse`                                                                 |
| `relay_state`          | string      | `CLOSED`, `OPEN`, or `UNKNOWN`                                                             |
| `relay_state_target`   | string/null | The relay state last commanded and not yet reached, or null                                |
| `always_on`            | bool        | Whether the panel keeps this circuit on and offers no relay control                        |
| `priority`             | string      | Shed priority as the panel publishes it: `NEVER`, `SOC_THRESHOLD`, `OFF_GRID` or `UNKNOWN` |
| `priority_target`      | string/null | The priority last commanded and not yet reached, or null                                   |
| `is_never_backup`      | bool        | Whether the panel pins the priority, so it cannot be set                                   |
| `is_user_controllable` | bool        | Whether the circuit relay can be toggled                                                   |
| `breaker_rating_a`     | float/null  | Breaker amperage rating (null if not reported)                                             |
| `entities`             | object      | Role-keyed map of entity IDs (see below)                                                   |

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

Not all roles are present on every circuit. A role is omitted when its entity does not exist: `current` is absent if the panel does not report per-circuit
current. `switch` and `select` are present only while the integration provides that control, meaning the live circuit qualifies for it and **Who may operate the
panel** is not **Nobody**. An unavailable state on a present `switch` or `select` is transient.

#### Sub-Device Object

| Field           | Type        | Description                                                                                                                                                                     |
| --------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`          | string      | HA device display name                                                                                                                                                          |
| `type`          | string      | `bess`, `mid`, `evse`, `pv`, or `unknown`                                                                                                                                       |
| `manufacturer`  | string/null | Device manufacturer                                                                                                                                                             |
| `model`         | string/null | Device model                                                                                                                                                                    |
| `serial_number` | string/null | Device serial number                                                                                                                                                            |
| `sw_version`    | string/null | Device firmware/software version                                                                                                                                                |
| `entities`      | object      | Entity ID keyed map with domain, name                                                                                                                                           |
| `solar`         | object      | PV devices only, and optional: absent on an inverter's device the panel did not report at setup, and on a Solar device when PV was not commissioned at setup. See Solar Object. |

#### Solar Object

| Field                  | Type        | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `role`                 | string      | `site` for the Solar device, `inverter` for an additional inverter's own device                                                                                                                                                                                                                                                                                                                                                                                           |
| `vendor`               | string/null | Vendor of what this device describes. On `site`, what the Solar device's PV entities read: the bound inverter, or the inverters together, where `null` means they differ. On `inverter`, that inverter's. `null` where nothing is published.                                                                                                                                                                                                                              |
| `model`                | string/null | Model of what this device describes, as `vendor` is.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `feed_circuit_id`      | string/null | The circuit that feeds the inverter this device describes. On `inverter`, null when no circuit feeds it. On `site`, null when no single circuit-fed inverter is drawn on this device: the inverters are read together, its lone inverter is fed by no circuit (an upstream inverter), none is published or bound, the bound inverter's circuit is gone, or the inverter has its own device. While a bound inverter's record has not yet arrived, it is the bound circuit. |
| `power_entity_id`      | string/null | That inverter's own power reading: the `power` entity of `feed_circuit_id`. Null when `feed_circuit_id` is, and for a moment while that circuit leaves the panel.                                                                                                                                                                                                                                                                                                         |
| `site_power_entity_id` | string/null | `site` only: PV Power, the site's total; null on `inverter`                                                                                                                                                                                                                                                                                                                                                                                                               |

`power_entity_id` is an entity id rather than a circuit id, so a consumer that merges several panels' circuits, as the card's favorites view does, can still
resolve it. `vendor` and `model` are what the panel publishes, `null` where it publishes nothing, whereas the device's `manufacturer` and `model` carry Home
Assistant's display placeholders. Which devices carry a block, and each block's circuit, are as of the integration's last setup, which the panel reporting a new
inverter repeats; `vendor` and `model` are read live, as the PV Vendor and PV Product sensors are.

### Errors

| Code             | Description                                   |
| ---------------- | --------------------------------------------- |
| device_not_found | The device_id does not exist in HA            |
| not_span_panel   | The device is not a SPAN Panel device         |
| not_panel_device | The device_id is a sub-device, not the panel  |
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

## `span_panel/adopted/list`

Returns every adopted row on a panel, grouped by the device card it renders on. An adopted row is a property the panel publishes that this integration models no
field for — a whole device nobody has modeled, or a vendor extension on a device it does model — surfaced as a disabled diagnostic entity in plain wire
vocabulary.

Those entities carry deliberately minimal metadata, because a state class is not declared on the wire and is not derivable from one, and a device class guessed
off a unit mislabels as often as it helps. The owner of the vendor device is not guessing, so this command is the input to an editor where they can say what the
integration refuses to infer. Each row therefore carries not only what the wire declares but the choices Core's own maps admit for that declaration, computed
server-side so a card never offers an option that would be refused on save.

Admin only, like every command here.

### Request

```json
{
  "type": "span_panel/adopted/list",
  "device_id": "<ha_device_registry_id>"
}
```

| Field       | Type   | Description                                                                                |
| ----------- | ------ | ------------------------------------------------------------------------------------------ |
| `device_id` | string | The device registry ID for the **main SPAN panel**, the same handle `panel_topology` takes |

### Response

```json
{
  "devices": [
    {
      "device_id": "abc123def456",
      "name": "Backup Generator",
      "adopted_device": true,
      "rows": [
        {
          "key": "nj-2316-005k6_adopted_generator-1/meter/active-power",
          "path": "meter/active-power",
          "platform": "sensor",
          "entity_id": "sensor.backup_generator_active_power",
          "datatype": "float",
          "unit": "W",
          "settable": false,
          "name": "Active Power",
          "curation": { "state_class": "measurement", "device_class": "power" },
          "allowed_device_classes": ["power"],
          "allowed_state_classes": ["measurement", "total", "total_increasing"],
          "stale_fields": []
        }
      ]
    }
  ]
}
```

#### Device Object

| Field            | Type        | Description                                                                        |
| ---------------- | ----------- | ---------------------------------------------------------------------------------- |
| `device_id`      | string/null | HA device registry ID, or null for an adopted device whose card is not created yet |
| `name`           | string      | The card's display name, or the wire label when there is no card yet               |
| `adopted_device` | bool        | Whether the card is one adoption minted, rather than a curated SPAN device         |
| `rows`           | object[]    | The curatable rows on that card (see below)                                        |

#### Row Object

| Field                    | Type        | Description                                                                   |
| ------------------------ | ----------- | ----------------------------------------------------------------------------- |
| `key`                    | string      | The curation key for this row — what a save is keyed on                       |
| `path`                   | string      | The `{node}/{property}` wire address                                          |
| `platform`               | string      | `sensor`, `binary_sensor`, `switch`, `select`, or `number`                    |
| `entity_id`              | string/null | Null when the entity is not in the registry yet                               |
| `datatype`               | string      | The declared Homie datatype                                                   |
| `unit`                   | string/null | The declared unit, verbatim                                                   |
| `settable`               | bool        | Whether the panel accepts a write to this property                            |
| `name`                   | string      | The entity's name in wire vocabulary                                          |
| `curation`               | object      | The stored record, as stored; `{}` when the row has never been curated        |
| `allowed_device_classes` | string[]    | Device classes admissible for this platform and unit; empty for a control row |
| `allowed_state_classes`  | string[]    | State classes admissible for this row; empty off a numeric sensor             |
| `stale_fields`           | string[]    | Stored fields the current declaration no longer supports                      |

`curation` reports what is stored rather than what would be applied. A field named in `stale_fields` is one the wire has outgrown since it was asserted — the
entity is built without it, and the editor shows it so the user can see their assertion was dropped rather than silently losing it.

A row is listed whether or not its entity exists yet: adopted entities are created disabled, and a vendor extension appears on the setup after its device card
does. Curation is keyed on the wire address rather than on a registry ID, so a row can be curated before its entity exists.

### Errors

| Code             | Description                                   |
| ---------------- | --------------------------------------------- |
| device_not_found | The device_id does not exist in HA            |
| not_span_panel   | The device is not a SPAN Panel device         |
| not_panel_device | The device_id is a sub-device, not the panel  |
| not_loaded       | The integration or config entry is not loaded |
| no_data          | The coordinator has no panel data yet         |

## `span_panel/adopted/curate`

Stores the metadata a user asserts for one adopted row, or clears it. The `key` is one `adopted/list` reported: the rows this command accepts are derived the
same way and from the same snapshot, so a key that command did not offer is refused rather than stored.

**This command writes no registry state.** Enabling an entity, renaming it, giving it an icon or an area, and choosing a display unit are all Core's own
websocket commands, which already ask for admin and already carry the undo. What is here is only what Core has nowhere to put — a state class, a device class,
and prominence for an entity built from a vendor declaration.

A successful save has three effects and no others: the record is written to the integration's own store, the config entry is scheduled for reload, and the
result is returned. The reload is not incidental. An entity description is fixed when the entity is constructed, so a record reaches its entity only by that
entity being built again — and being built _with_ it, since a state class that first appears after states have been recorded is a statistics reset rather than a
metadata change.

Admin only, like every command here.

### Request

```json
{
  "type": "span_panel/adopted/curate",
  "device_id": "<ha_device_registry_id>",
  "key": "nj-2316-005k6_adopted_generator-1/meter/active-power",
  "record": {
    "state_class": "measurement",
    "device_class": "power",
    "entity_category": "none"
  }
}
```

| Field       | Type   | Description                                                                                |
| ----------- | ------ | ------------------------------------------------------------------------------------------ |
| `device_id` | string | The device registry ID for the **main SPAN panel**, the same handle `panel_topology` takes |
| `key`       | string | The `key` of the row being curated, exactly as `adopted/list` reported it                  |
| `record`    | object | The full record to store; an empty object clears the row                                   |

#### Record Object

| Field             | Type   | Description                                                                            |
| ----------------- | ------ | -------------------------------------------------------------------------------------- |
| `state_class`     | string | `measurement`, `total`, or `total_increasing` — numeric sensor rows only               |
| `device_class`    | string | A sensor or binary-sensor device class the row's platform and declared unit admit      |
| `entity_category` | string | `none`, the one storable value — it promotes the entity out of the diagnostic category |

`record` replaces the stored record rather than merging into it: a field left out is a field cleared. The values admissible for a given row are exactly the
`allowed_state_classes` and `allowed_device_classes` that `adopted/list` reported for it, so a card built from that response never offers a value this command
refuses.

### Response

```json
{
  "record": {
    "state_class": "measurement",
    "device_class": "power",
    "entity_category": "none"
  },
  "warnings": []
}
```

| Field      | Type     | Description                                                   |
| ---------- | -------- | ------------------------------------------------------------- |
| `record`   | object   | The record now stored; `{}` when the row was cleared          |
| `warnings` | string[] | Advisory consequences of the save, which has already happened |

#### Warnings

| Code                 | Description                                                                                                                                                                                                         |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `statistics_removed` | The save leaves the row without a state class it previously had — cleared outright or narrowed to the other fields — so long-term statistics stop being compiled and HA raises its own `state_class_removed` repair |
| `total_increasing`   | The recorder reads a drop of more than a tenth as a meter reset and starts a new cycle, so a reading that legitimately falls manufactures consumption                                                               |

Warnings are never refusals. They name effects the stored record does not show on its face, because both are about the recorder rather than about the entity.

### Errors

| Code                       | Description                                                                           |
| -------------------------- | ------------------------------------------------------------------------------------- |
| device_not_found           | The device_id does not exist in HA                                                    |
| not_span_panel             | The device is not a SPAN Panel device                                                 |
| not_panel_device           | The device_id is a sub-device, not the panel                                          |
| not_loaded                 | The integration or config entry is not loaded                                         |
| no_data                    | The coordinator has no panel data yet                                                 |
| unknown_key                | No curatable row on this panel carries that key                                       |
| invalid_state_class        | A state class was asserted on a row that is not a numeric sensor                      |
| invalid_device_class       | The value is not a device class for this row's platform                               |
| incompatible_device_class  | The device class does not admit the unit the row declares                             |
| invalid_field_for_platform | A control row accepts prominence only, not a state class or a device class            |
| invalid_format             | The request failed the command schema — an unknown value or field, or a malformed key |

The first five are the codes `adopted/list` answers, from the same resolution: a consumer that learned them for one command does not meet a second set on the
next.
