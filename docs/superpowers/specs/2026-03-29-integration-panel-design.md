# SPAN Panel Integration Dashboard Design

## Overview

A full-page integration panel (sidebar entry) for the SPAN Panel integration, providing a physical breaker-box view of the panel with live data, monitoring
indicators, shedding configuration, and per-circuit settings. Replaces the need for users to hunt through entity lists or rely solely on the config flow for
ongoing configuration.

The panel is built by enhancing the existing span-card codebase and bundling a panel build alongside the existing Lovelace card build. The integration registers
the panel via `async_register_panel` so it appears in the HA sidebar automatically.

## Architecture

### Two deliverables from one codebase

The span-card repository produces two build outputs:

- `span-panel-card.js` -- standalone Lovelace card (existing, enhanced)
- `span-panel.js` -- full-page integration panel (new)

Both share core rendering modules (grid layout, circuit cells, charts, side panel). The integration repo references span-card as a git submodule, pulling the
built panel JS into `frontend/`.

### Source structure

```text
span-card/                    (existing repo, enhanced)
  src/
    core/                     shared rendering modules
      layout.js               tab-to-grid positioning (exists)
      circuit-cell.js         circuit card rendering (extracted)
      panel-grid.js           physical panel grid component
      chart.js                chart rendering (exists)
      side-panel.js           circuit/panel config side panel (new)
    card/                     Lovelace card entry point (exists)
    panel/                    integration panel entry point (new)
      span-panel.js           full-page shell, tab router
      tab-dashboard.js        Panel tab: physical view + header
      tab-monitoring.js       Monitoring tab: global + overrides
      tab-settings.js         Settings tab: general config
  dist/
    span-panel-card.js        card bundle (HACS)
    span-panel.js             panel bundle (integration)
  rollup.config.mjs           builds both outputs

span/ (integration repo)
  custom_components/span_panel/
    frontend/                 git submodule -> span-card/dist/
    __init__.py               async_register_panel pointing to frontend/
```

### Data sources

No new backend code is required. The panel consumes existing APIs:

- `panel_topology` WebSocket command for circuit layout, slot positions, entity mappings, and breaker ratings
- `hass.states[entityId]` for live power/current values (polled at 1s by the existing update loop)
- `get_monitoring_status` service (supports response) for monitoring thresholds, alert state, and per-circuit overrides (polled at 30s)
- Existing services for mutations:
  - `switch.turn_on` / `switch.turn_off` for relay control
  - `select.select_option` for shedding priority changes
  - `span_panel.set_circuit_threshold` / `span_panel.clear_circuit_threshold` for per-circuit monitoring overrides
  - `span_panel.set_mains_threshold` / `span_panel.clear_mains_threshold` for mains monitoring overrides
- Config entry options update for global monitoring and general settings

## Panel Tab (Default)

The primary view: a physical breaker-box representation of the panel.

### Panel header

Displays panel-level stats above the circuit grid. All power fields switch between watts and amps based on the A/W toggle.

| Field      | Entity                                  | W mode       | A mode                 |
| ---------- | --------------------------------------- | ------------ | ---------------------- |
| Site       | Site Power (`sitePowerW`)               | state (W)    | amperage attribute (A) |
| Upstream   | Current Power (`instantGridPowerW`)     | state (W)    | amperage attribute (A) |
| Grid       | Grid Power (`gridPowerFlowW`)           | state (W)    | amperage attribute (A) |
| Downstream | Feedthrough Power (`feedthroughPowerW`) | state (W)    | amperage attribute (A) |
| Solar      | PV Power (`pvPowerW`)                   | state (W)    | amperage attribute (A) |
| Battery    | Battery level sensor                    | always SoC % | always SoC %           |
| Grid state | `dsm_state` sensor                      | always text  | always text            |

Conditional display: Solar hidden when no PV entities exist. Battery hidden when no BESS entities exist. Downstream hidden when no feedthrough entities exist.
The header degrades gracefully.

Additional header elements:

- Panel name, serial number, firmware version (from `panel_topology`)
- Panel-level gear icon: opens the side panel with global monitoring settings
- A/W toggle button: persisted in card/panel config, switches all values and chart Y-axes between watts and amps

### Monitoring summary bar

A compact bar between the header and circuit grid showing:

- Monitoring active/disabled status
- Count of monitored circuits and mains legs
- Count of warnings (at or above 80% utilization)
- Count of alerts (at or above 100% utilization)
- Count of circuits with custom overrides

Hidden entirely when monitoring is disabled.

### Circuit grid

The existing span-card physical panel layout: two-column CSS grid with odd slots on the left, even slots on the right, slot numbers on the center spine.
Supports single-pole (120V, one slot), row-span (240V across same row), and col-span (240V down same column) breaker types. Empty slots shown as dashed
outlines.

### Circuit cell enhancements

Each circuit cell adds these elements to the existing span-card rendering:

**Shedding priority icon.** Always displayed. Uses MDI icons:

- `mdi:shield-check` (green) -- Never shed, protected
- `mdi:battery-alert-variant-outline` (purple) -- SoC Threshold
- `mdi:transmission-tower` (orange) -- Off-Grid, shed when islanded

**Gear icon.** Always present on every circuit. Dimmed when circuit uses default settings. Orange when circuit has custom monitoring overrides. Clicking opens
the side panel for that circuit.

**Utilization percentage.** Shown next to the current value in A mode. Color-coded: green below 80%, orange at 80-99%, red at 100% or above. Only shown when
monitoring is enabled.

**Alert state.** When a spike or continuous alert is active: red border, subtle glow, warning triangle icon next to the value. Only shown when monitoring is
enabled.

**Custom monitoring accent.** Orange left border on the circuit cell when the circuit has non-default monitoring thresholds.

**No indicators when using global defaults.** If a circuit has no custom monitoring overrides and monitoring is enabled, the cell shows utilization color-coding
and alert state but no override indicators. Clean by default.

**No monitoring indicators when monitoring is disabled.** The circuit cells fall back to the existing span-card appearance with only shedding icons and gear
icons added.

### A/W toggle behavior

A toggle button in the panel header switches the entire display:

- W mode: circuit values show power from the power entity state. Charts plot power. Header stats show watts/kilowatts.
- A mode: circuit values show current from the current entity state (or power entity amperage attribute as fallback). Charts plot current with breaker rating
  threshold lines. Header stats show amps. Utilization percentage shown next to values.

The toggle state is persisted in the card/panel configuration. The existing span-card `chart_metric` config option maps to this toggle.

## Side Panel (Circuit Config)

Opens when the user clicks a gear icon on a circuit. Slides in from the right following HA's more-info dialog pattern.

### Layout

```text
  Circuit Name
  Rating · Voltage · Tabs [N, N+2]

  Relay                    [On/Off toggle]

  Shedding Priority
  [dropdown: Never / SoC Threshold / Off-Grid]

  Monitoring                        [enabled toggle]
  ○ Global defaults
  ● Custom

  Continuous threshold           [  ]%
  Spike threshold                [  ]%
  Window duration                [  ]m
  Cooldown                       [  ]m
```

### Behavior

All controls are live -- changes fire immediately, no save button.

- **Relay toggle:** calls `switch.turn_on` / `switch.turn_off` on the circuit's switch entity. Only shown when `is_user_controllable` is true.
- **Shedding dropdown:** calls `select.select_option` on the circuit's priority select entity.
- **Monitoring enabled toggle:** next to the Monitoring label. When unchecked, hides the radio and threshold fields.
- **Global/Custom radio:** switching from Custom to Global immediately calls `span_panel.clear_circuit_threshold`. Switching to Custom makes threshold fields
  editable; changing any value immediately calls `span_panel.set_circuit_threshold` with the updated values.
- **Threshold fields:** when Global is selected, fields show global values greyed out for reference. When Custom is selected, fields are editable.

### Panel-level side panel

The gear icon next to the panel name in the header opens a side panel with global monitoring settings:

- Enable/disable monitoring toggle
- Global thresholds (continuous %, spike %, window duration, cooldown)
- Notification targets (comma-separated notify services)
- Persistent notifications toggle
- Event bus toggle

Changes here update the config entry options, same as the config flow monitoring options step.

## Monitoring Tab

Global monitoring configuration and override management.

### Global settings section

Same fields as the panel-level side panel, laid out in a full-page form:

- Enable/disable monitoring
- Continuous threshold percentage
- Spike threshold percentage
- Window duration (minutes)
- Cooldown duration (minutes)
- Notification targets
- Persistent notifications toggle
- Event bus toggle

### Overrides table

A table listing all circuits and mains legs that have custom monitoring overrides. Columns:

- Circuit/leg name (entity ID resolved to friendly name)
- Custom continuous threshold
- Custom spike threshold
- Custom window duration
- Monitoring enabled/disabled for that circuit
- Reset action (calls `clear_circuit_threshold` or `clear_mains_threshold`)

Empty state: "All circuits using global defaults."

## Settings Tab

General integration configuration, replacing the config flow's General Options step as the primary configuration surface.

- Entity naming pattern (friendly names, circuit numbers, legacy)
- Device prefix toggle
- Circuit number toggle

The config flow General Options step remains as a fallback but the panel becomes the recommended way to configure these settings.

## Integration Backend Changes

### Panel registration

`async_setup_entry` in `__init__.py` registers the panel:

```python
panel_url = f"/span_panel_frontend/{entry.entry_id}"
hass.http.register_static_path(
    panel_url,
    hass.config.path(
        "custom_components/span_panel/frontend/span-panel.js"
    ),
    cache_headers=True,
)
hass.components.panel_custom.async_register_panel(
    hass,
    webcomponent_name="span-panel",
    frontend_url_path=f"span-panel-{entry.entry_id}",
    sidebar_title="Span Panel",
    sidebar_icon="mdi:lightning-bolt",
    module_url=f"{panel_url}/span-panel.js",
    require_admin=False,
    config={"entry_id": entry.entry_id},
)
```

`async_unload_entry` removes the panel registration.

### No new services or WebSocket commands

The panel consumes existing APIs only. No backend changes beyond panel registration and static file serving.

### Config flow

Connection setup (host, authentication) stays in the config flow. General Options and Monitoring Options steps remain functional as a fallback. The panel UI
becomes the primary configuration surface for ongoing settings.

## Graceful degradation

- **No monitoring enabled:** circuit cells show shedding icons and gear icons only. No utilization colors, no alert indicators, no monitoring summary bar. The
  monitoring tab shows the enable toggle.
- **No BESS/PV:** header hides battery and solar fields. Circuit grid unaffected.
- **No lug current sensors:** header hides upstream/downstream fields.
- **No per-circuit current sensor:** A mode falls back to power entity's amperage attribute for that circuit.
- **Service call failures:** side panel shows inline error, does not close.

## Scope boundaries

This spec covers the integration panel, span-card enhancements, and the git submodule integration between the two repos.

Out of scope:

- Mobile-specific responsive design (the existing span-card responsive breakpoint at 600px carries over)
- Chart enhancements beyond A/W toggle and threshold lines
- Automation creation from the panel UI
- Multi-panel support (one panel per integration entry; multiple entries each get their own sidebar item)
