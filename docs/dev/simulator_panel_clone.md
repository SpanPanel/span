# eBus Panel Clone — Simulator Feature

## Context

The simulator supports BESS/SPAN Drive simulation, breaker ratings, and full Homie v5 eBus publishing. The SPAN integration can already clone the in-memory
simulator config via the dashboard. This feature extends that: given credentials for a **real** SPAN panel, the simulator connects to its eBus, scrapes every
retained topic, and translates the result into a simulator YAML config — a faithful starting point that can then be tuned.

The integration initiates the clone by contacting the simulator over a WebSocket, passing the target panel's host and passphrase. The simulator handles the
entire scrape-and-translate pipeline, writes the new config, and reloads.

---

## Scope

**Simulator** (this feature):

- New WebSocket endpoint accepting clone requests
- Lightweight MQTT scraper (connect to real panel, collect retained messages)
- eBus-to-YAML translation layer
- Config write + hot reload

**Integration** (future, not covered here):

- UI/service action to trigger a clone request against the simulator

---

## Architecture

### Transport: WSS over TLS

The clone WebSocket runs over TLS (WSS), not plain WS. The simulator already generates a `CertificateBundle` at startup for its MQTTS broker — the clone WSS
endpoint reuses the same certificate and key. This keeps credential management in one place and means the integration's connection to the simulator is encrypted
end-to-end, which matters because the passphrase for the real panel traverses this link.

The WSS endpoint runs on its own dedicated port, separate from the dashboard HTTP server. This avoids mixing long-lived WebSocket connections with the HTMX
request/response traffic on the dashboard, and allows the clone port to be independently firewalled or exposed.

### Port configuration

The clone WSS port follows the same pattern as every other simulator port:

| Layer            | Mechanism                                                   |
| ---------------- | ----------------------------------------------------------- |
| **Default**      | `CLONE_WSS_PORT = 19443` in `const.py`                      |
| **CLI arg**      | `--clone-wss-port 19443`                                    |
| **Env var**      | `CLONE_WSS_PORT=19443`                                      |
| **SimulatorApp** | Plumbed through `__init__` alongside `dashboard_port`, etc. |

### mDNS discovery

The integration needs to know which port the clone WSS endpoint is listening on. The simulator's `PanelAdvertiser` already puts custom properties into the
`_ebus._tcp` TXT record — `httpPort` is advertised when it differs from the default. The clone WSS port is advertised the same way:

```python
# In PanelAdvertiser.register_panel(), alongside the existing httpPort logic:
ebus_properties: dict[str, str] = {
    "homie_domain": "ebus",
    "homie_version": "5",
    "homie_roles": "device",
    "mqtt_broker": hostname,
    "txtvers": "1",
}
if self._http_port != 80:
    ebus_properties["httpPort"] = str(self._http_port)
if self._clone_wss_port:
    ebus_properties["cloneWssPort"] = str(self._clone_wss_port)
```

The integration reads TXT properties from zeroconf discovery records. When `cloneWssPort` is present, the integration knows this simulator supports panel
cloning and which port to connect to. When absent, clone functionality is unavailable (real panel, older simulator, or clone not configured).

This also means the `_span._tcp` TXT record does not need changes — `cloneWssPort` is a simulator-only capability advertised on the eBus service type that the
integration already parses.

### Sequence

```text
Integration / Dashboard        Simulator                        Real Panel
        |                          |                                |
        |-- WSS: clone_panel ----->|                                |
        |   {host, passphrase}     |                                |
        |                          |-- POST /api/v2/auth/register ->|
        |                          |<-- {mqtt_creds, serial} -------|
        |                          |-- GET /api/v2/certificate/ca ->|
        |                          |<-- PEM cert -------------------|
        |<-- WSS: "registering"    |                                |
        |                          |== MQTTS connect ===============|
        |                          |-- SUB ebus/5/{serial}/# ------>|
        |                          |<-- $state, $description -------|
        |                          |<-- retained property msgs -----|
        |<-- WSS: "scraping"       |                                |
        |                          |   (collect until stable)       |
        |                          |== MQTT disconnect =============|
        |<-- WSS: "translating"    |                                |
        |                          |-- parse $description           |
        |                          |-- map properties -> YAML       |
        |                          |-- write configs/{serial}-clone.yaml
        |                          |-- trigger reload               |
        |<-- WSS: "done"           |                                |
        |   {serial, filename}     |                                |
```

### Why WebSocket

The clone is inherently async — network round-trips to the real panel, waiting for retained messages, translation. A WebSocket lets the simulator stream status
updates back to the caller as each phase completes, and the caller can display progress or abort.

### Why the simulator scrapes directly

The simulator already has paho-mqtt infrastructure and runs its own MQTT broker. Having it connect directly to the real panel's broker (as a one-shot client)
avoids routing data through the integration and keeps the translation logic co-located with the config format it produces. The integration's only job is to
provide the panel address and passphrase.

---

## WebSocket Contract

### Endpoint

`wss://{simulator_host}:{clone_wss_port}/ws/clone`

The port is discovered via the `cloneWssPort` TXT property in the simulator's `_ebus._tcp` mDNS record. The TLS certificate is the simulator's self-signed CA —
the same one returned by `GET /api/v2/certificate/ca` on the simulator's bootstrap HTTP server. The integration already fetches and trusts this CA for MQTTS, so
it can reuse the same trust store for the WSS connection.

### Request message (integration sends)

```json
{
  "type": "clone_panel",
  "host": "192.168.1.100",
  "passphrase": "panel-passphrase"
}
```

| Field        | Type           | Required | Description                           |
| ------------ | -------------- | -------- | ------------------------------------- |
| `type`       | string         | yes      | Must be `"clone_panel"`               |
| `host`       | string         | yes      | IP or hostname of the real SPAN panel |
| `passphrase` | string or null | no       | Panel passphrase (null = door-bypass) |

### Status messages (simulator sends)

```json
{
  "type": "status",
  "phase": "registering",
  "detail": "Authenticating with panel at 192.168.1.100"
}
```

| Phase         | Meaning                                                      |
| ------------- | ------------------------------------------------------------ |
| `registering` | Calling `/api/v2/auth/register` and `/api/v2/certificate/ca` |
| `connecting`  | Opening MQTTS connection to the panel's broker               |
| `scraping`    | Subscribed to `ebus/5/{serial}/#`, collecting retained msgs  |
| `translating` | Parsing `$description` and mapping properties to YAML        |
| `writing`     | Writing config file and triggering reload                    |
| `done`        | Clone complete                                               |
| `error`       | Clone failed                                                 |

### Completion message

```json
{
  "type": "result",
  "status": "ok",
  "serial": "nj-2316-XXXX",
  "clone_serial": "nj-2316-XXXX-clone",
  "filename": "nj-2316-XXXX-clone.yaml",
  "circuits": 16,
  "has_bess": true,
  "has_pv": true,
  "has_evse": false
}
```

### Error message

```json
{
  "type": "result",
  "status": "error",
  "phase": "connecting",
  "message": "MQTTS connection refused: bad credentials"
}
```

---

## eBus Scrape Strategy

### Authentication

1. `POST http://{host}/api/v2/auth/register` with `{"name": "sim-clone-{uuid4}", "hopPassphrase": passphrase}`
2. Extract `ebusBrokerUsername`, `ebusBrokerPassword`, `ebusBrokerMqttsPort`, `serialNumber`
3. `GET http://{host}/api/v2/certificate/ca` for TLS trust

### MQTT collection

1. Connect via MQTTS (port from auth response, CA cert from step 3)
2. Subscribe to `ebus/5/{serial}/#` with QoS 0
3. Collect all retained messages into a `dict[str, str]` keyed by full topic
4. Stability gate: stop collecting when no new topics arrive for 5 seconds (retained messages arrive in a burst shortly after subscription)
5. Disconnect cleanly

### Required topics

The scraper must receive at minimum:

| Topic pattern                   | Purpose                         |
| ------------------------------- | ------------------------------- |
| `$state`                        | Confirm panel is `ready`        |
| `$description`                  | Node topology (types, node IDs) |
| `core/breaker-rating`           | Main breaker size               |
| `core/serial-number`            | Panel identity                  |
| `{circuit-uuid}/name`           | Circuit names                   |
| `{circuit-uuid}/space`          | Tab/breaker position            |
| `{circuit-uuid}/dipole`         | 240V detection                  |
| `{circuit-uuid}/breaker-rating` | Per-circuit breaker size        |
| `{circuit-uuid}/relay`          | Current relay state             |
| `{circuit-uuid}/shed-priority`  | Circuit priority                |
| `{circuit-uuid}/active-power`   | Current power (W)               |

Optional but used when available:

| Topic pattern                    | Purpose                     |
| -------------------------------- | --------------------------- |
| `{circuit-uuid}/imported-energy` | Seed energy accumulators    |
| `{circuit-uuid}/exported-energy` | Seed energy accumulators    |
| `bess-0/nameplate-capacity`      | Battery sizing              |
| `bess-0/soc`                     | Initial SOC                 |
| `bess-0/grid-state`              | Grid state at clone time    |
| `pv-0/nameplate-capacity`        | PV system sizing            |
| `pv-0/feed`                      | Which circuit PV feeds      |
| `evse-*/feed`                    | Which circuit EVSE feeds    |
| `evse-*/status`                  | Charger state at clone time |
| `upstream-lugs/active-power`     | Grid power reference        |
| `power-flows/*`                  | Validation / sanity check   |

---

## eBus-to-YAML Translation

### Units

All power values on the eBus are in **watts**. The Homie schema historically declared circuit `active-power` as `kW`, but this is a schema metadata error —
actual published values have always been watts. SPAN firmware 202609 corrects the schema declaration to `W`. The simulator config also uses watts. No unit
conversion is needed anywhere in the pipeline.

### Panel config

| eBus source                    | YAML target                  | Notes                               |
| ------------------------------ | ---------------------------- | ----------------------------------- |
| `core/serial-number`           | `panel_config.serial_number` | Append `-clone` suffix              |
| `core/breaker-rating`          | `panel_config.main_size`     | Integer amps                        |
| Panel size from `$description` | `panel_config.total_tabs`    | Count circuit space range           |
| —                              | `panel_config.latitude`      | Default 37.7 (user adjusts later)   |
| —                              | `panel_config.longitude`     | Default -122.4 (user adjusts later) |

Panel size is derived from the `$description` by examining the Homie schema's circuit `space` property format string (e.g. `"1:32:1"` means 32 spaces). This
matches how span-panel-api determines panel size.

### Circuit mapping

For each node in `$description` with type `energy.ebus.device.circuit`:

| eBus property     | YAML target                                        | Derivation                                                                           |
| ----------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `name`            | `circuits[].name`                                  | Direct                                                                               |
| `space`           | `circuits[].tabs`                                  | `[space]` for single-pole; `[space, space+2]` if dipole                              |
| `dipole`          | —                                                  | Determines tab count (true = 240V, 2 tabs)                                           |
| `breaker-rating`  | `circuits[].breaker_rating`                        | Integer amps                                                                         |
| `relay`           | `circuit_templates[].relay_behavior`               | `OPEN`/`CLOSED` = `controllable`; presence of `always-on: true` = `non_controllable` |
| `shed-priority`   | `circuit_templates[].priority`                     | Direct (`NEVER`, `SOC_THRESHOLD`, `OFF_GRID`)                                        |
| `active-power`    | `circuit_templates[].energy_profile.typical_power` | Absolute value in watts                                                              |
| `active-power`    | `circuit_templates[].energy_profile.power_range`   | `[0, breaker_rating * voltage]`                                                      |
| `imported-energy` | Energy accumulator seed                            | Optional: pre-populate consumed Wh                                                   |
| `exported-energy` | Energy accumulator seed                            | Optional: pre-populate produced Wh                                                   |

**Energy profile mode** is inferred from context:

- Circuits with a `pv-0/feed` reference pointing to them: `mode: "producer"`
- Circuits with a `bess-0/feed` reference pointing to them: `mode: "bidirectional"`
- Circuits with an `evse-*/feed` reference pointing to them: `mode: "bidirectional"`, `device_type: "evse"`
- Everything else: `mode: "consumer"`

**Circuit ID** in the YAML uses a stable scheme: `circuit_{space}` (e.g. `circuit_5` for space 5, `circuit_7` for a 240V circuit at spaces 7/9). The Homie UUID
is not carried over — the simulator generates its own UUIDs deterministically from circuit IDs.

### Template strategy

Each circuit gets its own template named `clone_{space}` (e.g. `clone_5`). While this produces more templates than a hand-authored config, it preserves
per-circuit fidelity from the real panel. The user can consolidate templates later via the dashboard.

Defaults applied to all cloned templates:

```yaml
energy_profile:
  mode: <inferred>
  power_range: [0, <breaker_rating * voltage>]
  typical_power: <observed active-power, abs>
  power_variation: 0.1
relay_behavior: <inferred>
priority: <from eBus>
```

### BESS mapping

If `$description` contains a node with type `energy.ebus.device.bess`:

| eBus property               | YAML target                                     |
| --------------------------- | ----------------------------------------------- |
| `bess-0/nameplate-capacity` | `battery_behavior.nameplate_capacity_kwh`       |
| `bess-0/soc`                | Initial SOC (engine start state)                |
| `bess-0/feed`               | Identifies which circuit is the battery circuit |

The battery circuit template gets:

```yaml
battery_behavior:
  enabled: true
  charge_mode: "custom"
  nameplate_capacity_kwh: <from eBus>
  backup_reserve_pct: 20.0
  charge_efficiency: 0.95
  discharge_efficiency: 0.95
  max_charge_power: <derived from breaker_rating * voltage * 0.8>
  max_discharge_power: <same>
  charge_hours: [0, 1, 2, 3, 4, 5]
  discharge_hours: [16, 17, 18, 19, 20, 21]
  idle_hours: [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 22, 23]
```

Charge/discharge schedule uses sensible defaults since the real panel's schedule is not exposed on the eBus.

### PV mapping

If `$description` contains a node with type `energy.ebus.device.pv`:

| eBus property             | YAML target                                |
| ------------------------- | ------------------------------------------ |
| `pv-0/nameplate-capacity` | `energy_profile.nameplate_capacity_w`      |
| `pv-0/feed`               | Identifies which circuit is the PV circuit |

The PV circuit template gets:

```yaml
device_type: "pv"
energy_profile:
  mode: "producer"
  power_range: [-<nameplate>, 0]
  typical_power: <-nameplate * 0.6>
  nameplate_capacity_w: <from eBus>
```

### EVSE mapping

If `$description` contains nodes with type `energy.ebus.device.evse`:

| eBus property | YAML target                                  |
| ------------- | -------------------------------------------- |
| `evse-*/feed` | Identifies which circuit is the EVSE circuit |

The EVSE circuit template gets:

```yaml
device_type: "evse"
energy_profile:
  mode: "bidirectional"
  power_range: [-<breaker_rating * voltage>, <breaker_rating * voltage>]
  typical_power: <observed active-power or 0>
time_of_day_profile:
  enabled: true
  hour_factors: <night charging preset>
```

### Simulation params

Cloned configs use conservative defaults:

```yaml
simulation_params:
  update_interval: 5
  time_acceleration: 1.0
  noise_factor: 0.02
  enable_realistic_behaviors: true
```

### Serial number

The clone serial is `{original_serial}-clone`. This ensures:

- No MQTT topic collision with a real panel on the same broker
- Clear provenance when inspecting configs
- The simulator's mDNS advertisement is distinguishable

### Output

Written to `{config_dir}/{original_serial}-clone.yaml`. If a file with that name already exists, it is overwritten (the user explicitly requested a re-clone).
After writing, the simulator triggers a hot reload to pick up the new panel.

---

## Name-based heuristics (future enhancement)

A later pass could apply smarter template defaults based on circuit names:

| Name pattern               | Applied template behavior              |
| -------------------------- | -------------------------------------- |
| `HVAC`, `AC`, `Heat Pump`  | `hvac_type`, cycling pattern           |
| `Refrigerator`, `Fridge`   | Cycling pattern (15min on / 30min off) |
| `Dryer`, `Washer`          | Time-of-day profile (daytime usage)    |
| `EV Charger`, `SPAN Drive` | EVSE schedule preset                   |
| `Pool Pump`                | Time-of-day profile + cycling          |

This is not part of the initial implementation. The baseline clone captures the topology and sizing accurately; behavioral patterns are tuned via the dashboard.

---

## Implementation plan

### Phase 1: WSS server and port plumbing

**Port constant and CLI arg**:

- Add `CLONE_WSS_PORT = 19443` to `const.py`
- Add `--clone-wss-port` arg to `__main__.py` (with `CLONE_WSS_PORT` env var fallback)
- Plumb through `SimulatorApp.__init__` and store as `self._clone_wss_port`

**mDNS advertisement**:

- Add `clone_wss_port` parameter to `PanelAdvertiser.__init__`
- Advertise `cloneWssPort` in `_ebus._tcp` TXT properties when the port is set

**WSS server lifecycle** (in `SimulatorApp.run()`):

- Create a dedicated `aiohttp.web.Application` with a single route: `/ws/clone`
- Bind it to an `ssl.SSLContext` using the existing `CertificateBundle` (same cert/key as MQTTS)
- Start as a `web.TCPSite` on `0.0.0.0:{clone_wss_port}`
- Shut down in the `finally` block alongside the dashboard and bootstrap servers

The WSS handler accepts the WebSocket upgrade, validates the `clone_panel` message, and drives the scrape-translate-write pipeline, sending status messages as
each phase progresses.

**Files**: `const.py`, `__main__.py`, `app.py`, `discovery.py`

### Phase 2: eBus scraper

New module `scraper.py` in the simulator package. Responsibilities:

1. Call the panel's v2 REST endpoints for auth and CA cert (using `aiohttp.ClientSession`)
2. Connect via paho-mqtt with TLS (reuse existing infrastructure patterns)
3. Subscribe to `ebus/5/{serial}/#` and collect retained messages
4. Return a `ScrapedPanel` dataclass containing the `$description` dict and all property values

This is a lightweight, purpose-built client — it does not import span-panel-api. It only needs to parse the `$description` JSON and collect string property
values.

**Files**: `scraper.py` (new)

### Phase 3: Translation layer

New module `clone.py` in the simulator package. Responsibilities:

1. Parse the `$description` to identify node types and IDs
2. Cross-reference `feed` properties to identify PV/BESS/EVSE circuits
3. Map each circuit's properties to a template + circuit definition
4. Build the complete YAML config dict
5. Validate via existing `validate_yaml_config()`
6. Write to the config directory

**Files**: `clone.py` (new)

### Phase 4: Integration trigger (separate repo)

Service action or config flow option in the SPAN integration that opens a WebSocket to the simulator and sends the `clone_panel` message. This is out of scope
for the simulator repo.

---

## Error handling

| Failure                         | Behavior                                           |
| ------------------------------- | -------------------------------------------------- |
| Panel unreachable               | Error at `registering` phase, WS error message     |
| Bad passphrase                  | Auth returns 401/403, WS error message             |
| MQTT connection refused         | Error at `connecting` phase                        |
| No `$description` received      | Timeout after 15s, error at `scraping` phase       |
| No circuit nodes in description | Error at `translating` phase                       |
| Config validation fails         | Error at `writing` phase, partial config not saved |
| Existing clone file             | Overwritten (intentional re-clone)                 |

All errors are reported via the WebSocket `result` message with `status: "error"` and a human-readable `message`. The simulator never crashes on a failed clone
attempt.

---

## Testing

| Test                                   | Validates                                       |
| -------------------------------------- | ----------------------------------------------- |
| Unit: translate `$description` + props | Correct YAML structure from known eBus fixture  |
| Unit: tab derivation from space+dipole | Single-pole and 240V mapping                    |
| Unit: device type inference from feeds | PV/BESS/EVSE circuit detection                  |
| Unit: serial suffix                    | `{serial}-clone` naming                         |
| Integration: full scrape mock          | WebSocket flow with mocked MQTT                 |
| Integration: config roundtrip          | Cloned config loads and simulates without error |
