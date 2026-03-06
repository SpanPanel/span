# Dynamic Enum Options

This document describes how the SPAN Panel integration handles enumerated state
values, where options are derived dynamically at runtime, and where hardcoded
values remain necessary.

## Background

SPAN Panel firmware publishes device state over MQTT using the Homie convention.
Many properties are typed as `enum` in the Homie schema (available at
`GET /api/v2/homie/schema`), but firmware releases may publish values not yet
declared in the schema. The integration must accept any value the panel sends
without raising errors.

### The Problem

Home Assistant's `SensorEntity` with `device_class=ENUM` requires an `options`
list. If the entity reports a state value not present in `options`, HA raises a
`ValueError` and the sensor becomes unavailable. Hardcoding the options list
creates a tight coupling to a specific firmware version and breaks when new
values appear.

### The Solution

Enum sensor options are built dynamically from observed MQTT values. Each enum
sensor starts with a seed list of `["unknown"]` and appends new values as they
arrive, before setting the native value. This prevents the `ValueError` and
makes the integration forward-compatible with any firmware changes.

## Entity Classification

### Fully Dynamic (no hardcoded state values)

These entities receive values directly from Homie MQTT properties. The
integration passes them through at face value in uppercase, matching the Homie
schema convention and preserving backward compatibility with v1 automations.

| Entity | Snapshot field | Homie property | Homie enum format |
| --- | --- | --- | --- |
| EVSE Charger Status | `evse.status` | `evse/status` | `UNKNOWN,AVAILABLE,PREPARING,...` |
| EVSE Lock State | `evse.lock_state` | `evse/lock-state` | `UNKNOWN,LOCKED,UNLOCKED` |
| Main Relay State | `main_relay_state` | `core/relay` | `UNKNOWN,OPEN,CLOSED` |
| Grid Forming Entity (DPS) | `dominant_power_source` | `core/dominant-power-source` | `GRID,BATTERY,PV,GENERATOR,NONE,UNKNOWN` |
| Vendor Cloud | `vendor_cloud` | `core/vendor-cloud` | `UNKNOWN,UNCONNECTED,CONNECTED` |

All of the above use `device_class=SensorDeviceClass.ENUM` with
`options=["UNKNOWN"]` and uppercase pass-through value functions. The dynamic
options mechanism in the base sensor class extends the options list at runtime.

### Case Convention

The integration preserves uppercase values as received from the Homie schema
(e.g., `CLOSED`, `GRID`, `CHARGING`). While HA core internally uses lowercase
for some built-in states (`STATE_UNKNOWN = "unknown"`), the SPAN Panel v1
integration established uppercase as its convention. All user automations,
dashboards, and scripts reference these uppercase values. Changing to lowercase
would break existing installations with no functional benefit.

Translation keys in `translations/*.json` use uppercase to match (e.g.,
`"CLOSED": "Closed"`, `"CHARGING": "Charging"`).

### Binary Sensors (boolean interpretation of Homie enums)

These map Homie enum values to True/False. The boolean logic must be defined in
code, but the set of recognized enum values does not need to be exhaustive --
unrecognized values simply resolve to the else/default branch.

| Entity | Homie property | True condition | False condition |
| --- | --- | --- | --- |
| Door State | `core/door` | `!= CLOSED` | `== CLOSED` |
| EVSE Charging | `evse/status` | status in charging set | otherwise |
| EVSE EV Connected | `evse/status` | status in connected set | otherwise |
| Ethernet Link | `core/ethernet` | boolean | boolean |
| Wi-Fi Link | `core/wifi` | boolean | boolean |

No options list is involved -- binary sensors are not subject to the `ValueError`
problem.

### Derived Values (computed in span-panel-api, not direct Homie properties)

These are synthesized from multiple Homie signals by `HomieDeviceConsumer` in
the library. Their possible values are defined by our derivation logic, not by
the panel firmware.

| Entity | Derivation logic | Possible values |
| --- | --- | --- |
| DSM State | `_derive_dsm_state()` — priority: bess/grid-state, then DPS + grid power heuristic | `DSM_ON_GRID`, `DSM_OFF_GRID`, `UNKNOWN` |
| DSM Grid State | Deprecated alias of DSM State | same as above |
| Current Run Config | `_derive_run_config()` — from dsm_state + islandable + DPS | `PANEL_ON_GRID`, `PANEL_OFF_GRID`, `PANEL_BACKUP`, `UNKNOWN` |

These values are controlled by our code and change only when we change the
derivation logic. They could be converted to ENUM sensors with a static options
list since we define the domain, but because the values are ours, hardcoding is
acceptable and there is no firmware drift risk.

### Select Entities (control, not observation)

Select entities present a list of options the user can choose from and write
back to the panel. The options must be known in advance because they define valid
commands.

| Entity | Options source | Hardcoded |
| --- | --- | --- |
| Circuit Priority | `CircuitPriority` enum: `NEVER`, `SOC_THRESHOLD`, `OFF_GRID` | Yes -- these are the only valid values the panel accepts |

The `UNKNOWN` member is excluded from the UI options because it is a read-only
state, not a valid command.

### Switch Entities (relay control)

The circuit relay switch sends `CLOSED` or `OPEN` to the panel. The relay state
read-back (`circuit.relay_state`) comes from Homie as an enum
(`UNKNOWN,OPEN,CLOSED`), but the switch maps it to a boolean (`is_on`). No
options list is involved.

### Numeric Sensors (no enum concern)

All power (W), energy (Wh), current (A), voltage (V), and percentage (%)
sensors report float values. They have no options list and are unaffected by
this pattern.

| Examples | Device class |
| --- | --- |
| Circuit Power, Grid Power, Feed Through Power | `POWER` |
| Consumed/Produced Energy, Net Energy | `ENERGY` |
| EVSE Advertised Current, Lug Currents | `CURRENT` |
| Battery Level | `BATTERY` |

## Translations

HA uses translation files (`translations/*.json`) to display friendly labels for
enum sensor states. The integration provides translations for all values declared
in the Homie schema across 5 languages (en, es, fr, pt, ja).

### Known values (schema-declared)

These have full translations. For example, EVSE status `CHARGING` displays as
"Charging" (en), "Cargando" (es), "En charge" (fr), etc. Translations are
provided for all dynamic enum sensors:

- `evse_status` — 10 OCPP-derived charger states
- `evse_lock_state` — 3 lock states
- `main_relay_state` — 3 relay states (open, closed, unknown)
- `grid_forming_entity` — 6 dominant power source values
- `vendor_cloud` — 3 cloud connection states

### Unknown values (dynamically discovered)

When firmware publishes a value not in the schema (e.g., `UNPLUGGED`), the
dynamic options mechanism accepts it and the sensor works correctly. However,
no translation exists for that value, so HA displays the raw uppercase key
string (e.g., "UNPLUGGED") in the UI.

This is acceptable behavior:

- The sensor remains functional -- no errors or unavailability
- The raw string is typically readable in English (Homie enum values are
  descriptive by convention)
- Non-English users see the untranslated English-like key until a translation
  is added in a subsequent release
- Adding a translation for a newly discovered value is a one-line change per
  language file, with no code changes required

### Translation file structure

Enum state translations live under `entity.sensor.<translation_key>.state` in
each translation file:

```json
{
  "entity": {
    "sensor": {
      "evse_status": {
        "state": {
          "UNKNOWN": "Unknown",
          "AVAILABLE": "Available",
          "CHARGING": "Charging"
        }
      }
    }
  }
}
```

## Implementation Details

### How dynamic options work

In `sensors/base.py :: SpanSensorBase._process_raw_value()`:

```python
if self._attr_device_class is SensorDeviceClass.ENUM:
    str_value = str_value.upper()
    if not hasattr(self, "_attr_options") or self._attr_options is None:
        self._attr_options = ["UNKNOWN"]
    if str_value not in self._attr_options:
        self._attr_options.append(str_value)
```

This runs before `self._attr_native_value` is set, so HA never sees a state
value that isn't in the options list. The `.upper()` normalization ensures
consistency even if firmware sends unexpected casing.

### The `/api/v2/homie/schema` endpoint

The panel exposes its full Homie schema at `GET /api/v2/homie/schema` (no auth
required). This returns every node type and property with datatype and format
metadata. For enum properties, the `format` field is a comma-separated list of
declared values.

The schema is useful for documentation and validation, but it is not the source
of truth for runtime options because:

1. Firmware may publish values not yet declared in the schema (observed:
   `UNPLUGGED` on `evse/status` is not in the schema)
2. The schema represents a point-in-time declaration, not a guarantee

The integration does not currently fetch the schema at runtime. If future needs
arise (e.g., pre-populating options for better initial UI), the endpoint is
available and unauthenticated.

## Schema Validation Utility

The integration intentionally avoids runtime warnings for unrecognized enum
values — the dynamic options mechanism handles them silently. However,
developers need a way to detect when the Homie schema declares values that the
integration's translation files don't cover (or vice versa).

A standalone CLI utility should be created (e.g., `scripts/validate_enum_schema.py`)
that runs outside of Home Assistant and performs the following checks:

### Inputs

1. **Homie schema** — fetched live from a panel at `GET http://<host>/api/v2/homie/schema`,
   or loaded from a saved JSON file for offline use.
2. **Translation files** — the `translations/*.json` files in the integration.
3. **Sensor definitions** — the enum sensor descriptions in `sensor_definitions.py`
   (to map Homie property paths to translation keys).

### Checks

| Check | Description |
| --- | --- |
| **Missing translations** | Schema declares an enum value (e.g., `UNPLUGGED`) that has no translation key in any language file. These values will display as raw uppercase strings in the UI. |
| **Extra translations** | A translation key exists for a value not declared in the schema. May indicate a stale translation or a firmware regression. |
| **Cross-language gaps** | A value has a translation in some languages but not all. |
| **Case mismatches** | Schema value casing doesn't match translation key casing (should both be uppercase). |

### Proposed usage

```bash
# Live panel scan
python scripts/validate_enum_schema.py --host 192.168.65.70

# Offline from saved schema
python scripts/validate_enum_schema.py --schema-file tests/fixtures/homie_schema.json

# CI integration (exit code 1 on any missing translations)
python scripts/validate_enum_schema.py --schema-file tests/fixtures/homie_schema.json --strict
```

### Mapping Homie properties to translation keys

The utility needs a mapping from Homie property paths to sensor `translation_key`
values, since the schema uses paths like `core/relay` while translations use
keys like `main_relay_state`. This mapping can be derived from the sensor
definitions or maintained as a small lookup table in the utility:

| Homie property | Translation key |
| --- | --- |
| `core/relay` | `main_relay_state` |
| `core/dominant-power-source` | `grid_forming_entity` |
| `core/vendor-cloud` | `vendor_cloud` |
| `evse/status` | `evse_status` |
| `evse/lock-state` | `evse_lock_state` |

This utility is not yet implemented — this section serves as the design spec.

## Summary Table

| Category | Options source | Hardcoded | Dynamic | Status |
| --- | --- | --- | --- | --- |
| EVSE enum sensors | MQTT observed values | No | Yes | Done |
| Panel enum sensors | MQTT observed values | No | Yes | Done |
| Derived state sensors | Our derivation logic | Acceptable | N/A | No change needed |
| Binary sensors | Boolean logic | Yes (True/False mapping) | N/A | No change needed |
| Select entities | Valid panel commands | Yes | N/A | No change needed |
| Switch entities | Boolean (OPEN/CLOSED) | Yes | N/A | No change needed |
| Numeric sensors | Float values | N/A | N/A | No change needed |
