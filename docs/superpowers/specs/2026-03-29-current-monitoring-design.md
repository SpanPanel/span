# SPAN Panel Current Monitoring System

## Overview

A monitoring system that detects when circuit or panel main current draw
exceeds configurable thresholds, providing notifications through multiple
channels. The system is monitoring-only — it never controls relays or
changes panel state.

## Phased Delivery

- **Phase 1:** Monitoring engine, global configuration via options flow,
  services API, notifications and events. Per-circuit overrides available
  via services for power users.
- **Phase 2:** Integration panel with circuit card-style view for visual
  monitoring and per-circuit configuration UI.

This spec covers Phase 1. Phase 2 will be its own design.

## Requirements

### V2-Only

Monitoring requires `current_a` and `breaker_rating_a` fields which are
available only on V2 (Gen2) panels. Circuits or mains legs where either
value is `None` are silently skipped. This follows the same conditional
pattern used for V2 sensor creation elsewhere in the integration.

### Alert Types

**Spike alert:** A single instantaneous current reading exceeds the spike
threshold percentage of the breaker rating.

**Continuous overload alert:** Current has remained above the continuous
threshold percentage of the breaker rating for longer than the configured
window duration.

### Monitoring Scope

- Per-circuit current vs circuit breaker rating
- Panel mains: upstream L1, upstream L2, downstream L1, downstream L2 —
  each leg independently monitored against `main_breaker_rating_a`
- Absolute value of current used for threshold comparison (power can flow
  bidirectionally on PV circuits)

## Configuration

### Global Settings (Integration Options Flow)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable_current_monitoring` | bool | `False` | Master toggle |
| `default_continuous_threshold_pct` | int | 80 | NEC continuous load rule |
| `default_spike_threshold_pct` | int | 100 | Breaker rating |
| `default_window_duration_m` | int | 15 | Sliding window for continuous |
| `cooldown_duration_m` | int | 15 | Min time between re-notifications |
| `notify_targets` | list[str] | `["notify.notify"]` | Notify service targets |
| `enable_persistent_notifications` | bool | `True` | HA notification panel |
| `enable_event_bus` | bool | `True` | Fire events on bus |

### Per-Circuit Overrides (Storage)

Stored in `.storage/span_panel_current_monitor.{entry_id}`:

```json
{
  "circuit_overrides": {
    "circuit_12": {
      "continuous_threshold_pct": 70,
      "spike_threshold_pct": 90,
      "window_duration_m": 30,
      "monitoring_enabled": true
    }
  },
  "mains_overrides": {
    "upstream_l1": {
      "continuous_threshold_pct": 75,
      "spike_threshold_pct": 95,
      "window_duration_m": 10,
      "monitoring_enabled": true
    },
    "upstream_l2": { ... },
    "downstream_l1": { ... },
    "downstream_l2": { ... }
  }
}
```

Circuits and mains legs without overrides inherit global defaults. The
`monitoring_enabled` flag allows disabling monitoring on specific
circuits (e.g., those with known benign spikes like a well pump) or
specific mains legs.

### Services

**`span_panel.set_circuit_threshold`**

Set per-circuit overrides. All threshold fields are optional — only
provided fields are updated.

**`span_panel.set_mains_threshold`**

Set per-mains-leg overrides. Same threshold fields as circuit overrides.

Fields:

- `leg` (required) — one of `upstream_l1`, `upstream_l2`,
  `downstream_l1`, `downstream_l2`
- `continuous_threshold_pct` (optional) — continuous load threshold
- `spike_threshold_pct` (optional) — spike threshold
- `window_duration_m` (optional) — window duration
- `monitoring_enabled` (optional) — per-leg toggle

**`span_panel.clear_mains_threshold`**

Remove per-mains-leg overrides, reverting to global defaults.

Fields:

- `leg` (required) — leg identifier

Fields:

- `circuit_id` (required) — circuit identifier
- `continuous_threshold_pct` (optional) — continuous load threshold
- `spike_threshold_pct` (optional) — spike threshold
- `window_duration_m` (optional) — window duration
- `monitoring_enabled` (optional) — per-circuit toggle

**`span_panel.clear_circuit_threshold`**

Remove per-circuit overrides, reverting to global defaults.

Fields:

- `circuit_id` (required) — circuit identifier

**`span_panel.get_monitoring_status`**

Returns current monitoring state for all monitored circuits: effective
thresholds, current readings, whether each circuit is in an active
overload window, and cooldown state.

No fields required.

## Monitoring Engine

### Architecture

A single `CurrentMonitor` class in `current_monitor.py`. The coordinator
holds a reference and calls `async_process_snapshot(snapshot)` on each
update. The monitor schedules its evaluation work on the HA event loop
via `hass.async_create_task`, ensuring it never blocks the coordinator's
update path.

When `enable_current_monitoring` is `False`, the monitor is never
instantiated. `coordinator.current_monitor` is `None` and the coordinator
skips the call entirely. Zero overhead when disabled.

### Per-Circuit State

For each monitored circuit (and each mains leg), the monitor tracks:

| Field | Type | Description |
|-------|------|-------------|
| `last_current_a` | float | Most recent reading |
| `over_threshold_since` | datetime or None | When continuous threshold was first exceeded |
| `last_spike_alert` | datetime or None | Last spike notification timestamp (cooldown) |
| `last_continuous_alert` | datetime or None | Last continuous notification timestamp (cooldown) |

No historical readings are stored. No deques, no averaging, no memory
growth.

### Threshold Evaluation

On each snapshot:

1. For each monitored circuit and mains leg, read `current_a` and
   resolve effective thresholds (per-circuit override or global default).

2. **Spike check:** If `abs(current_a) >= breaker_rating_a *
   spike_threshold_pct / 100` and cooldown has elapsed, fire spike alert.

3. **Continuous check:**
   - If `abs(current_a) >= breaker_rating_a * continuous_threshold_pct /
     100`: set `over_threshold_since` if not already set.
   - If below threshold: reset `over_threshold_since` to `None`.
   - If `over_threshold_since` is set and `now - over_threshold_since >=
     window_duration`: fire continuous overload alert (subject to
     cooldown).

4. If current dips below the continuous threshold even once, the window
   resets. This correctly models the NEC continuous load concept — the
   load must be sustained without interruption.

### Cooldown

A dict of `{(entity_key, alert_type): last_alert_timestamp}`. An alert
is suppressed if `now - last_alert < cooldown_duration`. Spike and
continuous alerts have independent cooldowns for the same circuit.

### Restart Behavior

On HA restart, all state starts clean — empty tracking dicts. Spike
detection works immediately. Continuous detection has a natural warm-up
period equal to the window duration before it can fire. This is
acceptable for a monitoring feature and avoids the complexity of recorder
backfill.

## Notification Dispatch

When a threshold is breached and cooldown has elapsed, the monitor
dispatches through all enabled channels:

### Event Bus

```yaml
event_type: span_panel_current_alert
data:
  circuit_id: "circuit_12"
  circuit_name: "Kitchen"
  alert_type: "continuous_overload"  # or "spike"
  current_a: 18.4
  breaker_rating_a: 20
  threshold_pct: 80
  utilization_pct: 92
  window_duration_s: 900        # continuous only
  over_threshold_since: "..."   # continuous only, ISO timestamp
  panel_serial: "ABC123"
```

### Notify Service

Called for each configured target in `notify_targets`.

- **Spike:** Title: `SPAN: Kitchen spike` / Message: `Kitchen spike at
  22.1A (110% of 20A rating)`
- **Continuous:** Title: `SPAN: Kitchen overload` / Message: `Kitchen
  drawing 18.4A (92% of 20A rating) — continuous threshold of 80%
  exceeded over 15 min`

Users configure which notify targets to use: `notify.notify` (all
services), `notify.mobile_app_brians_iphone` (specific device),
`notify.family_alerts` (group), etc.

### Persistent Notification

Via `persistent_notification.create`. Notification ID includes circuit +
alert type so repeated alerts for the same condition update the existing
notification rather than stacking duplicates.

## Integration Wiring

### Lifecycle

- **`async_setup_entry`:** If `enable_current_monitoring` is `True`,
  instantiate `CurrentMonitor(hass, entry)`, store on coordinator, load
  per-circuit overrides from storage, register services.
- **`async_unload_entry`:** Call `monitor.async_stop()` if monitor
  exists, unregister services.

### Coordinator Change

One addition to the coordinator's update path:

```python
if self.current_monitor is not None:
    self.current_monitor.async_process_snapshot(snapshot)
```

The coordinator knows nothing about monitoring internals.

### Config Flow Changes

New step or section in the existing options flow for monitoring settings.
Global defaults only — per-circuit overrides are managed via services
(and in Phase 2, via the integration panel UI).

## File Changes

### New Files

- `current_monitor.py` — `CurrentMonitor` class: threshold evaluation,
  notification dispatch, storage management, state tracking

### Modified Files

- `__init__.py` — instantiate/teardown monitor, register/unregister
  services
- `coordinator.py` — single line to invoke monitor on snapshot update
- `const.py` — constants for config keys, defaults, event type
- `strings.json` / translations — labels for new options flow fields
- `services.yaml` — service definitions

## Testing

- Threshold evaluation: spike fires at correct percentage, continuous
  fires after window duration, resets on dip below threshold
- Cooldown: alerts suppressed within cooldown period, fire again after
- Per-circuit overrides: override takes precedence, clear reverts to
  global, missing fields inherit from global
- Service calls: set/clear/get work correctly, persist to storage
- Notification dispatch: correct channels called based on config, correct
  payload structure, persistent notification ID deduplication
- V2 guard: circuits with `None` current or rating are skipped
- Disabled state: monitor not instantiated when toggle is off, no
  overhead
- Mains monitoring: each leg independently evaluated against main breaker
  rating
