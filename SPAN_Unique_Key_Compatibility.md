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

**How circuit_id is determined:**

- The `circuit_id` is taken directly from the `id` field in each circuit object returned by the SPAN panel API.
- This value is a UUID-style string (e.g., "0dad2f16cd514812ae1807b0457d473e").
- For example, if the API returns:

```json
"circuits": {
  "0dad2f16cd514812ae1807b0457d473e": {
    "id": "0dad2f16cd514812ae1807b0457d473e",
    "name": "Lights Dining Room",
    ...
  }
}
```

- The unique_id for a power sensor on this circuit would be:

```text
span_{serial_number}_0dad2f16cd514812ae1807b0457d473e_instantPowerW
```

- The integration always uses the exact value of the `id` field as the circuit_id in the unique_id pattern.

**Implications for unique_id stability:**

- The unique_id for each sensor is stable as long as the panel API provides a stable `circuit_id` for each circuit.
- If the panel API changes the identifiers (for example, due to a firmware update, migration, or new support for unmapped tabs), the unique_id for those circuits could change.
- The integration does not invent or remap circuit_ids; it uses what the panel API provides.

## Solar Synthetic Sensors

**Default Solar Sensor Unique ID Pattern (all released versions, including 1.0.10 and 1.2.0):**

```text
span_{serial_number}_solar_inverter_{suffix}
```

| Sensor Type         | Unique ID Example                                 | Description                |
|--------------------|---------------------------------------------------|----------------------------|
| Instant Power      | span_ABC123_solar_inverter_instant_power          | Solar inverter power       |
| Energy Produced    | span_ABC123_solar_inverter_energy_produced        | Solar energy produced      |
| Energy Consumed    | span_ABC123_solar_inverter_energy_consumed        | Solar energy consumed      |

**Advanced/User-Defined Synthetic Sensors (future/optional):**

If you define custom synthetic sensors (for example, combining multiple circuits or using the advanced YAML configuration), the unique_id pattern is:

```text
span_{serial_number}_synthetic_{leg1}_{leg2}_{yaml_key}
```

| Sensor Type         | Unique ID Example                                         | Description                        |
|--------------------|-----------------------------------------------------------|------------------------------------|
| Custom Synthetic   | span_ABC123_synthetic_15_16_solar_inverter_instant_power  | Multi-circuit solar (synthetic)    |

**Clarification:**

- By default, solar sensors use the `span_{serial_number}_solar_inverter_{suffix}` pattern in all released versions, including 1.0.10 and 1.2.0.
- The `{leg1}_{leg2}` synthetic pattern is only used for user-defined or advanced synthetic sensors, not for default solar sensors.
- If you migrate to or enable advanced synthetic configuration, those sensors will use the synthetic pattern as described above.

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

## Existing Installation Unique Key Patterns

The following patterns are used by existing installations and can be found in the Home Assistant entity registry, depending on installation version and configuration history:

### Regular Circuit Entities (All Versions)

**Unique ID Pattern:** `span_{serial_number}_{circuit_id}_{description_key}`

| Circuit Type | Unique ID Example | Description |
|--------------|-------------------|-------------|
| **Named Circuits** | `span_ABC123_{circuit_id}_instantPowerW` | Kitchen outlets on a circuit |
| **Named Circuits** | `span_ABC123_{circuit_id}_producedEnergyWh` | Solar on a circuit |
| **Switch Entities** | `span_ABC123_{circuit_id}_relay_1` | Circuit breaker control |

_Note: `{circuit_id}` is typically a UUID-style string (e.g., `0dad2f16cd514812ae1807b0457d473e`) as returned by the panel API._

### Unmapped Tab Entities (v1.2.0+)

**Unique ID Pattern:** `span_{serial_number}_unmapped_tab_{tab_number}_{description_key}`

| Circuit Type | Unique ID Example | Description |
|--------------|-------------------|-------------|
| **Unmapped Tab Sensors** | `span_ABC123_unmapped_tab_15_instantPowerW` | Unmapped breaker position 15 power |
| **Unmapped Tab Energy** | `span_ABC123_unmapped_tab_16_producedEnergyWh` | Unmapped breaker position 16 energy |

**Special Characteristics:**

- **Provided by**: Synthetic package (ha-synthetic-sensors), not native SPAN panel API circuits
- **Naming Stability**: Never subject to entity ID naming pattern changes
- **No Renaming**: Must not be renamed by the integration, similar to panel-level sensors
- **Visibility**: Not user visible - marked as invisible in Home Assistant UI
- **Purpose**: Intended solely as calculation operands for synthetic sensors (for solar, user defined)

**Description Keys Used:**

- `instantPowerW` → Power sensors
- `producedEnergyWh` → Energy produced sensors
- `consumedEnergyWh` → Energy consumed sensors
- `relay_` → Switch entities (breaker control)

### Panel-Level Entities (All Versions)

**Unique ID Pattern:** `span_{serial_number}_{panel_key}`

| Entity Type | Unique ID Example | Description |
|-------------|-------------------|-------------|
| **Panel Power** | `span_ABC123_instantGridPowerW` | Main panel power |
| **Panel Status** | `span_ABC123_dsmState` | Demand side management state |
| **Panel Info** | `span_ABC123_softwareVer` | Panel software version |
| **Door State** | `span_ABC123_doorState` | Panel door open/closed |
| **Network Links** | `span_ABC123_eth0Link` | Ethernet connection status |
| **Network Links** | `span_ABC123_wlanLink` | WiFi connection status |
| **Network Links** | `span_ABC123_wwanLink` | Cellular connection status |

**Special Characteristics:**

- **Naming Stability**: Never subject to entity ID naming pattern changes

### Solar Synthetic Sensors (v1.2.0+)

**Unique ID Pattern:** `span_{serial_number}_synthetic_{leg1}_{leg2}_{yaml_key}`

| Sensor Type | Unique ID Example | Description |
|-------------|-------------------|-------------|
| **Instant Power** | `span_ABC123_synthetic_15_16_solar_inverter_instant_power` | Solar inverter power |
| **Energy Produced** | `span_ABC123_synthetic_15_16_solar_inverter_energy_produced` | Solar energy produced |
| **Energy Consumed** | `span_ABC123_synthetic_15_16_solar_inverter_energy_consumed` | Solar energy consumed |

**Implementation Details:**

- **Provided by**: ha-synthetic-sensors integration
- **Source Data**: In version 1.2.O+ uses unmapped tab entities as calculation inputs.  Prior versions directly sourced circuits in panel data
- **Compatibility**: See below
- **Visibility**: User-visible synthetic sensors (unlike unmapped tabs)

Solar configurations existed prior to v1.2.0 but during an upgrade solar is migrated to the yaml
format However, the unique keys should follow the standard patterns for the particular versions as outlined below.  The unique keys were treated just like any other unique key sensor.

### Entity ID Evolution by Version

**Pre-1.0.4 Installations:**

- **Entity IDs**: `{circuit_name}_{suffix}` (e.g., `kitchen_outlets_power`)
- **Unique IDs**: `span_{serial}_{circuit_id}_{description_key}` (same as all versions)
- **Flags**: `USE_DEVICE_PREFIX: False`, `USE_CIRCUIT_NUMBERS: False` (or empty options)

**1.0.4 - 1.0.9 Installations:**

- **Entity IDs**: `span_panel_{circuit_name}_{suffix}` (e.g., `span_panel_kitchen_outlets_power`)
- **Unique IDs**: `span_{serial}_{circuit_id}_{description_key}` (same as all versions)
- **Flags**: `USE_DEVICE_PREFIX: True`, `USE_CIRCUIT_NUMBERS: False`

**1.0.9+ Installations:**

- **Entity IDs**: `span_panel_circuit_{number}_{suffix}` (e.g., `span_panel_circuit_15_power`)
- **Unique IDs**: `span_{serial}_{circuit_id}_{description_key}` (same as all versions)
- **Flags**: `USE_DEVICE_PREFIX: True`, `USE_CIRCUIT_NUMBERS: True`

### New Installation Defaults

**New Installations:**

- Set `USE_DEVICE_PREFIX: True` and `USE_CIRCUIT_NUMBERS: True` in `create_new_entry()`
- Use modern circuit number-based entity IDs by default
- Unique IDs remain consistent across all versions for compatibility

### Version-Specific Compatibility Notes

**Key Insight - Unique IDs vs Entity IDs:**

- **Unique IDs**: Always `span_{serial}_{circuit_id}_{description_key}` across ALL versions
- **Entity IDs**: Change based on version and user configuration flags
- **Statistics Preservation**: Unique IDs stay stable, entity IDs can be migrated

**Migration Behavior:**

- Existing installations preserve their current naming pattern on upgrade
- Empty options indicate existing installation (uses legacy defaults)
- Users can manually migrate entity naming patterns through options flow
- Unique IDs never change during migration, ensuring statistics continuity

**Unmapped Tab Entities (v1.2.0+):**

- Always use `unmapped_tab_N` format for circuit IDs
- Have stable entity IDs regardless of configuration flags
- Never participate in entity ID migration
- Must not be renamed by the integration (like panel-level sensors)
- Provided by synthetic package, not SPAN integration directly
- Not user visible - intended only as calculation inputs for synthetic sensors
