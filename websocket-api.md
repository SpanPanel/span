# WebSocket API

The integration exposes WebSocket commands for programmatic access to panel topology and entity mappings. These commands are available to custom cards,
AppDaemon scripts, or any WebSocket client connected to Home Assistant.

## `span_panel/panel_topology`

Returns the full physical layout of a SPAN panel in a single call — circuits with their breaker slot positions, entity IDs grouped by role (power, energy,
switch, select), and sub-devices (BESS, MID, EVSE) with their entities.

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

| Field         | Type        | Description                                         |
| ------------- | ----------- | --------------------------------------------------- |
| `serial`      | string      | Panel serial number                                 |
| `firmware`    | string      | Panel firmware version                              |
| `panel_size`  | int or null | Total breaker spaces (e.g., 32, 40)                 |
| `device_id`   | string      | HA device registry ID (echoed from request)         |
| `device_name` | string      | HA device display name                              |
| `circuits`    | object      | Circuit UUID keyed map (see below)                  |
| `sub_devices` | object      | HA device ID keyed map of BESS/MID/EVSE (see below) |

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
| `type`          | string      | `bess`, `mid`, `evse`, or `unknown`   |
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
field for — a whole device nobody has modelled, or a vendor extension on a device it does model — surfaced as a disabled diagnostic entity in plain wire
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
