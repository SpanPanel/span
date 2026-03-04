# Implementation Plan: DPS Select + BESS Connected Binary Sensor

## Summary

Two new entities:

1. **`select.{device}_dominant_power_source`** — sets `dominant-power-source` on the panel via eBus MQTT `/set`
2. **`binary_sensor.{device}_bess_connected`** — promotes `bess/connected` from a battery sensor attribute to a first-class entity

## Changes by File

### 1. API Library: `span-panel-api`

#### `protocol.py` — Add `PanelControlProtocol`

The existing `CircuitControlProtocol` is circuit-scoped. DPS is a panel-level control. Add a separate protocol:

```python
@runtime_checkable
class PanelControlProtocol(Protocol):
    """Control protocol for panel-level settable properties."""

    async def set_dominant_power_source(self, value: str) -> None: ...
```

This keeps circuit and panel controls in separate protocols, which is cleaner than mixing panel-level methods into `CircuitControlProtocol`.

#### `mqtt/client.py` — Implement `set_dominant_power_source`

Add method to `SpanMqttClient`:

```python
async def set_dominant_power_source(self, value: str) -> None:
    """Publish dominant-power-source change to the core node.

    Args:
        value: DPS enum value (GRID, BATTERY, NONE, GENERATOR, PV)
    """
    core_node = self._homie._find_node_by_type(TYPE_CORE)
    if core_node is None:
        raise SpanPanelServerError("Core node not found in topology")
    topic = PROPERTY_SET_TOPIC_FMT.format(
        serial=self._serial_number, node=core_node, prop="dominant-power-source"
    )
    if self._bridge is not None:
        self._bridge.publish(topic, value, qos=1)
```

Note: `_find_node_by_type` is on `HomieDeviceConsumer` (private). Either:

- Expose it as a public method on `HomieDeviceConsumer`
- Or have the client store the core node ID when the description arrives

The cleaner approach is to expose a public `find_node_by_type()` on `HomieDeviceConsumer` since `_find_node_by_type` is already a well-defined lookup with no
side effects.

#### `mqtt/client.py` — Update class docstring

Add `PanelControlProtocol` to the list of implemented protocols.

#### Tests

- `test_mqtt_client.py`: Test that `set_dominant_power_source("BATTERY")` publishes to `ebus/5/{serial}/{core_node}/dominant-power-source/set`
- Test that missing core node raises `SpanPanelServerError`

### 2. Integration: `binary_sensor.py` — BESS Connected

#### Add description

Add to the binary sensor descriptions (after the existing panel sensors):

```python
BESS_BINARY_SENSOR = SpanPanelBinarySensorEntityDescription(
    key="bess_connected",
    name="BESS Connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    value_fn=lambda s: s.battery.connected,
)
```

#### Conditional creation in `async_setup_entry`

Only create when BESS is commissioned (same `has_bess()` check used by battery sensors in `factory.py`):

```python
if has_bess(snapshot):
    entities.append(SpanPanelBinarySensor(coordinator, BESS_BINARY_SENSOR))
```

#### Remove attribute from `sensors/panel.py`

Remove lines 228-229 (`if batt.connected is not None: attributes["connected"] = batt.connected`) from `_add_bess_attributes()`. The data is now exposed as its
own entity.

### 3. Integration: `select.py` — DPS Select

#### Add DPS description

Define a panel-level select description (separate from circuit selects):

```python
DPS_OPTIONS: list[str] = ["GRID", "BATTERY", "GENERATOR", "PV"]

DPS_DESCRIPTION = SpanPanelSelectEntityDescriptionWrapper(
    key="dominant_power_source",
    name="Dominant Power Source",
    icon="mdi:transmission-tower",
    options_fn=lambda _: DPS_OPTIONS,
    current_option_fn=lambda snapshot: snapshot.dominant_power_source or "GRID",
)
```

Note: `current_option_fn` takes a `SpanCircuitSnapshot` in the existing wrapper. The DPS select needs a `SpanPanelSnapshot`. This means we either:

- Create a new entity class `SpanPanelSelect` that operates on snapshots (not circuits)
- Or create a second wrapper dataclass for panel-level selects

A new `SpanPanelDPSSelect` entity class is the right approach — it mirrors the existing `SpanPanelCircuitsSelect` but is panel-scoped.

#### `SpanPanelDPSSelect` entity class

Key methods:

- `__init__`: Set unique ID, device info (panel device), initial state
- `current_option` property: Read `snapshot.dominant_power_source` from coordinator data
- `async_select_option`: Call `client.set_dominant_power_source(option)`, request coordinator refresh
- Guard with `hasattr(client, "set_dominant_power_source")` for simulation mode

#### Conditional creation in `async_setup_entry`

Only create when MQTT transport is active (DPS is v2-only, not available via REST). Gate on `PanelCapability.EBUS_MQTT`:

```python
if PanelCapability.EBUS_MQTT in coordinator.span_panel.api.capabilities:
    entities.append(SpanPanelDPSSelect(coordinator))
```

#### Unique ID

Use panel serial + key: `span_{serial}_dominant_power_source`

### 4. Integration: `sensor_definitions.py` — No changes

`dominant_power_source` already exists as a read-only sensor. The select entity is additive — users get both the current value (sensor) and the ability to
override it (select).

### 5. Tests

#### `tests/test_binary_sensor.py` or new test file

- `test_bess_connected_true` — `battery.connected = True` → `is_on = True`
- `test_bess_connected_false` — `battery.connected = False` → `is_on = False`
- `test_bess_connected_none` — `battery.connected = None` → `is_on = None`
- `test_bess_connected_not_created_without_bess` — no BESS → entity not created

#### `tests/test_select.py` or new test file

- `test_dps_select_options` — verify options list
- `test_dps_select_current_option` — reads from snapshot
- `test_dps_select_set_option` — calls `set_dominant_power_source`
- `test_dps_select_simulation_mode` — graceful degradation without method
- `test_dps_select_not_created_without_mqtt` — REST mode → entity not created

## Files Modified

| File                                                 | Change                                           |
| ---------------------------------------------------- | ------------------------------------------------ |
| `span-panel-api/src/span_panel_api/protocol.py`      | Add `PanelControlProtocol`                       |
| `span-panel-api/src/span_panel_api/mqtt/homie.py`    | Expose `find_node_by_type()`                     |
| `span-panel-api/src/span_panel_api/mqtt/client.py`   | Add `set_dominant_power_source()`                |
| `span/custom_components/span_panel/binary_sensor.py` | Add `BESS_BINARY_SENSOR`, conditional creation   |
| `span/custom_components/span_panel/sensors/panel.py` | Remove `connected` attribute from battery sensor |
| `span/custom_components/span_panel/select.py`        | Add `SpanPanelDPSSelect`, `DPS_DESCRIPTION`      |
| Tests                                                | Binary sensor + select tests                     |

## Verification

1. `python -m pytest tests/ -q` in both repos — all tests pass
2. Panel with BESS → `binary_sensor.{device}_bess_connected` appears
3. Panel without BESS → binary sensor not created
4. MQTT panel → `select.{device}_dominant_power_source` appears with 5 options
5. REST/simulation panel → select not created
6. Setting a DPS value publishes to correct MQTT topic
7. `connected` attribute removed from battery power sensor
