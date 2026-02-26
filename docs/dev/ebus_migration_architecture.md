# eBus Migration Architecture & Implementation Plan

## Executive Summary

The SPAN Panel REST v1 API is deprecated (sunset 2026-12-31). The replacement is a v2 architecture comprising MQTT/Homie for runtime data and a minimal v2 REST
surface for auth and certificate provisioning. This document evaluates the external `span-hass` reference implementation, determines the correct architectural
approach for our stack, and lays out a phased implementation plan that preserves existing features and energy statistics history.

---

## Library Status — span-panel-api 2.0.0 (Updated 2026-02-25)

**span-panel-api v2.0.0 has shipped as MQTT-only.** REST transport, generated OpenAPI client, virtual circuits, delay registry, and all v1-specific code have
been removed. Users are told not to upgrade the integration until they have v2 firmware, eliminating the need for dual-transport support.

### What shipped

| Component             | Status                                                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `auth.py`             | v2 HTTP provisioning: `register_v2()`, `download_ca_cert()`, `get_homie_schema()`, `regenerate_passphrase()`, `get_v2_status()` |
| `detection.py`        | `detect_api_version()` → `DetectionResult` (unauthenticated v2 status probe)                                                    |
| `mqtt/`               | Full MQTT transport: `AsyncMQTTClient`, `AsyncMqttBridge`, `HomieDeviceConsumer`, `SpanMqttClient`                              |
| `factory.py`          | `create_span_client(host, passphrase?, mqtt_config?, serial_number?) → SpanMqttClient`                                          |
| `protocol.py`         | 3 protocols: `SpanPanelClientProtocol`, `CircuitControlProtocol`, `StreamingCapableProtocol`                                    |
| `models.py`           | `SpanPanelSnapshot`, `SpanCircuitSnapshot`, `SpanBatterySnapshot`, `V2AuthResponse`, `V2StatusInfo`, `V2HomieSchema`            |
| `simulation.py`       | `DynamicSimulationEngine` (YAML-driven, produces snapshots directly)                                                            |
| `phase_validation.py` | Electrical phase utilities                                                                                                      |

### What was removed (vs v1.x)

| Removed                                                  | Reason                                                   |
| -------------------------------------------------------- | -------------------------------------------------------- |
| `rest/` (entire directory)                               | v1 REST transport, delay registry, virtual circuits      |
| `client.py` (shim)                                       | `SpanPanelClient = SpanRestClient` backward-compat alias |
| `generated_client/`                                      | OpenAPI v1 generated models                              |
| `CircuitCorrelationProtocol`                             | Correlation moved to integration layer                   |
| `CorrelationUnavailableError`, `SpanPanelRetriableError` | REST-only exceptions                                     |
| `DeprecationInfo` model                                  | v1 deprecation header tracking                           |
| `PanelCapability.REST_V1`, `PanelCapability.SIMULATION`  | REST-only capability flags                               |
| `attrs`, `python-dateutil` dependencies                  | No longer needed                                         |

### Current PanelCapability flags

```python
class PanelCapability(Flag):
    NONE = 0
    PUSH_STREAMING = auto()
    EBUS_MQTT = auto()
    CIRCUIT_CONTROL = auto()
    BATTERY_SOE = auto()
```

### Factory signature (final)

```python
async def create_span_client(
    host: str,
    passphrase: str | None = None,
    mqtt_config: MqttClientConfig | None = None,
    serial_number: str | None = None,
) -> SpanMqttClient:
```

No `api_version` parameter — the factory is MQTT-only. If `mqtt_config` is omitted and `passphrase` is provided, the factory calls `register_v2()` to obtain
broker credentials automatically.

### Dependencies

| Package           | Status                           |
| ----------------- | -------------------------------- |
| `httpx`           | Required (auth.py, detection.py) |
| `paho-mqtt`       | Required (was optional in v1.x)  |
| `pyyaml`          | Required (simulation configs)    |
| `attrs`           | **Removed**                      |
| `python-dateutil` | **Removed**                      |

### Package structure

```text
src/span_panel_api/
├── __init__.py          # Public API exports
├── auth.py              # v2 HTTP provisioning (register, cert, schema)
├── const.py             # Panel state constants (DSM, relay)
├── detection.py         # API version detection
├── exceptions.py        # Exception hierarchy (simplified)
├── factory.py           # create_span_client() → SpanMqttClient
├── models.py            # Snapshot dataclasses + auth response models
├── phase_validation.py  # Electrical phase utilities
├── protocol.py          # PEP 544 protocols (3 protocols)
├── simulation.py        # Simulation engine (produces snapshots)
└── mqtt/
    ├── __init__.py
    ├── async_client.py  # AsyncMQTTClient + NullLock (HA core pattern)
    ├── client.py        # SpanMqttClient
    ├── connection.py    # AsyncMqttBridge (event-loop-driven paho wrapper)
    ├── const.py         # MQTT/Homie constants + UUID helpers
    ├── homie.py         # HomieDeviceConsumer (Homie v5 parser)
    └── models.py        # MqttClientConfig, MqttTransport
```

### Test stats

- 278 tests passing
- 91% coverage
- mypy strict clean, ruff clean, all pre-commit hooks pass

---

## Part 1 — DCJ `span-hass` Code Evaluation

This evalution was conducted to determine the delta between the self-described 'alpha' integration and the deployed production SpanPanel/span integration. The
decision matrix was pursued in an effort to determine the path of least risk for existing deployments and future development. A key input to this evaluatioin
was the attempted gRPC event driven development (Griswoldlabs, cecilkootz, et al) which illustrated the need for controlled CPU consumption of a large event
driven integration with many sensors potentially spread across multiple panels or other adjoining infrastructure

### What's Done Well

1. **Schema-driven entity generation (`node_mappers.py`)** Parses the Homie `$description` JSON and maps nodes/properties to HA `EntitySpec` objects via
   per-node mapper functions. Decouples entity creation from hard-coded panel topology — new properties get entities automatically.

2. **Push-based architecture** Uses MQTT callbacks with `hass.loop.call_soon_threadsafe()` to bridge from paho-mqtt's background thread to HA's event loop.
   Eliminates polling overhead and provides near-real-time updates.

3. **Entity base class (`entity_base.py`)**
   - `_attr_should_poll = False` (correct for push).
   - `_attr_has_entity_name = True` (HA best practice).
   - Registers callbacks in `async_added_to_hass`, unregisters in `async_will_remove_from_hass`.
   - Abstract `_update_from_value(value: str)` forces subclasses to implement type-specific parsing.
   - Sub-device grouping via `DeviceInfo` with `_SUB_DEVICE_TYPES`.

4. **Config flow** Zeroconf for both `_ebus._tcp` and `_secure-mqtt._tcp`. Serial extraction from mDNS instance name. Auth menu (passphrase or door bypass). CA
   certificate download and storage.

5. **`SpanPanel` bridge (`span_panel.py`)** Clean callback registration/unregistration pattern. Event-based synchronization (`description_received`,
   `device_ready`).

6. **Sensor value parsing (`sensor.py`)** Handles numeric vs string properties, negation support for inverted semantics (consumed energy), and feed-property
   resolution (circuit ID → human name).

### What's Problematic

1. **MQTT threading model incompatible with HA core patterns**
   - Both span-hass and its ebus-sdk dependency use paho-mqtt's
     `loop_start()` background thread with `call_soon_threadsafe()`
     dispatch.
   - HA core's own MQTT integration (`homeassistant.components.mqtt`)
     uses a fundamentally different approach: `AsyncMQTTClient`
     subclasses paho's `Client`, replaces all 7 internal threading
     locks with `NullLock` no-ops, and drives I/O entirely from the
     asyncio event loop via `add_reader`/`add_writer` on paho's socket
     with direct `loop_read()`/`loop_write()`/`loop_misc()` calls.
     Zero background threads.
   - Any integration targeting HA core acceptance must follow this
     pattern. span-panel-api's `AsyncMqttBridge` follows HA core's
     `AsyncMQTTClient`/`NullLock` approach with zero background threads
     and no `threading.Lock` usage. span-hass and ebus-sdk's threading
     model is baked in and unfixable without a rewrite.
   - `DiscoveredDevice.properties` dict is mutated on paho thread and
     read from HA thread without synchronization.
   - `_property_callbacks` dict is accessed from both threads (HA thread
     registers; paho thread iterates in `_on_property_changed`).
   - `_available` boolean is set from paho thread, read from HA thread.
   - `call_soon_threadsafe` is correct for dispatching *to* the HA loop,
     but data access *before* dispatch is not synchronized.

2. **Fire-and-forget control** `set_property()` publishes to MQTT and returns `True/False` based on publish success only. No optimistic state, no confirmation
   the relay changed, no timeout/retry. Entity state updates only when the next MQTT message arrives (or never, on loss).

3. **No energy / statistics handling** No `SensorStateClass.TOTAL_INCREASING`. No HA energy dashboard integration. No accumulated-energy tracking, spike
   detection, or grace-period logic.

4. **Error recovery** Relies entirely on paho-mqtt's auto-reconnect. No explicit availability management when the MQTT broker disappears without sending a state
   message.

5. **Missing HA integration features** No options flow. No config entry migration versioning. No entity migration. No diagnostics. No device triggers. Only one
   service (`link_subpanel`).

6. **Code maturity** No tests. No type annotations on some parameters (`panel: Any`). Alpha-quality SDK dependency.

7. **ebus-sdk quality concerns**
   - Comments literally say *"TODO: Make getting and setting a property's value thread-safe."*
   - Debug imports (`pp`, `pformat`) in production code.
   - `asyncio.ensure_future(callback, loop=...)` — deprecated in Python 3.10+.
   - Bare `except:` handlers (catch SystemExit, KeyboardInterrupt).
   - No type hints throughout.
   - Global mutable state via environment variables.

---

## Part 2 — Architecture Decision

### The Question

> Should MQTT/Homie support be added to span-panel-api or directly to
> the span integration? And should it coexist with REST, or replace it?

### Key Finding: span-panel-api Already Has a Transport Abstraction

The span-panel-api library already defined:

- **`SpanPanelClientProtocol`** — core protocol every transport must satisfy (`connect`, `close`, `ping`, `get_snapshot`).
- **`StreamingCapableProtocol`** — mixin for push-based transports (`register_callback`, `start_streaming`, `stop_streaming`).
- **`PanelCapability` flags** — runtime feature advertisement including `PUSH_STREAMING`.
- **`SpanPanelSnapshot` / `SpanCircuitSnapshot`** — transport-agnostic data models.
- **`create_span_client()` factory** — creates transport clients.

This architecture was designed for adding transports. We used this to
add MQTT alongside REST, then removed REST entirely in v2.0.0.

### Options Evaluated

| Option                                 | Description                                   | Verdict                                                                      |
| -------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------- |
| **A: Add MQTT to span-panel-api, then remove REST** | MQTT replaces all legacy transports | **Selected** |
| **B: Use ebus-sdk directly from span** | Skip span-panel-api, depend on ebus-sdk       | Rejected — alpha quality, no thread safety, no async, no types               |
| **C: MQTT directly in span**           | Bypass span-panel-api entirely for MQTT       | Rejected — violates established library boundary, duplicates transport logic |
| ~~**D: Add MQTT as a third transport alongside REST**~~ | Keep REST + MQTT coexisting | Rejected — unnecessary complexity; v1 REST sunset 2026-12-31, users told to upgrade firmware before upgrading integration |

### Decision: Replace All Transports with MQTT in span-panel-api (COMPLETE)

**Outcome (2026-02-24):** MQTT was added alongside REST as an
intermediate step, then REST was surgically removed. The result
is span-panel-api 2.0.0 — an MQTT-only library. Users are told not to
upgrade the integration until they have v2 firmware, eliminating the need
for dual-transport coexistence.

**Rationale:**

1. **Architectural alignment** — span-panel-api's purpose is "communicate
   with SPAN Panel hardware." MQTT is the v2 transport for that purpose.
   With v1 REST sunset at 2026-12-31, maintaining multiple transports
   added complexity with no long-term benefit.

2. **Reject ebus-sdk dependency** — Alpha quality with admitted thread-safety
   problems, no async support, debug code in production. We should not
   depend on it for a production integration.

3. **Homie protocol is straightforward** — The ebus-sdk's ~1500-line
   `homie.py` is mostly for *publishing* devices. We only need the
   *consumer/controller* side: subscribe to topics, parse `$description`,
   track property values, publish to `/set` topics. This is ~300-400
   lines of well-typed code.

4. **MQTT client architecture matches HA core.** span-panel-api's
   `AsyncMqttBridge` follows HA core's own MQTT pattern
   (`homeassistant.components.mqtt`):
   - **No background thread** — paho's socket is registered with the
     asyncio event loop via `add_reader`/`add_writer`, calling
     `loop_read()`/`loop_write()`/`loop_misc()` directly from the
     event loop.
   - **NullLock** — `AsyncMQTTClient` subclasses paho's `Client` and
     replaces all 7 internal threading locks with no-ops, because
     everything runs on the single event loop thread.
   - **Zero threading** — no background thread, no `threading.Lock`,
     fully event-loop driven.

   This architecture is implemented once in span-panel-api. The
   coordinator sees the same `SpanMqttClient` interface. Had we adopted
   ebus-sdk, its incompatible threading model would have been unfixable
   without a rewrite — a key factor in the architecture decision.

5. **Unified data models** — `SpanPanelSnapshot` / `SpanCircuitSnapshot`
   already represent the data we need. The MQTT transport populates them
   from MQTT/Homie instead of REST JSON.

6. **Factory simplification** — `create_span_client()` is MQTT-only. No
   `api_version` parameter, no detection cascade. Accepts `passphrase`
   or pre-built `MqttClientConfig`.

### The API Boundary (Updated for v2.0.0)

```text
┌──────────────────────────────────────────────────────────┐
│                     span (HA integration)                │
│                                                          │
│  config_flow  → detects v2 firmware, passphrase auth     │
│  coordinator  → hybrid push/poll on SpanPanelSnapshot    │
│  entities     → read snapshot fields, write via client   │
│  services     → energy spike cleanup, undo stats, etc.   │
│  migration    → v1→v2 entity/unique ID, solar→PV         │
└──────────────────────┬───────────────────────────────────┘
                       │ uses
┌──────────────────────┴───────────────────────────────────┐
│                span-panel-api 2.0.0 (library)            │
│                                                          │
│  SpanMqttClient        MQTT/Homie transport (only)       │
│    implements:  SpanPanelClientProtocol                   │
│                 CircuitControlProtocol                    │
│                 StreamingCapableProtocol                  │
│                                                          │
│  auth.py               v2 REST: register, cert, schema   │
│  detection.py          v2 status probe                   │
│  simulation.py         YAML-driven snapshot engine        │
│                                                          │
│  SpanPanelSnapshot  =  library↔integration contract      │
└──────────────────────────────────────────────────────────┘
```

### What NOT to Do

- **Do not adopt ebus-sdk.** Too risky for production.
- **Do not change the `span_panel` domain name.** Entity ID and statistics
  preservation requires keeping the domain.
- **Do not maintain dual-transport code.** REST v1 has been dropped in
  span-panel-api 2.0.0. Users are told not to upgrade the integration
  until they have v2 firmware. The integration requires
  `span-panel-api>=2.0.0` and removes all REST client references.

---

## Part 2b — Build on Existing vs Fresh Canvas

### The Question

> Given that the v1 API will be sunset, should we start from a fresh canvas for span-panel-api or build on the existing codebase?

### Decision: Build on Existing → Then Cut (COMPLETE)

**Outcome (2026-02-24):** This approach was followed and completed. span-panel-api v2.0.0 was built by adding MQTT alongside REST, then surgically removing all
REST code. The result is an MQTT-only library arrived at through subtraction.

Users are told not to upgrade the integration until they have v2 firmware, eliminating the dual-transport coexistence period originally anticipated.

### Original Rationale (preserved for context)

1. **The architecture already exists for this.** `SpanPanelClientProtocol`, `create_span_client()` factory, and `PanelCapability` flags were specifically
   designed to support multiple transports behind a single interface. The MQTT transport goes in `mqtt/` as a new subpackage — zero coupling to the REST code.

2. **The "remove legacy" step is surgical, not architectural.** Deletion targets were well-isolated — `client.py`, `generated_client/`, factory branches,
   `attrs`/`python-dateutil` dependencies. What remains is effectively the fresh canvas arrived at through subtraction.

### Actual Timeline

| When           | What                                                   |
| -------------- | ------------------------------------------------------ |
| **Phases 0–5** | Added MQTT transport alongside REST in span-panel-api  |
| **v2.0.0**     | Cut REST entirely. MQTT-only. ~7600 lines removed.     |
| **Next**       | Update span integration to use span-panel-api >= 2.0.0 |

---

## Part 2c — API Version Detection Strategy

The `SpanPanelClientProtocol` interface does not need to change. Detection is purely a factory and config-flow concern. SPAN provides **three independent,
layered detection signals**.

### Detection Signal 1: mDNS Service Advertisements (Best — Zero Network Cost)

v2-capable firmware advertises services that v1-only firmware does not:

| Service Type                                | v1-only firmware | v2 firmware |
| ------------------------------------------- | ---------------- | ----------- |
| `_span._tcp.local.`                         | Yes              | Yes         |
| `_http._tcp.local.` (TXT: `versions=v1,v2`) | No               | Yes         |
| `_secure-mqtt._tcp.local.`                  | No               | Yes         |
| `_ebus._tcp.local.`                         | No               | Yes         |

During zeroconf discovery, the **presence of `_ebus._tcp` or `_secure-mqtt._tcp`** indicates v2 before making any HTTP request. The `_http._tcp` TXT record
includes `versions = v1,v2` and `v1_deprecated = true`.

Additional mDNS metadata available on v2 firmware:

- `_device-info._tcp` — TXT records: `manufacturer`, `model`, `hardware_version`, `serial_number`, `firmware_version`, MAC addresses.
- `_ebus._tcp` — TXT records: `homie_domain = ebus`, `homie_version = 5`, `homie_roles = device`, `mqtt_broker = span-{serial}`.

### Detection Signal 2: `GET /api/v2/status` (Lightweight REST Probe)

The v2 status endpoint is **unauthenticated** and returns only `serialNumber` and `firmwareVersion` — a much smaller response than v1's `GET /api/v1/status`
(which returns nested software, system, network, door state, etc.).

- `GET /api/v2/status` → **200** = v2 capable.
- `GET /api/v2/status` → **404** = v1 only.

### Detection Signal 3: `GET /api/v2/homie/schema` (Unauthenticated Schema)

Returns the full Homie property schema plus `firmwareVersion` and `typesSchemaHash`. Also unauthenticated. **404 = v1 only.**

The `typesSchemaHash` (SHA-256, first 16 hex chars) enables clients to detect schema changes across firmware versions without comparing the full `types` object.

### Detection Signal 4: v1 Deprecation Headers

All `/api/v1/*` endpoints on v2-capable firmware return HTTP headers:

| Header        | Value                                    |
| ------------- | ---------------------------------------- |
| `Deprecation` | `true`                                   |
| `Sunset`      | `2026-12-31`                             |
| `Link`        | `</api/v2/...>; rel="successor-version"` |

These headers are only visible to installations still running the v1 REST integration.
They indicate that v2 firmware is present and the v1 API has a sunset date — purely informational.
By the time a user has upgraded to the v2 integration (MQTT-only),
they have already completed passphrase auth and are no longer making v1 REST calls.
If a user upgrades the integration before their panel has v2 firmware,
the integration will fail to connect and they must roll back.

### Detection Implementation

#### In `create_span_client()` Factory (span-panel-api) — Updated

The factory is MQTT-only in v2.0.0. No `api_version` parameter:

```python
async def create_span_client(
    host: str,
    passphrase: str | None = None,
    mqtt_config: MqttClientConfig | None = None,
    serial_number: str | None = None,
) -> SpanMqttClient:
```

If `mqtt_config` is omitted and `passphrase` is provided, the factory calls `register_v2()` to obtain broker credentials automatically.

Detection is a separate function for use in config flows:

```python
async def detect_api_version(host: str) -> DetectionResult:
    """Probe GET /api/v2/status to detect API version."""
    # Returns DetectionResult with api_version ("v1"|"v2")
    # and optional V2StatusInfo (serial_number, firmware_version)
```

#### In Config Flow (span)

1. **Zeroconf path**: Service type in `discovery_info` determines version. `_ebus._tcp` or `_secure-mqtt._tcp` → v2. `_span._tcp` → probe v2 status endpoint to
   confirm.

2. **Manual host entry path**: Probe `GET /api/v2/status` first. If 200, extract `serialNumber` + `firmwareVersion` from the v2 response. If 404, fall back to
   `GET /api/v1/status`.

3. **Existing installations after firmware update**: Coordinator detects deprecation headers on v1 responses and surfaces a persistent notification suggesting
   re-authentication for MQTT.

#### Interface Impact (Updated for v2.0.0)

- `SpanPanelClientProtocol` — **no change** (3 protocols retained).
- `create_span_client()` — MQTT-only, no `api_version` parameter. Accepts `passphrase` or pre-built `MqttClientConfig`.
- `detect_api_version()` — separate function for config flow use. Returns `DetectionResult` with `api_version` and `V2StatusInfo`.
- Config entry data — stores MQTT credentials from `V2AuthResponse`.
- `manifest.json` — adds zeroconf entries for `_ebus._tcp.local.` and `_secure-mqtt._tcp.local.`.

#### Firmware Version Format Difference

The firmware version string format is consistent (`"spanos2/r202546/03"`) but the JSON structure differs between v1 and v2 status endpoints:

- **v1** `GET /api/v1/status`: nested `{"software": {"firmwareVersion": "..."}}` plus `system`, `network`, door state, etc.
- **v2** `GET /api/v2/status`: flat `{"serialNumber": "...", "firmwareVersion": "..."}`

This is handled inside each transport's implementation, not in the protocol interface.

---

## Part 2d — Layer Allocation

The migration touches two repositories. This section makes explicit which concerns belong where, preventing scope creep across the boundary.

### span-panel-api (transport library) — Updated for v2.0.0

Everything below the HA integration boundary — no HA imports, no entity registry knowledge, no statistics access. **REST transport removed in v2.0.0.**

| Concern               | Notes                                                                           |
| --------------------- | ------------------------------------------------------------------------------- |
| API version detection | `detect_api_version()` in `detection.py`                                        |
| v2 REST endpoints     | Auth, CA cert, status, Homie schema (in `auth.py`)                              |
| MQTT transport        | Connection, Homie parser, client (in `mqtt/`)                                   |
| Data models           | `SpanPanelSnapshot`, `SpanCircuitSnapshot`, `SpanBatterySnapshot`               |
| Protocol interfaces   | `SpanPanelClientProtocol`, `CircuitControlProtocol`, `StreamingCapableProtocol` |
| Factory               | `create_span_client(host, passphrase?, mqtt_config?, serial_number?)`           |
| Energy value negation | Homie's inverted naming → snapshot fields (transport concern)                   |
| Simulation engine     | `DynamicSimulationEngine`, YAML configs                                         |
| MQTT reconnect logic  | Async reconnect loop with exponential backoff (managed by `AsyncMqttBridge`)    |

### span (HA integration) — Updated for v2.0.0

Everything that touches HA internals — entity registry, statistics DB, config entries, device registry, persistent notifications, translations.

| Concern                      | Notes                                                                                     |
| ---------------------------- | ----------------------------------------------------------------------------------------- |
| Entity ID naming patterns    | `EntityNamingPattern`, friendly vs circuit numbers                                        |
| Entity ID migration          | `EntityIdMigrationManager`, unique ID remapping (v1→v2 UUID)                              |
| Circuit UUID correlation     | **Integration-only** — builds `{v1_uuid: v2_uuid}` from entity registry + snapshot        |
| Circuit name syncing         | Detects name changes, triggers reload                                                     |
| Energy spike cleanup + undo  | Operates on HA `recorder` statistics DB                                                   |
| Main meter reset detection   | `async_track_state_change_event`, persistent notification                                 |
| Energy grace period          | `RestoreSensor` mixin, `SpanEnergyExtraStoredData`                                        |
| Net energy / solar sensors   | Computed HA sensor entities                                                               |
| Options flow                 | MQTT-specific options only (transport, reconnect). Remove REST retry options.             |
| Display precision            | `suggested_display_precision` on entities                                                 |
| Custom entity attributes     | tabs, voltage, amperage extra state attrs                                                 |
| Panel diagnostic sensors     | DSM state, grid state, relay state, run config                                            |
| Config entry migration       | v2 → v3 schema with MQTT credentials                                                      |
| Config export service        | `export_synthetic_config` (reads snapshot)                                                |
| Persistent notifications     | Firmware reset, priority errors                                                           |
| Coordinator perf logging     | MQTT connection health + snapshot freshness                                               |
| Translations                 | `strings.json`, `en.json`                                                                 |
| Simulation toggle + clone UI | Retained — `DynamicSimulationEngine` produces snapshots directly, no transport dependency |
| SSL/TLS toggle               | MQTTS (port 8883) vs WS (non-TLS). No REST mode.                                          |

### Split concerns — Updated for v2.0.0

| Concern             | API Layer                           | Integration Layer                                                                                                                    |
| ------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Simulation          | Engine, data generation             | Toggle, clone UI, start time, offline minutes                                                                                        |
| Resilience          | Async reconnect loop, backoff       | Connection state → entity availability                                                                                               |
| Circuit correlation | **None** (removed in v2.0.0)        | Full responsibility: entity registry has v1 UUIDs, snapshot has v2 UUIDs, integration builds correlation map and migrates unique IDs |
| Availability        | Connection state, `$state` tracking | Entity `available` property, grace period                                                                                            |

---

## Part 3 — Implementation Plan

### Phase 0: v2 REST Auth in span-panel-api — COMPLETE

**Goal:** Implement v2 REST auth, certificate provisioning, and API version detection in span-panel-api.

**Status:** Complete. Shipped in span-panel-api 2.0.0. Auth functions live in `auth.py` (moved from `rest/auth_v2.py` during REST removal).

**Deliverables (span-panel-api):**

1. **API version detection module** (`detection.py`):
   - `detect_api_version(host) → "v1" | "v2"` via `GET /api/v2/status`.
   - Returns `serialNumber` and `firmwareVersion` as side-product.
   - Unauthenticated — works before any auth credentials exist.
2. **v2 auth endpoint** (`POST /api/v2/auth/register`):
   - Request body: `{"name": "<client-name>", "hopPassphrase": "..."}`. The `name` field identifies the client (e.g., `"span-panel-ha"`); `hopPassphrase` may be
     omitted during 15-minute door bypass window.
   - Returns: `accessToken`, `tokenType`, `iatMs`, `ebusBrokerUsername`, `ebusBrokerPassword`, `ebusBrokerHost`, `ebusBrokerMqttsPort`, `ebusBrokerWsPort`,
     `ebusBrokerWssPort`, `hostname`, `serialNumber`, `hopPassphrase`.
   - **Important:** `hopPassphrase` and `ebusBrokerPassword` are currently identical in beta but **will diverge** in the future. Always use `ebusBrokerPassword`
     for MQTT auth, `hopPassphrase` for REST auth.
   - HTTP 401 = wrong passphrase / door bypass not active.
   - **Token lifetime:** Not documented. The span-auth script provides a `refresh` command that re-calls `/auth/register`, suggesting tokens can expire.
     Implementation should handle 401 on REST calls by re-authenticating.
3. **Passphrase regeneration** (`PUT /api/v2/auth/passphrase`):
   - Authenticated (Bearer `accessToken`). Invalidates existing password and generates new ones. Response includes `ebusBrokerPassword`, `hopPassphrase`, plus
     `accessToken`, `serialNumber`, `hostname`, `iatMs`. All existing MQTT connections using the old password will need to reconnect.
4. **CA certificate download** (`GET /api/v2/certificate/ca`):
   - Unauthenticated. Returns PEM-encoded self-signed CA certificate.
   - Used for TLS verification on both MQTTS and HTTPS connections.
   - **Not persisted** — fetched fresh on each connect/reconnect. The SPAN Panel server-certificate regenerates whenever network interfaces get new IP
     addresses, and the CA certificate itself can change (firmware update, factory reset). Storing a stale copy in config entry would cause silent TLS failures.
   - Cached in memory for the session duration only.
5. **Homie schema endpoint** (`GET /api/v2/homie/schema`):
   - Unauthenticated. Returns property schema organized by node type.
   - Includes `firmwareVersion`, `typesSchemaHash` for cache invalidation.
6. **v1 deprecation header detection:**
   - Parse `Deprecation`, `Sunset`, and `Link` headers from v1 responses to flag panels eligible for migration.
7. **`create_span_client()` factory** — MQTT-only. See factory signature in Library Status section. No `api_version` parameter.

**Tests:** Unit tests for v2 status probe, auth flow, cert download, schema endpoint. 278 tests passing, 91% coverage.

### Phase 1: MQTT/Homie Transport in span-panel-api — COMPLETE

**Goal:** Production-quality MQTT client implementing existing protocol interfaces.

**Status:** Complete. Shipped in span-panel-api 2.0.0. REST transport fully removed — `SpanMqttClient` is the only transport. `paho-mqtt` is a required
dependency (no longer optional).

**Final module structure:**

```text
src/span_panel_api/
└── mqtt/
    ├── __init__.py           # Exports SpanMqttClient, AsyncMQTTClient
    ├── async_client.py       # AsyncMQTTClient + NullLock (HA core pattern)
    ├── client.py             # SpanMqttClient — main transport
    ├── connection.py         # AsyncMqttBridge — event-loop-driven paho
    │                         #   wrapper (add_reader/add_writer, no threads)
    ├── homie.py              # HomieDeviceConsumer — parse $description,
    │                         #   track property values, build snapshot
    ├── const.py              # MQTT constants, topic patterns
    └── models.py             # MQTT-specific config dataclass
```

**Deliverables:**

1. **`AsyncMqttBridge`** (`connection.py`) + **`AsyncMQTTClient`** (`async_client.py`)
   - Wraps paho-mqtt with configurable transport:
     - TLS enabled (default): MQTTS on `ebusBrokerMqttsPort` (8883). CA cert fetched fresh via `GET /api/v2/certificate/ca` on each connect/reconnect (not
       stored — see Phase 0 notes).
     - TLS disabled: plain WebSocket on `ebusBrokerWsPort`.
     - WSS also available via `ebusBrokerWssPort` (TLS over WebSocket).
   - Follows HA core's async MQTT pattern: `AsyncMQTTClient` subclass
     with `NullLock` replacing paho's 7 internal threading locks, asyncio
     `add_reader`/`add_writer` on paho's socket, `loop_read()`/`loop_write()`/
     `loop_misc()` driven from the event loop. Zero background threads.
   - Async reconnect loop with exponential backoff (managed by the bridge,
     not paho's built-in threaded reconnection).
   - Last Will and Testament for `$state = lost`.
   - Connection state tracking with callbacks.

2. **`HomieDeviceConsumer`** (`homie.py`)
   - Subscribes to single device: `ebus/5/{serial}/#`.
   - Parses `$description` JSON into typed schema.
   - **`$state` lifecycle management (mandatory):** Tracks and responds to `$state` transitions. `$description` is only valid when `$state == ready`. Full state
     set: `init`, `ready`, `disconnected`, `sleeping`, `lost`, `alert`.
   - Stores property values in dict (single-thread, event-loop only).
   - Property change callbacks: `(node_id, property_id, value, old_value)`.
   - Builds `SpanPanelSnapshot` from current state.
   - **Circuit node identification:** Circuit node IDs are **opaque UUIDs** (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`), not position-based strings. Circuits
     are discovered by matching `type == "energy.ebus.device.circuit"` in the `$description`. The SPAN API explicitly warns that node IDs are opaque and **may
     change over time** — use `type` for identification.
   - Maps Homie node/property IDs to `SpanCircuitSnapshot` fields:

     Per-circuit properties (keyed by UUID node ID):
     - `{uuid}/active-power` → `power_w`. Sign: negative = consumption, positive = generation. **Firmware bug:** schema declares kW but values are actually
       watts.
     - `{uuid}/exported-energy` → `energy_consumed_wh` (panel exports TO circuit = consumption; the large accumulator)
     - `{uuid}/imported-energy` → `energy_produced_wh` (panel imports FROM circuit = generation/backfeed; near-zero for loads)
     - `{uuid}/relay` → `relay_state` / `is_on` (enum: UNKNOWN, OPEN, CLOSED; settable)
     - `{uuid}/shed-priority` → `priority` (enum; settable)
     - `{uuid}/name` → `name` (user-assigned circuit label)
     - `{uuid}/space` → `tabs` (breaker position number)
     - `{uuid}/dipole` → `is_240v` (boolean, 240V double-pole)
     - `{uuid}/current` → `current_a`
     - `{uuid}/breaker-rating` → `breaker_rating_a`

     Non-circuit nodes (well-known string IDs):
     - `core/*` → panel-level fields (door, relay, voltages, connectivity, firmware version, dominant-power-source)
     - `lugs.upstream/*` → main meter fields. Note: `imported-energy` = grid consumption (energy panel receives), `exported-energy` = grid export (solar/battery
       backfeed)
     - `lugs.downstream/*` → feedthrough fields
     - `power-flows/*` → power flow fields
     - `bess/*` → battery fields (soc, soe, grid-state, etc.)
     - `pv/*` → solar fields (nameplate-capacity, vendor, feed)
     - `evse/*` → SPAN Drive fields (status, lock-state, current)
     - `pcs/*` → power control system fields

3. **`SpanMqttClient`** (`client.py`)
   - Implements `SpanPanelClientProtocol`:
     - `connect()` → MQTT connect + wait for `$description` + `ready`.
     - `close()` → MQTT disconnect.
     - `ping()` → check MQTT `is_connected()`.
     - `get_snapshot()` → return current `SpanPanelSnapshot` from `HomieDeviceConsumer` (no network call needed — already in memory).
   - Implements `StreamingCapableProtocol`:
     - `register_callback()` → property-change notification.
     - `start_streaming()` → begin MQTT subscriptions.
     - `stop_streaming()` → unsubscribe.
   - Implements `CircuitControlProtocol`:
     - `set_circuit_relay()` → publish to `ebus/5/{serial}/{circuit-uuid}/relay/set`. Values: `"OPEN"` or `"CLOSED"`.
     - `set_circuit_priority()` → publish to `ebus/5/{serial}/{circuit-uuid}/shed-priority/set`. Values: enum from `$description` `format` field.
   - Capability flags: `EBUS_MQTT | PUSH_STREAMING | CIRCUIT_CONTROL` (plus `BATTERY_SOE` when BESS node is present).

4. **Factory** — `create_span_client()` is MQTT-only (no `api_version` parameter). Accepts either `mqtt_config` (pre-built credentials) or `passphrase` (calls
   `register_v2()` internally). See factory signature in Library Status section above.

**Dependencies:** `paho-mqtt>=2.0.0` is now a required dependency (was optional in v1.x). `attrs` and `python-dateutil` removed.

**Tests:** 278 tests passing, 91% coverage, mypy strict clean.

### Phase 2: Config Flow Update (span) — COMPLETE

**Goal:** Replace v1 bearer-token config flow with v2 passphrase-based auth and MQTT setup. No dual-mode — v2 firmware required.

**Status:** Complete. 288 tests passing. The integration now detects v2 panels, collects passphrase credentials, and stores MQTT config in entry data. However,
the runtime still uses v1 REST polling via `SpanPanelClient` → `SpanPanelApi` → `SpanPanel`. Phases 3–5 replace the entire runtime data path.

**Key change from original plan:** Since span-panel-api 2.0.0 is MQTT-only, there is no v1 fallback. Users must have v2 firmware before upgrading the
integration. Existing v1 installations continue running the old integration version until firmware is updated.

**Deliverables:**

1. **API version detection in config flow:**
   - **Zeroconf path**: If discovered via `_ebus._tcp` or `_secure-mqtt._tcp` → v2 confirmed. If discovered via `_span._tcp` → probe `GET /api/v2/status` via
     `detect_api_version()` to confirm v2 firmware.
   - **Manual host entry path**: Call `detect_api_version(host)`. If `result.api_version == "v2"`, proceed with v2 auth. If v1-only, show error: "v2 firmware
     required."
   - **No v1 fallback** — the integration requires v2 firmware.
   - Store detected firmware version and serial in config entry data.

2. **v2 auth flow:**
   - Passphrase step: input `hopPassphrase`, call `register_v2()` from span-panel-api, receive `V2AuthResponse` with `accessToken` + all `ebusBroker*` MQTT
     credentials.
   - **User guidance text** for passphrase entry: the config flow must include clear instructions directing the user to find their passphrase in the SPAN Home
     app: *"Open the SPAN Home app → Settings → All Settings → On-Premise Settings → Passphrase"*
   - Door bypass step: prompt user to press door button 3 times within 15 minutes, then call v2 auth (passphrase check bypassed during the 15-minute window).
     Pass `passphrase=None` to `register_v2()`.
   - Download CA certificate via `download_ca_cert()` — verified during setup but not stored in config entry. Fetched fresh on each MQTT connect/reconnect by
     `AsyncMqttBridge`.

3. **Config entry data (v2-only):**
   - `serial_number`: panel serial (from auth response).
   - `firmware_version`: panel firmware (from detection).
   - `mqtt_broker_host`: broker hostname (from `V2AuthResponse.ebus_broker_host`).
   - `mqtt_broker_mqtts_port`: MQTTS port (from `V2AuthResponse.ebus_broker_mqtts_port`).
   - `mqtt_broker_ws_port`: WebSocket port (from `V2AuthResponse.ebus_broker_ws_port`).
   - `mqtt_broker_wss_port`: WSS port (from `V2AuthResponse.ebus_broker_wss_port`).
   - `mqtt_username`: eBus broker username (from `V2AuthResponse.ebus_broker_username`).
   - `mqtt_password`: eBus broker password (from `V2AuthResponse.ebus_broker_password`).
   - `hop_passphrase`: stored for re-auth (from `V2AuthResponse.hop_passphrase`).
   - ~~`mqtt_ca_cert`~~: **Not stored.** CA cert fetched fresh on each MQTT connect via `download_ca_cert()`. The SPAN Panel regenerates server certs on IP
     changes and the CA cert can rotate on firmware updates. Storing would risk stale cert → silent TLS failures.
   - **Important:** `hop_passphrase` and `mqtt_password` are currently identical in beta but **will diverge** after `regenerate_passphrase()` is called. Always
     use `mqtt_password` for MQTT auth, `hop_passphrase` for REST re-auth.

4. **Config entry version migration** (v2 → v3):
   - Existing v2 config entries (v1 REST installations) cannot be auto-migrated — they lack MQTT credentials.
   - Migration sets a flag triggering re-auth flow on next load.
   - User completes passphrase entry → config entry populated with MQTT credentials.

5. **Zeroconf update:**
   - Add `_ebus._tcp.local.` and `_secure-mqtt._tcp.local.` service types to `manifest.json` alongside existing `_span._tcp.local.`.
   - Handle mDNS TXT record metadata from `_device-info._tcp` (serial, firmware, model) and `_ebus._tcp` (homie_domain, homie_version, mqtt_broker).

6. **Re-authentication flow:**
   - When config entry lacks MQTT credentials (v1 → v2 upgrade), trigger a repair/re-auth flow.
   - On successful v2 auth, update config entry with MQTT credentials.
   - Integration reloads and starts using MQTT transport.

7. **Dependency update:**
   - `manifest.json`: require `span-panel-api>=2.0.0`
   - Remove all imports of: `SpanPanelClient`, `SpanRestClient`, `set_async_delay_func`, `CircuitCorrelationProtocol`, `CorrelationUnavailableError`,
     `SpanPanelRetriableError`

8. **Simulation mode retained:**
   - `DynamicSimulationEngine` in span-panel-api produces `SpanPanelSnapshot` directly — no REST or MQTT dependency.
   - Config flow simulation toggle, clone-panel step, start time, and offline minutes remain in the options flow.
   - The simulator path bypasses MQTT entirely: engine generates snapshots on the coordinator interval, fed via `async_set_updated_data()` identically to the
     MQTT path.
   - No changes needed to simulation config entry data fields.

### Phases 3–5: Coordinator Rewrite, Entity Migration & Cleanup

**Goal:** Replace the entire runtime data path with span-panel-api 2.0.0's MQTT-based `SpanMqttClient` and `SpanPanelSnapshot`. The coordinator switches from
polling to push. All v1 data models are removed. Solar virtual sensors are removed (PV is a standard circuit in v2).

#### Architecture Change

```text
BEFORE (v1 REST):
  SpanPanelClient → SpanPanelApi → SpanPanel → Coordinator(poll 15s) → Entities

AFTER (v2 MQTT):
  SpanMqttClient → streaming callback → SpanPanelSnapshot → Coordinator(push) → Entities

AFTER (simulation):
  DynamicSimulationEngine → timer poll → SpanPanelSnapshot → Coordinator(poll) → Entities
```

The coordinator data type changes from `SpanPanel` to `SpanPanelSnapshot`. Entities read snapshot fields directly.

#### Coordinator Rationale: Pure Push vs. Polling

HA supports two entity update models:

1. **Polling:** `DataUpdateCoordinator` calls the library on a timer, gets data, pushes to entities. This is what the v1 REST integration does.
2. **Pure push:** Each entity subscribes to MQTT callbacks, calls `async_write_ha_state()` on every update. This is what dcj's span-hass does.

Pure push has a **CPU cost problem** on SPAN panels. This was
demonstrated empirically during the abandoned gRPC event-driven
prototype (Griswoldlabs, cecilkootz, et al): a Gen3 panel streaming
per-property updates via gRPC caused unsustainable CPU load on
Raspberry Pi hardware, forcing the effort to be abandoned. The root
cause was not gRPC itself but the per-event HA state write overhead.

A 32-circuit panel publishes `active-power`, `exported-energy`,
`imported-energy`, `relay`, `shed-priority`, and more per circuit —
160+ properties. Power values update continuously. Each
`async_write_ha_state()` call triggers:

- State machine write
- Event bus fire (`state_changed` event)
- All state change listeners notified (automations, templates, logbook)
- Recorder queues a DB write (batched at ~1s, but still per-entity)

On Raspberry Pi hardware common among HA users, hundreds of state
writes per second is significant. **HA does not rate-limit or coalesce
these.** The gRPC prototype proved this — and MQTT/Homie has the same
property-level push granularity. Without coalescing, MQTT would
reproduce the same CPU problem.

**Solution: Hybrid Coordinator.** The key insight from the gRPC failure
is that the problem is not the push transport — it's firing HA state
writes on every individual property change. The fix is to **decouple
MQTT message receipt from HA state writes** using the snapshot as a
coalescing boundary.

Use `DataUpdateCoordinator` with `update_interval=timedelta(seconds=60)`
(fallback poll). MQTT pushes trigger snapshot builds on a controlled
cadence rather than per-property.

```text
MQTT broker
  → paho socket (event loop: add_reader → loop_read)
    → AsyncMqttBridge._on_message()                ← runs on event loop thread
      → HomieDeviceConsumer._handle_property()      ← cheap dict write, no HA
        → property_values[node_id][prop_id] = value

(on controlled interval or debounced after burst)

HomieDeviceConsumer.build_snapshot()               ← builds frozen dataclass
  → coordinator.async_set_updated_data(snapshot)   ← single coordinator push
    → each entity reads its field from snapshot
      → async_write_ha_state() ONLY if value changed
```

The snapshot is the **natural coalescing boundary** — many MQTT property updates collapse into one snapshot, one coordinator push, and selective entity state
writes.

#### Critical Constraint: Unique ID Stability

Sensor definition `key` values flow into unique_id construction via `helpers.py` suffix mappings:

- `build_circuit_unique_id(serial, circuit_id, key)` → uses `CIRCUIT_SUFFIX_MAPPING[key]`
- `build_panel_unique_id(serial, key)` → uses `PANEL_ENTITY_SUFFIX_MAPPING[key]`

**All `key` values MUST remain unchanged.** Only `value_fn` lambdas change.

#### Files to Delete

| File                            | Reason                                                                       |
| ------------------------------- | ---------------------------------------------------------------------------- |
| `span_panel.py`                 | v1 data container — replaced by `SpanPanelSnapshot`                          |
| `span_panel_api.py`             | v1 REST wrapper — replaced by direct library client                          |
| `span_panel_data.py`            | v1 panel data model — replaced by snapshot fields                            |
| `span_panel_circuit.py`         | v1 circuit model — replaced by `SpanCircuitSnapshot`                         |
| `span_panel_hardware_status.py` | v1 status model — replaced by snapshot fields                                |
| `span_panel_storage_battery.py` | v1 battery model — replaced by `SpanBatterySnapshot`                         |
| `v2_provisioning.py`            | Temporary httpx module — library provides `detect_api_version`/`register_v2` |
| `sensors/solar.py`              | Virtual solar sensors — PV is a standard circuit in v2                       |

#### Step-by-Step Implementation

##### Step 1: `manifest.json` — Bump library requirement

```json
"requirements": ["span-panel-api>=2.0.0"]
```

##### Step 2: `const.py` — Remove v1 constants, update enums

**Remove:**

- `URL_STATUS`, `URL_SPACES`, `URL_CIRCUITS`, `URL_PANEL`, `URL_REGISTER`, `URL_STORAGE_BATTERY`
- `STORAGE_BATTERY_PERCENTAGE`, `CIRCUITS_NAME`, `CIRCUITS_RELAY`, `CIRCUITS_POWER`, `CIRCUITS_ENERGY_PRODUCED`, `CIRCUITS_ENERGY_CONSUMED`,
  `CIRCUITS_BREAKER_POSITIONS`, `CIRCUITS_PRIORITY`, `CIRCUITS_IS_USER_CONTROLLABLE`, `CIRCUITS_IS_SHEDDABLE`, `CIRCUITS_IS_NEVER_BACKUP`
- `SPAN_CIRCUITS`, `SPAN_SOE`, `SPAN_SYSTEM`, `PANEL_POWER`
- `STATUS_SOFTWARE_VER`, `DSM_GRID_STATE` (the const), `DSM_STATE` (the const), `CURRENT_RUN_CONFIG` (the const), `MAIN_RELAY_STATE` (the const)
- `PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE`
- `API_TIMEOUT`, `CONFIG_TIMEOUT`
- `CONF_API_RETRIES`, `CONF_API_RETRY_TIMEOUT`, `CONF_API_RETRY_BACKOFF_MULTIPLIER`, `DEFAULT_API_RETRIES`, `DEFAULT_API_RETRY_TIMEOUT`,
  `DEFAULT_API_RETRY_BACKOFF_MULTIPLIER`, `CONFIG_API_RETRIES`, `CONFIG_API_RETRY_TIMEOUT`, `CONFIG_API_RETRY_BACKOFF_MULTIPLIER`

**Keep:** `SYSTEM_DOOR_STATE_*`, `SYSTEM_*_LINK`, `PANEL_STATUS` (used in binary_sensor), `DSM_GRID_UP/DOWN`, `DSM_ON_GRID/OFF_GRID`,
`PANEL_ON_GRID/OFF_GRID/BACKUP`, `CircuitRelayState`, `EntityNamingPattern`, all naming/migration/solar net energy/simulation constants.

**Update `CircuitPriority`:**

```python
class CircuitPriority(enum.Enum):
    NEVER = "Never"
    SOC_THRESHOLD = "SOC Threshold"
    OFF_GRID = "Off-Grid"
    UNKNOWN = "Unknown"
```

##### Step 3: `util.py` — Snapshot-based device info

Replace `panel_to_device_info(panel: SpanPanel, device_name)` with:

```python
def snapshot_to_device_info(
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
    is_simulator: bool = False,
    host: str | None = None,
) -> DeviceInfo:
```

- Use `snapshot.serial_number`, `snapshot.firmware_version`
- For simulator: use `slugify(device_name)` as identifier (existing logic)
- Model: `"SPAN Panel"` (v2 snapshot has no model field)
- `configuration_url`: use `host` parameter (snapshot doesn't store host)

##### Step 4: `coordinator.py` — Hybrid push/poll coordinator

**Type change:** `DataUpdateCoordinator[SpanPanel]` → `DataUpdateCoordinator[SpanPanelSnapshot]`

**Constructor:**

```python
def __init__(
    self,
    hass: HomeAssistant,
    client: SpanMqttClient | DynamicSimulationEngine,
    config_entry: ConfigEntry,
    is_streaming: bool = False,
):
```

- Store `self._client` (typed union — no adapter)
- For MQTT (`is_streaming=True`): `update_interval=timedelta(seconds=60)` (fallback poll)
- For simulation: `update_interval` from options (default 15s)

**`_async_update_data() -> SpanPanelSnapshot`:**

- **Simulation offline check first:** If `self._simulation_offline_minutes > 0` and within the offline window, raise
  `SpanPanelConnectionError("Panel is offline in simulation mode")` — this triggers the grace period path in energy sensor base classes. When the window
  expires, resume normal `get_snapshot()`. This re-implements the logic from `SpanPanelApi._is_panel_offline()`.
- Call `self._client.get_snapshot()` — works for both MQTT (cached, instant) and simulation
- Error handling: catch `SpanPanelConnectionError`, `SpanPanelTimeoutError`, `SpanPanelAPIError`
- Set `self._panel_offline` flag on error, return stale data for grace period

**Simulation offline mode support:**

- `self._simulation_offline_minutes: int` (from options)
- `self._offline_start_time: datetime | None` (set when offline minutes first configured)
- `set_simulation_offline_mode(minutes: int)` — called from options update listener
- `_is_simulation_offline() -> bool` — checks elapsed time against configured window

**`async_setup_streaming()`:**

- Guard: only if `isinstance(self._client, SpanMqttClient)`
- Call `self._unregister_streaming = self._client.register_snapshot_callback(self._on_snapshot_push)`
- Call `await self._client.start_streaming()`

**`_on_snapshot_push(snapshot: SpanPanelSnapshot)`:**

- `self._panel_offline = False`
- `self.async_set_updated_data(snapshot)`

**`async_shutdown()`:**

- If streaming: `stop_streaming()`, call unregister
- If MQTT client: `await self._client.close()`

**Expose `client` property** for switch/select relay/priority control.

**Keep:** `_migration_manager`, `_handle_pending_legacy_migration()`, `_handle_pending_naming_migration()`, reload request logic. These operate on entity
registry, not data models — update `SpanPanel` references to `SpanPanelSnapshot`.

##### Step 5: `sensor_definitions.py` — Update value_fn targets

**Type changes only** — all `key` values stay identical:

- `SpanPanelCircuitsRequiredKeysMixin.value_fn`: `Callable[[SpanPanelCircuit], float]` → `Callable[[SpanCircuitSnapshot], float]`
- `SpanPanelDataRequiredKeysMixin.value_fn`: `Callable[[SpanPanelData], float | str]` → `Callable[[SpanPanelSnapshot], float | str]`
- `SpanPanelStatusRequiredKeysMixin.value_fn`: `Callable[[SpanPanelHardwareStatus], str]` → `Callable[[SpanPanelSnapshot], str]`
- `SpanPanelBatteryRequiredKeysMixin.value_fn`: `Callable[[SpanPanelStorageBattery], int]` → `Callable[[SpanBatterySnapshot], float | None]`

**Panel data sensors** (key unchanged, value_fn updated):

- `"dsm_state"`: `lambda s: s.dsm_state`
- `"dsm_grid_state"`: `lambda s: s.dsm_grid_state`
- `"current_run_config"`: `lambda s: s.current_run_config`
- `"main_relay_state"`: `lambda s: s.main_relay_state`

**Status sensors:**

- `"software_version"`: `lambda s: s.firmware_version`

**Panel power sensors:**

- `"instantGridPowerW"`: `lambda s: s.instant_grid_power_w`
- `"feedthroughPowerW"`: `lambda s: s.feedthrough_power_w`

**Panel energy sensors:**

- `"mainMeterEnergyProducedWh"`: `lambda s: s.main_meter_energy_produced_wh`
- `"mainMeterEnergyConsumedWh"`: `lambda s: s.main_meter_energy_consumed_wh`
- `"feedthroughEnergyProducedWh"`: `lambda s: s.feedthrough_energy_produced_wh`
- `"feedthroughEnergyConsumedWh"`: `lambda s: s.feedthrough_energy_consumed_wh`
- Net energy: arithmetic on snapshot fields

**Circuit sensors:**

- `"circuit_power"`: `lambda c: c.instant_power_w`
- `"circuit_energy_produced"`: `lambda c: c.produced_energy_wh`
- `"circuit_energy_consumed"`: `lambda c: c.consumed_energy_wh`
- `"circuit_energy_net"`: `lambda c: (c.consumed_energy_wh or 0) - (c.produced_energy_wh or 0)`

**Unmapped sensors** (key = v1 camelCase for unique_id stability):

- `CIRCUITS_POWER` key stays `"instantPowerW"`: `lambda c: c.instant_power_w`
- `CIRCUITS_ENERGY_PRODUCED` key stays `"producedEnergyWh"`: `lambda c: c.produced_energy_wh`
- `CIRCUITS_ENERGY_CONSUMED` key stays `"consumedEnergyWh"`: `lambda c: c.consumed_energy_wh`

The const `CIRCUITS_POWER` etc. are being deleted in Step 2. Replace the const references with inline string literals to preserve the key values.

**Battery sensor:**

- `"storage_battery_percentage"`: `lambda b: b.soe_percentage`

**Delete:** `SpanSolarSensorEntityDescription`, `SOLAR_SENSORS`

##### Step 6: `sensors/base.py` — Snapshot-based entity base

- Change `get_data_source(self, span_panel: SpanPanel) -> D` to `get_data_source(self, snapshot: SpanPanelSnapshot) -> D`
- Constructor: `span_panel: SpanPanel` → remove parameter (get serial from coordinator)
- `_handle_coordinator_update()`: `data_source = self.get_data_source(self.coordinator.data)` — already works since `coordinator.data` is now
  `SpanPanelSnapshot`
- Replace `from .span_panel import SpanPanel` with `from span_panel_api import SpanPanelSnapshot, SpanCircuitSnapshot, SpanBatterySnapshot`
- Name sync: `self.coordinator.data.circuits[circuit_id].name` — works with `SpanPanelSnapshot.circuits`
- Device info: `snapshot_to_device_info(self.coordinator.data, ...)` instead of `panel_to_device_info(span_panel, ...)`

##### Step 7: `sensors/panel.py` — Panel sensor data sources

- Panel data sensors: `get_data_source(snapshot) -> SpanPanelSnapshot` (return snapshot itself)
- Status sensors: `get_data_source(snapshot) -> SpanPanelSnapshot` (merge — all fields on snapshot)
- Battery sensor: `get_data_source(snapshot) -> SpanBatterySnapshot` (return `snapshot.battery`)
- Power/energy sensors: `get_data_source(snapshot) -> SpanPanelSnapshot`
- Remove all `SpanPanelData`, `SpanPanelHardwareStatus`, `SpanPanelStorageBattery` imports

##### Step 8: `sensors/circuit.py` — Circuit sensor data sources

- `get_data_source(snapshot) -> SpanCircuitSnapshot` (return `snapshot.circuits[self.circuit_id]`)
- Replace `SpanPanelCircuit` with `SpanCircuitSnapshot`

##### Step 9: `sensors/factory.py` — Sensor creation

- Change `span_panel: SpanPanel` to `snapshot: SpanPanelSnapshot`
- Use `snapshot.circuits` for circuit iteration
- Remove `create_solar_sensors()` entirely
- Battery: check `snapshot.battery.soe_percentage is not None`

##### Step 10: `switch.py` — Relay control via client

- Read circuit state from `self.coordinator.data.circuits[circuit_id].relay_state`
- Control: `await self.coordinator.client.set_circuit_relay(circuit_id, "CLOSED")` / `"OPEN"`
- Guard for simulation: `if not isinstance(self.coordinator.client, SpanMqttClient)` → log warning, skip
- Remove `.copy()` calls (snapshot is frozen)

##### Step 11: `select.py` — Priority control via client

- Read priority from `self.coordinator.data.circuits[circuit_id].priority`
- Control: `await self.coordinator.client.set_circuit_priority(circuit_id, priority_str)`
- Update `CircuitPriority` enum usage to v2 values (NEVER/SOC_THRESHOLD/OFF_GRID)
- Guard for simulation same as switch

##### Step 12: `binary_sensor.py` — Snapshot-based status

- Value functions take `SpanPanelSnapshot` directly (not `SpanPanelHardwareStatus`):
  - Door: `lambda s: s.door_state != "CLOSED"` (with UNKNOWN handling)
  - Ethernet: `lambda s: s.eth0_link`
  - WiFi: `lambda s: s.wlan_link`
  - Cellular: `lambda s: s.wwan_link`

##### Step 13: `helpers.py` — Remove SpanPanel references

- `_get_device_identifier_for_unique_ids()`: change `span_panel: SpanPanel` to `snapshot: SpanPanelSnapshot`; use `snapshot.serial_number`
- All `*_for_entry()` functions: `span_panel: SpanPanel` → `snapshot: SpanPanelSnapshot`
- `construct_entity_id()`: `span_panel: SpanPanel` → `snapshot: SpanPanelSnapshot`
- Keep suffix mapping tables unchanged (preserve unique_id patterns)
- Delete `is_solar_sensor_key()`, `extract_solar_info_from_sensor_key()`
- Update `construct_voltage_attribute(circuit: SpanPanelCircuit)` → `construct_voltage_attribute(circuit: SpanCircuitSnapshot)` — use `circuit.is_240v` instead
  of `len(circuit.tabs)`. Optionally accept snapshot for real `l1_voltage`/`l2_voltage`.
- Update `construct_tabs_attribute(circuit: SpanPanelCircuit)` → `construct_tabs_attribute(circuit: SpanCircuitSnapshot)` — field name identical, just type
  change.

##### Step 14: `__init__.py` — Setup/teardown rewrite

**Remove:**

- `SpanPanel`, `SpanPanelApi`, `Options` imports
- `set_async_delay_func` import
- `_test_connection()`, `_test_authenticated_connection()` helpers
- `span_panel.api.setup()` call

**`async_setup_entry()` for `api_version == "v2"`:**

1. Build `MqttClientConfig` from entry data
2. Create client: `SpanMqttClient(host, serial_number, broker_config)` or use `create_span_client()`
3. `await client.connect()`
4. Create `SpanPanelCoordinator(hass, client, entry, is_streaming=True)`
5. `await coordinator.async_config_entry_first_refresh()`
6. `await coordinator.async_setup_streaming()`

**`async_setup_entry()` for `api_version == "simulation"`:**

1. Create `DynamicSimulationEngine(serial_number=serial, config_path=path)`
2. `await engine.initialize_async()`
3. Create `SpanPanelCoordinator(hass, engine, entry, is_streaming=False)`
4. `await coordinator.async_config_entry_first_refresh()`

**`async_setup_entry()` for `api_version == "v1"`:**

- Raise `ConfigEntryNotReady("v2 firmware required...")`

**`async_unload_entry()`:**

- `await coordinator.async_shutdown()`

**`ensure_device_registered()`:**

- Use `snapshot_to_device_info(coordinator.data, device_name, is_simulator, host)`

##### Step 15: `config_flow.py` — Replace v2_provisioning imports

- Change `from .v2_provisioning import ...` to `from span_panel_api import detect_api_version, register_v2, V2AuthResponse`
- Also used: `V2AuthError` → `SpanPanelAuthError`, `V2ConnectionError` → `SpanPanelConnectionError`
- Need to verify if the library's `detect_api_version` and `register_v2` have the same signatures as `v2_provisioning.py`
- Remove `from .span_panel_hardware_status import SpanPanelHardwareStatus`

##### Step 16: `config_flow_utils/validation.py` — Replace v2_provisioning imports

- Same import changes as config_flow.py
- Remove `from span_panel_api import SpanPanelClient` (v1 client gone)
- `validate_host()` and `validate_auth_token()` — these used `SpanPanelClient` for v1 validation. Since we only support v2 now, rewrite to use
  `detect_api_version()` for host validation and `register_v2()` for auth validation.

##### Step 17: `config_flow_utils/options.py` — Remove REST retry options

- Remove any remaining `CONF_API_RETRIES`, `CONF_API_RETRY_TIMEOUT`, `CONF_API_RETRY_BACKOFF_MULTIPLIER` references
- Keep solar, battery, grace period, naming, simulation options

##### Step 18: Delete files (see Files to Delete table above)

All solar-specific code is removed in the same step as v1 file deletions:

- `sensors/solar.py` — delete entirely
- `sensor_definitions.py` — delete `SpanSolarSensorEntityDescription`, `SOLAR_SENSORS`
- `sensors/factory.py` — delete `create_solar_sensors()`
- `config_flow_utils/options.py` — delete `build_solar_options_schema()`, solar validation calls, `INVERTER_*` imports
- `config_flow_utils/validation.py` — delete `validate_solar_tab_selection()`, `get_filtered_tab_options()`, `validate_solar_configuration()`,
  `get_available_unmapped_tabs()`
- `config_flow.py` — delete solar options schema, `INVERTER_*` imports
- `helpers.py` — delete `is_solar_sensor_key()`, `extract_solar_info_from_sensor_key()`
- `entity_summary.py` — remove solar entity count logic
- `simulation_utils.py` — remove `INVERTER_LEG1`/`INVERTER_LEG2` references
- `strings.json` + all `translations/*.json` — remove `enable_solar_circuit` strings
- `__init__.py` — remove solar options from update_listener

PV sensors are now created by the standard circuit sensor factory — no special solar code path.

##### Step 19: Update `conftest.py` — Test mock overhaul

**Remove:** The entire v1 mock block (`span_panel_api_mock`, `sys.modules["span_panel_api"]` override)

**Replace with:** Proper v2 mocking:

- Keep real exception classes (or import from library)
- Mock `SpanMqttClient` (implements 3 protocols)
- Mock `create_span_client` to return mock client
- Use real `SpanPanelSnapshot` / `SpanCircuitSnapshot` instances from the library (frozen dataclasses — no mocking needed)
- Mock `DynamicSimulationEngine` for simulation tests
- Keep `phase_validation` import (real module, pure math)

##### Step 20: Update tests

- All tests referencing `SpanPanel`, `SpanPanelCircuit`, `SpanPanelData` → use snapshot types
- Test v2 entry setup: mock client, verify streaming setup
- Test simulation entry setup: mock engine, verify polling
- Test v1 entry rejection: verify `ConfigEntryNotReady`

##### Step 21: Services & remaining files

- `services/cleanup_energy_spikes.py`, `main_meter_monitoring.py`, `undo_stats_adjustments.py` — operate on HA statistics DB, minimal snapshot reference changes
- `entity_summary.py` — update `SpanPanel` references, remove solar entity count logic
- `entity_id_naming_patterns.py` — update `SpanPanelCircuit` → `SpanCircuitSnapshot` (lines 13, 79, 933, 981); `.tabs`, `.name` accessors are identical on both
  types. **Entity naming system (EntityNamingPattern, USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX, friendly names vs tab-based names) is fully preserved.**
- `migration_utils.py` — operate on entity registry, keep as-is
- `exceptions.py` — remove `SpanPanelReturnedEmptyData` if present

#### Circuit Attributes & Naming (Preserved)

**Tabs:** `SpanCircuitSnapshot.tabs: list[int]` is identical to v1 `SpanPanelCircuit.tabs`. Tab-based circuit naming (circuit_1, circuit_3_5 for 240V) works
unchanged.

**Entity naming scheme:** `EntityNamingPattern` (FRIENDLY_NAMES / CIRCUIT_NUMBERS / LEGACY_NAMES), `USE_DEVICE_PREFIX`, `USE_CIRCUIT_NUMBERS` — all config flow
options and the `EntityIdMigrationManager` are preserved. New installations continue to offer the choice between friendly names and tab-based names.

**Extra state attributes (tabs, voltage, amperage):** Currently exposed on circuit power sensors (`sensors/circuit.py:130-160`) and energy sensors
(`sensors/circuit.py:266-286`):

- `tabs` — from `circuit.tabs` → unchanged, reads `SpanCircuitSnapshot.tabs`
- `voltage` — v1 derives from tab count (1 tab = 120V, 2 tabs = 240V) via `helpers.py:construct_voltage_attribute()`. v2 improves: `SpanCircuitSnapshot.is_240v`
  gives a direct boolean, and `SpanPanelSnapshot.l1_voltage` / `l2_voltage` give real measured voltages. Update `construct_voltage_attribute()` to use `is_240v`
  first, with measured voltage as enhancement.
- `amperage` — v1 calculates as `power / voltage`. v2 provides `SpanCircuitSnapshot.current_a` (actual measured current). Update the attribute to prefer
  `current_a` when available, falling back to the calculation.

**New v2-only sensor opportunities** (future follow-up, not in this PR):

- Per-circuit: `breaker_rating_a`, `always_on`, `relay_requester`
- Panel-level: `l1_voltage`, `l2_voltage`, `main_breaker_rating_a`, `wifi_ssid`, `dominant_power_source`, `grid_state`, `grid_islandable`

These fields are on `SpanCircuitSnapshot` / `SpanPanelSnapshot` but no dedicated sensor definitions exist for them yet. Adding full sensors is a separate
feature PR after the v2 migration lands.

#### Solar Config Schema Migration (Step 14a — inside `async_migrate_entry`)

**When:** Config version bump to v4 (v3 → v4), applied during `async_migrate_entry`.

**Logic:**

```python
# --- v3 → v4: solar migration flag + remove solar options ---
if config_entry.version < 4:
    updated_options = dict(config_entry.options)
    updated_data = dict(config_entry.data)

    # Check if user had solar configured under v1 options layout
    solar_was_enabled = updated_options.pop("enable_solar_circuit", False)
    updated_options.pop("leg1", None)  # INVERTER_LEG1
    updated_options.pop("leg2", None)  # INVERTER_LEG2
    # Keep ENABLE_SOLAR_NET_ENERGY_SENSORS — still relevant for circuit net energy

    if solar_was_enabled:
        # PV circuit UUID is only known at runtime (from MQTT data),
        # so defer entity registry update to first coordinator refresh.
        updated_data["solar_migration_pending"] = True
        _LOGGER.info(
            "Solar was configured — setting solar_migration_pending flag "
            "for runtime entity registry migration"
        )

    # Remove v1 REST retry options (no longer applicable)
    for key in ("api_retries", "api_retry_timeout", "api_retry_backoff_multiplier"):
        updated_options.pop(key, None)

    hass.config_entries.async_update_entry(
        config_entry,
        data=updated_data,
        options=updated_options,
        version=4,
    )
```

**Files affected:**

- `__init__.py`: Add v3→v4 migration block, bump `CURRENT_CONFIG_VERSION = 4`
- `options.py`: Remove `INVERTER_ENABLE`, `INVERTER_LEG1`, `INVERTER_LEG2`, `INVERTER_MAXLEG` constants and all solar fields from `Options` class /
  `get_options()`

#### Solar Runtime Migration (Step 14b — coordinator first refresh)

**When:** First `_async_update_data()` after setup, if `solar_migration_pending` is set in config entry data.

**Where:** New method `_handle_solar_migration(snapshot)` called from coordinator after first successful refresh.

**Logic:**

1. Find PV circuit(s) in snapshot: `pv_circuits = [c for c in snapshot.circuits.values() if c.device_type == "pv"]`
2. **Single PV found:**
   - Walk entity registry for this config entry
   - Find entities with old solar unique IDs matching pattern `span_{serial}_solar_*`:
     - `span_{serial}_solar_current_power` → `span_{serial}_{pv_uuid}_power`
     - `span_{serial}_solar_produced_energy` → `span_{serial}_{pv_uuid}_produced_energy_wh`
     - `span_{serial}_solar_consumed_energy` → `span_{serial}_{pv_uuid}_consumed_energy_wh`
     - `span_{serial}_solar_net_energy` → `span_{serial}_{pv_uuid}_net_energy`
   - Update each unique_id in-place via `entity_registry.async_update_entity(entity_id, new_unique_id=...)`
   - Entity IDs remain unchanged → history and statistics preserved
   - Clear `solar_migration_pending` from config entry data
   - Schedule integration reload so platform re-registers with updated unique IDs
3. **No PV found:**
   - Panel has no solar — remove stale solar entities from entity registry (virtual sensors with no v2 equivalent)
   - Clear `solar_migration_pending`
4. **Multiple PV found:**
   - Log warning, skip automatic migration
   - Surface persistent notification guiding user to reconfigure solar manually
   - Leave `solar_migration_pending` set (can be retried)

**Retirement note** (add as code comment):

```python
# TODO(post-2.0.0): Remove solar_migration_pending handling once all users
# have been forced through the 2.0.x upgrade path. After that point, no
# config entries will have legacy solar options to migrate.
```

**Files affected:**

- `coordinator.py`: Add `_handle_solar_migration()` method, call after first successful refresh
- `helpers.py`: Keep `SOLAR_UNIQUE_ID_MAPPING` constant for the old→new unique ID patterns (used only during migration, delete post-2.0.0)

#### Library Prerequisite: PV Circuit Support (Phase 0)

**Problem:** `SpanCircuitSnapshot` has no `device_type` field. The homie parser (`homie.py:189-191`) only includes nodes with `TYPE_CIRCUIT` in the snapshot's
`circuits` dict. PV nodes (`TYPE_PV = "energy.ebus.device.pv"`) are defined in `mqtt/const.py:29` but never processed — silently dropped. The integration cannot
identify PV circuits for solar migration without this.

**Required library changes (span-panel-api 2.0.0):**

1. **`models.py`**: Add `device_type: str = "circuit"` field to `SpanCircuitSnapshot`
2. **`mqtt/homie.py`**: Expand circuit building to include PV (and EVSE) node types:
   - `_is_circuit_like_node(node_id)` → checks `TYPE_CIRCUIT`, `TYPE_PV`, `TYPE_EVSE`
   - `_build_circuit()` sets `device_type` from the node's type string (e.g., `"pv"`, `"evse"`, `"circuit"`)
   - PV/EVSE nodes have the same MQTT properties as circuits (power, energy, relay, name, space, etc.)
3. **`simulation/`**: DynamicSimulationEngine should also set `device_type` on simulated PV circuits

This enables the integration to find PV circuits via: `[c for c in snapshot.circuits.values() if c.device_type == "pv"]`

#### Library Signature Differences (v2_provisioning.py → library)

The config flow must be adapted when swapping imports. Key differences:

##### `detect_api_version(host)` → `DetectionResult`

- **Temporary**: `DetectionResult(api_version, serial_number, firmware_version)` — flat
- **Library**: `DetectionResult(api_version, status_info: V2StatusInfo | None)` — nested
- Config flow must change: `result.serial_number` → `result.status_info.serial_number`

##### `register_v2(host, name, passphrase)` → return type

- **Temporary**: `V2AuthResult(access_token, broker_host, broker_port, broker_username, broker_password, panel_serial)`
- **Library**:
  `V2AuthResponse(access_token, ebus_broker_host, ebus_broker_mqtts_port, ebus_broker_username, ebus_broker_password, serial_number, hop_passphrase, ...)`
- Config flow must change: `result.broker_host` → `result.ebus_broker_host`, `result.broker_port` → `result.ebus_broker_mqtts_port`, `result.panel_serial` →
  `result.serial_number`

##### Exception types

- **Temporary**: `V2AuthError`, `V2ConnectionError`
- **Library**: `SpanPanelAuthError`, `SpanPanelConnectionError`
- Config flow and validation.py must update exception catch clauses

##### URL scheme

- **Temporary**: `https://{host}/api/v2/...` (with `verify=False`)
- **Library**: `http://{host}/api/v2/...` (with `verify=False`)

#### Implementation Order

The order ensures the codebase compiles after each step:

**Phase 0 — Library prerequisite (span-panel-api repo):** 0. Add `device_type` field to `SpanCircuitSnapshot`, include PV/EVSE nodes in circuit building

**Phase 3 — Integration data path rewrite:**

1. Step 1: Bump library requirement to `>=2.0.0`
2. Step 2: Clean const.py (remove v1 constants, update enums)
3. Step 3: Rewrite util.py (snapshot-based device info)
4. Step 5: Rewrite sensor_definitions.py (types + value_fn)
5. Step 6: Rewrite sensors/base.py
6. Step 7: Rewrite sensors/panel.py
7. Step 8: Rewrite sensors/circuit.py
8. Step 9: Rewrite sensors/factory.py (delete create_solar_sensors)
9. Step 10: Rewrite switch.py
10. Step 11: Rewrite select.py
11. Step 12: Rewrite binary_sensor.py
12. Step 13: Update helpers.py (remove solar helper fns)
13. Step 4: Rewrite coordinator.py (hybrid push/poll + solar runtime migration)
14. Step 14: Rewrite **init**.py (setup/teardown + config v3→v4 solar migration)

**Phase 4 — Config flow & cleanup:** 15. Step 15-16: Update config_flow + validation (library imports) 16. Step 17: Clean options.py (remove solar + retry
options) 17. Step 18: Delete v1 files + solar code removal

**Phase 5 — Tests & remaining:** 18. Step 19-20: Update tests (mock overhaul, snapshot types) 19. Step 21: Clean remaining files (services, entity_summary,
translations)

Note: Steps 1-12 can be done as a batch since they're all type/import changes that compile together. The coordinator (13) and **init** (14) are the structural
changes that tie everything together. Solar migration spans steps 13, 14, and 18.

#### Resolved Decisions

**Simulation offline mode: PRESERVE.** This feature is critical for testing the energy grace period. Without it there's no way to verify that transient network
outages don't cause energy spikes (values going Unknown → 0 → restored accumulator = apparent spike on dashboards). The offline timer currently lives in
`SpanPanelApi._is_panel_offline()`. In the new coordinator, it moves to `_async_update_data()`: when `simulation_offline_minutes > 0`, the coordinator tracks a
start time and raises `SpanPanelConnectionError` during the configured window (triggering the grace period path in entity base classes), then resumes normal
`get_snapshot()` calls when the window expires.

**v1 config entries: BLOCK.** Entries with `api_version="v1"` raise `ConfigEntryNotReady` with a clear message to upgrade firmware. No dual-transport code.

**Solar migration: DEFERRED TO RUNTIME.** PV circuit UUID is only available from MQTT data, not config entry. Config migration sets a flag; coordinator
first-refresh does the entity registry rewrite. Retirement target: post-2.0.0.

#### Verification

1. `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -q` — all tests pass
2. No imports of deleted modules (`SpanPanel`, `SpanPanelApi`, `SpanPanelClient`, `SpanPanelData`, `SpanPanelCircuit`, `SpanPanelHardwareStatus`,
   `SpanPanelStorageBattery`, `v2_provisioning`)
3. `grep -r "from .span_panel" custom_components/` returns zero hits (except `span_panel_api` library imports)
4. `grep -r "SpanPanelClient" custom_components/` returns zero hits
5. Simulation mode: entry with `api_version="simulation"` loads and produces entities
6. MQTT mode: entry with `api_version="v2"` creates MQTT client and starts streaming
7. Config migration v3→v4: entry with solar options (`enable_solar_circuit=True, leg1=X, leg2=Y`) migrates to v4 with `solar_migration_pending=True` in data and
   solar options removed
8. Solar runtime migration: on first coordinator refresh with `solar_migration_pending`, PV circuit UUID is found, entity registry unique IDs are updated
   in-place, flag is cleared
9. No references to `INVERTER_ENABLE`, `INVERTER_LEG1`, `INVERTER_LEG2` outside of migration code
10. Library: `SpanCircuitSnapshot` includes `device_type` field; PV nodes appear in `snapshot.circuits`

#### Entity & Statistics Migration (preserved context)

**Live panel verification (2026-02-25):** Connected to panel `nj-2316-005k6` (firmware `spanos2/r202603/05`) via both REST v1 and MQTT v2 and compared all
circuit identifiers. Results:

- **v2 Homie circuit node IDs are identical dashless UUIDs** — same format, same strings as v1. No dashes, no transformation needed.
- 22 of 22 v1 circuits matched exactly in v2.
- 1 new circuit in v2 only: `b0b7ca7583294630816812812c9bb916` ("Commissioned PV System" at space 30).
- The SPAN API warning that node IDs are "opaque strings which may change over time" appears to be future-proofing, not current behavior.

**Conclusion: No UUID correlation logic is required for current firmware.** Entity unique IDs (`span_{serial}_{uuid}_{suffix}`) will match v2 data directly.

**Tab model difference (v1 REST vs v2 Homie):**

v2 Homie uses `space` (starting breaker position) + `dipole` (boolean) instead of v1's explicit `tabs` array. For dipole (240V) circuits, the second tab is
`space + 2` (next position on the **same bus bar side**), not `space + 1`. This was verified against all 6 dipole circuits on a live panel — the derived tabs
match v1 exactly.

| Circuit          | v1 `tabs` | v2 `space` | v2 `dipole` | Derived `[space, space+2]` | Match            |
| ---------------- | --------- | ---------- | ----------- | -------------------------- | ---------------- |
| Small Garage EV  | [11, 13]  | 11         | true        | [11, 13]                   | YES              |
| Air Conditioner  | [15, 17]  | 15         | true        | [15, 17]                   | YES              |
| Large Garage EV  | [18, 20]  | 18         | true        | [18, 20]                   | YES              |
| Microwave & Oven | [19, 21]  | 19         | true        | [19, 21]                   | YES              |
| Dryer            | [24, 26]  | 24         | true        | [24, 26]                   | YES              |
| Spa              | [29, 31]  | 29         | true        | [29, 31]                   | YES              |
| PV System (new)  | n/a       | 30         | true        | [30, 32]                   | Confirmed in app |

**Current unique ID formats (from codebase analysis):**

| Entity Type    | Unique ID Pattern                         | Example                                    |
| -------------- | ----------------------------------------- | ------------------------------------------ |
| Circuit sensor | `span_{serial.lower()}_{uuid}_{suffix}`   | `span_nj-2316-005k6_0dad2f16cd...e_power`  |
| Switch         | `span_{serial}_relay_{uuid}`              | `span_nj-2316-005K6_relay_0dad2f16cd...e`  |
| Select         | `span_{serial}_select_{uuid}`             | `span_nj-2316-005K6_select_0dad2f16cd...e` |
| Binary sensor  | `span_{serial}_{key}`                     | `span_nj-2316-005k6_doorState`             |
| Panel sensor   | `span_{serial.lower()}_{suffix}`          | `span_nj-2316-005k6_current_power`         |
| Solar sensor   | `span_{serial}_{suffix}`                  | `span_nj-2316-005k6_solar_current_power`   |
| Unmapped tab   | `span_{serial}_unmapped_tab_{N}_{suffix}` | `span_nj-2316-005k6_unmapped_tab_32_power` |

**Note:** There is an existing inconsistency — `build_circuit_unique_id()` calls `serial.lower()` but `build_switch_unique_id()` and `build_select_unique_id()`
do not. This should be normalized during the migration.

**Breaking property name changes (handled in library snapshot builder):**

| Breaking Change | v1 Pattern         | v2 Pattern                                             | Mitigation                   |
| --------------- | ------------------ | ------------------------------------------------------ | ---------------------------- |
| Property names  | `consumedEnergyWh` | `exported-energy` (inverted semantics)                 | Mapping table in code        |
| Property names  | `producedEnergyWh` | `imported-energy` (inverted semantics)                 | Mapping table in code        |
| Property names  | `instantPowerW`    | `active-power` (negated sign, watts despite kW schema) | Negation in snapshot builder |
| Property names  | `relayState`       | `relay` (enum: UNKNOWN,OPEN,CLOSED)                    | Mapping table                |
| Property names  | `priority`         | `shed-priority` (distinct from `pcs-priority`)         | Mapping table                |
| Domain          | `span_panel`       | Must stay `span_panel`                                 | No change needed             |

### Phase 6: New Capabilities from eBus (span)

**Goal:** Leverage new data available via MQTT that wasn't in REST v1.

| New Capability                | Homie Source              | Entity Type      |
| ----------------------------- | ------------------------- | ---------------- |
| Battery SOE, power, state     | `bess/*` node             | Sensors          |
| PCS power (grid-tie inverter) | `pcs/*` node              | Sensors          |
| EVSE status, power            | `evse/*` node             | Sensors + binary |
| Per-circuit voltage           | `circuit-NN/voltage`      | Sensor           |
| Per-circuit current           | `circuit-NN/current`      | Sensor           |
| Per-circuit power factor      | `circuit-NN/power-factor` | Sensor           |
| Per-circuit frequency         | `circuit-NN/frequency`    | Sensor           |
| Grid frequency                | `power-flows/frequency`   | Sensor           |
| Sub-panel topology            | Homie child devices       | Device registry  |

**MQTT-only feature notes:**

With REST removed from span-panel-api 2.0.0, no dual-mode handling is needed in the integration:

- **Options flow**: SSL/TLS toggle selects MQTTS (port 8883, TLS with CA cert) vs WS (non-TLS WebSocket via `ebusBrokerWsPort`). Default TLS enabled. Remove all
  REST retry options (retry count, timeout, backoff multiplier).
- **Simulation mode**: `DynamicSimulationEngine` produces `SpanPanelSnapshot` directly from YAML configs — no REST or MQTT dependency. Works standalone for
  development and testing.
- **Config export**: Reads from `SpanPanelSnapshot` — already transport-agnostic.

---

## Part 4 — Sequencing & Dependencies (Updated)

```text
Phase 0: v2 REST Auth (span-panel-api) ........... COMPLETE
Phase 1: MQTT Transport (span-panel-api) ......... COMPLETE
Phase 2: Config Flow (span) ...................... COMPLETE (288 tests)
  │
  │  span-panel-api 2.0.0 shipped (MQTT-only, REST removed)
  │  Config flow detects v2, collects passphrase, stores MQTT config
  │
  v
Phase 0b: Library PV support (span-panel-api)
  │  Add device_type to SpanCircuitSnapshot
  │  Include PV/EVSE nodes in circuit building
  │
  v
Phase 3: Data Path Rewrite (span) ← Steps 1-14
  │  Bump library, clean const, rewrite coordinator,
  │  update all entities to SpanPanelSnapshot,
  │  solar config migration (v3→v4),
  │  solar runtime migration (coordinator first refresh)
  │
  v
Phase 4: Config Flow Cleanup (span) ← Steps 15-18
  │  Replace v2_provisioning with library imports,
  │  clean options, delete v1 files + solar code
  │
  v
Phase 5: Tests & Remaining (span) ← Steps 19-21
  │  Mock overhaul, snapshot types in tests,
  │  services, entity_summary cleanup
  │
  v
Phase 6: New Capabilities (span)
     New v2-only sensors (future PR)
```

Steps 1-12 can be done as a batch (type/import changes that compile together). The coordinator (Step 4/13) and `__init__.py` (Step 14) are the structural
changes that tie everything together. Solar migration spans steps 13, 14, and 18.

---

## Part 5 — Risk Assessment (Updated)

| Risk                                                   | Impact   | Likelihood | Mitigation                                                                                      |
| ------------------------------------------------------ | -------- | ---------- | ----------------------------------------------------------------------------------------------- |
| SPAN changes Homie schema before GA                    | High     | Medium     | Schema-driven entity generation; `typesSchemaHash` in `V2HomieSchema` detects changes           |
| Energy statistics gap during migration                 | High     | Low        | Statistics transfer API; test UUID migration extensively before release                         |
| ~~paho-mqtt threaded model vs HA async patterns~~      | ~~High~~ | ~~Certain~~ | **Resolved.** `AsyncMqttBridge` uses HA core's `AsyncMQTTClient`/`NullLock`/`add_reader` pattern. Zero background threads. |
| ~~v1 API sunset accelerated~~                          | ~~High~~ | N/A        | **Mitigated** — library is already MQTT-only                                                    |
| MQTT broker unreachable (panel offline)                | Medium   | Medium     | Availability tracking; paho-mqtt auto-reconnect; graceful degradation                           |
| Circuit UUID correlation fails                         | Medium   | Low        | Verified identical on live panel; defensive fallback via dash-stripping + name+tabs correlation |
| Users upgrade integration before firmware              | Medium   | Medium     | `api_version="v1"` raises `ConfigEntryNotReady` with clear message                              |
| Solar migration fails (multiple PV circuits)           | Low      | Low        | Persistent notification guides manual reconfiguration; flag remains set for retry               |
| Library signature mismatch (v2_provisioning → library) | Medium   | Medium     | Documented field name mapping; test coverage for detection + registration flows                 |

---

## Part 6 — What to Extract from DCJ's Work

While we won't adopt `span-hass` directly, these elements inform our implementation:

| Element                        | Use                                                |
| ------------------------------ | -------------------------------------------------- |
| Homie node/property naming     | Validates our property mapping tables              |
| zeroconf service types         | `_ebus._tcp`, `_secure-mqtt._tcp`                  |
| Auth flow sequence             | v2 REST endpoint contract, credential fields       |
| `call_soon_threadsafe` pattern | Identified the anti-pattern; span-panel-api uses HA core's event-loop approach instead |
| `EntitySpec` concept           | Similar to our existing sensor factory pattern     |
| Sub-device grouping            | `_SUB_DEVICE_TYPES` informs device registry layout |
| `$description` parsing         | Validates schema-driven entity discovery           |
| Negation for energy values     | `exported-energy` = consumed (inverted)            |

**What we explicitly reject from DCJ:**

- ebus-sdk dependency (alpha quality, thread-unsafe)
- Fire-and-forget relay control (need confirmation/optimistic state)
- No energy statistics handling
- No migration support
- Domain name change (`span_ebus` → keeping `span_panel`)
