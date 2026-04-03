# Frontend Dashboard

The SPAN Panel integration includes a built-in frontend dashboard accessible from the Home Assistant sidebar. The dashboard provides real-time visualization of
your panel's electrical activity, circuit-level monitoring configuration, and integration settings — all without requiring a separate Lovelace card.

## Enabling the Dashboard

The dashboard is enabled via the integration's configuration options:

1. Go to `Settings` > `Devices & Services` > `SPAN Panel` > `Configure` > `General Options`
2. Check **Show Dashboard in Sidebar** to add a sidebar entry
3. Optionally check **Admin Users Only** to restrict access to administrator accounts

![Configuration Options](images/config_flow.png)

## Panel View

The Panel tab displays your SPAN panel layout with real-time circuit-level power graphs. Each circuit card shows:

- **Breaker rating** and **current power draw** in the card header
- **Circuit priority icons** indicating always-on, never-shed, SoC threshold, or off-grid shed behavior
- **Historical power or current graph** with configurable time horizon globally or per circuit
- **Switch control** for user-controllable circuits (via the safety slider in the card header)

The top banner summarizes panel-level metrics: site power, grid state, upstream/downstream current, and solar production. A firmware version badge and legend
for circuit priority icons are displayed alongside.

Use the **Enable Switches** toggle in the banner to globally enable or disable circuit switch controls.

![Panel Dashboard](images/frontend.png)

## Monitoring View

The Monitoring tab provides current-based alerting for individual circuits. It detects sustained high utilization and transient spikes relative to each
circuit's breaker rating, then delivers notifications through configurable channels.

### Global Settings

- **Continuous (%)** — Sustained utilization threshold as a percentage of breaker rating. When a circuit draws more than this percentage continuously for the
  configured window duration, an alert is triggered. For example, a 15A breaker at 80% fires when the circuit sustains above 12A.
- **Spike (%)** — Instantaneous spike threshold as a percentage of breaker rating. A single reading above this level triggers an immediate alert without waiting
  for the window duration. For example, a 15A breaker at 100% fires the moment current exceeds 15A.
- **Window (min)** — How long a circuit must continuously exceed the continuous threshold before an alert fires. Short windows (e.g., 1–2 min) catch brief
  overloads; longer windows (e.g., 5–15 min) filter out transient loads like motor startup surges.
- **Cooldown (min)** — Minimum quiet period after an alert before the same circuit can trigger another alert. Prevents notification floods when a circuit
  oscillates around the threshold. For example, a 15-minute cooldown means you receive at most one alert per circuit every 15 minutes.

### Notification Settings

- **All Targets** — Select all notification targets at once
- **Notify Targets** — Individual notification targets including mobile devices, persistent notification, and the HA event bus
- **Priority** — Notification priority level (controls iOS interruption level and Android notification channel)
- **Title/Message Templates** — Customizable templates with the following variables:

  | Variable             | Description                     | Example                     |
  | -------------------- | ------------------------------- | --------------------------- |
  | `{name}`             | Circuit or mains friendly name  | Kitchen Oven                |
  | `{entity_id}`        | HA entity ID                    | sensor.kitchen_oven_current |
  | `{alert_type}`       | Alert category                  | spike, continuous_overload  |
  | `{current_a}`        | Current draw in amps            | 18.3                        |
  | `{breaker_rating_a}` | Breaker rating in amps          | 20                          |
  | `{threshold_pct}`    | Configured threshold percentage | 80                          |
  | `{utilization_pct}`  | Actual utilization percentage   | 91.5                        |
  | `{window_m}`         | Window duration in minutes      | 5                           |
  | `{local_time}`       | Local time of the alert         | 2:15 PM                     |

### Monitored Points

Each circuit can be individually enabled or disabled for monitoring, with per-circuit overrides for continuous threshold, spike threshold, window, and cooldown
values.

![Monitoring Configuration](images/monitoring.png)

## Settings View

The Settings tab provides access to integration configuration options without navigating through the Home Assistant settings menu.
