# DSM State Sensor

The `DSM State` sensor (`DSM_ON_GRID` / `DSM_OFF_GRID`) is being restored in v2.1 with the same name and values. The `DSM Grid State` sensor from v2.0 will be
deprecated in favor of it.

The eBus MQTT schema does not expose the firmware's internal `dsmState` computation. The integration derives grid connectivity from four independent signals in
priority order:

| Priority | Signal                | Condition                                              | Result         | Rationale                                                              |
| -------- | --------------------- | ------------------------------------------------------ | -------------- | ---------------------------------------------------------------------- |
| 1        | Battery grid-state    | BESS reports `ON_GRID` or `OFF_GRID`                   | Use directly   | Grid-tie inverter has authoritative knowledge                          |
| 2        | Dominant power source | Value is `GRID`                                        | `DSM_ON_GRID`  | Grid is the primary source, must be connected                          |
| 3        | Upstream lugs power   | Non-zero watts                                         | `DSM_ON_GRID`  | Power flowing to/from grid even if not dominant                        |
| 4        | Power-flows grid      | Non-zero watts                                         | `DSM_ON_GRID`  | Independent grid exchange calculation, catches measurement timing gaps |
| 5        | All grid signals zero | Both lugs and power-flows read zero, grid not dominant | `DSM_OFF_GRID` | No grid exchange, panel is islanded                                    |

For panels without a battery, signals 2-5 provide the determination. For panels with a battery, signal 1 is authoritative.

## Sensors

| Sensor                  | Values                                              | Use case                         |
| ----------------------- | --------------------------------------------------- | -------------------------------- |
| `DSM State`             | `DSM_ON_GRID` / `DSM_OFF_GRID`                      | Grid connectivity                |
| `Dominant Power Source` | GRID, BATTERY, PV, GENERATOR, NONE                  | What is providing the most power |
| `Current Run Config`    | `PANEL_ON_GRID` / `PANEL_OFF_GRID` / `PANEL_BACKUP` | Panel operating mode             |
