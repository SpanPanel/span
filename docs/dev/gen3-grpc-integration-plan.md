# Gen3 gRPC Integration Plan

## Status

**Library work**: Complete on `span-panel-api` branch
[`grpc_addition`](https://github.com/SpanPanel/span-panel-api/tree/grpc_addition) — version **1.1.15**.

**Integration work**: Phases 1, 2a, and 2b complete on branch `gen3-grpc-integration` — version **1.3.2**.

---

## Background

PR #169 (`Griswoldlabs:gen3-grpc-support`) demonstrated Gen3 panel (MLO48 /
MAIN40) gRPC connectivity by placing transport-layer code directly inside the
integration under `custom_components/span_panel/gen3/`.  Transport code
belongs in the `span-panel-api` library instead.

The library's `grpc_addition` branch introduces:

- `PanelCapability` flags for runtime feature advertisement
- `SpanPanelClientProtocol` + capability Protocol mixins for static type narrowing
- `SpanPanelSnapshot` / `SpanCircuitSnapshot` — transport-agnostic data models
- `SpanGrpcClient` — the Gen3 gRPC transport (migrated from PR #169's `gen3/`)
- `create_span_client()` — factory with auto-detection

**Reference**: `span-panel-api/docs/dev/grpc-transport-design.md` on the
`grpc_addition` branch contains the full transport-layer architecture and
interface specification.

---

## Phase 1 — Completed Changes

### 1. `const.py` — Add `CONF_PANEL_GENERATION`

Added a single new constant for the config-entry key that stores the panel
generation chosen by the user or detected at setup time:

```python
CONF_PANEL_GENERATION = "panel_generation"
```

Note: PR #169's `gen3/` directory was never present on this branch, so there
are no local generation constants to remove.  `PanelGeneration` is available
from the library and used where needed (e.g. `async_step_gen3_setup`).

### 2. `span_panel_api.py` — Expose `capabilities` Property

Rather than restructuring `SpanPanelApi` to accept a protocol type (which
would break the existing init signature used throughout the integration),
a `capabilities` property is added that delegates directly to the underlying
client.  This is the single authoritative source for capability data:

```python
from span_panel_api import PanelCapability

@property
def capabilities(self) -> PanelCapability:
    if self._client is not None:
        return self._client.capabilities
    return PanelCapability.GEN2_FULL
```

The `_client` attribute is still typed as `SpanPanelClient | None`.  Full
migration to `SpanPanelClientProtocol` (accepting any transport) is deferred
to Phase 2 — it requires updating `_create_client()` to instantiate
`SpanGrpcClient` for Gen3 entries, which needs hardware to validate.

### 3. `__init__.py` — Capability-Gated Platform Loading

The static `PLATFORMS` list is replaced with a capability-driven build at
entry setup time.  The loaded platform set is stored per entry so
`async_unload_entry` always unloads exactly what was loaded:

```python
_BASE_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]

_CAPABILITY_PLATFORMS: dict[PanelCapability, Platform] = {
    PanelCapability.RELAY_CONTROL:    Platform.SWITCH,
    PanelCapability.PRIORITY_CONTROL: Platform.SELECT,
}

_ACTIVE_PLATFORMS = "active_platforms"  # key in hass.data per entry
```

In `async_setup_entry` (after `api.setup()` completes):

```python
capabilities = span_panel.api.capabilities
active_platforms: list[Platform] = list(_BASE_PLATFORMS)
for cap, platform in _CAPABILITY_PLATFORMS.items():
    if cap in capabilities:
        active_platforms.append(platform)

hass.data[DOMAIN][entry.entry_id] = {
    COORDINATOR: coordinator,
    NAME: name,
    _ACTIVE_PLATFORMS: active_platforms,
}

await hass.config_entries.async_forward_entry_setups(entry, active_platforms)
```

In `async_unload_entry`:

```python
entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
active_platforms = entry_data.get(_ACTIVE_PLATFORMS, PLATFORMS)
unload_ok = await hass.config_entries.async_unload_platforms(entry, active_platforms)
```

**Gen2 result**: all four platforms loaded (`BINARY_SENSOR`, `SENSOR`,
`SWITCH`, `SELECT`) — identical to previous behaviour.

**Gen3 result**: `GEN3_INITIAL` = `PUSH_STREAMING` only → only
`BINARY_SENSOR` + `SENSOR` loaded; no switches, no selects.

### 4. `config_flow.py` — Panel Generation Selector

A dropdown is added to the initial user form so the user can select panel
generation before the first connection attempt:

```python
vol.Optional(CONF_PANEL_GENERATION, default="auto"): selector({
    "select": {
        "options": [
            {"value": "auto",  "label": "Auto-detect"},
            {"value": "gen2",  "label": "Gen2 (OpenAPI/HTTP)"},
            {"value": "gen3",  "label": "Gen3 (gRPC)"},
        ],
        "mode": "dropdown",
    }
})
```

Routing in `async_step_user`:

- **gen2 / auto**: existing HTTP validation → `setup_flow()` → JWT auth steps
  (unchanged behaviour).
- **gen3**: redirected to `async_step_gen3_setup`.

`async_step_gen3_setup` probes port 50065 via `SpanGrpcClient`, retrieves the
serial number from `get_snapshot()`, sets an empty access token (Gen3 needs no
auth), then jumps directly to entity naming — bypassing all JWT auth steps.

`CONF_PANEL_GENERATION` is stored in config entry `data` by `create_new_entry`:

```python
data={
    CONF_HOST: host,
    CONF_ACCESS_TOKEN: access_token,   # empty string for Gen3
    CONF_USE_SSL: self.use_ssl,
    CONF_PANEL_GENERATION: self.panel_generation,
    "device_name": device_name,
},
```

### 5. `sensors/factory.py` — Capability-Gated Sensor Creation

Sensor groups that have no data on Gen3 are skipped when the corresponding
capability flag is absent.  All gates read from `span_panel.api.capabilities`:

| Sensor group | Capability gate | Always for Gen2? |
|--------------|-----------------|-----------------|
| DSM status sensors | `DSM_STATE` | ✅ |
| Panel power sensors | _(none — always created)_ | ✅ |
| Panel energy sensors | `ENERGY_HISTORY` | ✅ |
| Hardware status sensors (door, WiFi, cellular) | `HARDWARE_STATUS` | ✅ |
| Circuit power sensors | _(none — always created)_ | ✅ |
| Circuit energy sensors | `ENERGY_HISTORY` | ✅ |
| Battery sensor | `BATTERY` | ✅ (user option still applies) |
| Solar sensors | `SOLAR` | ✅ (user option still applies) |

### 6. `manifest.json` — Version and Dependency

```json
"requirements": ["span-panel-api[grpc]~=1.1.15"],
"version": "1.3.2"
```

---

## Phase 2a — Snapshot Migration (Gen2 hardware, no Gen3 hardware required)

The current data path called four individual API methods and mapped
OpenAPI-generated types into integration domain objects.  This phase migrates
to `get_snapshot()` as the single data-fetch call for both generations,
eliminating any dependency on OpenAPI types above `span-panel-api`.

### `SpanPanel.update()` — single `get_snapshot()` call

```python
snapshot = await self.api.get_snapshot()
self._update_status(SpanPanelHardwareStatus.from_snapshot(snapshot))
self._update_panel(SpanPanelData.from_snapshot(snapshot))
self._update_circuits(
    {cid: SpanPanelCircuit.from_snapshot(cs) for cid, cs in snapshot.circuits.items()}
)
if battery_option_enabled:
    self._update_storage_battery(SpanPanelStorageBattery.from_snapshot(snapshot))
```

### `from_snapshot()` factories

Each domain object gained a `from_snapshot()` classmethod.  Gen3-only fields
(e.g. `main_voltage_v`) are populated if present; Gen2-only fields (energy,
relay state, DSM) default to `None` / `""` / `0.0` when absent.  Entity
classes that read those fields are already gated on the corresponding
`PanelCapability` flag and are never created for Gen3.

---

## Phase 2b — Gen3 Runtime Wiring (Complete)

Depends on Phase 2a being complete.

### `span_panel_api.py` — Gen3 client creation

`_create_client()` instantiates `SpanGrpcClient` when
`panel_generation == "gen3"`:

```python
if self._panel_generation == "gen3":
    from span_panel_api.grpc import SpanGrpcClient
    self._client = SpanGrpcClient(host=self.host)
    return
```

`setup()` for Gen3 calls `connect()` then `start_streaming()` on the gRPC
client.  `_ensure_authenticated()` is a no-op for Gen3 (no JWT).

`register_push_callback()` was added to expose callback registration without
requiring callers to access the private `_client`:

```python
def register_push_callback(self, cb: Callable[[], None]) -> Callable[[], None] | None:
    if self._client is None or not isinstance(self._client, StreamingCapableProtocol):
        return None
    return cast(StreamingCapableProtocol, self._client).register_callback(cb)
```

### `coordinator.py` — Push-streaming extensions to `SpanPanelCoordinator`

Rather than a separate coordinator class, push-streaming behaviour was folded
into the existing `SpanPanelCoordinator` via capability detection at init time:

```python
is_push_streaming = PanelCapability.PUSH_STREAMING in span_panel.api.capabilities

if is_push_streaming:
    update_interval = None          # disable polling timer for Gen3
else:
    update_interval = timedelta(seconds=scan_interval_seconds)

super().__init__(..., update_interval=update_interval)

if is_push_streaming:
    self._register_push_callback()
```

Key methods added:

- `_register_push_callback()` — calls `span_panel.api.register_push_callback(self._on_push_data)`
- `_on_push_data()` — sync callback; guards against concurrent tasks with `_push_update_pending` flag; schedules `_async_push_update`
- `_async_push_update()` — calls `span_panel.update()` then `async_set_updated_data(span_panel)`
- `async_shutdown()` override — unregisters the push callback before delegating to `super()`

**Rationale for extending vs. subclassing**: A single coordinator class is
easier to maintain and avoids duplicating all the migration/reload logic.  The
`update_interval=None` + push-callback approach is the idiomatic HA pattern
for push-driven coordinators.

### `__init__.py` — `async_unload_entry` cleanup fix

`async_unload_entry` was looking for `coordinator.span_panel_api` (which does
not exist).  Fixed to `coordinator.span_panel` so `span_panel.close()` is
reliably called during unload, stopping the gRPC streaming task and closing
the channel.

### `__init__.py` — Migration stamps `panel_generation` on Gen2 upgrades

In `async_migrate_entry` (v1 → v2), `CONF_PANEL_GENERATION: "gen2"` is added
to the config entry data if absent.  All v1 entries pre-date Gen3 support and
are definitively Gen2:

```python
migrated_data = dict(config_entry.data)
if CONF_PANEL_GENERATION not in migrated_data:
    migrated_data[CONF_PANEL_GENERATION] = "gen2"
```

### `span_panel_api.py` — Simulation always uses Gen2 transport

Gen3 has no simulation infrastructure; simulation is a `SpanPanelClient` (Gen2 REST) feature only.
Rather than blocking the combination in the UI, `SpanPanelApi.__init__` silently normalises
`_panel_generation` to `"gen2"` whenever `simulation_mode=True`:

```python
self._panel_generation = "gen2" if simulation_mode else panel_generation
```

This means a user can leave the generation dropdown at any value and check the simulator box —
the correct Gen2 transport is used automatically.  No config flow guard or translation error
key is needed.

### `sensors/factory.py` + `sensor_definitions.py` — Gen3-only power metrics

Gen3 exposes per-circuit `voltage_v`, `current_a`, `apparent_power_va`,
`reactive_power_var`, `frequency_hz`, `power_factor` — fields that have no
Gen2 equivalent.  These are defined in `CIRCUIT_GEN3_SENSORS` and
`PANEL_GEN3_SENSORS`, both gated on `PanelCapability.PUSH_STREAMING`:

```python
if PanelCapability.PUSH_STREAMING in capabilities:
    entities.extend(create_gen3_circuit_sensors(coordinator, circuits))
    entities.extend(create_panel_gen3_sensors(coordinator))
```

---

## Open Questions

| # | Question | Decision |
|---|----------|----------|
| 1 | Where to store detected generation? | `entry.data` as `CONF_PANEL_GENERATION` ✅ |
| 2 | Push coordinator: separate class or flag? | Flag + extensions in existing `SpanPanelCoordinator`; `update_interval=None` disables polling ✅ |
| 3 | Gen3 sensors: existing file with capability-gates or new file? | Capability-gates in existing `sensors/factory.py` ✅ |
| 4 | `get_snapshot()` replace or coexist with individual Gen2 calls? | Replace — `update()` calls `get_snapshot()` exclusively ✅ |
| 5 | Minimum `span-panel-api` version in `manifest.json`? | `~=1.1.15` with `[grpc]` extra ✅ |
| 6 | Gen3 entity architecture: separate classes, translation layer, or shared base? | Shared base via `SpanCircuitSnapshot`; Gen3-only metrics in dedicated sensor definitions gated on `PUSH_STREAMING` ✅ |

---

## Sequencing

```text
[span-panel-api grpc_addition v1.1.15] ──── complete ────►
        ↓
[span gen3-grpc-integration v1.3.2]
  Phase 1 complete:
    ✅ const.py — CONF_PANEL_GENERATION
    ✅ span_panel_api.py — capabilities property
    ✅ __init__.py — capability-gated platform loading
    ✅ config_flow.py — generation dropdown + async_step_gen3_setup
    ✅ sensors/factory.py — capability-gated sensor groups
    ✅ manifest.json — version 1.3.2, span-panel-api[grpc]~=1.1.15
  Phase 2a complete:
    ✅ span_panel_api.py — get_snapshot() delegates to client.get_snapshot()
    ✅ span_panel.py — update() calls get_snapshot(); populates all domain objects
    ✅ span_panel_circuit.py — from_snapshot(SpanCircuitSnapshot) factory
    ✅ span_panel_data.py — from_snapshot(SpanPanelSnapshot) factory
    ✅ span_panel_hardware_status.py — from_snapshot(SpanPanelSnapshot) factory
    ✅ span_panel_storage_battery.py — from_snapshot(SpanPanelSnapshot) factory
  Phase 2b complete:
    ✅ span_panel_api.py — _create_client() Gen3 branch; register_push_callback()
    ✅ coordinator.py — push-streaming extensions; _register_push_callback(),
                        _on_push_data(), _async_push_update(), async_shutdown()
    ✅ __init__.py — async_unload_entry cleanup fix; migration stamps panel_generation
    ✅ span_panel_api.py — simulation normalises _panel_generation to "gen2"
    ✅ config_flow.py — Gen3 + simulation no longer needs a guard (normalised at API layer)
    ✅ sensor_definitions.py — CIRCUIT_GEN3_SENSORS, PANEL_GEN3_SENSORS
    ✅ sensors/factory.py — Gen3-only sensor creation gated on PUSH_STREAMING
  Circuit IID mapping bug fixed (span-panel-api grpc/client.py):
    ✅ Removed hardcoded METRIC_IID_OFFSET=27 — wrong for MLO48 (reported in PR #169)
    ✅ _parse_instances() now pairs trait 16 / trait 26 IIDs by sorted position
    ✅ CircuitInfo.name_iid stores trait 16 IID for correct GetRevision calls
    ✅ _metric_iid_to_circuit reverse map enables O(1) streaming lookup
        ↓
        → PR review + Gen3 hardware validation
```

The library branch must be merged and published **before** this integration
branch can be merged, since the integration depends on the new library API.

---

## Files Affected in This Integration

| File | Change | Status |
|------|--------|--------|
| `custom_components/span_panel/const.py` | Added `CONF_PANEL_GENERATION` | ✅ Done |
| `custom_components/span_panel/span_panel_api.py` | `capabilities` property; `_create_client()` Gen3 branch; `register_push_callback()`; Gen3 `setup()`/`ping()`/`close()` paths | ✅ Done |
| `custom_components/span_panel/__init__.py` | Capability-gated platform loading; `async_unload_entry` fix; migration stamps `panel_generation` | ✅ Done |
| `custom_components/span_panel/config_flow.py` | Generation dropdown; `async_step_gen3_setup` | ✅ Done |
| `custom_components/span_panel/sensors/factory.py` | Capability-gated sensor groups; Gen3-only sensor creation | ✅ Done |
| `custom_components/span_panel/sensor_definitions.py` | `CIRCUIT_GEN3_SENSORS`, `PANEL_GEN3_SENSORS` | ✅ Done |
| `custom_components/span_panel/manifest.json` | Version 1.3.2; `span-panel-api[grpc]~=1.1.15` | ✅ Done |
| `custom_components/span_panel/span_panel.py` | `update()` calls `get_snapshot()`; all domain objects from snapshot | ✅ Done |
| `custom_components/span_panel/span_panel_circuit.py` | `from_snapshot(SpanCircuitSnapshot)` factory | ✅ Done |
| `custom_components/span_panel/span_panel_data.py` | `from_snapshot(SpanPanelSnapshot)` factory; Gen3 main feed fields | ✅ Done |
| `custom_components/span_panel/span_panel_hardware_status.py` | `from_snapshot(SpanPanelSnapshot)` factory | ✅ Done |
| `custom_components/span_panel/span_panel_storage_battery.py` | `from_snapshot(SpanPanelSnapshot)` factory | ✅ Done |
| `custom_components/span_panel/coordinator.py` | Push-streaming extensions: capability detection, `update_interval=None` for Gen3, push callback methods, `async_shutdown()` | ✅ Done |
| `custom_components/span_panel/translations/` | No simulation-specific strings needed | ✅ Done |
