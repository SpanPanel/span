# BESS & Grid Management Deep Dive

This document provides detailed technical context for how the SPAN panel manages grid status, load shedding, and battery system interaction. It supplements the
[BESS & Grid Management](README.md#bess--grid-management) section in the README.

## What You Should Know

**The panel detects outages on its own.** When the utility grid drops, the panel sees the voltage sag and responds immediately — even if BESS communication is
already lost. No action is needed from you.

**The panel cannot detect grid restoration while islanded.** Once the MID (Microgrid Interconnect Device) opens to isolate the home, every sensor on the panel
measures battery-supplied power. Grid restoration on the utility side of the open MID is invisible. This is a physical limitation — no combination of panel
sensors or integration logic can work around it without an upstream electrical current clamp or Automatic Transfer Switch (ATS) signal.

**If BESS communication is lost while islanded, shedding continues indefinitely** — even after the grid comes back. The panel's last-known state was "off-grid"
and it has no way to learn otherwise until either the BESS reconnects or you intervene.

**The GFE Override button is the manual fix.** It tells the panel the grid is back and stops shedding. But only press it when you are confident the grid has
actually been restored — pressing it while truly off-grid means unmanaged battery drain and reduced runtime. See
[GFE Override — Risk by Direction](#gfe-override--risk-by-direction) for details.

**An external utility-side sensor is the best long-term solution.** A current clamp (e.g., Emporia Vue), ATS/MTS contact closure, or any device on the grid side
of the MID, integrated into Home Assistant as a binary sensor, can detect grid restoration and trigger automations that the panel's own sensors cannot.

**The `DSM State` sensor adds confidence but shares the blind spot.** It corroborates the Grid Forming Entity with additional signals and catches some edge
cases (like the panel self-correcting after detecting grid loss). But for the core problem — grid restored while islanded with BESS comms lost — it cannot help.
All its inputs measure the home side of the open MID.

**Generators appear as grid power.** The panel cannot distinguish between utility and generator power. GFE reports GRID when a generator is running. No
automatic load shedding is available with generators — that requires a
[compatible integrated BESS](https://support.span.io/hc/en-us/articles/4412059545111-Storage-System-Integrations-with-SPAN).

**Non-integrated battery systems provide no panel awareness.** If the BESS is not on the
[compatible integrations list](https://support.span.io/hc/en-us/articles/4412059545111-Storage-System-Integrations-with-SPAN), the panel does not know it is
islanded during an outage. GFE reports GRID, power flows show battery as grid, and no automatic shedding is available.

## System Topology

The diagrams below show the key components and where sensors can observe power. The critical insight is that the panel's sensors are on the **home side** of the
MID — they cannot see what is happening on the utility side when the MID is open.

![Integrated BESS Topology](images/bess-topology-integrated.svg)

Editable source: [images/bess-topology-integrated.drawio](images/bess-topology-integrated.drawio)

**Key observations:**

- When the MID is **closed** (on-grid), the panel's sensors see real grid power and can detect changes.
- When the MID is **open** (islanded), the panel's sensors see battery-supplied power only. The utility side is invisible.
- A **utility-side sensor** (current clamp, ATS contact closure, etc.) is the only way to detect grid restoration while islanded.
- **Generators** connect upstream of the MID via an ATS/MTS. The panel sees generator power as grid power — it cannot distinguish between the two.

### Non-Integrated BESS Topology

When the battery system is not on the
[compatible integrations list](https://support.span.io/hc/en-us/articles/4412059545111-Storage-System-Integrations-with-SPAN), there is no communication link
between the BESS and the panel. The panel cannot distinguish battery power from grid power and has no awareness that it is islanded during an outage. No
automatic load shedding is available.

![Non-Integrated BESS Topology](images/bess-topology-non-integrated.svg)

Editable source: [images/bess-topology-non-integrated.drawio](images/bess-topology-non-integrated.drawio)

**Key differences from the integrated topology:**

- **No comms link** — The panel has no communication with the BESS and cannot receive grid state updates.
- **GFE reports GRID** even when islanded — the panel does not know it is running on battery.
- **No automatic shedding** — Without BESS communication, the panel cannot manage circuits during an outage.
- **Power flows show battery as grid** — All incoming power appears as grid power in sensors and dashboards.

## Technical Details

### How the Panel Determines Grid Status

The Grid Forming Entity (GFE) is determined from multiple inputs:

1. **BESS reporting** — The BESS communicates grid state to the panel and is authoritative when connected. This is the primary source for grid status.
2. **Panel voltage monitoring** — The panel monitors voltage on the main conductors and can independently detect grid loss (voltage sag/collapse) even if BESS
   communication is already lost.

The panel is **not** entirely BESS-dependent for detecting outages. However, for detecting grid **restoration** while islanded, the BESS is the only source —
the panel's voltage monitoring cannot distinguish BESS-generated power from utility power on the home side of an open MID.

### Stale GFE Scenarios

When BESS communication is lost, the GFE value reflects the last-known state. The following matrix shows what happens in each failure sequence and whether the
actual state is detectable.

| #   | Sequence                                           | GFE (stale) | Actual State | Impact                                                          | Detectable?                                                                                   |
| --- | -------------------------------------------------- | ----------- | ------------ | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| 1   | BESS comms drop while on-grid, grid stays up       | GRID        | On-grid      | None — stale value happens to be correct                        | N/A                                                                                           |
| 2   | BESS comms drop while off-grid, grid stays down    | BATTERY     | Off-grid     | None — stale value happens to be correct                        | N/A                                                                                           |
| 3   | Grid restored, BESS comms still down               | BATTERY     | On-grid      | Unnecessary shedding continues                                  | **No** — MID is open, all panel sensors show battery power. Only correctable via GFE Override |
| 4   | BESS comms drop while on-grid, then grid drops     | GRID        | Off-grid     | No shedding — battery drains faster, reduced runtime            | **Yes** — MID still closed, panel detects voltage sag independently and self-corrects         |
| 5   | BESS comms drop while off-grid, then grid restores | BATTERY     | On-grid      | Unnecessary shedding continues (same as #3)                     | **No** — MID is open, same blind spot as #3. Only correctable via GFE Override                |
| 6   | BESS itself fails (comms + battery), grid up       | Stale       | On-grid      | Shedding state depends on last GFE; `bess_connected` goes false | **Yes** — `bess_connected` = false is a distinct signal, but GFE may still be stale           |

Scenario 4 self-corrects because the MID is still closed and the panel can see the real voltage change. Scenarios 3 and 5 cannot self-correct because the MID is
open and all panel-side signals measure battery power. The GFE Override button or an external utility-side sensor is the only resolution.

### DSM State Sensor — What It Can and Cannot Do

The integration's `DSM State` sensor corroborates GFE with additional signals:

- `bess/grid-state` — The BESS's own view of grid connectivity
- Power flow measurements — Grid power flow and upstream lugs active power

This multi-signal approach adds genuine value for:

- **Transient inconsistencies** during normal BESS communication
- **Scenario 4** — Grid drops while on-grid with BESS comms already lost. The MID is still closed, so power measurements reflect reality and confirm the panel's
  self-correction.
- **Scenario 6** — BESS failure detection via `bess_connected`
- **Defense-in-depth** for edge cases not yet enumerated

**Limitation:** For scenarios 3 and 5 (grid restored while islanded, BESS comms lost, MID open), no combination of panel-sourced signals can help. All signals
measure the home side of the open MID. Only the GFE Override button or an external utility-side sensor can resolve the stale state.

### GFE Override — Risk by Direction

The GFE Override button allows a user or automation to tell the panel what power regime it should operate in. Not all directions carry equal risk.

| Direction                 | Action             | Risk                                                                                                       | Automation Safe?              |
| ------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------- | ----------------------------- |
| GRID &rarr; BATTERY       | Triggers shedding  | **Low** — conservative, extends runtime. Worst case is unnecessary circuit disruption.                     | Yes                           |
| BATTERY &rarr; GRID       | Stops shedding     | **Moderate** — if actually off-grid, unmanaged battery drain reduces runtime, could affect critical loads. | User confirmation recommended |
| Any &rarr; UNKNOWN        | Behavior undefined | **Unknown**                                                                                                | No                            |
| Any &rarr; PV / GENERATOR | Future / undefined | **Unknown**                                                                                                | No                            |

**Firmware reclaim behavior:** When the BESS restores communication, it reasserts its authoritative grid state. This produces a new state transition that
overrides any previous GFE Override command. The override is a temporary measure for the BESS-communication-loss window, not a persistent latch.

### Generator Systems

SPAN is always installed downstream of a transfer switch (ATS or MTS) in generator installations. There is no communication wiring between the panel and the
generator — the panel sees whatever voltage the transfer switch feeds it. When a generator is running, the panel reports GFE as GRID because it literally cannot
distinguish generator power from utility power.

Implications:

- **GFE = GRID does not necessarily mean utility power.** On panels with a generator, it could mean generator power.
- **No automatic load shedding with generators.** Shedding requires a
  [compatible integrated BESS](https://support.span.io/hc/en-us/articles/4412059545111-Storage-System-Integrations-with-SPAN).
- **Power flow sensors report generator power as grid power.** This is expected behavior, not a bug.
- The `GENERATOR` GFE value is reserved for possible future use. Distinguishing generator from utility would require either communication with the
  generator/transfer switch or a grid-side voltage sensor, neither of which exists in current configurations.

### Non-Integrated BESS

For battery systems that are not on the
[compatible integrations list](https://support.span.io/hc/en-us/articles/4412059545111-Storage-System-Integrations-with-SPAN), the panel has no communication
with the battery system. During an outage with a non-integrated BESS:

- GFE reports **GRID** even when actually off-grid — the panel does not know it is islanded.
- **No automatic load shedding** is available.
- Power flow sensors report all incoming power as grid power.

This underscores a broader point: GFE reflects what the panel **knows**, which depends on what systems are communicating with it. A non-integrated BESS provides
backup power but no panel awareness.

## Reference Links

- [Storage System Integrations with SPAN](https://support.span.io/hc/en-us/articles/4412059545111-Storage-System-Integrations-with-SPAN)
- [Adding Battery Backup to your SPAN Panel](https://support.span.io/hc/en-us/articles/4574797073303-Adding-Battery-Backup-to-your-SPAN-Panel)
- [Can I install SPAN with a standby generator?](https://support.span.io/hc/en-us/articles/6064757711255-Can-I-install-SPAN-with-a-standby-generator)
- [SPAN API Scope & Responsibility Model](https://github.com/spanio/SPAN-API-Client-Docs#span-api-scope--responsibility-model)
- [GitHub Discussion: Migration Guide — v1 dsmState to v2 dominant-power-source](https://github.com/spanio/SPAN-API-Client-Docs/discussions/8)
