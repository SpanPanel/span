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

## Phase 2 — Deferred (Requires Gen3 Hardware)

The following items are designed and documented but require a real Gen3 panel
(MLO48 / MAIN40) to implement and validate:

### Coordinator — Push vs. Poll

```python
if PanelCapability.PUSH_STREAMING in caps:
    coordinator = SpanPanelPushCoordinator(hass, api)
else:
    coordinator = SpanPanelCoordinator(hass, api)
```

`SpanPanelPushCoordinator` calls `client.register_callback()` and triggers
`async_set_updated_data()` on each streaming notification.

### `SpanPanelApi` — Accept Protocol, Not Concrete Class

`_create_client()` needs to instantiate `SpanGrpcClient` when
`CONF_PANEL_GENERATION` is `"gen3"`, and `SpanPanelClient` otherwise.
The `_client` attribute should be widened to `SpanPanelClientProtocol | None`
once this is in place.

### Sensors — Gen3 Power Metrics

Gen3 exposes per-circuit `voltage_v`, `current_a`, `apparent_power_va`,
`reactive_power_var`, `frequency_hz`, `power_factor` via `SpanCircuitSnapshot`.
These become optional sensor entities, created only when the field is not `None`
in the first snapshot:

```python
first_circuit = next(iter(snapshot.circuits.values()), None)
if first_circuit and first_circuit.voltage_v is not None:
    entities.extend(build_gen3_circuit_sensors(snapshot))
```

---

## Open Questions — Resolved

| # | Question | Decision |
|---|----------|----------|
| 1 | Where to store detected generation? | `entry.data` as `CONF_PANEL_GENERATION` ✅ |
| 2 | Push coordinator: separate class or flag? | Separate class (`SpanPanelPushCoordinator`) — deferred Phase 2 |
| 3 | Gen3 sensors: existing file with capability-gates or new file? | Capability-gates in existing `sensors/factory.py` ✅ |
| 4 | `get_snapshot()` replace or coexist with individual Gen2 calls? | Coexist; `get_snapshot()` used only in Gen3 config-flow probe for now ✅ |
| 5 | Minimum `span-panel-api` version in `manifest.json`? | `~=1.1.15` with `[grpc]` extra ✅ |

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
  Phase 2 pending (Gen3 hardware required):
    ⏳ coordinator.py — SpanPanelPushCoordinator
    ⏳ span_panel_api.py — _create_client() Gen3 branch
    ⏳ sensors/factory.py — Gen3 power-metric sensors
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
| `custom_components/span_panel/coordinator.py` | Add `SpanPanelPushCoordinator` | ⏳ Phase 2 |
| `custom_components/span_panel/span_panel_api.py` | `_create_client()` Gen3 branch; widen `_client` to `SpanPanelClientProtocol` | ⏳ Phase 2 |
| `custom_components/span_panel/sensors/factory.py` | Gen3 power-metric sensor entities | ⏳ Phase 2 |
| `custom_components/span_panel/binary_sensor.py` | Gen3 binary sensors (None-guarded) | ⏳ Phase 2 |
