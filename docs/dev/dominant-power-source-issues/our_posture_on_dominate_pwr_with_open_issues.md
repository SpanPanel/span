# DPS `/set` Follow-up Questions

Reference: [spanio/SPAN-API-Client-Docs#8](https://github.com/spanio/SPAN-API-Client-Docs/discussions/8)

## Additional Open Questions

1. **Firmware reclaim behavior**: After a user/automation `/set`s DPS, does firmware reclaim it when BESS comms are restored? Or is the `/set` latched until
   explicitly changed back? This determines whether `/set` is a momentary nudge or a persistent override.

2. **Panel-independent grid detection**: Does the panel use any of its own measurements (`power-flows/grid`, `lugs-upstream/active-power`, voltage) to
   independently determine grid status, or does it rely entirely on BESS for grid-up/grid-down awareness?

3. **DPS update source**: Is DPS always computed from BESS reporting, or are there other inputs (e.g., voltage collapse detection, ATS position sensing)?

4. **Generator interaction**: On 32-tab panels where generator feeds through the same lugs as grid, does `DPS=GENERATOR` exist in firmware today or is it
   reserved for future use? How would the panel distinguish utility from generator?

## Stale DPS Scenario Matrix

The core problem: the panel depends on BESS for grid state awareness. When BESS comms are lost, DPS is stale. Our `dsm_state` sensor addresses this by combining
`bess/grid-state`, `dominant-power-source`, `power-flows/grid`, and `lugs-upstream/active-power` — signals the panel has but doesn't use for DPS computation.
The scenarios below show where this combined signal detects what DPS alone cannot.

| #   | Sequence                                           | DPS (stale)         | Actual State       | Impact                                               | Detectable?                                                          |
| --- | -------------------------------------------------- | ------------------- | ------------------ | ---------------------------------------------------- | -------------------------------------------------------------------- |
| 1   | BESS comms drop while on-grid, grid stays up       | `GRID`              | On-grid            | None — stale value happens to be correct             | N/A                                                                  |
| 2   | BESS comms drop while off-grid, grid stays down    | `BATTERY`           | Off-grid           | None — stale value happens to be correct             | N/A                                                                  |
| 3   | Grid restored, BESS comms still down               | `BATTERY`           | On-grid            | Unnecessary shedding continues                       | Yes: `power-flows/grid` resumes, `lugs-upstream/active-power` shifts |
| 4   | BESS comms drop while on-grid, then grid drops     | `GRID`              | Off-grid           | No shedding — battery drains faster, reduced runtime | Yes: `power-flows/grid` drops to zero, voltage may sag               |
| 5   | BESS comms drop while off-grid, then grid restores | `BATTERY`           | On-grid            | Same as #3 — unnecessary shedding                    | Yes: `power-flows/grid` resumes                                      |
| 6   | BESS itself fails (comms + battery), grid up       | `BATTERY` or `GRID` | On-grid, no backup | Shedding state depends on last DPS                   | Yes: `bess/connected` = false                                        |

## DPS `/set` Safety by Direction

Not all `/set` directions carry equal risk. Shedding unnecessarily is annoying; failing to shed during an outage means unmanaged battery drain and reduced
runtime (the battery protects itself and disconnects when depleted — there is no overload risk).

| Direction          | Action            | Risk                                                                 | Automation Safe?              |
| ------------------ | ----------------- | -------------------------------------------------------------------- | ----------------------------- |
| `GRID` → `BATTERY` | Triggers shedding | **Low** — conservative, extends runtime                              | Yes                           |
| `BATTERY` → `GRID` | Stops shedding    | **Moderate** — if actually off-grid, unmanaged drain reduces runtime | User confirmation recommended |

## Selector Design

The selector is a DPS `/set` — it tells the panel what the power source is. The panel owns all relay management. The integration does not manage relays based on
DPS.

### Why the integration should not manage relays

The panel's firmware actively manages circuit relays based on `shed-priority` and SOC thresholds. If the integration also toggles relays, two independent actors
would be controlling the same relays with no coordination mechanism. Firmware can change relays at any time (SOC threshold crossings, BESS state changes),
overwriting what the integration set, leading to flip-flop conflicts.

### Values

The firmware's shedding is binary: `DPS == GRID` → no shedding, `DPS != GRID` → shed per `shed-priority`. The firmware does not differentiate between non-GRID
values for shedding purposes.

| Value       | Behavior                                                                  |
| ----------- | ------------------------------------------------------------------------- |
| `GRID`      | No shedding — panel assumes full rated capacity                           |
| `BATTERY`   | Shed per circuit `shed-priority` and SOC thresholds                       |
| `GENERATOR` | Same shedding as `BATTERY` (firmware has no generator-specific logic yet) |
| `PV`        | Same shedding as `BATTERY` (firmware has no PV-specific logic yet)        |

All non-GRID values trigger identical firmware shedding. `GENERATOR` and `PV` are included for semantic accuracy — they set the correct DPS value on the panel
so firmware can differentiate in the future if support is added.

`UNKNOWN` is excluded — it is not actionable and would put the panel in an undefined state.

### Guard Rails

- **`BATTERY → GRID`** is the only moderate-risk direction. Recommend users pair this transition with a condition that `power-flows/grid` is non-zero.
- **Example automation (safe direction)**: If `power-flows/grid` ≈ 0 AND `bess/connected` = false AND `DPS` = `GRID` for > 30 seconds → set selector to
  `BATTERY`. This covers scenario #4.
- **Example automation (user-confirm direction)**: If `power-flows/grid` > 0 AND selector = `BATTERY` for > 60 seconds → send notification, let user decide.

### Generator/PV Circuit Control

Users who need source-specific circuit management (e.g., shedding additional circuits when on generator due to capacity constraints) can build automations using
the individual circuit relay controls the integration already exposes. The integration provides the primitives — the user builds the policy for their specific
system.

## BESS Communication Loss Detection

### Promote `bess/connected` to a binary sensor

Currently `bess/connected` is buried as an attribute on the battery SOC sensor. Promoting it to a first-class `binary_sensor.span_panel_bess_connected` gives
users a clean automation trigger.

### Signal availability during BESS comms loss

When BESS comms are down, `dsm_state` loses its two BESS-dependent inputs but retains the panel's own independent measurements:

| Signal                       | Source                     | Available when BESS down? |
| ---------------------------- | -------------------------- | ------------------------- |
| `bess/grid-state`            | BESS                       | No — stale or `UNKNOWN`   |
| `dominant-power-source`      | Panel (computed from BESS) | Stale — last known value  |
| `power-flows/grid`           | Panel (own CTs)            | **Yes**                   |
| `lugs-upstream/active-power` | Panel (own CTs)            | **Yes**                   |

`dsm_state` can still detect whether power is flowing from the grid via the panel's own measurements, even without BESS confirmation.

### User automation primitives

The combination of `bess_connected` (binary sensor) + `dsm_state` (combined signal) gives the user everything they need:

- `bess_connected` = off AND `dsm_state` = `ON_GRID` → grid is up despite stale DPS, user may want to set DPS selector to `GRID` to stop unnecessary shedding
- `bess_connected` = off AND `dsm_state` = `OFF_GRID` → grid is down despite stale DPS, user may want to set DPS selector to `BATTERY` to trigger shedding
- `bess_connected` = on → BESS is communicating, firmware manages DPS correctly, no user intervention needed

The integration detects and exposes; the user decides and acts.

## Integration Posture

1. **Keep `dsm_state` as our combined read-only sensor** — it fills the gap the panel's DPS computation leaves open during BESS comms loss.
2. **Promote `bess/connected` to a binary sensor** — first-class entity for automating around BESS communication loss.
3. **Add a DPS `select` entity** (`GRID`, `BATTERY`, `GENERATOR`, `PV`) — a `/set` to the panel, giving users/automations the ability to act on what `dsm_state`
   detects.
4. **Do not manage relays** — the panel owns shedding. The integration detects and exposes; the user decides and acts.
