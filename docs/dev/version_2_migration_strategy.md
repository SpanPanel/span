# Version 2 Migration Strategy

## Overview

This document outlines the migration strategy for upgrading existing SPAN Panel installations to version 2, which introduces synthetic sensors with YAML-based
configuration. The primary goal is to preserve existing entity IDs and user configurations while seamlessly transitioning to the new synthetic sensor
architecture.

## Proposed Migration Strategy

### Core Principles

1. **Config Entry-Driven Migration**: Start with existing config entries as the only known data source along with the entity registry
2. **Per-Device YAML Generation**: Generate complete YAML configuration for each device/config entry independently
3. **Preserve All Identifiers**: Keep existing unique IDs as sensor keys and preserve all entity IDs as is
4. **Storage-Based Persistence**: Store generated YAML in ha-synthetic-sensors storage for each device
5. **Seamless Boot Transition**: After YAML generation, boot normally as if it were a pre-configured installation

Existing installations have:

- **One or more config entries** (each representing a SPAN Panel device)
- **Device, host, and token information** in each config entry
- **Existing entities** in the entity registry associated with each config entry
- **No knowledge of YAML/synthetic sensor process**
- **No knowledge of simulation modes**

The migration must:

- **Discover all config entries** for the SPAN Panel domain
- **For each config entry**: Generate complete YAML configuration for that device's sensor set
- **Store YAML configurations** in ha-synthetic-sensors storage using device identifiers
- **Preserve all existing identifiers** (unique IDs become sensor keys, entity IDs preserved)
- **Continue normal boot** as if the installation had always been configured with synthetic sensors

After migration, the normal boot process takes over. The key is that after migration, each config entry will find that:

1. **Storage manager exists and is populated** with sensor sets for each device
2. **YAML configurations are already stored** for each device identifier
3. **Boot process proceeds normally** as if it were a pre-configured installation
4. **Boot process provides sensor key to backing entity mapping** as if it were a pre-configured installation

## Revised Migration Approach (unique_id normalization + registry lookups)

### Summary

- During migration, first normalize all existing sensor unique_ids in the registry for each device/config entry to the helper-format used by the integration.
- During migration, set a transient migration flag and trigger a reload. Do not generate YAML during migration.
- On the next normal boot (with the flag set), generate YAML for all config entries (one sensor set per entry/device).
- In migration mode, generation uses helpers to build sensor keys and looks up entity_ids in the registry by helper-format unique_id, using those entity_ids
  instead of constructing new ones. This preserves existing entity_ids.

### Rationale

- Unique_ids are the primary join key for helper logic; normalizing them up front removes fragile translation layers.
- Entity_ids must remain unchanged; resolving them via the registry guarantees preservation.
- After normalization, generation and setup paths can operate exactly like a fresh install (with registry lookups succeeding).

### Scope

- Applies to `sensor` domain entities on the `span_panel` platform.
- `switch`, `select`, and `binary_sensor` unique_ids already conform and need no changes.

### Phase 1: Unique_id normalization (entity registry)

- For each SPAN config entry to be migrated:
  - Read registry entries for that entry.
  - Compute helper-format unique_id for each sensor:
    - Circuit sensors: `span_{identifier}_{circuit_id}_{power|energy_produced|energy_consumed}` where
      - `instantPowerW → power`, `producedEnergyWh → energy_produced`, `consumedEnergyWh → energy_consumed`.
    - Panel sensors: `span_{identifier}_{current_power|feed_through_power|main_meter_produced_energy|main_meter_consumed_energy|...}` where
      - `instantGridPowerW → current_power`, `feedthroughPowerW → feed_through_power`,
      - `mainMeterEnergy.producedEnergyWh → main_meter_produced_energy`,
      - `mainMeterEnergy.consumedEnergyWh → main_meter_consumed_energy`,
      - `feedthroughEnergy.producedEnergyWh → feed_through_produced_energy`,
      - `feedthroughEnergy.consumedEnergyWh → feed_through_consumed_energy`.
  - If different, update the registry unique_id in place (idempotent; preserves entity_id).

### Phase 2: Set per-entry migration flags and reload

- After unique_id normalization completes, set a transient migration flag per config entry so entries can migrate independently and clear themselves when done.
  - Example storage (either pattern is fine):
    - Per-entry key: `hass.data[DOMAIN].setdefault(entry.entry_id, {})["migration_mode"] = True`
    - Or central set: `hass.data[DOMAIN].setdefault("migration_entry_ids", set()).add(entry.entry_id)`
- Trigger a reload so the next boot runs the standard setup path. Each entry checks its own flag to decide if it should perform migration-mode YAML generation.

### Phase 3: Normal boot YAML generation (migration mode)

- During platform setup, detect the migration flag:
  - For each config entry (device), generate a complete YAML sensor set using existing helpers to form sensor keys and backing metadata.
  - Resolve entity_ids by registry lookup: `entity_registry.async_get_entity_id("sensor", "span_panel", <helper_unique_id>)`.
    - If found, use the existing entity_id.
    - If missing, fall back to helper-constructed entity_id for new-only cases.
  - Store/import YAML into ha-synthetic-sensors storage under `{device_identifier}_sensors`.

### Phase 4: Solar handling (CRUD during first normal boot)

- During the first normal boot in migration mode, perform solar CRUD inline, immediately after initial synthetic setup and before the existing solar setup block
  runs.
  - If entry options indicate solar is enabled, or prior solar entities exist for this entry, add/update solar sensors in the same SensorSet/YAML using
    helper-format unique_ids and registry entity_id lookups.
  - Then run the existing solar setup logic, which will pick up the newly added solar config.
- If neither condition applies, skip solar; user can enable later via options and a standard reload will add solar.

- Entity_id preservation for solar:
  - Solar generation must mirror the main generation’s behavior: when migration mode is active, resolve entity_ids from the registry using the helper-format
    unique_id for each solar sensor key; only fall back to helper-built entity_ids if no registry entry exists.
  - Ensure the migration flag remains set until solar CRUD completes so the solar path knows to perform registry lookups rather than generate new entity_ids.

### Phase 5: Clear per-entry migration flags and steady state

- After successful setup, including solar CRUD and solar setup, clear the per-entry migration flag so subsequent boots follow the standard path with no special
  handling.
  - For per-entry key: `hass.data[DOMAIN][entry.entry_id].pop("migration_mode", None)` (delete dict if empty).
  - For central set: remove `entry.entry_id`; delete the set when empty.

### Idempotency and safety

- unique_id normalization is no-op on already-normalized installs.
- YAML generation/import is safe to re-run; entity_ids remain unchanged.
- User names/visibility preserved; only required backing entities may be enabled explicitly.

### Validation

- Post-boot (per entry):
  - Registry shows helper-format unique_ids for SPAN sensors.
  - Storage contains YAML for the device’s sensor set.
  - Synthetic sensors register with existing entity_ids via helper-format unique_ids.

## Implementation Tasks

- unique_id normalization
  - Implement per-entry normalization of `sensor` unique_ids to helper format; idempotent and conflict-safe.
  - Leave `switch/select/binary_sensor` untouched.
- migration flag & reload
  - Set per-entry migration flag on successful normalization; trigger reload.
  - On setup, check and clear flag after successful generation.
- YAML generation (migration mode)
  - For each entry on normal boot, generate full YAML using helpers; resolve entity_ids via registry lookup by helper-format unique_id with fallback to
    helper-built entity_id only when missing.
  - Store under `{device_identifier}_sensors`.
- Solar CRUD (first boot only)
  - After initial synthetic setup returns a SensorSet and StorageManager, perform solar CRUD if enabled or previously present; when migration mode is active,
    resolve entity_ids for solar by registry lookup using helper-format unique_ids; then call existing solar setup.
  - Keep the per-entry migration flag set until solar CRUD and solar setup complete, then clear it.
- Multi-device
  - Ensure the above runs independently per config entry; one sensor set per device identifier.
- Safety & telemetry
  - Log normalization counts, skipped conflicts, and generation outcomes; avoid duplicate unique_ids; ensure idempotent reruns.

## Unique ID Patterns by Version

This section documents the unique ID patterns found in different SPAN Panel integration versions to aid in migration development and testing.

### Version 1.0.4 Unique ID Patterns

**Panel Power Sensors:**

- `span_nj-2316-005k6_instantGridPowerW` → `sensor.span_panel_current_power`
- `span_nj-2316-005k6_feedthroughPowerW` → `sensor.span_panel_feed_through_power`

**Panel Energy Sensors:**

- `span_nj-2316-005k6_mainMeterEnergy.producedEnergyWh` → `sensor.span_panel_main_meter_produced_energy`
- `span_nj-2316-005k6_mainMeterEnergy.consumedEnergyWh` → `sensor.span_panel_main_meter_consumed_energy`
- `span_nj-2316-005k6_feedthroughEnergy.producedEnergyWh` → `sensor.span_panel_feed_through_produced_energy`
- `span_nj-2316-005k6_feedthroughEnergy.consumedEnergyWh` → `sensor.span_panel_feed_through_consumed_energy`

**Circuit Power Sensors:**

- `span_nj-2316-005k6_{circuit_id}_instantPowerW` → `sensor.span_panel_circuit_{number}_power`
- Example: `span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_instantPowerW` → `sensor.span_panel_circuit_2_power`

**Circuit Energy Sensors:**

- `span_nj-2316-005k6_{circuit_id}_producedEnergyWh` → `sensor.span_panel_circuit_{number}_energy_produced`
- `span_nj-2316-005k6_{circuit_id}_consumedEnergyWh` → `sensor.span_panel_circuit_{number}_energy_consumed`
- Example: `span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_producedEnergyWh` → `sensor.span_panel_circuit_2_energy_produced`

**Status Sensors (unchanged):**

- `span_nj-2316-005k6_doorState` → `binary_sensor.span_panel_door_state`
- `span_nj-2316-005k6_eth0Link` → `binary_sensor.span_panel_ethernet_link`
- `span_nj-2316-005k6_wlanLink` → `binary_sensor.span_panel_wi_fi_link`
- `span_nj-2316-005k6_wwanLink` → `binary_sensor.span_panel_cellular_link`

### Version 1.0.10 Unique ID Patterns

**Panel Power Sensors:**

- `span_nj-2316-005k6_instantGridPowerW` → `sensor.span_panel_current_power`
- `span_nj-2316-005k6_feedthroughPowerW` → `sensor.span_panel_feed_through_power`

**Panel Energy Sensors:**

- `span_nj-2316-005k6_mainMeterEnergy.producedEnergyWh` → `sensor.span_panel_main_meter_produced_energy`
- `span_nj-2316-005k6_mainMeterEnergy.consumedEnergyWh` → `sensor.span_panel_main_meter_consumed_energy`
- `span_nj-2316-005k6_feedthroughEnergy.producedEnergyWh` → `sensor.span_panel_feed_through_produced_energy`
- `span_nj-2316-005k6_feedthroughEnergy.consumedEnergyWh` → `sensor.span_panel_feed_through_consumed_energy`

**Circuit Power Sensors:**

- `span_nj-2316-005k6_{circuit_id}_instantPowerW` → `sensor.span_panel_circuit_{number}_power`
- Example: `span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_instantPowerW` → `sensor.span_panel_circuit_2_power`

**Circuit Energy Sensors:**

- `span_nj-2316-005k6_{circuit_id}_producedEnergyWh` → `sensor.span_panel_circuit_{number}_energy_produced`
- `span_nj-2316-005k6_{circuit_id}_consumedEnergyWh` → `sensor.span_panel_circuit_{number}_energy_consumed`
- Example: `span_nj-2316-005k6_0dad2f16cd514812ae1807b0457d473e_producedEnergyWh` → `sensor.span_panel_circuit_2_energy_produced`

**Status Sensors (unchanged):**

- `span_nj-2316-005k6_doorState` → `binary_sensor.span_panel_door_state`
- `span_nj-2316-005k6_eth0Link` → `binary_sensor.span_panel_ethernet_link`
- `span_nj-2316-005k6_wlanLink` → `binary_sensor.span_panel_wi_fi_link`
- `span_nj-2316-005k6_wwanLink` → `binary_sensor.span_panel_cellular_link`

### Key Differences Between Versions

**No Differences Found:** The unique ID patterns between v1.0.4 and v1.0.10 are **identical**. Both versions use:

- Old naming conventions (`instantGridPowerW`, `feedthroughPowerW`, `instantPowerW`, `producedEnergyWh`, `consumedEnergyWh`)
- Same circuit ID patterns with UUIDs
- Same panel sensor patterns with dot notation for energy sensors
- Same status sensor patterns

**Migration Implications:**

- The migration logic can be the same for both v1.0.4 and v1.0.10
- Both versions need normalization to the new helper format:
  - `instantGridPowerW` → `current_power`
  - `feedthroughPowerW` → `feed_through_power`
  - `instantPowerW` → `power`
  - `producedEnergyWh` → `energy_produced`
  - `consumedEnergyWh` → `energy_consumed`
  - `mainMeterEnergy.producedEnergyWh` → `main_meter_produced_energy`
  - `mainMeterEnergy.consumedEnergyWh` → `main_meter_consumed_energy`
  - `feedthroughEnergy.producedEnergyWh` → `feed_through_produced_energy`
  - `feedthroughEnergy.consumedEnergyWh` → `feed_through_consumed_energy`

**Sensor Counts:**

- Both versions have 24 power sensors (2 panel + 22 circuit)
- Both versions have 48 energy sensors (4 panel + 44 circuit)
- Total: 72 sensors per version
