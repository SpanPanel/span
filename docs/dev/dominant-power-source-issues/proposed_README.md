# Dominant Power Source Control

The SPAN panel tracks which power source is currently dominant — Grid, Battery, Generator, or PV (solar). This Dominant Power Source (DPS) setting drives
circuit load shedding. When DPS is Grid, all circuits remain on. Any other value triggers shedding based on each circuit's configured shed priority.

When a battery system (BESS) is installed, the panel relies on the BESS to determine whether the grid is online and to set the DPS accordingly. If BESS
communication is lost, the DPS value becomes stale — it may show Battery when the grid is actually up (causing unnecessary shedding), or Grid when the grid is
actually down (preventing shedding and draining the battery faster).

This integration provides three entities that surface this condition and give the user the tools to respond.

## Entities

### DSM State (`sensor.{device_name}_dsm_state`)

A read-only sensor that combines multiple independent signals to determine grid status:

- Battery system grid state (when available)
- Dominant Power Source
- Grid power flow (panel's own measurement)
- Upstream lug power (panel's own measurement)

The grid power flow and lug power signals are the panel's own measurements and remain available even when the battery system is offline.

### BESS Connected (`binary_sensor.{device_name}_bess_connected`)

Indicates whether the battery system is communicating with the panel. When this sensor turns off, the panel's Dominant Power Source may be stale.

### Dominant Power Source (`select.{device_name}_dominant_power_source`)

Allows overriding the panel's power source setting. Available values:

| Value       | When to override                                 |
| ----------- | ------------------------------------------------ |
| Grid        | Grid is confirmed up but panel is stuck shedding |
| Battery     | Grid is confirmed down but panel is not shedding |
| Generator\* | System is running on generator backup            |
| PV\*        | System is running on solar only                  |

\*Currently only Grid and Battery affect shedding behavior. Generator and PV are treated as off-grid (same as Battery) but are provided for future panel
firmware enhancements.

## Automation Example

A typical automation monitors the battery connection and grid state together:

**Grid restored but panel still shedding:**

- Trigger: `bess_connected` = off for 30 seconds
- Condition: `dsm_state` = on-grid
- Action: Set Dominant Power Source to Grid

**Grid lost but panel not shedding:**

- Trigger: `bess_connected` = off for 30 seconds
- Condition: `dsm_state` = off-grid
- Action: Set Dominant Power Source to Battery

**Battery system reconnected:**

- Trigger: `bess_connected` = on
- Action: No action needed — firmware resumes normal management

Setting the Dominant Power Source to Grid when actually off-grid will prevent shedding and drain the battery faster. Pair any Battery-to-Grid transition with a
check that grid power flow is non-zero.
