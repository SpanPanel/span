# Gen3 gRPC Integration Plan

## Status

**Library work**: Complete on `span-panel-api` branch
[`grpc_addition`](https://github.com/SpanPanel/span-panel-api/tree/grpc_addition) — version **1.1.15**.

**Integration work**: Phase 1 complete on branch `gen3-grpc-integration` — version **1.3.2**.
Phase 2 (push coordinator, Gen3 power-metric sensors) is deferred until Gen3 hardware
is available for testing.

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

**Reference**: `span-panel-api/docs/Dev/grpc-transport-design.md` on the
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
    """Return the panel's capabilities.

    Reads directly from the underlying client so the value reflects the
    connected transport (GEN2_FULL for OpenAPI/HTTP, GEN3_INITIAL for gRPC).
    Falls back to GEN2_FULL when the client has not yet been created.
    """
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
    PanelCapability.RELAY_CONTROL:   Platform.SWITCH,
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

**Gen3 result** (when `_create_client()` is updated in Phase 2):
`GEN3_INITIAL` = `PUSH_STREAMING` only → only `BINARY_SENSOR` + `SENSOR`
loaded; no switches, no selects.

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

The current data path calls four individual API methods and maps OpenAPI-generated
types into integration domain objects.  This phase migrates to `get_snapshot()` as
the single data-fetch call for both generations, eliminating any dependency on
OpenAPI types above `span-panel-api`.

### Why This Must Precede Phase 2b

Phase 2b slots in `SpanGrpcClient` behind the same interface.  If entity classes
still read from OpenAPI-backed properties, Gen3 data has nowhere to go.  Completing
this migration means entities read from `SpanCircuitSnapshot` fields — the same
fields `SpanGrpcClient.get_snapshot()` populates — so Gen3 entities require no
additional changes for the metrics that both generations share.

### `SpanPanelApi.update()` — call `get_snapshot()` instead of four methods

Replace the four individual calls with one:

```python
# Before
status = await self._client.get_status()
panel  = await self._client.get_panel_state()
circs  = await self._client.get_circuits()
batt   = await self._client.get_storage_soe()

# After
snapshot = await self._client.get_snapshot()
```

`SpanPanelClient.get_snapshot()` already makes those same four calls internally —
this is a refactor, not a behaviour change.

### `SpanPanel` — populate from `SpanPanelSnapshot`

`SpanPanel` currently holds the four OpenAPI response objects.  Replace with a
single `SpanPanelSnapshot` (or equivalent fields derived from it):

```python
self._snapshot = snapshot          # SpanPanelSnapshot
self.main_power_w   = snapshot.main_power_w
self.grid_power_w   = snapshot.grid_power_w
self.battery_soe    = snapshot.battery_soe
self.dsm_state      = snapshot.dsm_state
# ... etc.
```

### `SpanPanelCircuit` — wrap `SpanCircuitSnapshot`

`SpanPanelCircuit` currently wraps the OpenAPI `Circuit` type.  Redirect it to
wrap `SpanCircuitSnapshot` instead, mapping field names as needed:

| Old (`Circuit` field) | New (`SpanCircuitSnapshot` field) |
|-----------------------|----------------------------------|
| `instantPowerW` | `power_w` |
| `name` | `name` |
| `relayState` | `relay_state` |
| `priority` | `priority` |
| `tabs` | `tabs` |
| `energyAccumImportWh` | `energy_consumed_wh` |
| `energyAccumExportWh` | `energy_produced_wh` |

Entity classes need no changes — they continue reading from `SpanPanelCircuit`
properties.  Only the backing field source changes.

---

## Phase 2b — Gen3 Runtime Wiring (Gen3 hardware required)

Depends on Phase 2a being complete.  The entity data path must already read from
`SpanCircuitSnapshot`-backed properties before Gen3 data can flow through it.

### `SpanPanelApi._create_client()` — Gen3 branch

```python
def _create_client(self) -> SpanPanelClientProtocol:
    if self._config.get(CONF_PANEL_GENERATION) == "gen3":
        from span_panel_api.grpc import SpanGrpcClient  # pylint: disable=import-outside-toplevel
        from span_panel_api.grpc.const import DEFAULT_GRPC_PORT  # pylint: disable=import-outside-toplevel
        return SpanGrpcClient(host=self._host, port=DEFAULT_GRPC_PORT)
    return SpanPanelClient(host=self._host, ...)
```

`_client` is widened to `SpanPanelClientProtocol | None`.  All callers that
currently rely on `SpanPanelClient`-specific methods (authenticate, etc.) must
be guarded by `isinstance(self._client, AuthCapableProtocol)`.

### `coordinator.py` — `SpanPanelPushCoordinator`

```python
class SpanPanelPushCoordinator(DataUpdateCoordinator):
    async def async_setup(self) -> None:
        assert isinstance(self._api.client, StreamingCapableProtocol)
        self._unsub = self._api.client.register_callback(self._on_push)
        await self._api.client.start_streaming()

    def _on_push(self) -> None:
        # get_snapshot() on Gen3 is a cheap in-memory conversion — no I/O
        snapshot = asyncio.get_event_loop().run_until_complete(
            self._api.client.get_snapshot()
        )
        self.async_set_updated_data(snapshot)

    async def async_teardown(self) -> None:
        if self._unsub:
            self._unsub()
        await self._api.client.stop_streaming()
```

### `__init__.py` — choose coordinator at setup time

```python
caps = span_panel.api.capabilities
if PanelCapability.PUSH_STREAMING in caps:
    coordinator = SpanPanelPushCoordinator(hass, span_panel.api)
    await coordinator.async_setup()
else:
    coordinator = SpanPanelCoordinator(hass, span_panel.api)
    await coordinator.async_config_entry_first_refresh()
```

### `SpanGrpcClient` hardware validation

Before wiring the runtime path, validate the library client against a real panel:

- `connect()` populates circuits with correct IIDs and names
- `Subscribe` stream delivers notifications at expected cadence
- `_decode_circuit_metrics()` produces correct power/voltage values
- Dual-phase circuit detection works correctly
- `get_snapshot()` returns consistent data during active streaming

See `span-panel-api/docs/Dev/grpc-transport-design.md` → "Hardware Validation
Required" for the full validation checklist.

### Sensors — Gen3-only power metrics

Gen3 exposes per-circuit `voltage_v`, `current_a`, `apparent_power_va`,
`reactive_power_var`, `frequency_hz`, `power_factor` — fields that have no Gen2
equivalent.  These are added as new entity classes that read directly from
`SpanCircuitSnapshot`, created only when the field is not `None` in the first
snapshot:

```python
first_circuit = next(iter(snapshot.circuits.values()), None)
if first_circuit and first_circuit.voltage_v is not None:
    entities.extend(build_gen3_circuit_sensors(snapshot))
```

Sensors that exist on both generations (circuit power, panel power) need no new
entity classes — after Phase 2a they already read from `SpanCircuitSnapshot`.

---

## Open Questions

| # | Question | Decision |
|---|----------|----------|
| 1 | Where to store detected generation? | `entry.data` as `CONF_PANEL_GENERATION` ✅ |
| 2 | Push coordinator: separate class or flag? | Separate class (`SpanPanelPushCoordinator`) — Phase 2b ⏳ |
| 3 | Gen3 sensors: existing file with capability-gates or new file? | Capability-gates in existing `sensors/factory.py` ✅ |
| 4 | `get_snapshot()` replace or coexist with individual Gen2 calls? | Replace — `update()` calls `get_snapshot()` exclusively (Phase 2a) ⏳ |
| 5 | Minimum `span-panel-api` version in `manifest.json`? | `~=1.1.15` with `[grpc]` extra ✅ |
| 6 | Gen3 entity architecture: separate classes, translation layer, or shared base? | Shared base via `SpanCircuitSnapshot` (Phase 2a migration eliminates the choice — existing entity classes work unchanged for overlapping metrics) ✅ |

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
  Phase 2a pending (Gen2 hardware sufficient):
    ⏳ span_panel_api.py — update() calls get_snapshot()
    ⏳ span_panel.py — populate from SpanPanelSnapshot
    ⏳ span_panel_circuit.py — wrap SpanCircuitSnapshot instead of OpenAPI Circuit
  Phase 2b pending (Gen3 hardware required, depends on 2a):
    ⏳ span_panel_api.py — _create_client() Gen3 branch; widen _client type
    ⏳ coordinator.py — SpanPanelPushCoordinator
    ⏳ __init__.py — coordinator selection by capability
    ⏳ sensors/factory.py — Gen3-only sensor entities (voltage, current, etc.)
        ↓
        → PR review
```

The library branch must be merged and published **before** this integration
branch can be merged, since the integration depends on the new library API.

---

## Files Affected in This Integration

| File | Change | Status |
|------|--------|--------|
| `custom_components/span_panel/const.py` | Added `CONF_PANEL_GENERATION` | ✅ Done |
| `custom_components/span_panel/span_panel_api.py` | Added `capabilities` property | ✅ Done |
| `custom_components/span_panel/__init__.py` | Capability-gated platform loading; store `active_platforms` per entry | ✅ Done |
| `custom_components/span_panel/config_flow.py` | Generation dropdown; `async_step_gen3_setup`; store generation in entry data | ✅ Done |
| `custom_components/span_panel/sensors/factory.py` | Capability-gated sensor groups (DSM, energy, hardware, battery, solar) | ✅ Done |
| `custom_components/span_panel/manifest.json` | Version 1.3.2; `span-panel-api[grpc]~=1.1.15` | ✅ Done |
| `custom_components/span_panel/span_panel_api.py` | `update()` calls `get_snapshot()`; widen `_client` to protocol type | ⏳ Phase 2a/2b |
| `custom_components/span_panel/span_panel.py` | Populate from `SpanPanelSnapshot` instead of OpenAPI types | ⏳ Phase 2a |
| `custom_components/span_panel/span_panel_circuit.py` | Wrap `SpanCircuitSnapshot` instead of OpenAPI `Circuit` | ⏳ Phase 2a |
| `custom_components/span_panel/coordinator.py` | Add `SpanPanelPushCoordinator`; coordinator selection in setup | ⏳ Phase 2b |
| `custom_components/span_panel/__init__.py` | Coordinator selection by `PUSH_STREAMING` capability | ⏳ Phase 2b |
| `custom_components/span_panel/sensors/factory.py` | Gen3-only sensor entities (voltage, current, apparent/reactive power, frequency, power factor) | ⏳ Phase 2b |
