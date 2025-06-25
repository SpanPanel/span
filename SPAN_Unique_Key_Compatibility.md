# SPAN Unique Key Compatibility

Without unique key comnpatability an upgrade will create new sensors or will create sensors with '_2' suffix
since the entity id will match but the unique ID will not.  This behavior will result in lost statistics
as the new sensor will not match the old sensor with the eixsting history.  The integration has tests
to specifically ensure compatability from one release to the next.

## Regular Circuit Entities

**New Installation Unique ID Pattern:**

```text
span_{serial_number}_{circuit_id}_{description_key}
```

**Examples:**

- `span_ABC123_kitchen_outlets_power`
- `span_ABC123_unmapped_tab_15_power`
- `span_ABC123_circuit_15_power`

**Implementation:** Native SPAN integration. Unique ID patterns for existing installations
depend on multiple factors including installation history and migration state.
New installations use circuit_id based patterns for stability.

## Solar Synthetic Sensors

**Unique ID Pattern (v1.0.10+ Compatible):**

```text
span_{serial_number}_synthetic_{leg1}_{leg2}_{yaml_key}
```

**Examples:**

- `span_ABC123_synthetic_15_16_solar_inverter_instant_power`
- `span_ABC123_synthetic_15_16_solar_inverter_energy_produced`

**Implementation (v1.0.10+ Compatibility):**

1. SPAN integration auto-generates 3 sensors with keys: `solar_inverter_{suffix}`
   (suffixes: `instant_power`, `energy_produced`, `energy_consumed`)
2. SPAN integration passes prefix: `span_{serial}_synthetic_{circuits}`
3. ha-synthetic-sensors combines: `{prefix}_{yaml_key}` → final unique ID

**Key Requirement:** Solar synthetic sensors maintain compatibility with v1.0.10+ synthetic naming patterns to ensure stable unique IDs regardless of underlying circuit naming evolution.

## User-Defined Synthetic Sensors

**Planned Unique ID Pattern:**

```text
span_{serial_number}_synthetic_{user_identifier}_{user_sensor_name}
```

**Examples:**

- `span_ABC123_synthetic_backup_circuits_total_consumption`
- `span_ABC123_synthetic_ev_charging_energy_used`
- `span_ABC123_synthetic_whole_house_net_power`

**Implementation (Planned):**

1. User defines individual sensors with custom names in YAML
2. SPAN integration passes prefix: `span_{serial}_synthetic_{user_identifier}`
3. ha-synthetic-sensors combines: `{prefix}_{user_sensor_name}` → final unique ID

**Key Difference:** Unlike solar (which auto-generates 3 sensors with suffixes),
user-defined sensors are individually created with custom names chosen by the user.

## Key Differences

| Aspect | Regular Circuits | Solar Sensors | User-Defined (Future) |
|--------|------------------|---------------|----------------------|
| **Handler** | SPAN Panel (native) | ha-synthetic-sensors | ha-synthetic-sensors |
| **Generation** | Individual entities | Auto-generates 3 sensors | User creates individual sensors |
| **Naming** | New installs: circuit_id based | Automatic suffixes | User-defined names |
| **Format** | Direct assembly | Prefix + auto key | Prefix + user key |
| **Multi-Panel** | Serial in unique ID | Serial in prefix | Serial in prefix |
| **Compatibility** | Depends on install history | v1.0.10+ synthetic naming | Planned: v1.0.10+ synthetic naming |

**Key Point:** Both solar and planned user-defined synthetic sensors use v1.0.10+ compatible
synthetic naming patterns with the ha-synthetic-sensors integration, ensuring consistency
and stability independent of regular circuit unique ID evolution.
