# Energy Dip Compensation

## Rationale

SPAN panels occasionally report lower energy readings for `TOTAL_INCREASING` sensors (consumed/produced energy at panel and circuit level). Home Assistant's
statistics engine interprets any decrease as a counter reset, creating negative spikes in the energy dashboard.

A manual `cleanup_energy_spikes` service already exists to fix historical data. This feature adds **proactive** compensation: the integration maintains a
cumulative offset per sensor so HA never sees a decrease.

## Design Principles

- **Lightweight** -- no Storage files, no I/O in the MQTT event hot path. The offset lives in memory and is persisted via HA's `ExtraStoredData` mechanism (same
  as grace period state).
- **Opt-in for existing installs** -- defaults to OFF on upgrade (no schema migration needed; `options.get(key, False)` handles it).
- **On by default for new installs** -- the config flow checkbox defaults to `True`.
- **Covering an upstream defect** -- this feature may be short-lived if SPAN fixes the firmware bug; disabling clears all accumulated offsets cleanly.

## Architecture

### Data Flow

```text
Panel reading (raw)
  → SpanEnergySensorBase._process_raw_value()
    → dip detected? (raw < last_panel_reading by ≥ 1.0 Wh)
      YES → offset += dip; report to coordinator
      NO  → passthrough
    → super()._process_raw_value(raw + offset)
      → HA sees monotonically increasing value
```

### Key Fields (SpanEnergySensorBase)

| Field                       | Type    | Description                                 |
| --------------------------- | ------- | ------------------------------------------- | --------------------------------------------- |
| `_energy_offset`            | `float` | Cumulative Wh added to compensate dips      |
| `_last_panel_reading`       | `float  | None`                                       | Last raw panel value (before offset)          |
| `_last_dip_delta`           | `float  | None`                                       | Size of the most recent dip (for diagnostics) |
| `_is_total_increasing`      | `bool`  | Whether this sensor's state_class qualifies |
| `_dip_compensation_enabled` | `bool`  | Current option value (re-read each cycle)   |

### Persistence (SpanEnergyExtraStoredData)

Three new optional fields: `energy_offset`, `last_panel_reading`, `last_dip_delta`. Old stored data missing these keys deserializes with `None` -- no migration
needed.

Restoration only happens when compensation is **enabled**. Disabling and reloading leaves the init defaults (0.0/None), implementing "disabling clears offsets."

## Configuration

### Fresh Install

The config flow (`config_flow.py`) shows a checkbox: "Auto-Compensate Energy Dips" -- defaults to **True**.

### Options Flow

General Options includes a toggle: "Auto-Compensate Energy Dips" -- defaults to **False** for existing installs.

The description notes: "Disabling clears all accumulated offsets."

## Affected Sensors

Only `TOTAL_INCREASING` energy sensors get compensation:

| Sensor                      | Class                     | Level   |
| --------------------------- | ------------------------- | ------- |
| `mainMeterEnergyProducedWh` | `SpanPanelEnergySensor`   | Panel   |
| `mainMeterEnergyConsumedWh` | `SpanPanelEnergySensor`   | Panel   |
| `circuit_energy_produced`   | `SpanCircuitEnergySensor` | Circuit |
| `circuit_energy_consumed`   | `SpanCircuitEnergySensor` | Circuit |

`TOTAL` sensors (net energy, feedthrough) and `MEASUREMENT` sensors pass through unchanged.

## Sensor Attributes

When compensation is enabled and the sensor is `TOTAL_INCREASING`:

| Attribute        | Shown When       | Description                        |
| ---------------- | ---------------- | ---------------------------------- |
| `energy_offset`  | offset > 0       | Cumulative Wh compensation applied |
| `last_dip_delta` | dip has occurred | Size of the most recent dip in Wh  |

## Notification Behavior

When one or more sensors detect a dip during an update cycle:

1. Each sensor calls `coordinator.report_energy_dip()` (sync, no I/O).
2. After all entities update, `_run_post_update_tasks()` drains the list.
3. A single persistent notification is created listing all affected sensors.
4. Uses stable `notification_id=span_energy_dip_{entry_id}` so repeated events update rather than stack.
5. The notification mentions the `cleanup_energy_spikes` service for historical data.

## Edge Cases

| Case                           | Behavior                                             |
| ------------------------------ | ---------------------------------------------------- |
| First reading (no baseline)    | Sets baseline, no compensation                       |
| HA restart                     | Offset + last_panel_reading restored from storage    |
| Disable then re-enable         | Disable skips restoration (clears offsets), fresh    |
| Float precision noise          | 1.0 Wh minimum threshold prevents false triggers     |
| Multiple sensors dip at once   | All detected independently, batched notification     |
| Panel firmware reset           | Primary use case -- offset compensates automatically |
| Dip below threshold (< 1.0 Wh) | Ignored, treated as normal fluctuation               |
