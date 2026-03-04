# DSM State Sensor Analysis

## Background

The v1 integration (commit `8eb592a`) added two DSM-related sensors from the REST API:

| Sensor         | REST field     | Values                          | Question answered                      |
| -------------- | -------------- | ------------------------------- | -------------------------------------- |
| DSM State      | `dsmState`     | `DSM_ON_GRID` / `DSM_OFF_GRID`  | Is the grid connected?                 |
| DSM Grid State | `dsmGridState` | `DSM_GRID_UP` / `DSM_GRID_DOWN` | Is the grid the dominant power source? |

## What the v1 REST API provided

The OpenAPI spec (`openapi.json`) provides no documentation for either field — both are bare `type: "string"` with auto-generated titles, no descriptions, and
no enum definitions. SPAN did not document the semantics.

However, the two fields use **different value vocabularies**, which strongly suggests the firmware computes them independently:

- `dsmState` → `DSM_ON_GRID` / `DSM_OFF_GRID` (connectivity-focused vocabulary)
- `dsmGridState` → `DSM_GRID_UP` / `DSM_GRID_DOWN` (dominance-focused vocabulary)

Live panel observations (nj-2316-005k6, firmware spanos2/r202603/05) confirm they are distinct computations:

```text
dsmState=DSM_ON_GRID  dsmGridState=DSM_GRID_UP  currentRunConfig=PANEL_ON_GRID  gridPower=191.3
dsmState=DSM_ON_GRID  dsmGridState=DSM_GRID_UP  currentRunConfig=PANEL_ON_GRID  gridPower=252.3
dsmState=DSM_ON_GRID  dsmGridState=DSM_GRID_UP  currentRunConfig=PANEL_ON_GRID  gridPower=175.4
```

The firmware has access to internal hardware signals (transfer switch state, relay position, internal CT measurements) that are not exposed via the eBus MQTT
schema. The v1 `dsmState` was likely a **reliable, firmware-computed grid connectivity indicator**.

## eBus transition

The eBus MQTT schema does not expose `dsmState` or `dsmGridState`. During the v2 eBus migration, `dsm_state` was temporarily removed while a reliable derivation
was developed. The `dsm_grid_state` sensor was repurposed with `DSM_ON_GRID`/`DSM_OFF_GRID` values as an interim grid connectivity indicator.

With the derivation now validated against live panel data, `dsm_state` is restored and `dsm_grid_state` is deprecated.

## Available eBus signals

Grid connectivity must be derived from raw signals. The following were observed on the live panel:

| MQTT topic                   | Live value | Notes                                             |
| ---------------------------- | ---------- | ------------------------------------------------- |
| `core/dominant-power-source` | `GRID`     | Enum: GRID, BATTERY, PV, GENERATOR, NONE, UNKNOWN |
| `core/grid-islandable`       | `false`    | Static panel capability                           |
| `lugs-upstream/active-power` | `502.6`    | Grid power from upstream lugs (W)                 |
| `power-flows/grid`           | `-483.2`   | Grid power from power-flows node (W)              |
| `power-flows/battery`        | `0`        | Battery exchange (W)                              |
| `power-flows/pv`             | `-341.5`   | PV production (W)                                 |
| `power-flows/site`           | `824.7`    | Total site consumption (W)                        |
| `bess/grid-state`            | _(absent)_ | Only present when BESS is commissioned            |

Two independent grid power measurements exist (`lugs-upstream/active-power` and `power-flows/grid`). Both being non-zero is strong evidence the grid is
connected. Together, these signals approximate what the firmware computes internally for `dsmState`.

## Multi-signal derivation

The `dsm_state` value is derived from four signals in priority order:

```text
1. bess/grid-state available?                        -> use it (authoritative)
2. dominant-power-source == GRID?                    -> DSM_ON_GRID
3. DPS != GRID AND lugs-upstream/active-power != 0?  -> DSM_ON_GRID
4. DPS != GRID AND power-flows/grid != 0?            -> DSM_ON_GRID
5. DPS != GRID AND both grid signals == 0?           -> DSM_OFF_GRID
6. No core node                                      -> UNKNOWN
```

Step 4 uses `power-flows/grid` as a corroborating signal. If the upstream lugs report zero but `power-flows/grid` reports non-zero (or vice versa due to
measurement timing), the panel is still grid-connected. Both signals must be zero to conclude off-grid.

### Edge cases

| Scenario                                        | lugs-upstream | power-flows/grid | DPS     | Result            | Correct?                                                 |
| ----------------------------------------------- | ------------- | ---------------- | ------- | ----------------- | -------------------------------------------------------- |
| Normal grid-dominant                            | 500           | -483             | GRID    | ON_GRID (step 2)  | Yes                                                      |
| PV-dominant, grid importing                     | 200           | -200             | PV      | ON_GRID (step 3)  | Yes                                                      |
| PV-dominant, net zero on lugs but flow non-zero | 0             | -5               | PV      | ON_GRID (step 4)  | Yes -- catches measurement granularity difference        |
| True island (grid down)                         | 0             | 0                | BATTERY | OFF_GRID (step 5) | Yes                                                      |
| BESS reports off-grid                           | 100           | -100             | GRID    | OFF_GRID (step 1) | Yes -- BESS is authoritative even with residual readings |

## Sensor set

| Sensor                  | Values                                                          | Purpose                       | Status                        |
| ----------------------- | --------------------------------------------------------------- | ----------------------------- | ----------------------------- |
| `dsm_state`             | `DSM_ON_GRID` / `DSM_OFF_GRID` / `UNKNOWN`                      | Is the grid connected?        | Restored (multi-signal)       |
| `dominant_power_source` | GRID / BATTERY / PV / GENERATOR / NONE / UNKNOWN                | What is providing most power? | Kept                          |
| `current_run_config`    | `PANEL_ON_GRID` / `PANEL_OFF_GRID` / `PANEL_BACKUP` / `UNKNOWN` | What mode is the panel in?    | Kept (derived from dsm_state) |
| `dsm_grid_state`        | `DSM_ON_GRID` / `DSM_OFF_GRID` / `UNKNOWN`                      | Duplicate of dsm_state        | Deprecated                    |

## Implementation

### Library (`span-panel-api`)

Rename `_derive_dsm_grid_state()` to `_derive_dsm_state()` and add the `power-flows/grid` signal:

```python
def _derive_dsm_state(
    self, core_node: str | None, grid_power: float, power_flow_grid: float | None
) -> str:
    # 1. BESS grid-state is authoritative when available
    bess_node = self._find_node_by_type(TYPE_BESS)
    if bess_node is not None:
        gs = self._get_prop(bess_node, "grid-state")
        if gs == "ON_GRID":
            return "DSM_ON_GRID"
        if gs == "OFF_GRID":
            return "DSM_OFF_GRID"

    # 2-5. Fallback heuristic using DPS and grid power signals
    if core_node is not None:
        dps = self._get_prop(core_node, "dominant-power-source")
        if dps == "GRID":
            return "DSM_ON_GRID"

        if dps in ("BATTERY", "PV", "GENERATOR"):
            if grid_power != 0.0:
                return "DSM_ON_GRID"
            if power_flow_grid is not None and power_flow_grid != 0.0:
                return "DSM_ON_GRID"
            return "DSM_OFF_GRID"

    return "UNKNOWN"
```

The `SpanPanelSnapshot` field should be `dsm_state` (not `dsm_grid_state`). The `_derive_run_config()` method should consume `dsm_state` instead of
`dsm_grid_state`.

### Integration (`span`)

- Restore `dsm_state` sensor definition with `value_fn=lambda s: s.dsm_state`
- Mark `dsm_grid_state` as deprecated (alias to `dsm_state` value for backward compat during transition)
- Remove the v4-to-v5 migration that deletes `dsm_state` entities from the registry
- Update `current_run_config` derivation to use `dsm_state`

## Migration path for users

| Sensor                  | v1 values                       | v2.0                           | v2.1                           |
| ----------------------- | ------------------------------- | ------------------------------ | ------------------------------ |
| `dsm_state`             | `DSM_ON_GRID` / `DSM_OFF_GRID`  | _(temporarily removed)_        | `DSM_ON_GRID` / `DSM_OFF_GRID` |
| `dsm_grid_state`        | `DSM_GRID_UP` / `DSM_GRID_DOWN` | `DSM_ON_GRID` / `DSM_OFF_GRID` | Deprecated (same as dsm_state) |
| `dominant_power_source` | _(did not exist)_               | GRID / BATTERY / PV / ...      | Unchanged                      |
